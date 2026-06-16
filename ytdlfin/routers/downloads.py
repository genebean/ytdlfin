"""Download queue HTMX partials and JSON API routes."""

from __future__ import annotations

import asyncio
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db as database
from ..auth import get_current_user
from ..db import get_db
from ..models import DownloadRequest
from ..utils import _execute_create_download, _validate_url_scheme, templates
from ..ytdlp import get_available_resolutions

logger = logging.getLogger(__name__)

router = APIRouter()


async def _queue_partial_response(
    request: Request, user: dict, conn: aiosqlite.Connection
) -> HTMLResponse:
    """Render the queue partial with all context it needs."""
    queue = await database.get_queue(conn)
    categories = await database.list_categories(conn)
    return templates.TemplateResponse(
        request,
        "queue_partial.html",
        {"queue": queue, "user": user, "categories": categories},
    )


# ── HTMX partial: resolution picker ──────────────────────────────────────────

@router.get("/api/resolutions", response_class=HTMLResponse)
async def api_resolutions(
    request: Request, url: str = "", user=Depends(get_current_user)
):
    """Return the quality <select> HTML filled with available resolutions for url."""
    resolutions: list[int] = []
    if url and _validate_url_scheme(url):
        try:
            loop = asyncio.get_running_loop()
            resolutions = await loop.run_in_executor(
                None, lambda: get_available_resolutions(url)
            )
        except Exception:
            logger.warning("Resolution lookup failed for %s", url)

    if 1080 in resolutions:
        selected = "1080p"
    elif resolutions:
        selected = f"{resolutions[0]}p"
    else:
        selected = "1080p"

    return templates.TemplateResponse(
        request,
        "partials/quality_select.html",
        {"resolutions": resolutions, "quality_selected": selected},
    )


# ── HTMX partial: active queue ────────────────────────────────────────────────

@router.get("/api/queue", response_class=HTMLResponse)
async def api_queue(
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    """Returns an HTML partial for the queue section (polled every 3s by HTMX)."""
    return await _queue_partial_response(request, user, conn)


@router.post("/api/queue/start", response_class=HTMLResponse)
async def api_queue_start(
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    """Enqueue all pending downloads so the worker starts processing them."""
    for pid in await database.get_pending_ids(conn):
        await request.app.state.queue.put(pid)
    return await _queue_partial_response(request, user, conn)


@router.patch("/api/downloads/{download_id}", response_class=HTMLResponse)
async def api_update_download(
    download_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    """Change the category of a pending download (HTMX inline edit)."""
    record = await database.get_download(conn, download_id)
    if not record:
        raise HTTPException(404, "Download not found")
    if record["status"] != "pending":
        raise HTTPException(409, "Only pending downloads can be edited")
    if not user["is_admin"] and record["requested_by_email"] != user["email"]:
        raise HTTPException(403, "Not allowed")

    form = await request.form()
    try:
        category_id = int(form.get("category_id", 0))
    except ValueError:
        raise HTTPException(400, "Invalid category_id")

    category = await database.get_category(conn, category_id)
    if not category:
        raise HTTPException(400, "Category not found")

    updated = await database.update_download_category(conn, download_id, category)
    categories = await database.list_categories(conn)
    return templates.TemplateResponse(
        request,
        "partials/queue_row.html",
        {"item": updated, "user": user, "categories": categories},
    )


@router.get("/partials/queue/{download_id}", response_class=HTMLResponse)
async def partial_queue_row(
    download_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    """Normal queue row (used to cancel an in-progress edit)."""
    record = await database.get_download(conn, download_id)
    if not record:
        raise HTTPException(404)
    categories = await database.list_categories(conn)
    return templates.TemplateResponse(
        request,
        "partials/queue_row.html",
        {"item": record, "user": user, "categories": categories},
    )


@router.get("/partials/queue/{download_id}/edit", response_class=HTMLResponse)
async def partial_queue_row_edit(
    download_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    """Edit-mode queue row with category dropdown."""
    record = await database.get_download(conn, download_id)
    if not record:
        raise HTTPException(404)
    if record["status"] != "pending":
        raise HTTPException(409, "Only pending downloads can be edited")
    categories = await database.list_categories(conn)
    return templates.TemplateResponse(
        request,
        "partials/queue_row_edit.html",
        {"item": record, "user": user, "categories": categories},
    )


# ── JSON API: downloads ───────────────────────────────────────────────────────

@router.post("/api/downloads", status_code=201)
async def api_create_download(
    payload: DownloadRequest,
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    return await _execute_create_download(
        conn,
        payload.url,
        payload.category_id,
        payload.quality,
        payload.custom_title,
        user,
    )


@router.get("/api/downloads")
async def api_list_downloads(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    status: str = "",
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    return await database.list_downloads(
        conn,
        page=page,
        per_page=per_page,
        status=status or None,
        user_email=user["email"],
        is_admin=user["is_admin"],
    )


@router.delete("/api/downloads/{download_id}")
async def api_cancel_download(
    download_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn: aiosqlite.Connection = Depends(get_db),
):
    record = await database.get_download(conn, download_id)
    if not record:
        raise HTTPException(404, "Download not found")
    if not user["is_admin"] and record["requested_by_email"] != user["email"]:
        raise HTTPException(403, "Not allowed")
    if record["status"] != "pending":
        raise HTTPException(409, "Only pending downloads can be cancelled")
    await database.cancel_download(conn, download_id)
    return {"ok": True}
