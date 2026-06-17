"""FastAPI application — factory, lifespan, middleware, and exception handlers."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db as database
from .auth import (
    NotAdmin,
    NotAuthenticated,
    router as auth_router,
)
from .routers.categories import router as categories_router
from .routers.downloads import router as downloads_router
from .routers.pages import router as pages_router
from .worker import download_worker, recover_staging
from .ytdlp import STAGING_DIR

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY == "change-me-in-production":
    raise RuntimeError(
        "SECRET_KEY must be set to a strong random value via the environment. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
PORT = int(os.environ.get("PORT", "8001"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info")
# Set HTTPS_ONLY=true when running behind an HTTPS reverse proxy (e.g. nginx).
# Marks session cookies Secure so browsers only send them over TLS.
HTTPS_ONLY = os.environ.get("HTTPS_ONLY", "false").lower() == "true"
# IPs allowed to set X-Forwarded-* headers. Defaults to 127.0.0.1 for the
# standard same-host nginx deployment.
TRUSTED_PROXY_IPS = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1")

STATIC_DIR = Path(__file__).parent / "static"


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # Dedicated connection for the download worker — lives for the app lifetime.
    # Request handlers open their own per-request connections via the get_db dependency.
    worker_conn = await database.open_db()
    await database.init_schema(worker_conn)

    await recover_staging()
    await database.reset_interrupted_downloads(worker_conn)

    queue: asyncio.Queue[int] = asyncio.Queue()
    app.state.queue = queue

    worker_task = asyncio.create_task(download_worker(worker_conn, queue))

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await worker_conn.close()


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="ytdlfin", lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.add_middleware(
        SessionMiddleware,
        secret_key=SECRET_KEY,
        max_age=60 * 60 * 24 * 7,  # 1 week
        https_only=HTTPS_ONLY,
        same_site="lax",
    )

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/auth/login", status_code=303)

    @app.exception_handler(NotAdmin)
    async def not_admin_handler(request: Request, exc: NotAdmin):
        return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)

    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(downloads_router)
    app.include_router(categories_router)

    return app


# ── Module-level app instance and entry point ─────────────────────────────────

app = create_app()


def run() -> None:
    """Entry point called by the `ytdlfin` console script."""
    import uvicorn

    uvicorn.run(
        "ytdlfin.main:app",
        host="0.0.0.0",
        port=PORT,
        log_level=LOG_LEVEL,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips=TRUSTED_PROXY_IPS,
    )
