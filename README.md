# Papertrail — Chat with PDF

Upload PDFs and ask questions about them. Google OAuth handles sign-in, PostgreSQL stores document metadata and conversations, Cloudflare R2 stores the PDFs, and Gemini handles OCR, embeddings, and answers. A separate worker indexes uploads before they can be queried.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh/) for the frontend
- PostgreSQL with the `vector` extension available
- A Gemini API key, a Cloudflare R2 bucket with an S3 API token, and a Google OAuth Web application client

## Setup

1. Copy the example environment file and fill in the credentials:

   ```sh
   cp backend/.env.example backend/.env
   ```

   Set `DATABASE_URL`, `GEMINI_API_KEY`, `SESSION_SECRET`, `R2_ENDPOINT`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET`. A database URL looks like `postgresql+psycopg://user:password@localhost:5432/chat_with_pdf`. Generate `SESSION_SECRET` with `openssl rand -hex 32`. The R2 endpoint is `https://<account-id>.r2.cloudflarestorage.com`; the S3 token needs object read, write, and delete access.

2. In the Google OAuth client, add `http://localhost:8000/auth/google/callback` as an authorized redirect URI. It must match `GOOGLE_REDIRECT_URI` in `backend/.env`. If the consent screen is in testing, add your account as a test user.

3. Install dependencies and run migrations:

   ```sh
   cd backend
   uv sync
   uv run alembic upgrade head
   cd ../frontend
   bun install
   ```

   The migration creates the `vector` extension, which must be installed on the PostgreSQL server and available to the database user.

## Run locally

Start these in separate terminals from the repository root:

```sh
cd backend && uv run uvicorn main:app --reload --host localhost --port 8000
```

```sh
cd backend && uv run python worker.py
```

```sh
cd frontend && bun run dev
```

Open `http://localhost:5173`. The API is at `http://localhost:8000`; its interactive docs are at `/docs`. The worker processes queued PDFs, retries jobs that stalled during processing, and moves legacy PDFs from `data/` to R2.

## Backend structure

```text
backend/
├── main.py              # ASGI entrypoint
├── worker.py            # ingestion worker entrypoint
├── app/
│   ├── main.py          # FastAPI setup, middleware, router registration
│   ├── auth.py          # Google OAuth, sessions, current user
│   ├── documents.py     # upload, download, metadata, retry, delete
│   ├── chat.py          # message routes and answer streaming
│   ├── ingestion.py     # job claiming and indexing workflow
│   ├── indexing.py      # PDF text, OCR, chunking, document embeddings
│   ├── retrieval.py     # question embeddings and hybrid search
│   ├── config.py        # environment settings
│   ├── db.py            # database engine and sessions
│   ├── ai.py            # Gemini client
│   ├── models.py        # SQLAlchemy models
│   └── storage.py       # R2 access
├── migrations/          # Alembic schema migrations
└── test_smoke.py        # end-to-end backend smoke test
```

Uploads are limited to 100 MB. A new document is queued until the worker extracts text, uses OCR when needed, chunks and embeds it, and marks it ready. Questions use semantic and keyword retrieval over that document's chunks; answers stream as newline-delimited JSON with page sources.

## Retrieval techniques

The application uses a hybrid retrieval pipeline that blends dense semantic vector search with sparse lexical search, re-ranked via Reciprocal Rank Fusion (RRF):

- **Asymmetric Task Instruction Prefixing**: Chunks and questions are embedded with `gemini-embedding-2` (768 dimensions). Queries use the prefix `task: question answering | query: {question}` to optimize the embedding representation for asymmetric search, while document chunks are embedded with `title: {title} | text: {content}`.
- **Dense Semantic Retrieval (Vector Search)**: Queries document chunk embeddings in PostgreSQL using `pgvector` with an **HNSW** (Hierarchical Navigable Small World) index configured for cosine distance (`vector_cosine_ops`). Retrieves the top 12 semantic candidates for the document.
- **Sparse Lexical Retrieval (Full-Text Search)**: Executes native PostgreSQL full-text search (`to_tsvector` / `plainto_tsquery` in English) accelerated by a **GIN** index. Candidates are ranked using Cover Density Ranking (`ts_rank_cd`), which scores matches higher when query terms appear in close proximity. Retrieves the top 12 keyword candidates.
- **Reciprocal Rank Fusion (RRF) Re-ranking**: Combines rankings from the semantic and lexical result sets without requiring raw score normalization. Fused scores are calculated using:
  $$\text{Score}(d) = \sum_{m \in \{\text{semantic}, \text{lexical}\}} \frac{1}{60 + \text{rank}_m(d)}$$
  Chunks present in both candidate sets receive combined boosts. The top 8 chunks with the highest fused score are provided as context to the LLM.
- **Sliding-Window Chunking with Overlap**: Extracted document text is partitioned into 450-word windows with a 70-word overlap to preserve context across chunk boundaries, falling back to Gemini Vision OCR when extracted page text is sparse (< 40 characters).
- **Document-Scoped Filtering & Source Grounding**: All search queries strictly filter by `document_id` to guarantee document isolation. Chunks retain their original page numbers, enabling the LLM to ground answers and cite exact source pages (`[1]`, `[2]`).

## Configuration and checks

`FRONTEND_ORIGIN` defaults to `http://localhost:5173`. Set it to the frontend's exact origin when hosting elsewhere. Set `VITE_API_URL` in the frontend environment when the API is not at `http://localhost:8000`. `GOOGLE_REDIRECT_URI` and `GEMINI_MODEL` can also be changed in `backend/.env`.

Run the backend smoke test against a configured PostgreSQL database:

```sh
cd backend && uv run python test_smoke.py
```

It creates and drops an isolated test schema and uses fake storage and Gemini responses. To check schema drift, run `cd backend && uv run alembic check`.
