"""Generates Jellyfin-compatible movie NFO XML from a yt-dlp info dict."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path


def _sub(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text or ""
    return el


def generate_nfo(info: dict, display_title: str) -> str:
    """Return NFO XML string for the given yt-dlp info dict."""
    movie = ET.Element("movie")

    _sub(movie, "title", display_title)
    # originaltitle always holds the raw yt-dlp title, even when a custom title was set
    _sub(movie, "originaltitle", info.get("title", display_title))

    description = info.get("description") or ""
    _sub(movie, "plot", description[:4000])

    upload_date = info.get("upload_date", "")  # YYYYMMDD
    if upload_date and len(upload_date) >= 8:
        _sub(movie, "year", upload_date[:4])
        _sub(movie, "premiered", f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}")

    _sub(movie, "dateadded", date.today().isoformat())

    channel = info.get("channel") or info.get("uploader", "")
    _sub(movie, "studio", channel)

    video_id = info.get("id", "")
    extractor = info.get("extractor_key", "generic").lower()
    uid = ET.SubElement(movie, "uniqueid", type=extractor, default="true")
    uid.text = video_id
    _sub(movie, "id", video_id)

    _sub(movie, "source", "WEB-DL")

    for genre in info.get("categories", []):
        _sub(movie, "genre", genre)

    for tag in (info.get("tags") or [])[:20]:
        _sub(movie, "tag", tag)

    ET.indent(movie, space="  ")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(
        movie, encoding="unicode"
    )


def write_nfo(info: dict, display_title: str, dest: Path) -> None:
    dest.write_text(generate_nfo(info, display_title), encoding="utf-8")
