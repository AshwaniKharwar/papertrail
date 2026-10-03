import logging
import re
import time
from pathlib import Path

import pymupdf as fitz
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Chunk, Document

log = logging.getLogger(__name__)
EMBED_MODEL = "gemini-embedding-2"
EMBED_DIMENSIONS = 768
CHUNK_WORDS = 450
CHUNK_OVERLAP = 70
BATCH_SIZE = 16


def chunks_from_text(text: str, page_number: int):
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    if not words or words == [""]:
        return
    for start in range(0, len(words), CHUNK_WORDS - CHUNK_OVERLAP):
        section = words[start:start + CHUNK_WORDS]
        if section:
            yield page_number, " ".join(section)
        if start + CHUNK_WORDS >= len(words):
            break


def ocr_page(page, gemini, model: str) -> str:
    image = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("jpeg")
    result = gemini.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image, mime_type="image/jpeg"), "Transcribe all readable text on this page in reading order. Return only the text."],
    )
    return result.text or ""


def extract_pdf(path: Path, gemini, model: str):
    with fitz.open(path) as pdf:
        if not pdf.is_pdf:
            raise ValueError("File is not a PDF")
        for number, page in enumerate(pdf, 1):
            text = page.get_text("text", sort=True)
            if len(text.strip()) < 40:
                text = ocr_page(page, gemini, model)
            yield number, text


def embed_documents(gemini, title: str, contents: list[str]) -> list[list[float]]:
    inputs = [types.Content(parts=[types.Part.from_text(text=f"title: {title} | text: {content}")]) for content in contents]
    for attempt in range(4):
        try:
            result = gemini.models.embed_content(
                model=EMBED_MODEL,
                contents=inputs,
                config=types.EmbedContentConfig(output_dimensionality=EMBED_DIMENSIONS),
            )
            vectors = [embedding.values for embedding in result.embeddings]
            if len(vectors) != len(contents) or any(len(vector) != EMBED_DIMENSIONS for vector in vectors):
                raise ValueError("Gemini returned an unexpected number of embeddings")
            return vectors
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def embed_question(gemini, question: str) -> list[float]:
    result = gemini.models.embed_content(
        model=EMBED_MODEL,
        contents=f"task: question answering | query: {question}",
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIMENSIONS),
    )
    return result.embeddings[0].values


def retrieve(db: Session, document: Document, vector: list[float], question: str) -> list[Chunk]:
    semantic = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id)
        .order_by(Chunk.embedding.cosine_distance(vector))
        .limit(12)
    ).all()
    query = func.plainto_tsquery("english", question)
    lexical = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id, func.to_tsvector("english", Chunk.content).op("@@")(query))
        .order_by(func.ts_rank_cd(func.to_tsvector("english", Chunk.content), query).desc())
        .limit(12)
    ).all()
    scores = {}
    found = {}
    for rank, chunk in enumerate(semantic):
        found[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank)
    for rank, chunk in enumerate(lexical):
        found[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank)
    return [found[chunk_id] for chunk_id in sorted(scores, key=scores.get, reverse=True)[:8]]
