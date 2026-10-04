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
    """Atomically find and claim the next available document for processing.
    
    Uses PostgreSQL row-level locking (`FOR UPDATE SKIP LOCKED`) to ensure 
    safe concurrent worker execution without race conditions. Also re-claims 
    stale jobs whose worker may have crashed (>20 minutes without progress).
    """
    # Define stale threshold: jobs stuck in 'processing' longer than 20 mins are considered crashed
    stale = datetime.now(timezone.utc) - timedelta(minutes=20)
    with database.SessionLocal.begin() as db:
        # Query for the oldest pending or stale document
        # 'skip_locked=True' lets concurrent workers skip rows locked by other workers
        document = db.scalar(
            select(Document)
            .where(or_(Document.status.in_(["queued", "legacy"]), (Document.status == "processing") & (Document.processing_started_at < stale)))
            .order_by(Document.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not document:
            return None
        
        # Mark as processing and set timestamp as an active heartbeat
        document.status = "processing"
        document.processing_started_at = datetime.now(timezone.utc)
        document.error = None
        return document.id


def save_batch(document_id: str, title: str, batch: list[tuple[int, str]], start_index: int):
    """Embed and persist a batch of chunks to the database.
    
    Also updates the document's `processing_started_at` to serve as a heartbeat,
    preventing other workers from considering the job stale during long indexing runs.
    """
    # Generate dense vector embeddings for the batch of chunk texts via Gemini
    vectors = embed_documents(ai.gemini, title, [content for _, content in batch])
    with database.SessionLocal.begin() as db:
        # Insert chunk records with page number, sequence index, text, and embedding vector
        for index, ((page, content), vector) in enumerate(zip(batch, vectors), start=start_index):
            db.add(Chunk(document_id=document_id, page_number=page, chunk_index=index, content=content, embedding=vector))
        
        # Heartbeat: update timestamp so long-running PDF processing isn't flagged as abandoned
        document = db.get(Document, document_id)
        if document:
            document.processing_started_at = datetime.now(timezone.utc)


def process_document(document_id: str):
    """End-to-end ingestion pipeline for a single PDF document:
    
    1. Resolves storage location (migrating legacy local storage if needed).
    2. Downloads PDF to a temporary file.
    3. Clears previous chunks to ensure idempotent re-runs.
    4. Extracts text per page (with OCR fallback where applicable).
    5. Chunks text and generates vector embeddings in batches.
    6. Updates document status to 'ready' upon completion.
    """
    with database.SessionLocal() as db:
        document = db.get(Document, document_id)
        if not document:
            return
        key, title = document.storage_key, document.filename

        # Step 1: Backward-compatibility migration for older local files to object storage
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

        # Step 2: Download PDF from object storage into a local temporary file for processing
        with NamedTemporaryFile(suffix=".pdf") as temp:
            storage.download(temp, key)
            temp.flush()

            # Inspect PDF metadata (total page count)
            with fitz.open(temp.name) as pdf:
                page_count = pdf.page_count

            # Delete any existing chunks (for idempotency if retrying failed/re-queued runs)
            db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            db.commit()

            # Step 3: Extract text, chunk, and embed in batches
            batch: list[tuple[int, str]] = []
            total = 0
            # extract_pdf yields (page_number, text) with visual OCR fallback if text is sparse
            for page_number, text in extract_pdf(Path(temp.name), ai.gemini, config.GEMINI_MODEL):
                batch.extend(chunks_from_text(text, page_number))
                # Process and flush in fixed batch sizes to manage memory and API rate limits
                while len(batch) >= BATCH_SIZE:
                    current, batch = batch[:BATCH_SIZE], batch[BATCH_SIZE:]
                    save_batch(document_id, title, current, total)
                    total += len(current)

            # Flush any remaining chunks in the final partial batch
            if batch:
                save_batch(document_id, title, batch, total)
                total += len(batch)

            # Validation check: Ensure the PDF yielded readable content
            if total == 0:
                raise ValueError("No readable text was found in this PDF")

            # Step 4: Finalize document status as 'ready'
            document = db.get(Document, document_id)
            if document:
                document.status = "ready"
                document.page_count = page_count
                document.error = None
                document.processing_started_at = None
                db.commit()
            log.info("Indexed %s: %s pages, %s chunks", document_id, page_count, total)


def run_once() -> bool:
    """Execute a single polling cycle of the background ingestion worker.
    
    Checks storage readiness, claims the next queued document, processes it,
    and updates the database status to 'failed' if any unhandled error occurs.
    Returns True if a document was claimed and processed, False otherwise.
    """
    # Ensure storage service (e.g. S3 / MinIO) is reachable before claiming work
    if not storage.ready():
        return False

    # Attempt to claim a document lock from the queue
    document_id = claim_document()
    if not document_id:
        return False

    # Process document and handle failures gracefully
    try:
        process_document(document_id)
    except Exception:
        log.exception("Failed to index %s", document_id)
        # Record failure status and error message in DB for user feedback
        with database.SessionLocal.begin() as db:
            document = db.get(Document, document_id)
            if document:
                document.status = "failed"
                document.error = "Indexing failed. Retry this PDF."
                document.processing_started_at = None
    return True
