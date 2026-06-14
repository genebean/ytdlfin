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

The dev shell also provides `pre-commit`, `deadnix`, and `nixfmt-tree`.
Run `nix fmt` to reformat all Nix files — the `formatter` output is wired to
`nixfmt-tree`, so that is the correct command (not `nixfmt` directly).

---

## Project layout

```
ytdlfin/
├── flake.nix               # devShell + buildPythonApplication + nixosModules.default
├── pyproject.toml          # hatchling build, dependencies, entry point
├── ytdlfin/
│   ├── main.py             # FastAPI app factory, lifespan, middleware, all routes
│   ├── auth.py             # OIDC flow, session helpers, dependencies
│   ├── db.py               # aiosqlite helpers, schema, all CRUD
│   ├── models.py           # Pydantic models
│   ├── worker.py           # asyncio download queue + process_download
│   ├── ytdlp.py            # yt-dlp wrapper (extract_info, download, staging logic)
│   ├── nfo.py              # NFO XML generation
│   └── templates/          # Jinja2 templates (base, page, HTMX partials)
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
| `PORT` | No | Default: 8000 |
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
proxy_pass http://127.0.0.1:8000;
```

**ytdlfin** needs `HTTPS_ONLY=true` in its environment (the NixOS module sets this
automatically). This does two things:
- Tells uvicorn to trust `X-Forwarded-Proto` from `127.0.0.1`, so `request.url.scheme`
  returns `https` — required for authlib to build the correct OIDC redirect URI.
- Marks session cookies `Secure` so browsers only send them over TLS.

Leave `HTTPS_ONLY` unset (defaults to `false`) for local HTTP development.

---

## Documentation standard

All docs are plain HTML files in `docs/`. No static site generator. Edit HTML directly.
`docs/reference/spec.html` is the authoritative spec — update it when changing
architecture, data model, API, or module options.

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

1. `pre-commit run --all-files` — catches formatting and dead-code issues fast
2. `nix build` — if `flake.nix` or `pyproject.toml` changed
3. Manual smoke test in `nix develop` — start uvicorn, verify the login redirect works
4. Update `docs/reference/spec.html` if architecture or API changed
