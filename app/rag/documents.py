from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document as WordDocument

from app.services.errors import AppError

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


@dataclass(frozen=True)
class TextChunk:
    text: str
    page: int | None
    chunk_index: int


def extract_pages(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    extension = Path(filename).suffix.lower()
    if extension == ".pdf":
        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                return [(index + 1, page.get_text()) for index, page in enumerate(pdf)]
        except Exception as exc:
            raise AppError("invalid_document", "The uploaded PDF could not be read.", 422) from exc
    if extension == ".docx":
        try:
            document = WordDocument(BytesIO(data))
            return [(None, "\n".join(paragraph.text for paragraph in document.paragraphs))]
        except Exception as exc:
            raise AppError("invalid_document", "The uploaded DOCX could not be read.", 422) from exc
    if extension == ".txt":
        try:
            return [(None, data.decode("utf-8"))]
        except UnicodeDecodeError as exc:
            raise AppError("invalid_document", "Text documents must be UTF-8 encoded.", 422) from exc
    raise AppError("unsupported_document", "Only PDF, DOCX and TXT documents are supported.", 422)


def chunk_pages(pages: list[tuple[int | None, str]], size: int = 800, overlap: int = 100) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page, text in pages:
        clean_text = " ".join(text.split())
        start = 0
        while start < len(clean_text):
            part = clean_text[start : start + size].strip()
            if part:
                chunks.append(TextChunk(text=part, page=page, chunk_index=len(chunks)))
            if start + size >= len(clean_text):
                break
            start += size - overlap
    return chunks
