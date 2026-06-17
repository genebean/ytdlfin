# AGENTS.md — ytdlfin

Self-contained working guide for any agent dropped into this repo cold.

---

## What this project is

**ytdlfin** is a self-hosted FastAPI web app that downloads videos via yt-dlp into
organized Jellyfin library directories. Users paste a URL, pick a category, and the
file lands in the right place with NFO metadata and poster art. It is deployed as a
NixOS module via flake and authenticated via PocketID (OIDC).

The authoritative spec is at `docs/reference/spec.html`. Read it before making any
structural changes. Update it when making decisions that belong in it.

---

## Who you're working with

An experienced infrastructure/SRE engineer who is not an application developer.
Comment generously — future maintenance may be done by an agent without full context.
Prefer explicit over implicit. Prefer simple over clever.

---

## Naming

Everything uses **ytdlfin** (not ytdl-web, not ytdl_web):
- Python package directory: `ytdlfin/`
- NixOS service: `services.ytdlfin`
- systemd unit: `ytdlfin`
- Binary entrypoint: `ytdlfin`
- Data dir: `/var/lib/ytdlfin`

---

## Tooling contract — Nix dev shell

This repo has a `flake.nix` with `devShells.default` pinned to `nixos-26.05`.
All runtimes and tools come from `nix develop`. Never assume Python, ffmpeg, or
yt-dlp exist on the host PATH outside the shell.

```
nix develop                          # enters the dev shell
pre-commit install                   # one-time setup after entering shell for the first time
uvicorn ytdlfin.main:app --reload    # run locally inside the shell
```

The dev shell also provides `pre-commit`, `deadnix`, `nixfmt-tree`, and `imagemagick` (available as `magick`).
Run `nix fmt` to reformat all Nix files — the `formatter` output is wired to
`nixfmt-tree`, so that is the correct command (not `nixfmt` directly).

---

## Project layout

```
ytdlfin/
├── flake.nix               # devShell + buildPythonApplication + nixosModules.default
├── pyproject.toml          # hatchling build, dependencies, entry point
├── ytdlfin/
│   ├── main.py             # FastAPI app factory, lifespan, middleware, exception handlers
│   ├── auth.py             # OIDC flow, session helpers, dependencies
│   ├── db.py               # aiosqlite helpers, schema, all CRUD
│   ├── models.py           # Pydantic models + normalize_quality()
│   ├── utils.py            # Shared: templates, _render, _validate_*, _parse_category,
│   │                       #   _execute_create_download, MEDIA_DIRECTORIES
│   ├── routers/
│   │   ├── pages.py        # HTML page routes: /, /history, /admin, /auth/denied, /downloads
│   │   ├── downloads.py    # Queue HTMX partials + JSON API: /api/queue*, /api/downloads*, /partials/queue/*
│   │   └── categories.py   # Category CRUD + HTMX partials: /api/categories*, /partials/categories/*
│   ├── worker.py           # asyncio download queue + process_download
│   ├── ytdlp.py            # yt-dlp wrapper (extract_info, download, staging logic)
│   ├── nfo.py              # NFO XML generation
│   └── templates/          # Jinja2 templates (base, page, HTMX partials)
├── tests/
│   ├── conftest.py         # Env setup, db fixture, user_client/admin_client fixtures
│   ├── test_db.py          # DB CRUD, state transitions, pagination
│   ├── test_models.py      # normalize_quality, DownloadRequest, CategoryUpdate
│   ├── test_nfo.py         # NFO XML generation
│   ├── test_routes.py      # HTTP integration tests (JSON + HTMX paths)
│   ├── test_utils.py       # URL scheme + path validation
│   ├── test_worker.py      # Worker orchestration (mocks extract_info + download_async)
│   └── test_ytdlp.py       # Format constants and selection logic
├── nix/
│   └── module.nix          # NixOS service module
└── docs/                   # HTML documentation (no generators, no build step)
```

---

## Tech stack summary

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Database | SQLite via `aiosqlite` (single writer, default journal mode) |
| Templates | Jinja2 |
| Frontend | HTMX 2.0.4 + Pico CSS 2.1.1 — both via CDN with SRI hashes |
| Auth | authlib (OIDC via PocketID), SessionMiddleware |
| Downloader | yt-dlp Python library (not subprocess) |
| System dep | ffmpeg (muxed into PATH by Nix wrapper) |

---

## Key design decisions

- **Serial queue**: one download at a time, intentional — avoids rate limits.
- **Staging + atomic move**: downloads assemble in `{STAGING_DIR}/{id}/`, then
  `shutil.move` into the category path. Keep staging on the same filesystem as the
  Jellyfin library for a true rename (not copy+delete).
- **No WAL**: default SQLite journal mode; no `-wal`/`-shm` sidecar files, restic
  can snapshot the DB at any time.
- **yt-dlp as library**: `import yt_dlp` — no subprocess. Progress hooks and error
  introspection work cleanly.
- **CDN with SRI**: Pico CSS and HTMX are loaded from jsDelivr with pinned versions
  and SHA-384 integrity hashes. Update hashes when bumping versions:
  ```
  curl -s <url> | openssl dgst -sha384 -binary | openssl base64 -A
  ```
- **Group-based access**: PocketID group membership checked at login via the `groups`
  OIDC scope/claim. `ADMIN_GROUP` members get admin; `USER_GROUP` members get user access.
  Users in neither group are denied at `/auth/denied`. No per-request DB lookup.

---

## Configuration (environment variables)

All config via env vars. The NixOS module sets non-secret vars and loads secrets from
`environmentFile`.

| Variable | Required | Notes |
|---|---|---|
| `DATA_DIR` | Yes | Directory for `ytdlfin.db`. Back this up with restic. |
| `STAGING_DIR` | No | Default: `{DATA_DIR}/staging`. Override to same FS as Jellyfin library. |
| `SECRET_KEY` | Yes | 32+ random chars (in environmentFile) |
| `OIDC_ISSUER_URL` | Yes | PocketID base URL |
| `OIDC_CLIENT_ID` | Yes | OIDC client ID |
| `OIDC_CLIENT_SECRET` | Yes | OIDC client secret (in environmentFile) |
| `OIDC_REDIRECT_URI` | Yes | Full callback URL |
| `ADMIN_GROUP` | Yes | PocketID group name for admin access |
| `USER_GROUP` | Yes | PocketID group name for regular user access |
| `PORT` | No | Default: 8001 |
| `LOG_LEVEL` | No | Default: info |
| `HTTPS_ONLY` | No | Set `true` behind an HTTPS proxy; marks cookies Secure and trusts X-Forwarded-Proto |
| `TRUSTED_PROXY_IPS` | No | Default: `*` (trust all). IPs allowed to set X-Forwarded-* headers. Set to `127.0.0.1` for same-host nginx. |

---

## NixOS module

The module lives in `nix/module.nix` and is exported as `nixosModules.default`.
Notable options:
- `services.ytdlfin.user`/`group`: default `ytdlfin`; set to Jellyfin's user/group
  so the service can write to the media library without extra ACLs.
- `services.ytdlfin.stagingDir`: override to a path on the same filesystem as the
  Jellyfin library (e.g. `/orico/jellyfin/.ytdlfin-staging`).
- `services.ytdlfin.settings.mediaDirectories`: list of Jellyfin library root paths
  that systemd's `ReadWritePaths` must include.
- `services.ytdlfin.settings.oidcAdminGroup`: PocketID group name for admin access.
- `services.ytdlfin.settings.oidcUserGroup`: PocketID group name for regular user access.

---

## Reverse proxy (nginx)

ytdlfin is designed to run behind nginx with TLS termination. Two things must be
wired up correctly:

**nginx** must forward the real scheme and client IP:
```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_pass http://127.0.0.1:8001;
```

**ytdlfin** needs `HTTPS_ONLY=true` in its environment (the NixOS module sets this
automatically). This does two things:
- Tells uvicorn to trust `X-Forwarded-Proto` from `127.0.0.1`, so `request.url.scheme`
  returns `https` — required for authlib to build the correct OIDC redirect URI.
- Marks session cookies `Secure` so browsers only send them over TLS.

Leave `HTTPS_ONLY` unset (defaults to `false`) for local HTTP development.

---

## Image assets

Source images (full-resolution PNG) live in `images/` at the repo root. Derived
web-ready copies (WebP, resized) go in `ytdlfin/static/` (served by the app) and
`docs/images/` (served by the docs site). Use `magick` from the dev shell to convert:

```
magick images/ytdlfin-icon-transparent.png -resize 256x256 ytdlfin/static/ytdlfin-icon.webp
cp ytdlfin/static/ytdlfin-icon.webp docs/images/ytdlfin-icon.webp
```

Always commit both the app and docs copies together. The `images/` source files are
not served — they exist for future resizing and for uploading to design tools.

---

## Documentation standard

All docs are plain HTML files in `docs/`. No static site generator. Edit HTML directly.
`docs/reference/spec.html` is the authoritative spec — update it when changing
architecture, data model, API, or module options.

---

## Testing

The test suite lives in `tests/` and runs under the Nix dev shell:

```
nix develop --command ytdlfin-test
```

This runs pytest with coverage output. Extra args are passed through to pytest
(e.g. `ytdlfin-test -v` for verbose, `ytdlfin-test -k test_name` to run one test).
Outside an active dev shell, use `nix develop --command ytdlfin-test` directly.

**Run tests before every commit.** If tests fail, fix them before committing —
don't commit broken tests and intend to fix them later.

**Write tests for new code whenever practical.** New routes, DB helpers, models,
and utility functions should come with tests in the same commit. Prefer unit tests
for pure logic (models, nfo, ytdlp format selection) and integration tests via
`TestClient` for routes. Use the existing conftest fixtures (`db`, `user_client`,
`admin_client`) for consistency.

Test files map to source modules:
- `test_db.py` → `db.py`
- `test_models.py` → `models.py`
- `test_nfo.py` → `nfo.py`
- `test_routes.py` → `routers/`
- `test_utils.py` → `utils.py`
- `test_worker.py` → `worker.py`
- `test_ytdlp.py` → `ytdlp.py`

---

## Commit and PR hygiene

- Commit messages describe what the code **is**, not what changed. Include a body
  for non-trivial commits.
- Squash local commits before pushing. What reaches the remote reflects the final,
  complete state.
- If a change affects `docs/`, update docs in the same branch and PR.
- Run `nix build` before pushing when `flake.nix` or `pyproject.toml` changes.

---

## Pre-push checklist

1. `nix develop --command ytdlfin-test` — all tests must pass before pushing
2. `pre-commit run --all-files` — catches formatting and dead-code issues fast
3. `nix build` — if `flake.nix` or `pyproject.toml` changed
4. Manual smoke test in `nix develop` — start uvicorn, verify the login redirect works
5. Update `docs/reference/spec.html` if architecture or API changed
