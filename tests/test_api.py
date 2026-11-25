from app.config.database import get_db
def test_menu_is_public_and_admin_routes_are_protected(client):
    assert client.get("/api/menu_items").status_code == 200
    unauthorized = client.post(
        "/api/menu_items",
        json={"title": "New Dish", "category": "Lunch", "vegetarian": True, "price": "120.00", "capacity": 3},
    )
    assert unauthorized.status_code == 401
    forbidden = client.post(
        "/api/menu_items",
        auth=("customer", "customer-pass"),
        json={"title": "New Dish", "category": "Lunch", "vegetarian": True, "price": "120.00", "capacity": 3},
    )
    assert forbidden.status_code == 403
    created = client.post(
        "/api/menu_items",
        auth=("admin", "admin-pass"),
        json={"title": "New Dish", "category": "Lunch", "vegetarian": True, "price": "120.00", "capacity": 3},
    )
    assert created.status_code == 201


def test_order_uses_server_price_and_is_idempotent(client):
    payload = {"lines": [{"item_id": 1, "quantity": 2}]}
    headers = {"Idempotency-Key": "order-test-1"}
    first = client.post("/api/orders", auth=("customer", "customer-pass"), json=payload, headers=headers)
    second = client.post("/api/orders", auth=("customer", "customer-pass"), json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["total"] == "180.00"
    assert len(client.get("/api/orders", auth=("customer", "customer-pass")).json()) == 1
    conflict = client.post(
        "/api/orders",
        auth=("customer", "customer-pass"),
        json={"lines": [{"item_id": 1, "quantity": 1}]},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_admin_can_list_orders_and_apply_allowed_status_transitions(client):
    order = client.post(
        "/api/orders",
        auth=("customer", "customer-pass"),
        json={"lines": [{"item_id": 1, "quantity": 1}]},
    ).json()
    admin = ("admin", "admin-pass")
    assert len(client.get("/api/admin/orders", auth=admin).json()) == 1
    preparing = client.patch(f"/api/admin/orders/{order['id']}/status", auth=admin, json={"status": "PREPARING"})
    assert preparing.status_code == 200 and preparing.json()["status"] == "PREPARING"
    invalid = client.patch(f"/api/admin/orders/{order['id']}/status", auth=admin, json={"status": "COMPLETED"})
    assert invalid.status_code == 409
    ready = client.patch(f"/api/admin/orders/{order['id']}/status", auth=admin, json={"status": "READY"})
    assert ready.status_code == 200 and ready.json()["status"] == "READY"


def test_order_owner_check_and_cancel_restore_capacity(client):
    created = client.post(
        "/api/orders",
        auth=("customer", "customer-pass"),
        headers={"Idempotency-Key": "owner-check"},
        json={"lines": [{"item_id": 1, "quantity": 2}]},
    ).json()
    forbidden_record = client.get(f"/api/orders/{created['id']}", auth=("other", "other-pass"))
    assert forbidden_record.status_code == 404
    cancelled = client.post(f"/api/orders/{created['id']}/cancel", auth=("customer", "customer-pass"))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert client.get("/api/menu_items/1").json()["capacity"] == 4
    repeated = client.post(f"/api/orders/{created['id']}/cancel", auth=("customer", "customer-pass"))
    assert repeated.status_code == 409
    assert client.get("/api/menu_items/1").json()["capacity"] == 4


def test_invalid_order_quantity_has_stable_error(client):
    response = client.post(
        "/api/orders",
        auth=("customer", "customer-pass"),
        json={"lines": [{"item_id": 1, "quantity": 11}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.headers["x-request-id"]


def test_chat_is_persisted_and_gemini_failure_is_explicit(client):
    auth = ("customer", "customer-pass")
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Dinner ideas"}).json()
    failed = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        auth=auth,
        json={"message": "Suggest a vegetarian dinner"},
    )
    assert failed.status_code == 503
    assert failed.json()["code"] == "ai_unavailable"
    messages = client.get(f"/api/chat/sessions/{session['id']}/messages", auth=auth).json()
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_chat_confirmation_is_idempotent(client):
    auth = ("customer", "customer-pass")
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Order help"}).json()
    db_override = client.app.dependency_overrides[get_db]
    with next(db_override()) as db:
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from app.models import PendingAction

        action = PendingAction(
            session_id=session["id"],
            user_id=1,
            action="CREATE_ORDER",
            payload={
                "lines": [{"item_id": 1, "quantity": 1, "title": "Masala Dosa", "unit_price": "90.00"}],
                "total": "90.00",
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            status="PENDING",
            idempotency_key=str(uuid4()),
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        action_id = action.id
    path = f"/api/chat/sessions/{session['id']}/actions/{action_id}/confirm"
    confirmed = client.post(path, auth=auth, json={"confirm": True})
    replay = client.post(path, auth=auth, json={"confirm": True})
    assert confirmed.status_code == 200
    assert replay.status_code == 200
    assert confirmed.json()["result"]["order_id"] == replay.json()["result"]["order_id"]
    assert len(client.get("/api/orders", auth=auth).json()) == 1
    assert client.get("/api/menu_items/1").json()["capacity"] == 3


def test_price_change_requires_a_new_confirmation(client):
    auth = ("customer", "customer-pass")
    session = client.post("/api/chat/sessions", auth=auth, json={"title": "Price check"}).json()
    dependency = client.app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from app.models import PendingAction

        action = PendingAction(
            session_id=session["id"],
            user_id=1,
            action="CREATE_ORDER",
            payload={"lines": [{"item_id": 1, "quantity": 1, "title": "Masala Dosa", "unit_price": "90.00"}], "total": "90.00"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            status="PENDING",
            idempotency_key=str(uuid4()),
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        action_id = action.id
    finally:
        db.close()
        dependency.close()

    updated = client.put(
        "/api/menu_items/1",
        auth=("admin", "admin-pass"),
        json={"title": "Masala Dosa", "category": "Breakfast", "vegetarian": True, "price": "100.00", "capacity": 4},
    )
    assert updated.status_code == 200
    old_confirmation = client.post(
        f"/api/chat/sessions/{session['id']}/actions/{action_id}/confirm",
        auth=auth,
        json={"confirm": True},
    )
    assert old_confirmation.status_code == 409
    history = client.get(f"/api/chat/sessions/{session['id']}/messages", auth=auth).json()
    replacement = history[-1]["metadata"]["pending_action"]
    assert replacement["payload"]["total"] == "100.00"
    assert replacement["id"] != action_id
    assert client.post(
        f"/api/chat/sessions/{session['id']}/actions/{replacement['id']}/confirm",
        auth=auth,
        json={"confirm": True},
    ).json()["result"]["total"] == "100.00"
