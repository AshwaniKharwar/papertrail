"""Run with `uv run python test_smoke.py` against configured PostgreSQL."""

import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import main
from app import ai, auth, config, db, ingestion, storage
from app.models import Base, User


class FakeModels:
    def embed_content(self, *, contents, **kwargs):
        count = len(contents) if isinstance(contents, list) else 1
        item = type("Embedding", (), {"values": [1.0] + [0.0] * 767})
        return type("Result", (), {"embeddings": [item() for _ in range(count)]})()

    def generate_content_stream(self, *, contents, **kwargs):
        assert "Retrieved excerpts:" in contents and "verification code" in contents.lower()
        yield type("Chunk", (), {"text": "The code "})()
        yield type("Chunk", (), {"text": "is 42 [1]."})()


def test_rag_flow():
    schema = "test_rag_" + uuid.uuid4().hex[:12]
    admin = create_engine(config.DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(config.DATABASE_URL).execution_options(schema_translate_map={None: schema})
    original = (db.SessionLocal, ai.gemini, config.DATA_DIR)
    objects: dict[str, bytes] = {}

    def upload(fileobj, key):
        objects[key] = fileobj.read()

    def download(fileobj, key):
        fileobj.write(objects[key])

    class Body:
        def __init__(self, data): self.data = data
        def iter_chunks(self, chunk_size): yield self.data
        def close(self): pass

    def open_object(key, byte_range=None):
        return {"Body": Body(objects[key]), "ContentLength": len(objects[key])}

    try:
        Base.metadata.create_all(engine)
        with admin.connect() as connection:
            assert connection.execute(text("select to_regclass(:table)"), {"table": f"{schema}.documents"}).scalar() is not None
        db.SessionLocal = sessionmaker(engine)
        ai.gemini = type("FakeGemini", (), {"models": FakeModels()})()
        with TemporaryDirectory() as folder, patch.object(storage, "ready", return_value=True), patch.object(storage, "upload", side_effect=upload), patch.object(storage, "download", side_effect=download), patch.object(storage, "open_object", side_effect=open_object), patch.object(storage, "delete", side_effect=lambda key: objects.pop(key, None)), TestClient(main.app) as client:
            config.DATA_DIR = Path(folder)
            with Session(engine) as test_db:
                user = User(google_sub="one", email="one@example.com", name="One")
                other = User(google_sub="two", email="two@example.com", name="Two")
                test_db.add_all([user, other])
                test_db.commit()
                user_id, other_id = user.id, other.id
            assert client.get("/documents").status_code == 401
            client.cookies.set("session", auth.signer.dumps(str(user_id)))
            pdf = pymupdf.open()
            pdf.new_page().insert_text((72, 72), "The verification code is 42. This page tests retrieval, page citations, and document isolation.")
            uploaded = client.post("/documents", files={"file": ("book.pdf", pdf.tobytes(), "application/pdf")}, headers={"Origin": config.FRONTEND_ORIGIN})
            assert uploaded.status_code == 201, uploaded.text
            doc_id = uploaded.json()["id"]
            assert uploaded.json()["status"] == "queued"
            assert ingestion.run_once()
            document = client.get("/documents").json()[0]
            assert document["status"] == "ready" and document["page_count"] == 1
            tagged = client.patch(f"/documents/{doc_id}", json={"tags": ["research", "book"]}, headers={"Origin": config.FRONTEND_ORIGIN})
            assert tagged.status_code == 200 and tagged.json()["tags"] == ["research", "book"]
            asked = client.post(f"/documents/{doc_id}/messages", json={"question": "What is the verification code?"}, headers={"Origin": config.FRONTEND_ORIGIN})
            assert asked.status_code == 200, asked.text
            events = [json.loads(line) for line in asked.text.splitlines()]
            assert [event["type"] for event in events] == ["delta", "delta", "done"]
            assert "".join(event["text"] for event in events[:-1]) == "The code is 42 [1]."
            assert events[-1]["sources"][0]["page"] == 1
            assert len(client.get(f"/documents/{doc_id}/messages").json()) == 2
            assert client.get(f"/documents/{doc_id}/file").status_code == 200
            client.cookies.clear()
            client.cookies.set("session", auth.signer.dumps(str(other_id)))
            assert client.get(f"/documents/{doc_id}/file").status_code == 404
            assert client.get(f"/documents/{doc_id}/messages").status_code == 404
    finally:
        db.SessionLocal, ai.gemini, config.DATA_DIR = original
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


if __name__ == "__main__":
    test_rag_flow()
    print("RAG smoke test passed")
