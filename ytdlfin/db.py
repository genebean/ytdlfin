"""Database layer — aiosqlite helpers, schema init, and all CRUD operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiosqlite

# Resolve data directory from environment; default matches NixOS module default.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/lib/ytdlfin"))
DB_PATH = DATA_DIR / "ytdlfin.db"

_CREATE_CATEGORIES = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT NOT NULL,
    category_id         INTEGER REFERENCES categories(id),
    category_name       TEXT NOT NULL,
    category_path       TEXT NOT NULL,
    custom_title        TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    quality             TEXT NOT NULL DEFAULT '1080p',
    requested_by_email  TEXT NOT NULL,
    requested_by_name   TEXT NOT NULL,
    requested_at        TEXT NOT NULL DEFAULT (datetime('now')),
    started_at          TEXT,
    completed_at        TEXT,
    title               TEXT,
    yt_title            TEXT,
    video_id            TEXT,
    final_path          TEXT,
    error_msg           TEXT,
    skipped_reason      TEXT
);
"""


def _row(row: aiosqlite.Row | None) -> dict | None:
    """Convert an aiosqlite.Row to a plain dict, or return None."""
    return dict(row) if row is not None else None


async def open_db() -> aiosqlite.Connection:
    """Open and configure the SQLite connection. Caller owns the connection."""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    return db


async def init_schema(db: aiosqlite.Connection) -> None:
    """Create tables on first run. Safe to call repeatedly."""
    # Explicit DELETE mode (the default) keeps backup simple: no -wal/-shm sidecar files.
    await db.execute("PRAGMA journal_mode=DELETE")
    await db.execute(_CREATE_CATEGORIES)
    await db.execute(_CREATE_DOWNLOADS)
    await db.commit()


# ── Categories ──────────────────────────────────────────────────────────────


async def list_categories(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM categories ORDER BY name") as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_category(db: aiosqlite.Connection, category_id: int) -> dict | None:
    async with db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)) as cur:
        return _row(await cur.fetchone())


async def create_category(
    db: aiosqlite.Connection,
    name: str,
    path: str,
    description: str | None,
) -> dict:
    async with db.execute(
        "INSERT INTO categories (name, path, description) VALUES (?, ?, ?) RETURNING *",
        (name, path, description),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return dict(row)


async def update_category(
    db: aiosqlite.Connection,
    category_id: int,
    name: str,
    path: str,
    description: str | None,
) -> dict | None:
    async with db.execute(
        """UPDATE categories SET name=?, path=?, description=?
           WHERE id=? RETURNING *""",
        (name, path, description, category_id),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return _row(row)


async def delete_category(db: aiosqlite.Connection, category_id: int) -> bool:
    """Delete a category. Returns False if not found."""
    async with db.execute(
        "DELETE FROM categories WHERE id=? RETURNING id", (category_id,)
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return row is not None


async def category_has_active_downloads(
    db: aiosqlite.Connection, category_id: int
) -> bool:
    async with db.execute(
        "SELECT 1 FROM downloads WHERE category_id=? AND status IN ('pending','downloading') LIMIT 1",
        (category_id,),
    ) as cur:
        return await cur.fetchone() is not None


# ── Downloads ────────────────────────────────────────────────────────────────


async def url_is_active(db: aiosqlite.Connection, url: str) -> bool:
    """Return True if the URL is already pending or downloading."""
    async with db.execute(
        "SELECT 1 FROM downloads WHERE url=? AND status IN ('pending','downloading') LIMIT 1",
        (url,),
    ) as cur:
        return await cur.fetchone() is not None


async def create_download(
    db: aiosqlite.Connection,
    url: str,
    category: dict,
    quality: str,
    custom_title: str | None,
    requested_by_email: str,
    requested_by_name: str,
) -> dict:
    async with db.execute(
        """INSERT INTO downloads
           (url, category_id, category_name, category_path,
            custom_title, quality, requested_by_email, requested_by_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
        (
            url,
            category["id"],
            category["name"],
            category["path"],
            custom_title or None,
            quality,
            requested_by_email,
            requested_by_name,
        ),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return dict(row)


async def get_download(db: aiosqlite.Connection, download_id: int) -> dict | None:
    async with db.execute("SELECT * FROM downloads WHERE id=?", (download_id,)) as cur:
        return _row(await cur.fetchone())


async def list_downloads(
    db: aiosqlite.Connection,
    page: int = 1,
    per_page: int = 20,
    status: str | None = None,
    user_email: str | None = None,
    is_admin: bool = False,
) -> dict:
    """Return paginated downloads. Admins see all; users see only their own."""
    conditions = []
    params: list[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(status)

    if not is_admin and user_email:
        conditions.append("requested_by_email = ?")
        params.append(user_email)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * per_page

    async with db.execute(
        f"SELECT COUNT(*) FROM downloads {where}", params
    ) as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"SELECT * FROM downloads {where} ORDER BY requested_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ) as cur:
        rows = await cur.fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


async def get_queue(db: aiosqlite.Connection) -> list[dict]:
    """Return all pending and downloading items in queue order."""
    async with db.execute(
        "SELECT * FROM downloads WHERE status IN ('pending','downloading') ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_pending_ids(db: aiosqlite.Connection) -> list[int]:
    """Return IDs of all pending downloads in submission order (for startup re-queue)."""
    async with db.execute(
        "SELECT id FROM downloads WHERE status='pending' ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def set_download_downloading(
    db: aiosqlite.Connection, download_id: int
) -> None:
    await db.execute(
        "UPDATE downloads SET status='downloading', started_at=datetime('now') WHERE id=?",
        (download_id,),
    )
    await db.commit()


async def set_download_info(
    db: aiosqlite.Connection,
    download_id: int,
    title: str,
    yt_title: str,
    video_id: str,
) -> None:
    await db.execute(
        "UPDATE downloads SET title=?, yt_title=?, video_id=? WHERE id=?",
        (title, yt_title, video_id, download_id),
    )
    await db.commit()


async def set_download_done(
    db: aiosqlite.Connection, download_id: int, final_path: str
) -> None:
    await db.execute(
        """UPDATE downloads SET status='done', completed_at=datetime('now'),
           final_path=? WHERE id=?""",
        (final_path, download_id),
    )
    await db.commit()


async def set_download_failed(
    db: aiosqlite.Connection, download_id: int, error_msg: str
) -> None:
    await db.execute(
        "UPDATE downloads SET status='failed', completed_at=datetime('now'), error_msg=? WHERE id=?",
        (error_msg, download_id),
    )
    await db.commit()


async def set_download_skipped(
    db: aiosqlite.Connection, download_id: int, reason: str
) -> None:
    await db.execute(
        "UPDATE downloads SET status='skipped', completed_at=datetime('now'), skipped_reason=? WHERE id=?",
        (reason, download_id),
    )
    await db.commit()


async def reset_interrupted_downloads(db: aiosqlite.Connection) -> None:
    """Reset any downloads left in 'downloading' state by a previous crash."""
    await db.execute(
        "UPDATE downloads SET status='pending', started_at=NULL WHERE status='downloading'"
    )
    await db.commit()


async def update_download_category(
    db: aiosqlite.Connection, download_id: int, category: dict
) -> dict | None:
    """Change the category of a pending download. Returns None if not found or not pending."""
    async with db.execute(
        """UPDATE downloads SET category_id=?, category_name=?, category_path=?
           WHERE id=? AND status='pending' RETURNING *""",
        (category["id"], category["name"], category["path"], download_id),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return _row(row)


async def cancel_download(db: aiosqlite.Connection, download_id: int) -> bool:
    """Delete a pending download record. Returns False if not found or not pending."""
    async with db.execute(
        "DELETE FROM downloads WHERE id=? AND status='pending' RETURNING id",
        (download_id,),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return row is not None
