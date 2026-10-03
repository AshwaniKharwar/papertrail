"""Question embeddings and hybrid document search."""

from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .indexing import EMBED_DIMENSIONS, EMBED_MODEL
from .models import Chunk, Document


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
