"""Integration tests for the database layer using an in-memory SQLite connection."""

import asyncio
import pytest


def run(coro):
    """Run an async coroutine synchronously inside a test."""
    return asyncio.run(coro)


# ── Schema ────────────────────────────────────────────────────────────────────


def test_init_schema_is_idempotent(db):
    from ytdlfin import db as database

    # Running init_schema a second time must not raise or corrupt data.
    async def _run():
        await database.create_category(db, "Movies", "/media/movies", None)
        await database.init_schema(db)  # second call
        cats = await database.list_categories(db)
        assert len(cats) == 1

    asyncio.run(_run())


# ── Categories ────────────────────────────────────────────────────────────────


def test_create_and_get_category(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", "Feature films")
        assert cat["name"] == "Movies"
        assert cat["path"] == "/media/movies"
        assert cat["description"] == "Feature films"
        assert cat["id"] is not None

        fetched = await database.get_category(db, cat["id"])
        assert fetched == cat

    asyncio.run(_run())


def test_get_category_not_found(db):
    from ytdlfin import db as database

    async def _run():
        result = await database.get_category(db, 9999)
        assert result is None

    asyncio.run(_run())


def test_list_categories_sorted_by_name(db):
    from ytdlfin import db as database

    async def _run():
        await database.create_category(db, "TV Shows", "/media/tv", None)
        await database.create_category(db, "Movies", "/media/movies", None)
        await database.create_category(db, "Anime", "/media/anime", None)
        cats = await database.list_categories(db)
        names = [c["name"] for c in cats]
        assert names == sorted(names)

    asyncio.run(_run())


def test_update_category(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        updated = await database.update_category(
            db, cat["id"], "Films", "/media/films", "Updated desc"
        )
        assert updated["name"] == "Films"
        assert updated["path"] == "/media/films"
        assert updated["description"] == "Updated desc"

    asyncio.run(_run())


def test_update_category_not_found(db):
    from ytdlfin import db as database

    async def _run():
        result = await database.update_category(db, 9999, "Ghost", "/ghost", None)
        assert result is None

    asyncio.run(_run())


def test_delete_category(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        deleted = await database.delete_category(db, cat["id"])
        assert deleted is True
        assert await database.get_category(db, cat["id"]) is None

    asyncio.run(_run())


def test_delete_category_not_found(db):
    from ytdlfin import db as database

    async def _run():
        assert await database.delete_category(db, 9999) is False

    asyncio.run(_run())


def test_category_unique_name_constraint(db):
    from ytdlfin import db as database

    async def _run():
        await database.create_category(db, "Movies", "/media/movies", None)
        with pytest.raises(Exception, match="UNIQUE constraint"):
            await database.create_category(db, "Movies", "/other/path", None)

    asyncio.run(_run())


# ── Downloads ─────────────────────────────────────────────────────────────────


async def _make_category(db):
    from ytdlfin import db as database

    return await database.create_category(db, "Movies", "/media/movies", None)


async def _make_download(db, url="https://youtube.com/watch?v=test", quality="1080p"):
    from ytdlfin import db as database

    cat = await _make_category(db)
    return await database.create_download(
        db,
        url=url,
        category=cat,
        quality=quality,
        custom_title=None,
        requested_by_email="user@example.com",
        requested_by_name="Test User",
    )


def test_create_download(db):
    async def _run():
        dl = await _make_download(db)
        assert dl["status"] == "pending"
        assert dl["url"] == "https://youtube.com/watch?v=test"
        assert dl["quality"] == "1080p"
        assert dl["requested_by_email"] == "user@example.com"

    asyncio.run(_run())


def test_get_download(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        fetched = await database.get_download(db, dl["id"])
        assert fetched["id"] == dl["id"]
        assert fetched["url"] == dl["url"]

    asyncio.run(_run())


def test_get_download_not_found(db):
    from ytdlfin import db as database

    async def _run():
        assert await database.get_download(db, 9999) is None

    asyncio.run(_run())


def test_url_is_active_pending(db):
    from ytdlfin import db as database

    async def _run():
        url = "https://youtube.com/watch?v=active"
        await _make_download(db, url=url)
        assert await database.url_is_active(db, url) is True

    asyncio.run(_run())


def test_url_is_active_false_for_done(db):
    from ytdlfin import db as database

    async def _run():
        url = "https://youtube.com/watch?v=done"
        dl = await _make_download(db, url=url)
        await database.set_download_done(db, dl["id"], "/media/movies/video")
        assert await database.url_is_active(db, url) is False

    asyncio.run(_run())


def test_url_is_active_false_for_unknown_url(db):
    from ytdlfin import db as database

    async def _run():
        assert await database.url_is_active(db, "https://youtube.com/watch?v=never") is False

    asyncio.run(_run())


def test_download_state_transitions(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        assert dl["status"] == "pending"

        await database.set_download_downloading(db, dl["id"])
        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "downloading"
        assert dl["started_at"] is not None

        await database.set_download_info(db, dl["id"], "My Video", "My Video (yt)", "abc123")
        dl = await database.get_download(db, dl["id"])
        assert dl["title"] == "My Video"
        assert dl["video_id"] == "abc123"

        await database.set_download_done(db, dl["id"], "/media/movies/My Video [abc123]")
        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "done"
        assert dl["final_path"] == "/media/movies/My Video [abc123]"
        assert dl["completed_at"] is not None

    asyncio.run(_run())


def test_download_failed_state(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        await database.set_download_failed(db, dl["id"], "Network error")
        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "failed"
        assert dl["error_msg"] == "Network error"

    asyncio.run(_run())


def test_download_skipped_state(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        await database.set_download_skipped(db, dl["id"], "already in archive")
        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "skipped"
        assert dl["skipped_reason"] == "already in archive"

    asyncio.run(_run())


def test_reset_interrupted_downloads(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        await database.set_download_downloading(db, dl["id"])

        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "downloading"

        await database.reset_interrupted_downloads(db)

        dl = await database.get_download(db, dl["id"])
        assert dl["status"] == "pending"
        assert dl["started_at"] is None

    asyncio.run(_run())


def test_cancel_download(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        result = await database.cancel_download(db, dl["id"])
        assert result is True
        assert await database.get_download(db, dl["id"]) is None

    asyncio.run(_run())


def test_cancel_download_not_pending(db):
    from ytdlfin import db as database

    async def _run():
        dl = await _make_download(db)
        await database.set_download_downloading(db, dl["id"])
        # Should return False — only pending downloads can be cancelled
        result = await database.cancel_download(db, dl["id"])
        assert result is False

    asyncio.run(_run())


def test_list_downloads_pagination(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        for i in range(5):
            await database.create_download(
                db,
                url=f"https://example.com/{i}",
                category=cat,
                quality="1080p",
                custom_title=None,
                requested_by_email="user@example.com",
                requested_by_name="User",
            )

        page1 = await database.list_downloads(db, page=1, per_page=3, is_admin=True)
        assert len(page1["items"]) == 3
        assert page1["total"] == 5
        assert page1["pages"] == 2

        page2 = await database.list_downloads(db, page=2, per_page=3, is_admin=True)
        assert len(page2["items"]) == 2

    asyncio.run(_run())


def test_list_downloads_user_sees_only_own(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        for email in ("alice@example.com", "bob@example.com", "bob@example.com"):
            await database.create_download(
                db,
                url=f"https://example.com/{email}",
                category=cat,
                quality="1080p",
                custom_title=None,
                requested_by_email=email,
                requested_by_name=email.split("@")[0],
            )

        bob_result = await database.list_downloads(
            db, is_admin=False, user_email="bob@example.com"
        )
        assert bob_result["total"] == 2
        assert all(d["requested_by_email"] == "bob@example.com" for d in bob_result["items"])

        admin_result = await database.list_downloads(db, is_admin=True)
        assert admin_result["total"] == 3

    asyncio.run(_run())


def test_list_downloads_status_filter(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        dl1 = await database.create_download(
            db, url="https://a.com/1", category=cat, quality="1080p",
            custom_title=None, requested_by_email="u@e.com", requested_by_name="U",
        )
        dl2 = await database.create_download(
            db, url="https://a.com/2", category=cat, quality="1080p",
            custom_title=None, requested_by_email="u@e.com", requested_by_name="U",
        )
        await database.set_download_done(db, dl2["id"], "/done")

        pending = await database.list_downloads(db, status="pending", is_admin=True)
        assert pending["total"] == 1
        assert pending["items"][0]["id"] == dl1["id"]

        done = await database.list_downloads(db, status="done", is_admin=True)
        assert done["total"] == 1

    asyncio.run(_run())


def test_get_queue_returns_active_only(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)

        dl_pending = await database.create_download(
            db, url="https://a.com/1", category=cat, quality="1080p",
            custom_title=None, requested_by_email="u@e.com", requested_by_name="U",
        )
        dl_done = await database.create_download(
            db, url="https://a.com/2", category=cat, quality="1080p",
            custom_title=None, requested_by_email="u@e.com", requested_by_name="U",
        )
        await database.set_download_done(db, dl_done["id"], "/done")

        queue = await database.get_queue(db)
        assert len(queue) == 1
        assert queue[0]["id"] == dl_pending["id"]

    asyncio.run(_run())


def test_category_has_active_downloads(db):
    from ytdlfin import db as database

    async def _run():
        cat = await database.create_category(db, "Movies", "/media/movies", None)
        dl = await database.create_download(
            db, url="https://a.com/1", category=cat, quality="1080p",
            custom_title=None, requested_by_email="u@e.com", requested_by_name="U",
        )

        assert await database.category_has_active_downloads(db, cat["id"]) is True

        await database.set_download_done(db, dl["id"], "/done")
        assert await database.category_has_active_downloads(db, cat["id"]) is False

    asyncio.run(_run())
