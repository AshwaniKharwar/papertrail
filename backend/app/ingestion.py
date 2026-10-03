"""Durable PDF ingestion worker."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import pymupdf as fitz
from sqlalchemy import delete, or_, select

from . import ai, config, db as database, storage
from .indexing import BATCH_SIZE, chunks_from_text, embed_documents, extract_pdf
from .models import Chunk, Document

log = logging.getLogger(__name__)


def claim_document() -> str | None:
    stale = datetime.now(timezone.utc) - timedelta(minutes=20)
    with database.SessionLocal.begin() as db:
        document = db.scalar(
            select(Document)
            .where(or_(Document.status.in_(["queued", "legacy"]), (Document.status == "processing") & (Document.processing_started_at < stale)))
            .order_by(Document.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not document:
            return None
        document.status = "processing"
        document.processing_started_at = datetime.now(timezone.utc)
        document.error = None
        return document.id


def save_batch(document_id: str, title: str, batch: list[tuple[int, str]], start_index: int):
    vectors = embed_documents(ai.gemini, title, [content for _, content in batch])
    with database.SessionLocal.begin() as db:
        for index, ((page, content), vector) in enumerate(zip(batch, vectors), start=start_index):
            db.add(Chunk(document_id=document_id, page_number=page, chunk_index=index, content=content, embedding=vector))
        document = db.get(Document, document_id)
        if document:
            document.processing_started_at = datetime.now(timezone.utc)


def process_document(document_id: str):
    with database.SessionLocal() as db:
        document = db.get(Document, document_id)
        if not document:
            return
        key, title = document.storage_key, document.filename
        if not key:
            legacy_file = config.DATA_DIR / f"{document_id}.pdf"
            if not legacy_file.exists():
                raise FileNotFoundError("Legacy PDF file is missing")
            key = f"users/{document.user_id}/{document.id}.pdf"
            with legacy_file.open("rb") as file:
                storage.upload(file, key)
            document.storage_key = key
            db.commit()
            legacy_file.unlink()
        with NamedTemporaryFile(suffix=".pdf") as temp:
            storage.download(temp, key)
            temp.flush()
            with fitz.open(temp.name) as pdf:
                page_count = pdf.page_count
            db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            db.commit()
            batch: list[tuple[int, str]] = []
            total = 0
            for page_number, text in extract_pdf(Path(temp.name), ai.gemini, config.GEMINI_MODEL):
                batch.extend(chunks_from_text(text, page_number))
                while len(batch) >= BATCH_SIZE:
                    current, batch = batch[:BATCH_SIZE], batch[BATCH_SIZE:]
                    save_batch(document_id, title, current, total)
                    total += len(current)
            if batch:
                save_batch(document_id, title, batch, total)
                total += len(batch)
            if total == 0:
                raise ValueError("No readable text was found in this PDF")
            document = db.get(Document, document_id)
            if document:
                document.status = "ready"
                document.page_count = page_count
                document.error = None
                document.processing_started_at = None
                db.commit()
            log.info("Indexed %s: %s pages, %s chunks", document_id, page_count, total)


def run_once() -> bool:
    if not storage.ready():
        return False
    document_id = claim_document()
    if not document_id:
        return False
    try:
        process_document(document_id)
    except Exception:
        log.exception("Failed to index %s", document_id)
        with database.SessionLocal.begin() as db:
            document = db.get(Document, document_id)
            if document:
                document.status = "failed"
                document.error = "Indexing failed. Retry this PDF."
                document.processing_started_at = None
    return True
