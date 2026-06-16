"""HTML page routes and browser form submit handler."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db as database
from ..auth import flash, get_current_user, require_admin
from ..models import normalize_quality
from ..utils import _execute_create_download, _render, templates

router = APIRouter()


@router.get("/auth/denied", response_class=HTMLResponse)
async def auth_denied(request: Request):
    """Shown when a user authenticates with PocketID but lacks group access."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": (
                "Your account is not in an authorized group for this application. "
                "Contact an administrator to request access."
            ),
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(get_current_user)):
    categories = await database.list_categories(request.app.state.db)
    queue = await database.get_queue(request.app.state.db)
    return _render(
        "index.html",
        request,
        categories=categories,
        queue=queue,
        resolutions=[],
        quality_selected="1080p",
    )


@router.get("/history", response_class=HTMLResponse)
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


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user=Depends(require_admin)):
    categories = await database.list_categories(request.app.state.db)
    return _render("admin.html", request, categories=categories)


@router.post("/downloads")
async def submit_download(request: Request, user=Depends(get_current_user)):
    """
    Browser form POST. Validates, creates the DB record, and redirects to /
    with a flash message. Errors are surfaced as flash messages, not exceptions.
    """
    form = await request.form()
    url = str(form.get("url", "")).strip()
    try:
        category_id = int(form.get("category_id", 0))
    except ValueError:
        category_id = 0
    quality = normalize_quality(str(form.get("quality", "1080p")))
    custom_title = str(form.get("custom_title", "")).strip() or None

    if not url:
        flash(request, "URL is required.", "error")
        return RedirectResponse(url="/", status_code=303)

    try:
        await _execute_create_download(
            request.app.state.db, url, category_id, quality, custom_title, user
        )
    except HTTPException as exc:
        flash(request, exc.detail, "error")
        return RedirectResponse(url="/", status_code=303)

    flash(request, "Added to queue. Click “Start downloads” when ready.", "success")
    return RedirectResponse(url="/", status_code=303)
