"""
Shared test configuration.

This module is loaded by pytest before any test imports, so the env var block at
the top runs before ytdlfin modules (which read env vars at import time) are imported.
"""

import os
import tempfile

# ── Env vars required by ytdlfin at import time ───────────────────────────────
# These must be set BEFORE any `from ytdlfin import ...` happens anywhere in the
# test suite, because the modules read them at module-load time.

# A real temp directory for STAGING_DIR (shared across the session — individual
# tests each get their own DATA_DIR and DB via the client fixtures below).
_SESSION_TMPDIR = tempfile.mkdtemp(prefix="ytdlfin_test_")
_SESSION_STAGING = os.path.join(_SESSION_TMPDIR, "staging")
os.makedirs(_SESSION_STAGING, exist_ok=True)

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production-xxxxx")
os.environ.setdefault("OIDC_ISSUER_URL", "https://id.example.com")
os.environ.setdefault("OIDC_CLIENT_ID", "test-client-id")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("OIDC_REDIRECT_URI", "https://app.example.com/auth/callback")
os.environ.setdefault("ADMIN_GROUP", "admins")
os.environ.setdefault("USER_GROUP", "users")
os.environ.setdefault("MEDIA_DIRECTORIES", "")   # no containment check in tests
os.environ.setdefault("DATA_DIR", _SESSION_TMPDIR)
os.environ.setdefault("STAGING_DIR", _SESSION_STAGING)

import pytest  # noqa: E402 — must come after env setup

# ── Test user constants ───────────────────────────────────────────────────────

TEST_USER = {
    "sub": "user-sub",
    "email": "user@example.com",
    "name": "Test User",
    "is_admin": False,
}

ADMIN_USER = {
    "sub": "admin-sub",
    "email": "admin@example.com",
    "name": "Admin User",
    "is_admin": True,
}


# ── In-memory DB fixture ──────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Fresh in-memory aiosqlite connection with schema applied."""
    import asyncio
    import aiosqlite
    from ytdlfin import db as database

    async def _setup():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await database.init_schema(conn)
        return conn

    conn = asyncio.run(_setup())
    yield conn
    asyncio.run(conn.close())


# ── HTTP test client helpers ──────────────────────────────────────────────────


def _make_client(tmp_path, monkeypatch, user: dict):
    """
    Build a TestClient for one test:
    - Gives each test a fresh DB file in tmp_path (so tests don't share state).
    - Patches DATA_DIR so the lifespan's mkdir succeeds.
    - Overrides auth dependencies so no OIDC round-trip is needed.

    Both get_current_user and require_admin are overridden so that the `user`
    dict is returned (or NotAdmin is raised for non-admins) without touching
    the session. This is necessary because require_admin calls get_current_user
    directly rather than through FastAPI's DI system.
    """
    import ytdlfin.db as db_module
    from fastapi import Request
    from starlette.testclient import TestClient
    from ytdlfin.auth import NotAdmin, get_current_user, require_admin
    from ytdlfin.main import create_app

    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "ytdlfin.db")
    monkeypatch.setattr(db_module, "_categories_cache", None)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user

    def _require_admin_override():
        if not user.get("is_admin"):
            raise NotAdmin()
        return user

    app.dependency_overrides[require_admin] = _require_admin_override

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def user_client(tmp_path, monkeypatch):
    """TestClient authenticated as a regular (non-admin) user."""
    with _make_client(tmp_path, monkeypatch, TEST_USER) as client:
        yield client


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """TestClient authenticated as an admin user."""
    with _make_client(tmp_path, monkeypatch, ADMIN_USER) as client:
        yield client
