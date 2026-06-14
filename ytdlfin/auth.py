"""OIDC authentication via PocketID — routes, session helpers, and FastAPI dependencies."""

from __future__ import annotations

import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse

# ── Configuration ─────────────────────────────────────────────────────────────

OIDC_ISSUER_URL = os.environ.get("OIDC_ISSUER_URL", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")

# PocketID group names that control access. Members of ADMIN_GROUP get full
# admin privileges; members of USER_GROUP get regular user access.
# If a user belongs to neither group they are denied access even if their
# PocketID login succeeds. Fail closed: empty strings deny everyone.
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "")
USER_GROUP = os.environ.get("USER_GROUP", "")

# ── OAuth client ──────────────────────────────────────────────────────────────

oauth = OAuth()
oauth.register(
    name="pocketid",
    # authlib fetches /.well-known/openid-configuration automatically.
    server_metadata_url=f"{OIDC_ISSUER_URL}/.well-known/openid-configuration",
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    # "groups" scope makes PocketID include group membership in the userinfo response.
    client_kwargs={"scope": "openid profile email groups"},
)

# ── Custom exceptions (caught by exception handlers in main.py) ───────────────


class NotAuthenticated(Exception):
    pass


class NotAdmin(Exception):
    pass


# ── Session helpers ───────────────────────────────────────────────────────────


def get_session_user(request: Request) -> dict | None:
    """Return the user dict from the session, or None if not logged in."""
    return request.session.get("user")


def set_session_user(request: Request, userinfo: dict) -> bool:
    """
    Store user info in the session after a successful OIDC callback.

    Returns True if the user belongs to at least one authorized group,
    False if they should be denied access. The session is NOT written on
    False so the caller can clear it and redirect to /auth/denied.
    """
    email = (userinfo.get("email") or "").lower()
    # PocketID returns group membership as a list of group names under "groups".
    groups: set[str] = set(userinfo.get("groups") or [])

    is_admin = bool(ADMIN_GROUP) and ADMIN_GROUP in groups
    is_user = bool(USER_GROUP) and USER_GROUP in groups

    if not (is_admin or is_user):
        return False

    request.session["user"] = {
        "sub": userinfo.get("sub", ""),
        "email": email,
        "name": userinfo.get("name") or email,
        "is_admin": is_admin,
    }
    return True


def flash(request: Request, message: str, category: str = "info") -> None:
    msgs = request.session.setdefault("_flash", [])
    msgs.append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flash", [])


# ── FastAPI dependencies ──────────────────────────────────────────────────────


def get_current_user(request: Request) -> dict:
    """
    Dependency that returns the logged-in user dict.
    Raises NotAuthenticated (caught by main.py exception handler → redirect to /auth/login).
    """
    user = get_session_user(request)
    if not user:
        raise NotAuthenticated()
    return user


def require_admin(request: Request) -> dict:
    """Dependency that requires admin. Raises NotAdmin if not."""
    user = get_current_user(request)
    if not user.get("is_admin"):
        raise NotAdmin()
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    return await oauth.pocketid.authorize_redirect(request, OIDC_REDIRECT_URI)


@router.get("/callback")
async def callback(request: Request):
    token = await oauth.pocketid.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.pocketid.userinfo(token=token)

    if not set_session_user(request, userinfo):
        # Authenticated with PocketID but not in any authorized group.
        request.session.clear()
        return RedirectResponse(url="/auth/denied", status_code=303)

    flash(request, f"Welcome, {request.session['user']['name']}!", "success")
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
