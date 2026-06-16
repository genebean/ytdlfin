"""
Worker orchestration tests.

Mocked at two seams:
  - ytdlfin.worker.extract_info  (the sync fn passed to run_in_executor)
  - ytdlfin.worker.download_async (the async download wrapper)

Everything else — DB state transitions, staging cleanup, AlreadyInArchive handling —
is exercised against a real in-memory SQLite connection.
"""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytdlfin import db as database
from ytdlfin.worker import download_worker, process_download, recover_staging
from ytdlfin.ytdlp import AlreadyInArchive

# ── Shared fixtures and helpers ───────────────────────────────────────────────

FAKE_INFO = {
    "title": "Test Video Title",
    "id": "vid123",
    "description": "A description.",
    "upload_date": "20240101",
    "channel": "Test Channel",
    "extractor_key": "Youtube",
    "categories": [],
    "tags": [],
}


async def _make_pending(db, tmp_path) -> dict:
    """Create a category + pending download record and return the download row."""
    cat_dir = tmp_path / "movies"
    cat_dir.mkdir(exist_ok=True)
    cat = await database.create_category(db, "Movies", str(cat_dir), None)
    return await database.create_download(
        db,
        url="https://youtube.com/watch?v=vid123",
        category=cat,
        quality="1080p",
        custom_title=None,
        requested_by_email="user@example.com",
        requested_by_name="User",
    )


# ── recover_staging ───────────────────────────────────────────────────────────


def test_recover_staging_removes_subdirs(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "1").mkdir()
    (staging / "2").mkdir()
    (staging / "2" / "deep").mkdir()

    async def _run():
        with patch("ytdlfin.worker.STAGING_DIR", staging):
            await recover_staging()

    asyncio.run(_run())

    assert not (staging / "1").exists()
    assert not (staging / "2").exists()
    assert staging.exists()  # the staging dir itself is preserved


def test_recover_staging_ignores_files(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "leftover.txt").write_text("stale")

    async def _run():
        with patch("ytdlfin.worker.STAGING_DIR", staging):
            await recover_staging()

    asyncio.run(_run())

    assert (staging / "leftover.txt").exists()  # files are not touched


def test_recover_staging_no_staging_dir(tmp_path):
    missing = tmp_path / "no_such_staging"

    async def _run():
        with patch("ytdlfin.worker.STAGING_DIR", missing):
            await recover_staging()  # must not raise

    asyncio.run(_run())


# ── process_download: success path ────────────────────────────────────────────


def test_process_download_success(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]

        # Fake staging dir that download_async "creates"
        staging = tmp_path / "staging" / str(dl_id)
        staging.mkdir(parents=True)

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch("ytdlfin.worker.download_async", new=AsyncMock(return_value=staging)),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "done"
        assert dl["title"] == "Test Video Title"
        assert dl["video_id"] == "vid123"
        assert dl["final_path"] is not None
        assert "Test Video Title [vid123]" in dl["final_path"]

    asyncio.run(_run())


def test_process_download_custom_title_overrides_yt_title(db, tmp_path):
    async def _run():
        cat_dir = tmp_path / "movies"
        cat_dir.mkdir()
        cat = await database.create_category(db, "Movies", str(cat_dir), None)
        record = await database.create_download(
            db,
            url="https://youtube.com/watch?v=vid123",
            category=cat,
            quality="1080p",
            custom_title="My Custom Title",
            requested_by_email="user@example.com",
            requested_by_name="User",
        )
        dl_id = record["id"]
        staging = tmp_path / "staging" / str(dl_id)
        staging.mkdir(parents=True)

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch("ytdlfin.worker.download_async", new=AsyncMock(return_value=staging)),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "done"
        assert dl["title"] == "My Custom Title"
        assert "My Custom Title [vid123]" in dl["final_path"]

    asyncio.run(_run())


def test_process_download_staging_cleaned_up_on_success(db, tmp_path):
    """After a successful move, the staging dir should no longer exist."""
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]
        staging = tmp_path / "staging" / str(dl_id)
        staging.mkdir(parents=True)

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch("ytdlfin.worker.download_async", new=AsyncMock(return_value=staging)),
        ):
            await process_download(db, record)

        # shutil.move renamed it to the final path; staging path is gone
        assert not staging.exists()

    asyncio.run(_run())


# ── process_download: AlreadyInArchive ────────────────────────────────────────


def test_process_download_already_in_archive(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch(
                "ytdlfin.worker.download_async",
                new=AsyncMock(side_effect=AlreadyInArchive()),
            ),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "skipped"
        assert dl["skipped_reason"] == "already in archive"

    asyncio.run(_run())


# ── process_download: extract_info failure ────────────────────────────────────


def test_process_download_extract_info_fails(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]

        with (
            patch("ytdlfin.worker.extract_info", side_effect=RuntimeError("network error")),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "failed"
        # Error message should be the sanitized user-facing string, not the raw exception
        assert "check server logs" in dl["error_msg"]

    asyncio.run(_run())


# ── process_download: download_async failure ──────────────────────────────────


def test_process_download_download_fails(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch(
                "ytdlfin.worker.download_async",
                new=AsyncMock(side_effect=Exception("yt-dlp exploded")),
            ),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "failed"

    asyncio.run(_run())


# ── process_download: move failure → staging cleanup ─────────────────────────


def test_process_download_staging_cleaned_up_on_move_failure(db, tmp_path):
    """
    If shutil.move fails, staging_dir was set so the finally block must clean it up.
    """
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]
        staging = tmp_path / "staging" / str(dl_id)
        staging.mkdir(parents=True)
        (staging / "video.mp4").write_text("fake")

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch("ytdlfin.worker.download_async", new=AsyncMock(return_value=staging)),
            patch("ytdlfin.worker.shutil.move", side_effect=OSError("disk full")),
        ):
            await process_download(db, record)

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "failed"
        # Staging dir must be cleaned up even though the move failed
        assert not staging.exists()

    asyncio.run(_run())


# ── download_worker: queue dispatch logic ─────────────────────────────────────


def test_download_worker_skips_not_found(db):
    async def _run():
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(download_worker(db, queue))
        await queue.put(9999)  # non-existent ID
        await queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    # Just verifying it doesn't raise or hang


def test_download_worker_skips_non_pending(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        await database.set_download_done(db, record["id"], "/done/path")

        queue: asyncio.Queue = asyncio.Queue()
        with patch("ytdlfin.worker.process_download") as mock_process:
            task = asyncio.create_task(download_worker(db, queue))
            await queue.put(record["id"])
            await queue.join()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            mock_process.assert_not_called()

    asyncio.run(_run())


def test_download_worker_processes_pending(db, tmp_path):
    async def _run():
        record = await _make_pending(db, tmp_path)
        dl_id = record["id"]
        staging = tmp_path / "staging" / str(dl_id)
        staging.mkdir(parents=True)

        queue: asyncio.Queue = asyncio.Queue()

        with (
            patch("ytdlfin.worker.extract_info", return_value=FAKE_INFO),
            patch("ytdlfin.worker.download_async", new=AsyncMock(return_value=staging)),
        ):
            task = asyncio.create_task(download_worker(db, queue))
            await queue.put(dl_id)
            await queue.join()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        dl = await database.get_download(db, dl_id)
        assert dl["status"] == "done"

    asyncio.run(_run())
