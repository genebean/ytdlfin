"""Shared utilities: template rendering, URL/path validation, category parsing."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db as database
from .auth import pop_flashes
from .models import CategoryCreate

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_raw_media_dirs = os.environ.get("MEDIA_DIRECTORIES", "")
MEDIA_DIRECTORIES: list[Path] = [
    Path(p).resolve() for p in _raw_media_dirs.split(":") if p.strip()
]


def _render(name: str, request: Request, **ctx) -> HTMLResponse:
    """Render a Jinja2 template with flash messages and current user injected."""
    ctx["flash_messages"] = pop_flashes(request)
    ctx["user"] = request.session.get("user")
    return templates.TemplateResponse(request, name, ctx)


def _validate_url_scheme(url: str) -> bool:
    """Return True only for http/https URLs — rejects file://, rtmp://, etc."""
    try:
        return urlparse(url).scheme in ("http", "https")
    except Exception:
        return False


def _validate_path(path: str) -> None:
    """Raise HTTP 400 if path is not a writable directory inside MEDIA_DIRECTORIES."""
    resolved = Path(path).resolve()
    if not resolved.is_dir() or not os.access(resolved, os.W_OK):
        raise HTTPException(400, "Path does not exist or is not writable.")
    if MEDIA_DIRECTORIES and not any(
        resolved == allowed or resolved.is_relative_to(allowed)
        for allowed in MEDIA_DIRECTORIES
    ):
        raise HTTPException(400, "Path is not within an allowed media directory.")


async def _parse_category(request: Request) -> CategoryCreate:
    """
    Parse a CategoryCreate payload from JSON or form data.
    Allows the same endpoint to serve both API clients and HTMX form submissions.
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


async def _execute_create_download(
    conn,
    url: str,
    category_id: int,
    quality: str,
    custom_title: str | None,
    user: dict,
) -> dict:
    """
    Shared create-download logic used by both the form handler and JSON API.
    Raises HTTPException on any validation failure.
    The form handler catches HTTPException and converts errors to flash messages.
    """
    if not _validate_url_scheme(url):
        raise HTTPException(400, "Only http:// and https:// URLs are supported.")
    if await database.url_is_active(conn, url):
        raise HTTPException(400, "URL is already pending or downloading")
    category = await database.get_category(conn, category_id)
    if not category:
        raise HTTPException(400, "Category not found")
    return await database.create_download(
        conn,
        url=url,
        category=category,
        quality=quality,
        custom_title=custom_title,
        requested_by_email=user["email"],
        requested_by_name=user["name"],
    )
