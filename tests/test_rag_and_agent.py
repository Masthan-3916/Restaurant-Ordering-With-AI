import pytest

from app.config.database import get_db
from app.agents.tools import function_declarations
from app.models import ChatMessage, ChatSession, Order, PendingAction, ToolRun, User
from app.rag.documents import chunk_pages, extract_pages
from app.services.errors import AppError


def test_text_chunker_overlaps_and_preserves_page():
    chunks = chunk_pages([(4, "a" * 1700)], size=800, overlap=100)
    assert len(chunks) == 3
    assert chunks[0].page == 4
    assert chunks[0].text[-100:] == chunks[1].text[:100]


def test_unsupported_document_type_is_explicit():
    with pytest.raises(AppError) as error:
        extract_pages("notes.csv", b"a,b")
    assert error.value.code == "unsupported_document"


def test_single_agent_exposes_only_allowlisted_tools():
    names = {declaration.name for declaration in function_declarations()}
    assert names == {
        "search_menu_items",
        "get_menu_item",
        "check_availability",
        "get_order_status",
        "retrieve_policy",
        "propose_order",
        "propose_cancel",
    }


def test_agent_write_tool_proposes_but_does_not_execute(client):
    auth = ("customer", "customer-pass")
    session_view = client.post("/api/chat/sessions", auth=auth, json={"title": "Tool proposal"}).json()
    dependency = client.app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        from sqlalchemy import select

        from app.agents.tools import AgentTools

        user = db.scalar(select(User).where(User.username == "customer"))
        session = db.get(ChatSession, session_view["id"])
        message = ChatMessage(session_id=session.id, seq_no=1, role="user", content="Order one Masala Dosa")
        db.add(message)
        db.commit()
        db.refresh(message)
        result = AgentTools(db, session, user, message, None).execute(
            "propose_order",
            {"lines": [{"item_id": 1, "quantity": 1}]},
        )
        assert result["requires_user_confirmation"] is True
        assert result["total"] == "90.00"
        assert db.query(PendingAction).count() == 1
        assert db.query(ToolRun).count() == 1
        assert db.query(Order).count() == 0
    finally:
        db.close()
        dependency.close()


def test_gemini_function_call_routes_through_agent_tools(client, monkeypatch):
    from types import SimpleNamespace

    from google.genai import types

    session = client.post("/api/chat/sessions", auth=("customer", "customer-pass"), json={"title": "Menu search"}).json()
    function_call = types.FunctionCall(name="search_menu_items", args={"vegetarian": True})
    tool_response = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(function_call=function_call)]))],
        text=None,
    )
    final_response = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="I found vegetarian options.")]))],
        text="I found vegetarian options.",
    )

    class FakeModels:
        def __init__(self):
            self.responses = [tool_response, final_response]
            self.received_result = None

        def generate_content(self, **kwargs):
            contents = kwargs["contents"]
            if len(contents) > 1:
                self.received_result = contents[-1]
            return self.responses.pop(0)

    models = FakeModels()

    class FakeGemini:
        def __init__(self):
            self.client = SimpleNamespace(models=models)

    monkeypatch.setattr("app.agents.single_agent.GeminiClient", FakeGemini)
    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        auth=("customer", "customer-pass"),
        json={"message": "Show vegetarian food"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "AGENT"
    assert response.json()["assistant_message"]["content"] == "I found vegetarian options."
    assert models.received_result.role == "user"
    tool_result = models.received_result.parts[0].function_response
    assert tool_result.name == "search_menu_items"
    assert tool_result.response["items"][0]["title"] == "Masala Dosa"


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_menu_item", {"item_id": 1}, {"item": {
            "id": 1, "title": "Masala Dosa", "category": "Breakfast",
            "vegetarian": True, "price": "90.00", "available_portions": 4,
        }}),
        ("check_availability", {"item_id": 1, "quantity": 4}, True),
        ("check_availability", {"item_id": 1, "quantity": 5}, False),
        ("check_availability", {"item_id": 1, "quantity": 0}, "invalid_tool_arguments"),
        ("check_availability", {"item_id": 1, "quantity": True}, "invalid_tool_arguments"),
        ("get_menu_item", {"item_id": 999}, "not_found"),
    ],
)
def test_menu_tools_validate_and_use_live_catalog(client, name, arguments, expected):
    from sqlalchemy import select

    from app.agents.tools import AgentTools

    auth = ("customer", "customer-pass")
    session_view = client.post("/api/chat/sessions", auth=auth, json={"title": "Menu"}).json()
    dependency = client.app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        user = db.scalar(select(User).where(User.username == "customer"))
        session = db.get(ChatSession, session_view["id"])
        message = ChatMessage(session_id=session.id, seq_no=1, role="user", content="Check the menu")
        db.add(message)
        db.commit()
        result = AgentTools(db, session, user, message, None).execute(name, arguments)
        if isinstance(expected, dict):
            assert result == expected
        elif isinstance(expected, bool):
            assert result["available"] is expected
            assert result["requested_quantity"] == arguments["quantity"]
        else:
            assert result["code"] == expected
        audit = db.scalar(select(ToolRun).where(ToolRun.message_id == message.id))
        assert audit.tool_name == name
        assert audit.status == ("FAILED" if isinstance(expected, str) else "SUCCESS")
    finally:
        db.close()
        dependency.close()


def test_gemini_order_tool_requires_explicit_confirmation(client, monkeypatch):
    from types import SimpleNamespace

    from google.genai import types

    auth = ("customer", "customer-pass")
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Order"}).json()
    call = types.FunctionCall(name="propose_order", args={"lines": [{"item_id": 1, "quantity": 2}]})
    proposal = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(function_call=call)]))],
        text=None,
    )
    final = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="Please confirm your order.")]))],
        text="Please confirm your order.",
    )

    class FakeModels:
        def __init__(self):
            self.responses = [proposal, final]

        def generate_content(self, **_kwargs):
            return self.responses.pop(0)

    class FakeGemini:
        def __init__(self):
            self.client = SimpleNamespace(models=FakeModels())

    monkeypatch.setattr("app.agents.single_agent.GeminiClient", FakeGemini)
    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        auth=auth,
        json={"message": "Order two Masala Dosas"},
    )
    assert response.status_code == 200
    pending = response.json()["pending_action"]
    assert pending["action"] == "CREATE_ORDER"
    assert pending["payload"]["total"] == "180.00"
    assert client.get("/api/orders", auth=auth).json() == []
    assert client.get("/api/menu_items/1").json()["capacity"] == 4
    confirm_url = f"/api/chat/sessions/{session['id']}/actions/{pending['id']}/confirm"
    confirmed = client.post(confirm_url, auth=auth, json={"confirm": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["result"]["total"] == "180.00"
    assert client.get("/api/menu_items/1").json()["capacity"] == 2
    assert client.post(confirm_url, auth=auth, json={"confirm": True}).json()["result"] == confirmed.json()["result"]
    assert len(client.get("/api/orders", auth=auth).json()) == 1


@pytest.mark.parametrize("confirm", [False, True])
def test_gemini_cancel_tool_waits_for_user_decision(client, monkeypatch, confirm):
    from types import SimpleNamespace

    from google.genai import types

    auth = ("customer", "customer-pass")
    order = client.post(
        "/api/orders",
        auth=auth,
        json={"lines": [{"item_id": 1, "quantity": 1}]},
    ).json()
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Cancellation"}).json()
    call = types.FunctionCall(name="propose_cancel", args={"order_id": order["id"]})
    proposal = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(function_call=call)]))],
        text=None,
    )
    final = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="Please review the cancellation.")]))],
        text="Please review the cancellation.",
    )

    class FakeModels:
        def __init__(self):
            self.responses = [proposal, final]

        def generate_content(self, **_kwargs):
            return self.responses.pop(0)

    class FakeGemini:
        def __init__(self):
            self.client = SimpleNamespace(models=FakeModels())

    monkeypatch.setattr("app.agents.single_agent.GeminiClient", FakeGemini)
    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        auth=auth,
        json={"message": f"Cancel order {order['id']}"},
    )
    assert response.status_code == 200
    pending = response.json()["pending_action"]
    assert pending["action"] == "CANCEL_ORDER"
    assert client.get(f"/api/orders/{order['id']}", auth=auth).json()["status"] == "CONFIRMED"
    decision = client.post(
        f"/api/chat/sessions/{session['id']}/actions/{pending['id']}/confirm",
        auth=auth,
        json={"confirm": confirm},
    )
    assert decision.status_code == 200
    assert client.get(f"/api/orders/{order['id']}", auth=auth).json()["status"] == (
        "CANCELLED" if confirm else "CONFIRMED"
    )
    assert client.get("/api/menu_items/1").json()["capacity"] == (4 if confirm else 3)


def test_cancel_proposal_cannot_target_another_users_order(client):
    from sqlalchemy import select

    from app.agents.tools import AgentTools

    customer = ("customer", "customer-pass")
    order = client.post(
        "/api/orders",
        auth=customer,
        json={"lines": [{"item_id": 1, "quantity": 1}]},
    ).json()
    admin = ("admin", "admin-pass")
    session_view = client.post("/api/chat/sessions", auth=admin, json={"title": "Cancellation"}).json()
    dependency = client.app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        user = db.scalar(select(User).where(User.username == "admin"))
        message = ChatMessage(session_id=session_view["id"], seq_no=1, role="user", content="Cancel an order")
        db.add(message)
        db.commit()
        result = AgentTools(db, db.get(ChatSession, session_view["id"]), user, message, None).execute(
            "propose_cancel", {"order_id": order["id"]}
        )
        assert result["code"] == "not_found"
        assert db.query(PendingAction).count() == 0
    finally:
        db.close()
        dependency.close()


@pytest.mark.parametrize(
    ("upstream_status", "expected_status", "expected_code"),
    [(503, 503, "ai_unavailable"), (429, 429, "ai_rate_limited"), (403, 503, "ai_access_denied")],
)
def test_agent_reports_gemini_errors_and_persists_failure(client, monkeypatch, upstream_status, expected_status, expected_code):
    from types import SimpleNamespace

    from google.genai import errors

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise errors.APIError(upstream_status, {"error": {"message": "provider error"}})

    class FakeGemini:
        def __init__(self):
            self.client = SimpleNamespace(models=FakeModels())

    monkeypatch.setattr("app.agents.single_agent.GeminiClient", FakeGemini)
    auth = ("customer", "customer-pass")
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Provider error"}).json()
    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        auth=auth,
        json={"message": "Find a meal"},
    )
    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    history = client.get(f"/api/chat/sessions/{session['id']}/messages", auth=auth).json()
    assert history[-1]["metadata"]["error"] == expected_code


def test_gemini_client_configures_bounded_retries(monkeypatch):
    from app.ai.gemini import GeminiClient

    received = {}
    monkeypatch.setattr("app.ai.gemini.settings.gemini_api_key", "test-key")
    monkeypatch.setattr("app.ai.gemini.genai.Client", lambda **kwargs: received.update(kwargs))
    GeminiClient()
    options = received["http_options"]
    assert options.timeout == 60000
    assert options.retry_options.attempts == 3
    assert 503 in options.retry_options.http_status_codes
