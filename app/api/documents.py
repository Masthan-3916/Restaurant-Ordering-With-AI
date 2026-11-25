import logging
import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gemini import GeminiClient
from app.config.database import get_db
from app.config.settings import settings
from app.models import Document, User
from app.rag.documents import ALLOWED_EXTENSIONS, chunk_pages, extract_pages
from app.rag.vector_store import PolicyVectorStore
from app.security.auth import admin_user
from app.services.errors import AppError

logger = logging.getLogger("food_ai.documents")
router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", status_code=201)
def upload_document(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(admin_user)],
):
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise AppError("unsupported_document", "Only PDF, DOCX and TXT documents are supported.", 422)
    data = file.file.read(settings.max_upload_bytes + 1)
    if not data or len(data) > settings.max_upload_bytes:
        raise AppError("invalid_document_size", f"Upload must be between 1 byte and {settings.max_upload_bytes} bytes.", 413)

    pages = extract_pages(filename, data)
    chunks = chunk_pages(pages)
    if not chunks:
        raise AppError("empty_document", "The document contains no indexable text.", 422)
    digest = hashlib.sha256(data).hexdigest()
    latest_version = db.scalar(select(Document.version).where(Document.filename == filename).order_by(Document.version.desc()).limit(1)) or 0
    document = Document(filename=filename, sha256=digest, version=latest_version + 1, status="INDEXING", uploaded_by=admin.id)
    db.add(document)
    db.commit()
    db.refresh(document)
    document_id = document.id
    try:
        PolicyVectorStore().index(document_id, filename, document.version, chunks, GeminiClient())
        db.query(Document).filter(Document.filename == filename, Document.id != document_id).update({"status": "INACTIVE"})
        document.status = "ACTIVE"
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(Document, document_id)
        if failed:
            failed.status = "FAILED"
            db.commit()
        logger.exception("Document indexing failed (document_id=%s)", document_id)
        if isinstance(exc, AppError):
            raise
        raise AppError("document_index_failed", "The document was saved but could not be indexed. Check server logs and retry.", 503) from exc
    return {"id": document.id, "filename": document.filename, "version": document.version, "status": document.status, "chunks": len(chunks)}


@router.get("")
def list_documents(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(admin_user)]):
    documents = db.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return [
        {"id": document.id, "filename": document.filename, "version": document.version, "status": document.status, "created_at": document.created_at}
        for document in documents
    ]
