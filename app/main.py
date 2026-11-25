import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, auth, chat, documents, menu, orders
from app.config.database import Base, SessionLocal, engine
from app.models import MenuItem, User
from app.security.passwords import hash_password
from app.services.errors import AppError

logger = logging.getLogger("food_ai")

DEMO_MENU = [
    ("Masala Dosa", "Breakfast", True, 90, 20),
    ("Paneer Biryani", "Main course", True, 180, 10),
    ("Idli Sambar", "Breakfast", True, 60, 25),
    ("Vegetable Pulao", "Main course", True, 140, 16),
    ("Chole Bhature", "Lunch", True, 130, 12),
    ("Chicken Biryani", "Main course", False, 220, 14),
    ("Grilled Chicken Wrap", "Snacks", False, 160, 10),
    ("Aloo Paratha", "Breakfast", True, 80, 18),
    ("Paneer Tikka", "Starters", True, 170, 9),
    ("Gulab Jamun", "Dessert", True, 50, 30),
]


def initialize_database() -> None:
    from app.config.settings import settings

    if settings.app_env.lower() not in {"development", "test"} and (
        settings.admin_password == "admin123!" or settings.user_password == "customer123!"
    ):
        raise RuntimeError("Change the demo account passwords before running outside development.")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(User).filter_by(username=settings.admin_username).first():
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password), role="admin"))
        if not db.query(User).filter_by(username=settings.user_username).first():
            db.add(User(username=settings.user_username, password_hash=hash_password(settings.user_password), role="user"))
        if db.query(MenuItem).count() == 0:
            db.add_all(
                [MenuItem(title=title, category=category, vegetarian=vegetarian, price=price, capacity=capacity) for title, category, vegetarian, price, capacity in DEMO_MENU]
            )
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="AI Smart Food Ordering", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "request_id": getattr(request.state, "request_id", "")},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"code": "http_error", "message": message, "request_id": getattr(request.state, "request_id", "")},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error", "message": "Request data is invalid.", "request_id": getattr(request.state, "request_id", "")},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "")
    logger.exception("Unhandled request error (request_id=%s)", request_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "An unexpected error occurred.", "request_id": request_id},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
