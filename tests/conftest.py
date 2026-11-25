from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.database import Base, get_db
from app.main import app
from app.models import MenuItem, User
from app.security.passwords import hash_password


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with test_sessions() as session:
            yield session

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    app.router.lifespan_context = no_lifespan
    app.dependency_overrides[get_db] = override_db
    with test_sessions() as session:
        session.add_all(
            [
                User(username="customer", password_hash=hash_password("customer-pass"), role="user"),
                User(username="other", password_hash=hash_password("other-pass"), role="user"),
                User(username="admin", password_hash=hash_password("admin-pass"), role="admin"),
            ]
        )
        session.add(MenuItem(title="Masala Dosa", category="Breakfast", vegetarian=True, price=90, capacity=4))
        session.commit()
    monkeypatch.setattr("app.config.settings.settings.gemini_api_key", "")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()
