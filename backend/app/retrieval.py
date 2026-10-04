"""Question embeddings and hybrid document search."""

from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .indexing import EMBED_DIMENSIONS, EMBED_MODEL
from .models import Chunk, Document


def embed_question(gemini, question: str) -> list[float]:
    """Generate dense vector embeddings for a user question using Gemini.
    
    Prepends the task instruction prefix so the embedding model optimizes 
    the representation for asymmetric question-answering retrieval.
    """
    result = gemini.models.embed_content(
        model=EMBED_MODEL,
        contents=f"task: question answering | query: {question}",
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIMENSIONS),
    )
    return result.embeddings[0].values


def retrieve(db: Session, document: Document, vector: list[float], question: str) -> list[Chunk]:
    """Retrieve the most relevant document chunks using Hybrid Search and RRF.
    
    1. Semantic search: Finds chunks with similar vector embeddings (cosine distance).
    2. Lexical search: Finds chunks matching exact keywords via PostgreSQL full-text search.
    3. Reciprocal Rank Fusion (RRF): Fuses both rankings using 1 / (60 + rank) to produce
       a balanced, robust top-8 chunk selection.
    """
    # 1. Semantic (Dense Vector) Retrieval
    # Query pgvector for the closest 12 chunks using cosine distance within this document.
    semantic = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id)
        .order_by(Chunk.embedding.cosine_distance(vector))
        .limit(12)
    ).all()

    # 2. Lexical (Full-Text / Keyword) Retrieval
    # Convert natural language question into PostgreSQL search tokens (handles stemming, stop words).
    query = func.plainto_tsquery("english", question)
    # Search document chunks using full-text vector matching (@@ operator)
    # and rank matches using cover density ranking (ts_rank_cd) which considers term proximity.
    lexical = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id, func.to_tsvector("english", Chunk.content).op("@@")(query))
        .order_by(func.ts_rank_cd(func.to_tsvector("english", Chunk.content), query).desc())
        .limit(12)
    ).all()

    # 3. Reciprocal Rank Fusion (RRF) Re-ranking
    # Accumulate RRF scores for chunks across both semantic and lexical result sets.
    # Formula: score = sum(1 / (k + rank)), where k = 60 is standard smoothing constant.
    scores = {}
    found = {}

    # Score chunks from semantic search (rank starts at 0 for best match)
    for rank, chunk in enumerate(semantic):
        found[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank)

    # Score chunks from lexical search; chunks appearing in both lists get combined scores
    for rank, chunk in enumerate(lexical):
        found[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank)

    # 4. Return top 8 chunks sorted by highest fused RRF score
    return [found[chunk_id] for chunk_id in sorted(scores, key=scores.get, reverse=True)[:8]]
