import logging
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, storage
from .auth import current_user
from .db import db_session
from .models import Document, User

router = APIRouter()
log = logging.getLogger(__name__)


def document_for_user(document_id: str, user: User, db: Session) -> Document:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if not document:
        raise HTTPException(404, "Document not found")
    return document


def document_json(document: Document) -> dict:
    return {
        "id": document.id,
        "filename": document.filename,
        "size": document.size,
        "status": document.status,
        "error": document.error,
        "page_count": document.page_count,
        "tags": document.tags or [],
        "created_at": document.created_at.isoformat(),
    }


class TagsUpdate(BaseModel):
    tags: list[str] = Field(max_length=10)


@router.get("/documents")
def list_documents(user: User = Depends(current_user), db: Session = Depends(db_session)):
    documents = db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())).all()
    return [document_json(document) for document in documents]


@router.post("/documents", status_code=201)
def upload_document(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(db_session)):
    name = Path(file.filename or "document.pdf").name[:255]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Choose a PDF file")
    if not storage.ready():
        raise HTTPException(503, "PDF storage is not configured")
    if file.file.read(5) != b"%PDF-":
        raise HTTPException(400, "The file is not a PDF")
    size = 5
    while chunk := file.file.read(1024 * 1024):
        size += len(chunk)
        if size > config.MAX_PDF_BYTES:
            raise HTTPException(413, "PDFs must be 100 MB or smaller")
    file.file.seek(0)
    document = Document(id=str(uuid.uuid4()), user_id=user.id, filename=name, size=size, status="queued", tags=[])
    document.storage_key = f"users/{user.id}/{document.id}.pdf"
    try:
        storage.upload(file.file, document.storage_key)
        db.add(document)
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        try:
            storage.delete(document.storage_key)
        except Exception:
            log.exception("Could not clean up failed upload")
        log.exception("R2 upload failed")
        raise HTTPException(502, "Could not store this PDF. Please try again.") from None
    return document_json(document)


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    document = document_for_user(document_id, user, db)
    if not document.storage_key:
        path = config.DATA_DIR / f"{document_id}.pdf"
        if path.exists():
            return FileResponse(path, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"})
        raise HTTPException(404, "PDF file not found")
    byte_range = request.headers.get("range")
    if byte_range and not re.fullmatch(r"bytes=\d+-\d*", byte_range):
        raise HTTPException(416, "Invalid byte range")
    try:
        object_response = storage.open_object(document.storage_key, byte_range)
    except Exception:
        log.exception("R2 download failed")
        raise HTTPException(502, "Could not load this PDF") from None
    body = object_response["Body"]

    def stream():
        try:
            yield from body.iter_chunks(chunk_size=1024 * 1024)
        finally:
            body.close()

    headers = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Content-Disposition": f"inline; filename*=UTF-8''{quote(document.filename)}"}
    if "ContentLength" in object_response:
        headers["Content-Length"] = str(object_response["ContentLength"])
    if "ContentRange" in object_response:
        headers["Content-Range"] = object_response["ContentRange"]
    return StreamingResponse(stream(), status_code=206 if "ContentRange" in object_response else 200, media_type="application/pdf", headers=headers)


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(db_session)):
    document = document_for_user(document_id, user, db)
    key = document.storage_key
    db.delete(document)
    db.commit()
    if key:
        try:
            storage.delete(key)
        except Exception:
            log.exception("R2 cleanup failed for %s", document_id)
    else:
        (config.DATA_DIR / f"{document_id}.pdf").unlink(missing_ok=True)
    return {"ok": True}


@router.patch("/documents/{document_id}")
def update_document(document_id: str, body: TagsUpdate, user: User = Depends(current_user), db: Session = Depends(db_session)):
    document = document_for_user(document_id, user, db)
    tags = list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip()))
    if any(len(tag) > 32 for tag in tags):
        raise HTTPException(400, "Tags must be 32 characters or shorter")
    document.tags = tags
    db.commit()
    db.refresh(document)
    return document_json(document)


@router.post("/documents/{document_id}/retry")
def retry_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(db_session)):
    document = document_for_user(document_id, user, db)
    if document.status != "failed":
        raise HTTPException(409, "This PDF does not need a retry")
    document.status = "queued"
    document.error = None
    db.commit()
    db.refresh(document)
    return document_json(document)
