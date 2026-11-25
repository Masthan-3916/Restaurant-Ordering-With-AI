from typing import Annotated

from fastapi import APIRouter, Depends

from app.models import User
from app.security.auth import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def identity(user: Annotated[User, Depends(current_user)]):
    return {"id": user.id, "username": user.username, "role": user.role}
