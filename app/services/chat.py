from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.single_agent import SingleAgent
from app.config.settings import settings
from app.models import ChatMessage, ChatSession, Order, PendingAction, User, utcnow
from app.repositories.catalog import CatalogRepository
from app.repositories.orders import OrderRepository
from app.services.catalog import CatalogService
from app.services.errors import AppError
from app.services.orders import OrderService


class ChatService:
    def __init__(self, db: Session):
        self.db = db

    def create_session(self, user: User, title: str) -> ChatSession:
        session = ChatSession(user_id=user.id, title=title)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def list_sessions(self, user: User) -> list[ChatSession]:
        return list(self.db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc())))

    def get_session(self, user: User, session_id: int) -> ChatSession:
        session = self.db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))
        if session is None:
            raise AppError("not_found", "Chat session not found.", 404)
        return session

    def messages(self, user: User, session_id: int) -> list[ChatMessage]:
        self.get_session(user, session_id)
        return list(self.db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.seq_no)))

    def _save_message(self, session: ChatSession, role: str, content: str, metadata: dict | None = None) -> ChatMessage:
        self.db.scalar(select(ChatSession).where(ChatSession.id == session.id).with_for_update())
        seq_no = self.db.scalar(select(func.max(ChatMessage.seq_no)).where(ChatMessage.session_id == session.id)) or 0
        message = ChatMessage(session_id=session.id, seq_no=seq_no + 1, role=role, content=content, metadata_json=metadata or {})
        self.db.add(message)
        session.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(message)
        return message

    def _finish(self, session: ChatSession, user_message: ChatMessage, text: str, mode: str, citations: list[dict] | None = None, pending: dict | None = None) -> dict:
        citations = citations or []
        metadata = {"mode": mode, "citations": citations, "pending_action": pending}
        assistant = self._save_message(session, "assistant", text, metadata)
        return {
            "user_message": self._message_view(user_message),
            "assistant_message": self._message_view(assistant),
            "mode": mode,
            "citations": citations,
            "pending_action": pending,
        }

    @staticmethod
    def _message_view(message: ChatMessage) -> dict:
        return {
            "id": message.id,
            "seq_no": message.seq_no,
            "role": message.role,
            "content": message.content,
            "metadata": message.metadata_json,
            "created_at": message.created_at.isoformat(),
        }

    def ask(self, user: User, session_id: int, text: str) -> dict:
        session = self.get_session(user, session_id)
        user_message = self._save_message(session, "user", text)
        if session.title == "New conversation":
            session.title = text[:80]
            self.db.commit()

        try:
            history = self.messages(user, session_id)[-8:]
            result = SingleAgent(self.db).respond(user, session, user_message, history)
        except AppError as exc:
            pending = self.db.scalar(
                select(PendingAction)
                .where(
                    PendingAction.session_id == session.id,
                    PendingAction.user_id == user.id,
                    PendingAction.status == "PENDING",
                    PendingAction.created_at >= user_message.created_at,
                )
                .order_by(PendingAction.id.desc())
            )
            metadata = {"mode": "GENERAL", "error": exc.code}
            if pending:
                metadata["pending_action"] = self._pending_view(pending)
            self._save_message(session, "assistant", exc.message, metadata)
            raise
        return self._finish(session, user_message, result["answer"], result["mode"], result["citations"], result["pending_action"])

    def confirm_action(self, user: User, session_id: int, action_id: int, confirm: bool) -> dict:
        session = self.get_session(user, session_id)
        pending = self.db.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.session_id == session.id, PendingAction.user_id == user.id)
            .with_for_update()
        )
        if pending is None:
            raise AppError("not_found", "Pending action not found.", 404)
        if pending.status == "CONFIRMED" and pending.result_json:
            return {"status": "CONFIRMED", "result": pending.result_json}
        if pending.status == "PROCESSING":
            result = None
            if pending.action == "CREATE_ORDER":
                order = self.db.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == pending.idempotency_key))
                if order:
                    result = {"order_id": order.id, "status": order.status, "total": str(order.total)}
            elif pending.action == "CANCEL_ORDER":
                order = self._order_service().get(user, pending.payload["order_id"])
                if order.status == "CANCELLED":
                    result = {"order_id": order.id, "status": order.status}
            if result:
                pending.status = "CONFIRMED"
                pending.result_json = result
                self.db.commit()
                return {"status": "CONFIRMED", "result": result}
            raise AppError("action_processing", "This action is still being processed. Try again shortly.", 409)
        if pending.status != "PENDING":
            raise AppError("invalid_action", f"This action is {pending.status.lower()} and cannot be changed.", 409)
        now = utcnow()
        expires_at = pending.expires_at.replace(tzinfo=now.tzinfo) if pending.expires_at.tzinfo is None else pending.expires_at
        if expires_at <= now:
            pending.status = "EXPIRED"
            self.db.commit()
            raise AppError("action_expired", "This confirmation expired. Please request the action again.", 409)
        if not confirm:
            pending.status = "REJECTED"
            self.db.commit()
            assistant = self._save_message(
                session,
                "assistant",
                "Action rejected. No changes were made.",
                {"mode": "AGENT", "action_id": pending.id},
            )
            return {"status": "REJECTED", "assistant_message": self._message_view(assistant)}

        pending.status = "PROCESSING"
        self.db.commit()
        if pending.action == "CREATE_ORDER":
            lines = pending.payload["lines"]
            for line in lines:
                item = CatalogService(self.db, CatalogRepository(self.db)).get(line["item_id"])
                if str(item.price) != line["unit_price"]:
                    refreshed_lines = []
                    total = item.price * 0
                    for entry in lines:
                        refreshed_item = CatalogService(self.db, CatalogRepository(self.db)).get(entry["item_id"])
                        refreshed_lines.append({**entry, "unit_price": str(refreshed_item.price)})
                        total += refreshed_item.price * entry["quantity"]
                    pending.status = "STALE"
                    self.db.commit()
                    updated = PendingAction(
                        session_id=session.id,
                        user_id=user.id,
                        action="CREATE_ORDER",
                        payload={"lines": refreshed_lines, "total": str(total)},
                        expires_at=utcnow() + timedelta(minutes=settings.order_action_ttl_minutes),
                        status="PENDING",
                        idempotency_key=str(uuid4()),
                    )
                    self.db.add(updated)
                    self.db.commit()
                    self.db.refresh(updated)
                    summary = ", ".join(
                        f"{entry['quantity']} x {entry['title']} @ INR {entry['unit_price']}" for entry in refreshed_lines
                    )
                    answer = f"A menu price changed. Please review the updated proposal: {summary}. Total: INR {total}. Confirm again if you agree."
                    self._save_message(
                        session,
                        "assistant",
                        answer,
                        {"mode": "AGENT", "pending_action": self._pending_view(updated)},
                    )
                    raise AppError("terms_changed", "The order terms changed. Review the updated confirmation in the chat.", 409)
            try:
                order = self._order_service().create(user, [{"item_id": line["item_id"], "quantity": line["quantity"]} for line in lines], pending.idempotency_key)
            except AppError:
                pending = self.db.get(PendingAction, pending.id)
                pending.status = "FAILED"
                self.db.commit()
                raise
            result = {"order_id": order.id, "status": order.status, "total": str(order.total)}
        elif pending.action == "CANCEL_ORDER":
            try:
                order = self._order_service().cancel(user, pending.payload["order_id"])
            except AppError:
                pending = self.db.get(PendingAction, pending.id)
                pending.status = "FAILED"
                self.db.commit()
                raise
            result = {"order_id": order.id, "status": order.status}
        else:
            pending.status = "FAILED"
            self.db.commit()
            raise AppError("invalid_action", "Unsupported pending action.", 409)
        pending.status = "CONFIRMED"
        pending.result_json = result
        self.db.commit()
        response = f"Confirmed. Order #{result['order_id']} status: {result['status']}."
        assistant = self._save_message(session, "assistant", response, {"mode": "AGENT", "action_id": pending.id})
        return {"status": "CONFIRMED", "result": result, "assistant_message": self._message_view(assistant)}

    def _order_service(self) -> OrderService:
        return OrderService(self.db, CatalogRepository(self.db), OrderRepository(self.db))

    @staticmethod
    def _pending_view(pending: PendingAction) -> dict:
        return {
            "id": pending.id,
            "action": pending.action,
            "payload": pending.payload,
            "status": pending.status,
            "expires_at": pending.expires_at.isoformat(),
        }
