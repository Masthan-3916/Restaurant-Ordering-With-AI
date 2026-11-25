from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models import User
from app.schemas.api import ChatInput, ChatResponse, ConfirmInput, MessageResponse, SessionInput, SessionResponse
from app.security.auth import current_user
from app.services.chat import ChatService

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/sessions", response_model=SessionResponse, status_code=201)
def create_session(
    data: SessionInput,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    return ChatService(db).create_session(user, data.title)


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    return ChatService(db).list_sessions(user)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def list_messages(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    return [
        MessageResponse(
            id=message.id,
            seq_no=message.seq_no,
            role=message.role,
            content=message.content,
            metadata=message.metadata_json,
            created_at=message.created_at,
        )
        for message in ChatService(db).messages(user, session_id)
    ]


@router.post("/sessions/{session_id}/messages", response_model=ChatResponse)
def send_message(
    session_id: int,
    data: ChatInput,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    return ChatService(db).ask(user, session_id, data.message)


@router.post("/sessions/{session_id}/actions/{action_id}/confirm")
def confirm_action(
    session_id: int,
    action_id: int,
    data: ConfirmInput,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    return ChatService(db).confirm_action(user, session_id, action_id, data.confirm)
