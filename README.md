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

3. Copy `.env.example` to `.env` and set `DATABASE_URL`, `GEMINI_API_KEY`, and `JWT_SECRET`. Generate a random secret with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   `JWT_SECRET` is required and must be at least 32 characters. For Supabase, use a PostgreSQL connection string with the appropriate pooler host and port.

4. Create the tables, indexes, and apply idempotent migrations:

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

After startup, create an anonymous identity and save its bearer token:

```powershell
$anonymous = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/auth/anonymous
$anonymousToken = $anonymous.access_token
```

Register using the anonymous token to keep its existing chats, then check the account:

```powershell
$credentials = @{ username = "reader01"; password = "a secure example password" } | ConvertTo-Json
$registered = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/auth/register `
  -Headers @{ Authorization = "Bearer $anonymousToken" } -ContentType "application/json" -Body $credentials
$userToken = $registered.access_token
Invoke-RestMethod http://127.0.0.1:8000/api/auth/me -Headers @{ Authorization = "Bearer $userToken" }
Invoke-RestMethod http://127.0.0.1:8000/api/sessions -Headers @{ Authorization = "Bearer $userToken" }
```

For an existing account, log in with `Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/auth/login -ContentType "application/json" -Body $credentials`, then use the returned `access_token` as the bearer token. `GET /api/auth/me` reports the current user; missing and invalid tokens return 401.

In the browser, the app provisions an anonymous token automatically. Create a chat, upload a small PDF or text file, wait for its status to become **Ready**, and ask a question. Sign up or log in from the top bar to keep your chats under a registered account.

## Architecture and privacy

- `app/routers` implements HTML pages, session/document CRUD, and SSE chat APIs.
- `app/services` handles extraction, recursive chunking, Gemini embeddings/generation, ingestion, and pgvector retrieval.
- `app/services/auth.py` hashes passwords with Argon2id and signs/verifies versioned JWTs. Tokens are held in browser `localStorage` and sent using `Authorization: Bearer ...`.
- `app/routers/auth.py` handles anonymous token creation, registration, login, current identity, and account-wide token revocation.
- `app/services/merge.py` upgrades an anonymous row in place at registration and transactionally moves anonymous-owned data on login.
- `app/deps.py` verifies bearer tokens and applies per-IP/action sliding-window limits; client IP is never an identity.
- `sql/schema.sql` and `sql/migrations/002_auth.sql` define account-ready users and a case-insensitive unique username index. `python -m app.init_db` applies both safely.
- Uploaded bytes are bounded and buffered in memory, passed directly to the ingestion background task, and discarded after processing. Multipart parsing is configured not to spill uploads to temporary files. Original files are never written to disk or object storage. Only extracted text, embeddings, metadata, messages, and citations are stored in PostgreSQL.
- Retrieval and session/document operations are scoped by the current token's verified `user_id`. The application trusts the first `X-Forwarded-For` value for rate limiting; deploy behind a trusted proxy that overwrites this header.
- Browser tokens live in `localStorage`, so they are available to same-origin JavaScript. Keep the supplied Content Security Policy, avoid adding unsafe inline scripts, and serve the app only over HTTPS in production.

## Authentication and account data

- Anonymous bearer tokens expire after `ANON_JWT_EXPIRE_DAYS`; registered tokens expire after `JWT_EXPIRE_DAYS`. `token_version` lets `/api/auth/logout-all` invalidate all existing tokens.
- Registering with a valid anonymous token upgrades that same user row, preserving chat ownership. Logging in with a valid anonymous token moves its sessions, documents, and chunks to the account in one transaction. Invalid or registered tokens are never used as merge sources.
- Anonymous users inactive for more than 90 days can be removed with `python -m app.cleanup`. Schedule this command daily using Windows Task Scheduler, cron, or another trusted scheduler with the application environment configured. It deletes only anonymous users; foreign-key cascades remove their associated data.

Embedding and generation model names are configurable with `EMBEDDING_MODEL` and `LLM_MODEL`. Embeddings are explicitly requested at 768 dimensions and use Gemini's retrieval document/query task types.