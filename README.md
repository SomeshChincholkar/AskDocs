# AskDocs

AskDocs is a document-grounded chat application built with FastAPI, Jinja2, vanilla JavaScript, PostgreSQL/pgvector, and Gemini. It extracts document text in memory, chunks and embeds it, stores only text and vectors, and answers questions using the visitor's session documents.

## Requirements

- Python 3.11+
- A PostgreSQL database with the pgvector extension available (Supabase works)
- A Gemini API key

## Setup

1. Create and activate a virtual environment:

   ```powershell
   py -3.11 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set `DATABASE_URL` and `GEMINI_API_KEY`. For Supabase, use a PostgreSQL connection string with the appropriate pooler host and port. Set `SECURE_COOKIES=true` when serving over HTTPS.

4. Create the tables and indexes:

   ```powershell
   python -m app.init_db
   ```

5. Start the server:

   ```powershell
   uvicorn app.main:app --reload
   ```

   Open `http://127.0.0.1:8000`. Check `http://127.0.0.1:8000/health` for database connectivity.

## Test

Run the focused unit tests:

```powershell
python -m pytest
```

After startup, verify the anonymous identity and session API:

```powershell
curl.exe -i -c cookies.txt http://127.0.0.1:8000/api/me
curl.exe -b cookies.txt http://127.0.0.1:8000/api/sessions
```

In the browser, create a chat, upload a small PDF or text file, wait for its status to become **Ready**, and ask a question. The answer streams into the chat and includes source references.

## Architecture and privacy

- `app/routers` implements HTML pages, session/document CRUD, and SSE chat APIs.
- `app/services` handles extraction, recursive chunking, Gemini embeddings/generation, ingestion, and pgvector retrieval.
- `app/deps.py` creates anonymous cookie identities and applies per-IP sliding-window limits.
- `sql/schema.sql` defines the account-ready user table and cascaded session/document/message data.
- Uploaded bytes are bounded and buffered in memory, passed directly to the ingestion background task, and discarded after processing. Multipart parsing is configured not to spill uploads to temporary files. Original files are never written to disk or object storage. Only extracted text, embeddings, metadata, messages, and citations are stored in PostgreSQL.
- Retrieval and session/document operations are scoped by the current anonymous `user_id`. The application trusts the first `X-Forwarded-For` value for rate limiting; deploy behind a trusted proxy that overwrites this header.

Embedding and generation model names are configurable with `EMBEDDING_MODEL` and `LLM_MODEL`. Embeddings are explicitly requested at 768 dimensions and use Gemini's retrieval document/query task types.