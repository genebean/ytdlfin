"""yt-dlp integration — extract_info, download orchestration, NFO generation."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import yt_dlp
import yt_dlp.utils

from .nfo import write_nfo

logger = logging.getLogger(__name__)

STAGING_DIR = Path(os.environ.get("STAGING_DIR", os.environ.get("DATA_DIR", "/var/lib/ytdlfin") + "/staging"))

# Format strings per the spec. MP4 + M4A preferred so ffmpeg can mux without re-encoding.
FORMAT_1080P = (
    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
    "/bestvideo[height<=1080]+bestaudio"
    "/best[height<=1080]/best"
)
FORMAT_BEST = (
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
    "/bestvideo+bestaudio/best"
)


class AlreadyInArchive(Exception):
    pass


def extract_info(url: str) -> dict:
    """Run yt-dlp extract_info synchronously (call via run_in_executor)."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        return ydl.extract_info(url, download=False)


def _make_progress_hook(on_progress):
    """Return a yt-dlp progress hook that calls on_progress(status, d)."""
    def hook(d: dict):
        on_progress(d["status"], d)
    return hook


def run_download(
    url: str,
    category_path: str,
    folder_name: str,
    download_id: int,
    quality: str,
    info: dict,
    display_title: str,
    on_progress=None,
) -> Path:
    """
    Execute the full yt-dlp download into a staging directory, write the NFO,
    verify required files, and return the staging folder path.
    Raises AlreadyInArchive if the URL is already recorded in the archive file.
    Raises yt_dlp.utils.DownloadError on other yt-dlp failures.
    """
    staging_dir = STAGING_DIR / str(download_id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    fmt = FORMAT_BEST if quality == "best" else FORMAT_1080P
    archive_path = str(Path(category_path) / ".ytdl-archive.txt")

    ydl_opts: dict = {
        "format": fmt,
        "outtmpl": {
            "default": str(staging_dir / f"{folder_name}.%(ext)s"),
            "thumbnail": str(staging_dir / f"{folder_name}-poster"),
        },
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
        "download_archive": archive_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    if on_progress:
        ydl_opts["progress_hooks"] = [_make_progress_hook(on_progress)]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "has already been recorded in the archive" in msg:
            raise AlreadyInArchive() from exc
        raise

    # Write NFO after download completes; info dict was populated by extract_info earlier.
    write_nfo(info, display_title, staging_dir / f"{folder_name}.nfo")

    mp4 = staging_dir / f"{folder_name}.mp4"
    nfo = staging_dir / f"{folder_name}.nfo"
    if not mp4.exists():
        raise FileNotFoundError(f"Expected MP4 not found in staging: {mp4}")
    if not nfo.exists():
        raise FileNotFoundError(f"Expected NFO not found in staging: {nfo}")
    poster = staging_dir / f"{folder_name}-poster.jpg"
    if not poster.exists():
        logger.warning("Poster not found in staging (non-fatal): %s", poster)

    return staging_dir


async def download_async(
    url: str,
    category_path: str,
    folder_name: str,
    download_id: int,
    quality: str,
    info: dict,
    display_title: str,
    on_progress=None,
) -> Path:
    """Async wrapper — runs the blocking yt-dlp call in the default executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: run_download(
            url, category_path, folder_name, download_id, quality, info, display_title, on_progress
        ),
    )
