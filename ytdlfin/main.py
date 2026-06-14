"""FastAPI application — factory, lifespan, middleware, and all route handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
import yt_dlp.utils
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db as database
from .auth import (
    NotAdmin,
    NotAuthenticated,
    flash,
    get_current_user,
    pop_flashes,
    require_admin,
    router as auth_router,
)
from .models import CategoryCreate, CategoryUpdate, DownloadRequest
from .worker import download_worker, recover_staging
from .ytdlp import STAGING_DIR

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
PORT = int(os.environ.get("PORT", "8000"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(name: str, request: Request, **ctx) -> HTMLResponse:
    """Render a Jinja2 template with flash messages injected."""
    ctx["flash_messages"] = pop_flashes(request)
    ctx["user"] = request.session.get("user")
    return templates.TemplateResponse(name, {"request": request, **ctx})


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the data directory exists.
    database.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    conn = await database.open_db()
    await database.init_schema(conn)

    # Recovery: clean up orphaned staging dirs, reset interrupted downloads.
    await recover_staging()
    await database.reset_interrupted_downloads(conn)

    # Build the download queue and re-enqueue pending items in submission order.
    queue: asyncio.Queue[int] = asyncio.Queue()
    for pending_id in await database.get_pending_ids(conn):
        await queue.put(pending_id)

    app.state.db = conn
    app.state.queue = queue

    worker_task = asyncio.create_task(download_worker(conn, queue))

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await conn.close()


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="ytdlfin", lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=SECRET_KEY,
        max_age=60 * 60 * 24 * 7,  # 1 week
        https_only=False,           # set True in production behind HTTPS
        same_site="lax",
    )

    # ── Exception handlers ────────────────────────────────────────────────────

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/auth/login", status_code=303)

    @app.exception_handler(NotAdmin)
    async def not_admin_handler(request: Request, exc: NotAdmin):
        return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)

    # ── Auth routes ───────────────────────────────────────────────────────────
    app.include_router(auth_router)

    @app.get("/auth/denied", response_class=HTMLResponse)
    async def auth_denied(request: Request):
        """Shown when a user authenticates with PocketID but lacks group access."""
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Your account is not in an authorized group for this application. "
                         "Contact an administrator to request access.",
            },
        )

    # ── Page routes ───────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, user=Depends(get_current_user)):
        categories = await database.list_categories(request.app.state.db)
        queue = await database.get_queue(request.app.state.db)
        return _render("index.html", request, categories=categories, queue=queue)

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(
        request: Request,
        page: int = 1,
        status: str = "",
        user=Depends(get_current_user),
    ):
        result = await database.list_downloads(
            request.app.state.db,
            page=page,
            per_page=20,
            status=status or None,
            user_email=user["email"],
            is_admin=user["is_admin"],
        )
        return _render(
            "history.html",
            request,
            downloads=result["items"],
            page=result["page"],
            pages=result["pages"],
            total=result["total"],
            status_filter=status,
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request, user=Depends(require_admin)):
        categories = await database.list_categories(request.app.state.db)
        return _render("admin.html", request, categories=categories)

    # ── Browser form: submit a download ──────────────────────────────────────

    @app.post("/downloads")
    async def submit_download(request: Request, user=Depends(get_current_user)):
        """
        Browser form POST handler. Validates, creates the DB record, enqueues,
        and redirects back to / with a flash message.
        """
        form = await request.form()
        url = str(form.get("url", "")).strip()
        try:
            category_id = int(form.get("category_id", 0))
        except ValueError:
            category_id = 0
        quality = str(form.get("quality", "1080p"))
        custom_title = str(form.get("custom_title", "")).strip() or None

        if not url:
            flash(request, "URL is required.", "error")
            return RedirectResponse(url="/", status_code=303)

        conn = request.app.state.db

        if await database.url_is_active(conn, url):
            flash(request, "That URL is already in the queue.", "error")
            return RedirectResponse(url="/", status_code=303)

        category = await database.get_category(conn, category_id)
        if not category:
            flash(request, "Invalid category selected.", "error")
            return RedirectResponse(url="/", status_code=303)

        if quality not in ("1080p", "best"):
            quality = "1080p"

        record = await database.create_download(
            conn,
            url=url,
            category=category,
            quality=quality,
            custom_title=custom_title,
            requested_by_email=user["email"],
            requested_by_name=user["name"],
        )
        await request.app.state.queue.put(record["id"])

        flash(request, "Download queued.", "success")
        return RedirectResponse(url="/", status_code=303)

    # ── HTMX partial: active queue ────────────────────────────────────────────

    @app.get("/api/queue", response_class=HTMLResponse)
    async def api_queue(request: Request, user=Depends(get_current_user)):
        """Returns an HTML partial for the queue section (polled every 3s by HTMX)."""
        queue = await database.get_queue(request.app.state.db)
        return templates.TemplateResponse(
            "queue_partial.html", {"request": request, "queue": queue, "user": user}
        )

    # ── JSON API: downloads ───────────────────────────────────────────────────

    @app.post("/api/downloads", status_code=201)
    async def api_create_download(
        payload: DownloadRequest,
        request: Request,
        user=Depends(get_current_user),
    ):
        conn = request.app.state.db

        if await database.url_is_active(conn, payload.url):
            raise HTTPException(400, "URL is already pending or downloading")

        category = await database.get_category(conn, payload.category_id)
        if not category:
            raise HTTPException(400, "Category not found")

        record = await database.create_download(
            conn,
            url=payload.url,
            category=category,
            quality=payload.quality,
            custom_title=payload.custom_title,
            requested_by_email=user["email"],
            requested_by_name=user["name"],
        )
        await request.app.state.queue.put(record["id"])
        return record

    @app.get("/api/downloads")
    async def api_list_downloads(
        request: Request,
        page: int = 1,
        per_page: int = 20,
        status: str = "",
        user=Depends(get_current_user),
    ):
        return await database.list_downloads(
            request.app.state.db,
            page=page,
            per_page=per_page,
            status=status or None,
            user_email=user["email"],
            is_admin=user["is_admin"],
        )

    @app.delete("/api/downloads/{download_id}")
    async def api_cancel_download(
        download_id: int,
        request: Request,
        user=Depends(get_current_user),
    ):
        conn = request.app.state.db
        record = await database.get_download(conn, download_id)
        if not record:
            raise HTTPException(404, "Download not found")

        # Only the requesting user or an admin may cancel.
        if not user["is_admin"] and record["requested_by_email"] != user["email"]:
            raise HTTPException(403, "Not allowed")

        if record["status"] != "pending":
            raise HTTPException(409, "Only pending downloads can be cancelled")

        await database.cancel_download(conn, download_id)
        return {"ok": True}

    # ── JSON API: categories ──────────────────────────────────────────────────

    @app.get("/api/categories")
    async def api_list_categories(
        request: Request, user=Depends(get_current_user)
    ):
        return await database.list_categories(request.app.state.db)

    @app.post("/api/categories")
    async def api_create_category(
        request: Request,
        user=Depends(require_admin),
    ):
        """
        Accepts JSON (programmatic) or form data (admin HTMX form).
        Returns the new category list HTML partial for HTMX callers, JSON for others.
        """
        is_htmx = request.headers.get("HX-Request") == "true"
        payload = await _parse_category(request)

        try:
            _validate_path(payload.path)
        except HTTPException as exc:
            if is_htmx:
                return HTMLResponse(exc.detail, status_code=200)
            raise

        try:
            cat = await database.create_category(
                request.app.state.db, payload.name, payload.path, payload.description
            )
        except Exception as exc:
            msg = (
                "A category with that name already exists"
                if "UNIQUE constraint" in str(exc)
                else str(exc)
            )
            if is_htmx:
                return HTMLResponse(msg, status_code=200)
            raise HTTPException(400, msg)

        if is_htmx:
            cats = await database.list_categories(request.app.state.db)
            return templates.TemplateResponse(
                "partials/category_list.html",
                {"request": request, "categories": cats},
            )
        return JSONResponse(content=cat, status_code=201)

    @app.put("/api/categories/{category_id}")
    async def api_update_category(
        category_id: int,
        request: Request,
        user=Depends(require_admin),
    ):
        """
        Accepts JSON or form data. Returns the updated row HTML for HTMX; JSON otherwise.
        On validation error for HTMX, returns the edit form with an inline error.
        """
        is_htmx = request.headers.get("HX-Request") == "true"
        payload = await _parse_category(request)

        try:
            _validate_path(payload.path)
        except HTTPException as exc:
            if is_htmx:
                # Return the edit row with the error shown inline.
                cat = await database.get_category(request.app.state.db, category_id)
                return templates.TemplateResponse(
                    "partials/category_edit_row.html",
                    {"request": request, "cat": cat, "error": exc.detail},
                )
            raise

        cat = await database.update_category(
            request.app.state.db,
            category_id,
            payload.name,
            payload.path,
            payload.description,
        )
        if not cat:
            raise HTTPException(404, "Category not found")

        if is_htmx:
            return templates.TemplateResponse(
                "partials/category_row.html",
                {"request": request, "cat": cat},
            )
        return cat

    @app.delete("/api/categories/{category_id}")
    async def api_delete_category(
        category_id: int,
        request: Request,
        user=Depends(require_admin),
    ):
        """
        Deletes a category. Returns empty HTML for HTMX (removes the row); JSON otherwise.
        """
        is_htmx = request.headers.get("HX-Request") == "true"
        conn = request.app.state.db

        if await database.category_has_active_downloads(conn, category_id):
            msg = "Category has pending or active downloads; cancel them first"
            if is_htmx:
                # Return the row unchanged so the UI stays consistent.
                cat = await database.get_category(conn, category_id)
                return templates.TemplateResponse(
                    "partials/category_row.html",
                    {"request": request, "cat": cat, "error": msg},
                )
            raise HTTPException(409, msg)

        deleted = await database.delete_category(conn, category_id)
        if not deleted:
            raise HTTPException(404, "Category not found")

        # Empty body causes hx-swap="outerHTML" to remove the row from the DOM.
        if is_htmx:
            return HTMLResponse("", status_code=200)
        return {"ok": True}

    # ── HTMX partials: admin category management ──────────────────────────────

    @app.get("/partials/categories", response_class=HTMLResponse)
    async def partial_category_list(request: Request, user=Depends(require_admin)):
        cats = await database.list_categories(request.app.state.db)
        return templates.TemplateResponse(
            "partials/category_list.html", {"request": request, "categories": cats}
        )

    @app.get("/partials/categories/{category_id}", response_class=HTMLResponse)
    async def partial_category_row(
        category_id: int, request: Request, user=Depends(require_admin)
    ):
        cat = await database.get_category(request.app.state.db, category_id)
        if not cat:
            raise HTTPException(404)
        return templates.TemplateResponse(
            "partials/category_row.html", {"request": request, "cat": cat}
        )

    @app.get("/partials/categories/{category_id}/edit", response_class=HTMLResponse)
    async def partial_category_edit(
        category_id: int, request: Request, user=Depends(require_admin)
    ):
        cat = await database.get_category(request.app.state.db, category_id)
        if not cat:
            raise HTTPException(404)
        return templates.TemplateResponse(
            "partials/category_edit_row.html", {"request": request, "cat": cat}
        )

    return app


def _validate_path(path: str) -> None:
    """Raise HTTP 400 if path does not exist or is not writable."""
    import os
    if not os.path.isdir(path) or not os.access(path, os.W_OK):
        raise HTTPException(
            400,
            f"Path does not exist or is not writable: {path}",
        )


async def _parse_category(request: Request) -> "CategoryCreate":
    """
    Parse a CategoryCreate/CategoryUpdate payload from either JSON or form data.
    Allows the same endpoint to serve both JSON API clients and HTMX form submissions.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = {
            "name": str(form.get("name", "")),
            "path": str(form.get("path", "")),
            "description": str(form.get("description", "")) or None,
        }
    return CategoryCreate(**data)


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
    )
