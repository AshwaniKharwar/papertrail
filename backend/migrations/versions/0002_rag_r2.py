"""Add R2 metadata and pgvector chunks.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("documents", sa.Column("storage_key", sa.String(255), nullable=True))
    op.add_column("documents", sa.Column("status", sa.String(20), nullable=False, server_default="queued"))
    op.add_column("documents", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
    op.create_unique_constraint("uq_documents_storage_key", "documents", ["storage_key"])
    op.execute("UPDATE documents SET status = 'legacy' WHERE storage_key IS NULL")
    op.add_column("messages", sa.Column("sources", sa.JSON(), nullable=False, server_default="[]"))
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_chunks_fts ON chunks USING gin (to_tsvector('english', content))")


def downgrade():
    op.drop_table("chunks")
    op.drop_column("messages", "sources")
    op.drop_constraint("uq_documents_storage_key", "documents", type_="unique")
    for name in ("tags", "page_count", "processing_started_at", "error", "status", "storage_key"):
        op.drop_column("documents", name)
