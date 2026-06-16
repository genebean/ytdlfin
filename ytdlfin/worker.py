"""Asyncio download worker — processes the queue serially, one download at a time."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import aiosqlite
import yt_dlp.utils

from . import db as database
from .ytdlp import AlreadyInArchive, download_async, extract_info, STAGING_DIR

logger = logging.getLogger(__name__)


async def recover_staging() -> None:
    """
    Delete any staging subdirectories left over from a previous crash.
    Called at startup before the worker task is launched.
    """
    if STAGING_DIR.exists():
        for child in STAGING_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                logger.info("Cleaned up leftover staging directory: %s", child)


async def process_download(conn: aiosqlite.Connection, record: dict) -> None:
    """Execute a single download end-to-end, updating the DB at each step."""
    download_id = record["id"]
    url = record["url"]
    quality = record["quality"]
    custom_title = record["custom_title"]
    category_path = record["category_path"]

    staging_dir = None
    try:
        await database.set_download_downloading(conn, download_id)

        # Step 1: Extract metadata without downloading.
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: extract_info(url))

        yt_title = info["title"]
        display_title = custom_title or yt_title
        video_id = info["id"]

        sanitized = yt_dlp.utils.sanitize_filename(display_title)
        folder_name = f"{sanitized} [{video_id}]"

        await database.set_download_info(conn, download_id, display_title, yt_title, video_id)

        # Step 2: Download into staging.
        staging_dir = await download_async(
            url=url,
            category_path=category_path,
            folder_name=folder_name,
            download_id=download_id,
            quality=quality,
            info=info,
            display_title=display_title,
        )

        # Step 3: Atomic move from staging to final destination.
        final_path = Path(category_path) / folder_name
        shutil.move(str(staging_dir), str(final_path))
        staging_dir = None  # Move succeeded; no cleanup needed.

        await database.set_download_done(conn, download_id, str(final_path))
        logger.info("Download %d complete: %s", download_id, final_path)

    except AlreadyInArchive:
        logger.info("Download %d skipped (already in archive): %s", download_id, url)
        await database.set_download_skipped(conn, download_id, "already in archive")

    except Exception:
        logger.exception("Download %d failed: %s", download_id, url)
        await database.set_download_failed(
            conn, download_id, "Download failed — check server logs for details."
        )

    finally:
        # Always clean up staging if it still exists (i.e., move failed or error occurred).
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


async def download_worker(conn: aiosqlite.Connection, queue: asyncio.Queue) -> None:
    """
    Long-running asyncio task. Waits for download IDs on `queue` and processes
    them one at a time. Designed to run for the lifetime of the application.
    """
    logger.info("Download worker started")
    while True:
        download_id = await queue.get()
        try:
            record = await database.get_download(conn, download_id)
            if record is None:
                logger.warning("Download %d not found in DB; skipping", download_id)
                continue
            if record["status"] != "pending":
                logger.debug(
                    "Download %d is no longer pending (status=%s); skipping",
                    download_id,
                    record["status"],
                )
                continue
            await process_download(conn, record)
        except Exception:
            logger.exception("Unexpected error in worker loop for download %d", download_id)
        finally:
            queue.task_done()
