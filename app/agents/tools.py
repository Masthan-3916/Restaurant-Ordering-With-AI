from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gemini import GeminiClient
from app.config.settings import settings
from app.models import ChatMessage, ChatSession, MenuItem, PendingAction, ToolRun, User, utcnow
from app.rag.vector_store import PolicyVectorStore
from app.repositories.catalog import CatalogRepository
from app.repositories.orders import OrderRepository
from app.services.catalog import CatalogService
from app.services.errors import AppError
from app.services.orders import OrderService


def function_declarations() -> list[types.FunctionDeclaration]:
    schema = types.Schema
    object_type = types.Type.OBJECT
    string_type = types.Type.STRING
    integer_type = types.Type.INTEGER
    number_type = types.Type.NUMBER
    boolean_type = types.Type.BOOLEAN
    return [
        types.FunctionDeclaration(
            name="search_menu_items",
            description="Search the live active menu. Use this for menu availability, dish suggestions, prices and filters.",
            parameters=schema(
                type=object_type,
                properties={
                    "vegetarian": schema(type=boolean_type, nullable=True),
                    "category": schema(type=string_type, nullable=True),
                    "max_price": schema(type=number_type, nullable=True),
                    "keyword": schema(type=string_type, nullable=True),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="get_menu_item",
            description="Get live details, price and available portions for one active menu item by its ID.",
            parameters=schema(type=object_type, properties={"item_id": schema(type=integer_type, minimum=1)}, required=["item_id"]),
        ),
        types.FunctionDeclaration(
            name="check_availability",
            description="Check whether a requested quantity of an active menu item is currently available. Availability is not a reservation.",
            parameters=schema(
                type=object_type,
                properties={
                    "item_id": schema(type=integer_type, minimum=1),
                    "quantity": schema(type=integer_type, minimum=1, maximum=10),
                },
                required=["item_id", "quantity"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_order_status",
            description="Get one order belonging to the authenticated user. Never guess its ID or status.",
            parameters=schema(type=object_type, properties={"order_id": schema(type=integer_type, minimum=1)}, required=["order_id"]),
        ),
        types.FunctionDeclaration(
            name="retrieve_policy",
            description="Retrieve active policy or ingredient evidence from indexed restaurant documents.",
            parameters=schema(type=object_type, properties={"question": schema(type=string_type, minLength=1, maxLength=1000)}, required=["question"]),
        ),
        types.FunctionDeclaration(
            name="propose_order",
            description="Prepare a draft order for explicit user confirmation. This function never places an order.",
            parameters=schema(
                type=object_type,
                properties={
                    "lines": schema(
                        type=types.Type.ARRAY,
                        minItems=1,
                        maxItems=20,
                        items=schema(
                            type=object_type,
                            properties={
                                "item_id": schema(type=integer_type, minimum=1),
                                "quantity": schema(type=integer_type, minimum=1, maximum=10),
                            },
                            required=["item_id", "quantity"],
                        ),
                    )
                },
                required=["lines"],
            ),
        ),
        types.FunctionDeclaration(
            name="propose_cancel",
            description="Prepare cancellation of an order belonging to the authenticated user for explicit confirmation. This never cancels directly.",
            parameters=schema(type=object_type, properties={"order_id": schema(type=integer_type, minimum=1)}, required=["order_id"]),
        ),
    ]


class AgentTools:
    def __init__(self, db: Session, session: ChatSession, user: User, user_message: ChatMessage, gemini: GeminiClient):
        self.db = db
        self.session = session
        self.user = user
        self.user_message = user_message
        self.gemini = gemini

    def _audit(self, name: str, args: dict, result: dict, status: str) -> None:
        self.db.add(ToolRun(message_id=self.user_message.id, tool_name=name, args=args, result=result, status=status))
        self.db.commit()

    def _pending(self, action: str, payload: dict) -> PendingAction:
        unresolved = self.db.scalar(
            select(PendingAction).where(
                PendingAction.session_id == self.session.id,
                PendingAction.user_id == self.user.id,
                PendingAction.status == "PENDING",
                PendingAction.expires_at > utcnow(),
            )
        )
        if unresolved is not None:
            raise AppError("pending_action_exists", "Resolve the current confirmation before requesting another action.", 409)
        pending = PendingAction(
            session_id=self.session.id,
            user_id=self.user.id,
            action=action,
            payload=payload,
            expires_at=utcnow() + timedelta(minutes=settings.order_action_ttl_minutes),
            status="PENDING",
            idempotency_key=str(uuid4()),
        )
        self.db.add(pending)
        self.db.commit()
        self.db.refresh(pending)
        return pending

    def execute(self, name: str, args: dict) -> dict:
        handlers = {
            "search_menu_items": self.search_menu_items,
            "get_menu_item": self.get_menu_item,
            "check_availability": self.check_availability,
            "get_order_status": self.get_order_status,
            "retrieve_policy": self.retrieve_policy,
            "propose_order": self.propose_order,
            "propose_cancel": self.propose_cancel,
        }
        handler = handlers.get(name)
        if handler is None:
            raise AppError("unknown_agent_tool", "The requested assistant tool is not allowed.", 400)
        try:
            result = handler(args)
        except AppError as exc:
            result = {"error": exc.message, "code": exc.code}
            self._audit(name, args, result, "FAILED")
            return result
        self._audit(name, args, result, "SUCCESS")
        return result

    def search_menu_items(self, args: dict) -> dict:
        vegetarian = args.get("vegetarian")
        category = args.get("category")
        keyword = args.get("keyword")
        if vegetarian is not None and not isinstance(vegetarian, bool):
            raise AppError("invalid_tool_arguments", "The vegetarian filter must be a boolean.", 422)
        if category is not None and (not isinstance(category, str) or len(category) > 80):
            raise AppError("invalid_tool_arguments", "Category filters must be 80 characters or fewer.", 422)
        if keyword is not None and (not isinstance(keyword, str) or len(keyword) > 120):
            raise AppError("invalid_tool_arguments", "Keyword filters must be 120 characters or fewer.", 422)
        max_price = args.get("max_price")
        if max_price is not None:
            if isinstance(max_price, bool) or not isinstance(max_price, (int, float, str)):
                raise AppError("invalid_tool_arguments", "Maximum price must be a valid number.", 422)
            try:
                max_price = Decimal(str(max_price))
            except (InvalidOperation, ValueError) as exc:
                raise AppError("invalid_tool_arguments", "Maximum price must be a valid number.", 422) from exc
            if max_price <= 0:
                raise AppError("invalid_tool_arguments", "Maximum price must be greater than zero.", 422)
        items = CatalogRepository(self.db).search(
            vegetarian=vegetarian,
            category=category,
            max_price=max_price,
            keyword=keyword,
        )
        return {
            "items": [
                self._item_view(item)
                for item in items
            ]
        }

    @staticmethod
    def _item_view(item: MenuItem) -> dict:
        return {
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "vegetarian": item.vegetarian,
            "price": str(item.price),
            "available_portions": item.capacity,
        }

    def get_menu_item(self, args: dict) -> dict:
        item_id = args.get("item_id")
        if type(item_id) is not int or item_id < 1:
            raise AppError("invalid_tool_arguments", "Item ID must be a positive integer.", 422)
        item = CatalogService(self.db, CatalogRepository(self.db)).get(item_id)
        return {"item": self._item_view(item)}

    def check_availability(self, args: dict) -> dict:
        item_id = args.get("item_id")
        quantity = args.get("quantity")
        if type(item_id) is not int or item_id < 1:
            raise AppError("invalid_tool_arguments", "Item ID must be a positive integer.", 422)
        if type(quantity) is not int or not 1 <= quantity <= 10:
            raise AppError("invalid_tool_arguments", "Quantity must be an integer from 1 to 10.", 422)
        item = CatalogService(self.db, CatalogRepository(self.db)).get(item_id)
        return {
            "item": self._item_view(item),
            "requested_quantity": quantity,
            "available": item.capacity >= quantity,
        }

    def get_order_status(self, args: dict) -> dict:
        if type(args.get("order_id")) is not int or args["order_id"] < 1:
            raise AppError("invalid_tool_arguments", "Order ID must be a positive integer.", 422)
        order = self._order_service().get(self.user, args["order_id"])
        return {"id": order.id, "status": order.status, "total": str(order.total)}

    def retrieve_policy(self, args: dict) -> dict:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise AppError("invalid_tool_arguments", "Policy question must contain between 1 and 1000 characters.", 422)
        matches = PolicyVectorStore().search(question, self.gemini)
        return {
            "evidence": [
                {
                    "text": match["text"],
                    "filename": match["filename"],
                    "page": match["page"],
                    "chunk_id": match["chunk_id"],
                    "version": match["version"],
                }
                for match in matches
            ]
        }

    def propose_order(self, args: dict) -> dict:
        requested_lines = args.get("lines")
        if not isinstance(requested_lines, list) or not 1 <= len(requested_lines) <= 20:
            raise AppError("invalid_tool_arguments", "An order proposal needs between 1 and 20 lines.", 422)
        quantities: dict[int, int] = {}
        for line in requested_lines:
            if not isinstance(line, dict):
                raise AppError("invalid_tool_arguments", "Each order line must include an item ID and quantity.", 422)
            item_id, quantity = line.get("item_id"), line.get("quantity")
            if type(item_id) is not int or item_id < 1 or type(quantity) is not int or quantity < 1 or quantity > 10:
                raise AppError("invalid_action", "Order lines must use valid item IDs and quantities from 1 to 10.", 422)
            quantities[item_id] = quantities.get(item_id, 0) + quantity
        items: dict[int, MenuItem] = {}
        for item_id, quantity in quantities.items():
            item = CatalogService(self.db, CatalogRepository(self.db)).get(item_id)
            if item.capacity < quantity:
                raise AppError("insufficient_capacity", f"{item.title} does not have enough portions available.", 409)
            items[item_id] = item
        lines = [
            {"item_id": item_id, "quantity": quantity, "title": items[item_id].title, "unit_price": str(items[item_id].price)}
            for item_id, quantity in quantities.items()
        ]
        total = sum((items[item_id].price * quantity for item_id, quantity in quantities.items()), start=items[next(iter(items))].price * 0)
        pending = self._pending("CREATE_ORDER", {"lines": lines, "total": str(total)})
        return {"pending_action_id": pending.id, "action": pending.action, "lines": lines, "total": str(total), "requires_user_confirmation": True}

    def propose_cancel(self, args: dict) -> dict:
        if type(args.get("order_id")) is not int or args["order_id"] < 1:
            raise AppError("invalid_tool_arguments", "Order ID must be a positive integer.", 422)
        order = self._order_service().get(self.user, args["order_id"])
        if order.user_id != self.user.id:
            raise AppError("not_found", "Order not found.", 404)
        if order.status != "CONFIRMED":
            raise AppError("invalid_transition", "Only an order that has not entered preparation can be cancelled.", 409)
        pending = self._pending("CANCEL_ORDER", {"order_id": order.id, "status": order.status})
        return {"pending_action_id": pending.id, "action": pending.action, "order_id": order.id, "status": order.status, "requires_user_confirmation": True}

    def _order_service(self) -> OrderService:
        return OrderService(self.db, CatalogRepository(self.db), OrderRepository(self.db))
