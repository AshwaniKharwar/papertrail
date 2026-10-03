# Papertrail — Chat with PDF

Upload a PDF, sign in with Google, and ask questions about the document. The API stores users, document metadata, tags, conversations, and pgvector embeddings in PostgreSQL. Cloudflare R2 stores PDF files. A worker extracts page text, uses Gemini OCR for scanned pages, chunks and embeds the text, then marks the document ready. Chat uses semantic and keyword retrieval and sends only relevant excerpts to Gemini.

## Setup

1. Copy `backend/.env.example` to `backend/.env`, then fill in `GEMINI_API_KEY`, `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `R2_ENDPOINT`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY`. Generate a random `SESSION_SECRET`, for example with `openssl rand -hex 32`. The R2 endpoint looks like `https://<account-id>.r2.cloudflarestorage.com`; use an S3 API token with object read, write, and delete permissions for the bucket.
2. Create a Google OAuth **Web application** client. Add `http://localhost:8000/auth/google/callback` to **Authorized redirect URIs**. This exact URL is also in `GOOGLE_REDIRECT_URI` in `backend/.env`. If the consent screen is in testing, add your Google account as a test user.
3. Use a PostgreSQL database URL such as `postgresql+psycopg://user:password@localhost:5432/chat_with_pdf`.
4. From `backend/`, run `uv sync`, then `uv run alembic upgrade head`. The migration installs the `vector` extension; pgvector must be available on the PostgreSQL server.
5. In separate terminals in `backend/`, run `uv run uvicorn main:app --reload --host localhost --port 8000` and `uv run python worker.py`. The worker processes queued PDFs, resumes stalled jobs, and migrates PDFs previously stored in the local `data/` directory to R2.
6. From `frontend/`, run `bun install`, then `bun run dev`.
7. Open `http://localhost:5173`.

PDF uploads are limited to 100 MB. The frontend origin defaults to `http://localhost:5173`; set `FRONTEND_ORIGIN` in `backend/.env` to change it. Set `VITE_API_URL` for the frontend if the API is hosted elsewhere. Use `uv run python test_smoke.py` to verify the isolated RAG flow against your PostgreSQL server.
