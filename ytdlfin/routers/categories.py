"""Category CRUD JSON API and HTMX partial routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db as database
from ..auth import require_admin
from ..utils import _parse_category, _validate_path, templates

router = APIRouter()


# ── JSON API: categories ──────────────────────────────────────────────────────

@router.get("/api/categories")
async def api_list_categories(request: Request, user=Depends(require_admin)):
    return await database.list_categories(request.app.state.db)


@router.post("/api/categories")
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
            return HTMLResponse(exc.detail, status_code=400)
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
            return HTMLResponse(msg, status_code=400)
        raise HTTPException(400, msg)

    if is_htmx:
        cats = await database.list_categories(request.app.state.db)
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": cats},
        )
    return JSONResponse(content=cat, status_code=201)


@router.put("/api/categories/{category_id}")
async def api_update_category(
    category_id: int,
    request: Request,
    user=Depends(require_admin),
):
    """
    Accepts JSON or form data. Returns the updated row HTML for HTMX; JSON otherwise.
    On path validation error for HTMX, returns the edit form with an inline error.
    """
    is_htmx = request.headers.get("HX-Request") == "true"
    payload = await _parse_category(request)

    try:
        _validate_path(payload.path)
    except HTTPException as exc:
        if is_htmx:
            cat = await database.get_category(request.app.state.db, category_id)
            return templates.TemplateResponse(
                request,
                "partials/category_edit_row.html",
                {"cat": cat, "error": exc.detail},
                status_code=422,
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
            request,
            "partials/category_row.html",
            {"cat": cat},
        )
    return cat


@router.delete("/api/categories/{category_id}")
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
            cat = await database.get_category(conn, category_id)
            return templates.TemplateResponse(
                request,
                "partials/category_row.html",
                {"cat": cat, "error": msg},
                status_code=409,
            )
        raise HTTPException(409, msg)

    deleted = await database.delete_category(conn, category_id)
    if not deleted:
        raise HTTPException(404, "Category not found")

    if is_htmx:
        # Empty body causes hx-swap="outerHTML" to remove the row from the DOM.
        return HTMLResponse("", status_code=200)
    return {"ok": True}


# ── HTMX partials: admin category management ──────────────────────────────────

@router.get("/partials/categories", response_class=HTMLResponse)
async def partial_category_list(request: Request, user=Depends(require_admin)):
    cats = await database.list_categories(request.app.state.db)
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": cats},
    )


@router.get("/partials/categories/{category_id}", response_class=HTMLResponse)
async def partial_category_row(
    category_id: int, request: Request, user=Depends(require_admin)
):
    cat = await database.get_category(request.app.state.db, category_id)
    if not cat:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "partials/category_row.html",
        {"cat": cat},
    )


@router.get("/partials/categories/{category_id}/edit", response_class=HTMLResponse)
async def partial_category_edit(
    category_id: int, request: Request, user=Depends(require_admin)
):
    cat = await database.get_category(request.app.state.db, category_id)
    if not cat:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "partials/category_edit_row.html",
        {"cat": cat},
    )
