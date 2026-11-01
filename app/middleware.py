"""Bound and keep multipart uploads in memory before FastAPI parses them."""

import json
from collections import deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings


class InMemoryUploadMiddleware:
    """Buffer bounded document uploads so multipart files cannot spill to disk."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.max_body_bytes = (
            settings.max_upload_mb * 1024 * 1024 * settings.max_files_per_session
            + 1024 * 1024
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        is_upload = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].startswith("/api/sessions/")
            and scope["path"].endswith("/documents")
        )
        if not is_upload:
            await self.app(scope, receive, send)
            return

        messages: deque[Message] = deque()
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            messages.append(message)
            total_bytes += len(message.get("body", b""))
            if total_bytes > self.max_body_bytes:
                body = json.dumps(
                    {"detail": "The upload request exceeds the maximum allowed size."}
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body,
                        "more_body": False,
                    }
                )
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)
