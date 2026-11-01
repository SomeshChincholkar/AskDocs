import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from app.config import Settings
from app.services import ingestion


class FakeCursor:
    def __init__(self) -> None:
        self.inserted_rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def executemany(self, _query, rows):
        self.inserted_rows = list(rows)


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.executed = []

    def transaction(self):
        return _AsyncContext(self)

    def cursor(self):
        return self.cursor_instance

    async def execute(self, query, params):
        self.executed.append((query, params))


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return None


class FakePool:
    def __init__(self):
        self.connection_instance = FakeConnection()

    @asynccontextmanager
    async def connection(self):
        yield self.connection_instance


def test_ingestion_inserts_chunks_with_async_cursor(monkeypatch):
    async def fake_extract(*_):
        return [(1, "Example document content.")]

    async def fake_embeddings(texts, _settings):
        return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(ingestion, "extract_document", fake_extract)
    monkeypatch.setattr(ingestion, "embed_documents", fake_embeddings)

    pool = FakePool()
    document_id, session_id, user_id = uuid4(), uuid4(), uuid4()
    asyncio.run(
        ingestion.ingest_document(
            pool,
            Settings(),
            document_id,
            session_id,
            user_id,
            "notes.txt",
            "text/plain",
            b"Example document content.",
        )
    )

    assert len(pool.connection_instance.cursor_instance.inserted_rows) == 1
    update_query, update_params = pool.connection_instance.executed[-1]
    assert "status = 'ready'" in update_query
    assert update_params == (1, 1, document_id, user_id)
