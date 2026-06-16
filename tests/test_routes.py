"""HTTP integration tests for page, download, and category routes."""

import aiosqlite
import pytest


# ── Pages ─────────────────────────────────────────────────────────────────────


def test_index_page_renders(user_client):
    resp = user_client.get("/")
    assert resp.status_code == 200
    assert "ytdlfin" in resp.text.lower()


def test_history_page_renders_empty(user_client):
    resp = user_client.get("/history")
    assert resp.status_code == 200
    assert "No downloads yet" in resp.text


def test_history_page_status_filter(user_client):
    resp = user_client.get("/history?status=done")
    assert resp.status_code == 200
    assert 'status="done"' in resp.text or "done" in resp.text


def test_admin_page_requires_admin(user_client):
    # user_client only overrides get_current_user, not require_admin, so /admin
    # should raise NotAdmin → 403
    resp = user_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_page_renders_for_admin(admin_client):
    resp = admin_client.get("/admin")
    assert resp.status_code == 200
    assert "Category" in resp.text


def test_auth_denied_page(user_client):
    resp = user_client.get("/auth/denied")
    assert resp.status_code == 200
    assert "authorized group" in resp.text


# ── Download form submission ──────────────────────────────────────────────────


def test_submit_download_requires_url(user_client, tmp_path):
    resp = user_client.post("/downloads", data={"url": "", "category_id": "1"}, follow_redirects=False)
    # Should redirect back to / with a flash error (no URL given)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_submit_download_rejects_non_http_url(user_client, tmp_path):
    resp = user_client.post(
        "/downloads",
        data={"url": "file:///etc/passwd", "category_id": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_submit_download_invalid_category(user_client):
    resp = user_client.post(
        "/downloads",
        data={"url": "https://example.com/video", "category_id": "9999"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_submit_download_success(admin_client, tmp_path):
    # Create a real category dir so path validation passes
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    # Create category via API
    resp = admin_client.post(
        "/api/categories",
        json={"name": "Movies", "path": str(cat_dir)},
    )
    assert resp.status_code == 201
    import json
    cat = json.loads(resp.content)
    cat_id = cat["id"]

    resp = admin_client.post(
        "/downloads",
        data={"url": "https://youtube.com/watch?v=test", "category_id": str(cat_id)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_submit_download_duplicate_url(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories",
        json={"name": "Movies", "path": str(cat_dir)},
    )
    import json
    cat_id = json.loads(resp.content)["id"]

    url = "https://youtube.com/watch?v=dup"
    for _ in range(2):
        resp = admin_client.post(
            "/downloads",
            data={"url": url, "category_id": str(cat_id)},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    # First request added it; second should redirect with error (still 303, but with flash)
    # We can't easily inspect flash messages without following the redirect, but
    # the important thing is that the second request doesn't crash.


# ── Queue API ────────────────────────────────────────────────────────────────


def test_api_queue_returns_html(user_client):
    resp = user_client.get("/api/queue")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_api_queue_start_returns_html(user_client):
    resp = user_client.post("/api/queue/start")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ── JSON API: categories ──────────────────────────────────────────────────────


def test_api_list_categories_empty(admin_client):
    resp = admin_client.get("/api/categories")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_create_category(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories",
        json={"name": "Movies", "path": str(cat_dir), "description": "Feature films"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Movies"
    assert data["description"] == "Feature films"


def test_api_create_category_invalid_path(admin_client):
    resp = admin_client.post(
        "/api/categories",
        json={"name": "Movies", "path": "/nonexistent/path/that/does/not/exist"},
    )
    assert resp.status_code == 400


def test_api_create_category_duplicate_name(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    admin_client.post("/api/categories", json={"name": "Movies", "path": str(cat_dir)})
    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_api_update_category(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    new_dir = tmp_path / "films"
    new_dir.mkdir()

    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    cat_id = resp.json()["id"]

    resp = admin_client.put(
        f"/api/categories/{cat_id}",
        json={"name": "Films", "path": str(new_dir)},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Films"


def test_api_delete_category(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    cat_id = resp.json()["id"]

    resp = admin_client.delete(f"/api/categories/{cat_id}")
    assert resp.status_code == 200

    resp = admin_client.get("/api/categories")
    assert resp.json() == []


def test_api_delete_category_not_found(admin_client):
    resp = admin_client.delete("/api/categories/9999")
    assert resp.status_code == 404


# ── JSON API: downloads ───────────────────────────────────────────────────────


def test_api_list_downloads_empty(user_client):
    resp = user_client.get("/api/downloads")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_api_create_download(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    cat_id = resp.json()["id"]

    resp = admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=api_test", "category_id": cat_id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["url"] == "https://youtube.com/watch?v=api_test"


def test_api_create_download_invalid_url_scheme(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    cat_id = resp.json()["id"]

    resp = admin_client.post(
        "/api/downloads",
        json={"url": "file:///etc/passwd", "category_id": cat_id},
    )
    assert resp.status_code == 400


def test_api_cancel_download(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()

    resp = admin_client.post(
        "/api/categories", json={"name": "Movies", "path": str(cat_dir)}
    )
    cat_id = resp.json()["id"]

    resp = admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=cancel_me", "category_id": cat_id},
    )
    dl_id = resp.json()["id"]

    resp = admin_client.delete(f"/api/downloads/{dl_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_api_cancel_download_not_found(admin_client):
    resp = admin_client.delete("/api/downloads/9999")
    assert resp.status_code == 404


# ── Resolution picker ─────────────────────────────────────────────────────────


def test_api_resolutions_empty_url(user_client):
    resp = user_client.get("/api/resolutions")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_api_resolutions_invalid_scheme(user_client):
    resp = user_client.get("/api/resolutions?url=file:///etc/passwd")
    assert resp.status_code == 200
    # Should return the empty quality select without making any yt-dlp call


# ── HTMX partials: categories ─────────────────────────────────────────────────


def test_partials_category_list(admin_client):
    resp = admin_client.get("/partials/categories")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_partials_category_row_not_found(admin_client):
    resp = admin_client.get("/partials/categories/9999")
    assert resp.status_code == 404


# ── HTMX mode: categories ─────────────────────────────────────────────────────

HTMX = {"HX-Request": "true"}


def _make_cat(client, path):
    """Create a category and return its id."""
    return client.post("/api/categories", json={"name": "Movies", "path": str(path)}).json()["id"]


def test_htmx_create_category_returns_html_list(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    resp = admin_client.post(
        "/api/categories",
        data={"name": "Movies", "path": str(cat_dir)},
        headers=HTMX,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Movies" in resp.text


def test_htmx_create_category_invalid_path_returns_error_html(admin_client):
    resp = admin_client.post(
        "/api/categories",
        data={"name": "Movies", "path": "/nonexistent/path"},
        headers=HTMX,
    )
    assert resp.status_code == 400
    assert "not exist" in resp.text.lower() or "writable" in resp.text.lower()


def test_htmx_create_category_duplicate_name_returns_error_html(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    admin_client.post("/api/categories", json={"name": "Movies", "path": str(cat_dir)})
    resp = admin_client.post(
        "/api/categories",
        data={"name": "Movies", "path": str(cat_dir)},
        headers=HTMX,
    )
    assert resp.status_code == 400
    assert "already exists" in resp.text.lower()


def test_htmx_update_category_returns_html_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    new_dir = tmp_path / "films"
    new_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)

    resp = admin_client.put(
        f"/api/categories/{cat_id}",
        data={"name": "Films", "path": str(new_dir)},
        headers=HTMX,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Films" in resp.text


def test_htmx_update_category_invalid_path_returns_edit_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)

    resp = admin_client.put(
        f"/api/categories/{cat_id}",
        data={"name": "Movies", "path": "/nonexistent"},
        headers=HTMX,
    )
    assert resp.status_code == 422
    assert "text/html" in resp.headers["content-type"]
    # Should render the edit form with an inline error, not just plain text
    assert "input" in resp.text.lower() or "form" in resp.text.lower()


def test_htmx_delete_category_returns_empty_body(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)

    resp = admin_client.delete(f"/api/categories/{cat_id}", headers=HTMX)
    assert resp.status_code == 200
    assert resp.text == ""


def test_htmx_delete_category_with_active_downloads_returns_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=active", "category_id": cat_id},
    )

    resp = admin_client.delete(f"/api/categories/{cat_id}", headers=HTMX)
    assert resp.status_code == 409
    # Returns the category row (unchanged) with an error annotation
    assert "text/html" in resp.headers["content-type"]
    assert "pending" in resp.text.lower() or "active" in resp.text.lower() or "cancel" in resp.text.lower()


# ── HTMX mode: category partials ─────────────────────────────────────────────


def test_partials_category_edit_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)

    resp = admin_client.get(f"/partials/categories/{cat_id}/edit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Movies" in resp.text


def test_partials_category_edit_row_not_found(admin_client):
    resp = admin_client.get("/partials/categories/9999/edit")
    assert resp.status_code == 404


def test_partials_category_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)

    resp = admin_client.get(f"/partials/categories/{cat_id}")
    assert resp.status_code == 200
    assert "Movies" in resp.text


# ── HTMX mode: queue partials ─────────────────────────────────────────────────


def _make_dl(client, cat_id):
    return client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=q1", "category_id": cat_id},
    ).json()["id"]


def test_partials_queue_row(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = _make_dl(admin_client, cat_id)

    resp = admin_client.get(f"/partials/queue/{dl_id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_partials_queue_row_not_found(admin_client):
    resp = admin_client.get("/partials/queue/9999")
    assert resp.status_code == 404


def test_partials_queue_row_edit(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = _make_dl(admin_client, cat_id)

    resp = admin_client.get(f"/partials/queue/{dl_id}/edit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_partials_queue_row_edit_non_pending_returns_409(admin_client, tmp_path):
    import ytdlfin.db as database_module
    import asyncio

    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = _make_dl(admin_client, cat_id)

    # Force status to done directly in the DB (no shared app.state.db; open our own)
    async def _mark_done():
        async with aiosqlite.connect(str(database_module.DB_PATH)) as conn:
            conn.row_factory = aiosqlite.Row
            await database_module.set_download_done(conn, dl_id, "/done")
    asyncio.run(_mark_done())

    resp = admin_client.get(f"/partials/queue/{dl_id}/edit")
    assert resp.status_code == 409


# ── HTMX mode: inline download category edit (PATCH) ─────────────────────────


def test_patch_download_category(admin_client, tmp_path):
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    tv_dir = tmp_path / "tv"
    tv_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    tv_id = admin_client.post(
        "/api/categories", json={"name": "TV Shows", "path": str(tv_dir)}
    ).json()["id"]
    dl_id = _make_dl(admin_client, cat_id)

    resp = admin_client.patch(
        f"/api/downloads/{dl_id}",
        data={"category_id": str(tv_id)},
        headers=HTMX,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "TV Shows" in resp.text


def test_patch_download_wrong_user_returns_403(user_client, admin_client, tmp_path):
    """A regular user cannot edit another user's download."""
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    # Admin creates the download (owned by admin)
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=admin_dl", "category_id": cat_id},
    ).json()["id"]

    # user_client is a different user — should be denied
    resp = user_client.patch(
        f"/api/downloads/{dl_id}",
        data={"category_id": str(cat_id)},
        headers=HTMX,
    )
    assert resp.status_code == 403


# ── Download cancel edge cases ────────────────────────────────────────────────


def test_cancel_download_non_pending_returns_409(admin_client, tmp_path):
    import ytdlfin.db as database_module
    import asyncio

    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = _make_dl(admin_client, cat_id)

    async def _mark_done():
        async with aiosqlite.connect(str(database_module.DB_PATH)) as conn:
            conn.row_factory = aiosqlite.Row
            await database_module.set_download_done(conn, dl_id, "/done")
    asyncio.run(_mark_done())

    resp = admin_client.delete(f"/api/downloads/{dl_id}")
    assert resp.status_code == 409


def test_cancel_download_wrong_user_returns_403(user_client, admin_client, tmp_path):
    """User cannot cancel another user's download."""
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    dl_id = admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=others", "category_id": cat_id},
    ).json()["id"]

    resp = user_client.delete(f"/api/downloads/{dl_id}")
    assert resp.status_code == 403


# ── Queue start with pending items ────────────────────────────────────────────


def test_api_queue_start_returns_html(admin_client, tmp_path):
    """Queue start should return the queue partial HTML (200 with HTML content-type)."""
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir()
    cat_id = _make_cat(admin_client, cat_dir)
    admin_client.post(
        "/api/downloads",
        json={"url": "https://youtube.com/watch?v=q1", "category_id": cat_id},
    )

    resp = admin_client.post("/api/queue/start")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
