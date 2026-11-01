from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from psycopg.errors import UniqueViolation


class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self.rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class FakeTransaction:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.snapshot = deepcopy(
            (self.pool.users, self.pool.sessions, self.pool.documents, self.pool.chunks)
        )
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            (
                self.pool.users,
                self.pool.sessions,
                self.pool.documents,
                self.pool.chunks,
            ) = self.snapshot
        return False


class FakeConnection:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return FakeTransaction(self.pool)

    async def execute(self, query, params=()):
        sql = " ".join(query.lower().split())
        if sql.startswith("select id, username, is_anonymous, token_version, password_hash"):
            username = params[0].casefold()
            return FakeCursor(
                [
                    row.copy()
                    for row in self.pool.users.values()
                    if row["username"] and row["username"].casefold() == username
                    and not row["is_anonymous"]
                ]
            )
        if sql.startswith("select id, username, is_anonymous, token_version from users"):
            user = self.pool.users.get(UUID(str(params[0])))
            return FakeCursor([user.copy()] if user else [])
        if sql.startswith("select id from users") and "for update" in sql:
            user = self.pool.users.get(UUID(str(params[0])))
            valid = user and user["is_anonymous"] and user["username"] is None
            return FakeCursor([{"id": user["id"]}] if valid else [])
        if sql.startswith("insert into users (id, is_anonymous)"):
            user_id = UUID(str(params[0]))
            user = {
                "id": user_id,
                "username": None,
                "password_hash": None,
                "is_anonymous": True,
                "token_version": 0,
            }
            self.pool.users[user_id] = user
            return FakeCursor([user.copy()], 1)
        if sql.startswith("update users set username ="):
            username, password_hash, user_id = params
            user = self.pool.users[UUID(str(user_id))]
            if any(
                row["username"]
                and row["username"].casefold() == username.casefold()
                and row["id"] != user["id"]
                for row in self.pool.users.values()
            ):
                raise UniqueViolation("duplicate username")
            user.update(
                username=username,
                password_hash=password_hash,
                is_anonymous=False,
                token_version=user["token_version"] + 1,
            )
            return FakeCursor([user.copy()], 1)
        if sql.startswith("insert into users (username, password_hash, is_anonymous)"):
            username, password_hash = params
            if any(
                row["username"] and row["username"].casefold() == username.casefold()
                for row in self.pool.users.values()
            ):
                raise UniqueViolation("duplicate username")
            user_id = UUID(int=len(self.pool.users) + 1)
            user = {
                "id": user_id,
                "username": username,
                "password_hash": password_hash,
                "is_anonymous": False,
                "token_version": 0,
            }
            self.pool.users[user_id] = user
            return FakeCursor([user.copy()], 1)
        if sql.startswith("select count(*) as total from sessions"):
            user_id = UUID(str(params[0]))
            return FakeCursor([{"total": sum(row["user_id"] == user_id for row in self.pool.sessions)}])
        if sql.startswith("select count(*) as total from documents"):
            user_id = UUID(str(params[0]))
            return FakeCursor([{"total": sum(row["user_id"] == user_id for row in self.pool.documents)}])
        if sql.startswith("update users set last_seen_at"):
            return FakeCursor(rowcount=1)
        if sql.startswith("select id, title, created_at, updated_at from sessions where user_id"):
            user_id = UUID(str(params[0]))
            rows = [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": row.get("created_at", datetime.now(timezone.utc)),
                    "updated_at": row.get("updated_at", datetime.now(timezone.utc)),
                }
                for row in self.pool.sessions
                if row["user_id"] == user_id
            ]
            return FakeCursor(rows)
        if sql.startswith("select 1 from sessions where id ="):
            session_id, user_id = UUID(str(params[0])), UUID(str(params[1]))
            found = any(
                row["id"] == session_id and row["user_id"] == user_id
                for row in self.pool.sessions
            )
            return FakeCursor([{"?column?": 1}] if found else [])
        if sql.startswith("update users set token_version = token_version + 1"):
            user = self.pool.users[UUID(str(params[0]))]
            user["token_version"] += 1
            return FakeCursor(rowcount=1)
        if sql.startswith("update sessions set user_id ="):
            new_id, old_id = map(lambda value: UUID(str(value)), params)
            rows = [row for row in self.pool.sessions if row["user_id"] == old_id]
            for row in rows:
                row["user_id"] = new_id
            return FakeCursor(rowcount=len(rows))
        if sql.startswith("update documents set user_id ="):
            if self.pool.fail_document_merge:
                raise RuntimeError("Simulated document move failure")
            new_id, old_id = map(lambda value: UUID(str(value)), params)
            rows = [row for row in self.pool.documents if row["user_id"] == old_id]
            for row in rows:
                row["user_id"] = new_id
            return FakeCursor(rowcount=len(rows))
        if sql.startswith("update chunks set user_id ="):
            new_id, old_id = map(lambda value: UUID(str(value)), params)
            rows = [row for row in self.pool.chunks if row["user_id"] == old_id]
            for row in rows:
                row["user_id"] = new_id
            return FakeCursor(rowcount=len(rows))
        if sql.startswith("delete from users"):
            user_id = UUID(str(params[0]))
            deleted = self.pool.users.pop(user_id, None)
            return FakeCursor(rowcount=int(deleted is not None))
        raise AssertionError(f"Unexpected SQL in fake database: {query}")


class FakePool:
    def __init__(self):
        self.users = {}
        self.sessions = []
        self.documents = []
        self.chunks = []
        self.fail_document_merge = False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield FakeConnection(self)


def new_user(pool, user_id: UUID, username=None, password_hash=None, anonymous=True, version=0):
    pool.users[user_id] = {
        "id": user_id,
        "username": username,
        "password_hash": password_hash,
        "is_anonymous": anonymous,
        "token_version": version,
    }
