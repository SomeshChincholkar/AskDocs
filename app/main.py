"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg_pool import AsyncConnectionPool
from starlette.formparsers import MultiPartParser

from app.config import Settings, get_settings
from app.deps import CurrentUser, get_current_user
from app.middleware import InMemoryUploadMiddleware, SecurityHeadersMiddleware
from app.routers import auth, chat, documents, pages, sessions
from app.services.auth import AuthError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application and its managed database pool."""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from app.db import create_pool

        pool = create_pool(app_settings)
        application.state.pool = pool
        application.state.settings = app_settings
        application.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
        if pool is not None:
            await pool.open(wait=True)
        try:
            yield
        finally:
            if pool is not None:
                await pool.close()

    application = FastAPI(title="AskDocs", lifespan=lifespan)
    upload_body_limit = (
        app_settings.max_upload_mb * 1024 * 1024 * app_settings.max_files_per_session
        + 1024 * 1024
    )
    MultiPartParser.spool_max_size = upload_body_limit + 1
    application.add_middleware(InMemoryUploadMiddleware, settings=app_settings)
    application.add_middleware(SecurityHeadersMiddleware)
    application.state.pool = None
    application.state.settings = app_settings
    application.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    application.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )
    application.include_router(pages.router)
    application.include_router(auth.router)
    application.include_router(sessions.router)
    application.include_router(documents.router)
    application.include_router(chat.router)

    @application.get("/health")
    async def health(request: Request) -> JSONResponse:
        pool: AsyncConnectionPool | None = request.app.state.pool
        if pool is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "not configured"},
            )
        try:
            async with pool.connection() as connection:
                await connection.execute("SELECT 1")
        except Exception:
            logger.exception("Database health check failed.")
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unreachable"},
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    @application.get("/api/me")
    async def me(
        user: CurrentUser = Depends(get_current_user),
    ) -> dict[str, object]:
        return {"user_id": user.id, "is_anonymous": user.is_anonymous}

    @application.exception_handler(AuthError)
    async def authentication_error_handler(
        _request: Request,
        error: AuthError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail, "code": error.code},
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled request error for %s.", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected server error occurred."},
        )

    return application


app = create_app()
