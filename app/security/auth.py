from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models import User
from app.security.passwords import DUMMY_PASSWORD_HASH, verify_password

basic_auth = HTTPBasic(auto_error=False)


def current_user(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )
    user = db.scalar(select(User).where(User.username == credentials.username))
    password_is_valid = verify_password(credentials.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    valid = user is not None and password_is_valid
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user
