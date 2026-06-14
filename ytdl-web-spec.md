# ytdl-web — Full Implementation Spec

A self-hosted web app for downloading online videos directly into organized Jellyfin library
directories. Mobile-friendly, OIDC-authenticated via PocketID, with a full download queue and
history. Deployable as a NixOS module via flake.

---

## Goals

- Any authenticated user pastes a URL, picks a category, hits go — file lands in the right
  Jellyfin subdirectory with proper folder structure, NFO metadata, and poster art
- Admin users manage categories (name → filesystem path) via the web UI
- Full download queue visible to all users; history of past downloads
- MP4 output, 1080p by default; per-download toggle for best available (4K/HDR)
- yt-dlp archive file per category to prevent re-downloading
- Works with any yt-dlp-supported site, not just YouTube

## Non-Goals

- No Jellyfin API integration — rely on Jellyfin's scheduled library scan
- No channel/playlist subscription (that's Pinchflat's job)
- No concurrent downloads — serial queue is intentional (avoids rate limits)

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Database | SQLite via `aiosqlite` |
| Templates | Jinja2 |
| Frontend | HTMX + Pico CSS (CDN, no build step) |
| Auth | `authlib` (OIDC), `itsdangerous` (session signing) |
| Downloader | `yt-dlp` Python library (not subprocess) |
| System deps | `ffmpeg` (muxing), `yt-dlp` binary on PATH as fallback |
| Package | `pyproject.toml` |
| Deployment | NixOS module via flake |

---

## Output File Structure

Each download produces a self-contained folder inside the category directory. Files are first
assembled in a staging directory and atomically moved to the final location only when complete
(see Staging below).

```
{category_path}/
└── {display_title} [{video_id}]/
    ├── {display_title} [{video_id}].mp4
    ├── {display_title} [{video_id}].nfo
    └── {display_title} [{video_id}]-poster.jpg
```

`display_title` is the user-supplied custom title if provided, otherwise the title pulled from
yt-dlp. The `[video_id]` suffix is always appended regardless.

**Real examples** (matching existing Jellyfin library conventions):

```
/orico/jellyfin/data/YouTube/Funny/
├── Wonky Donkey [SDeQT9zCvi4]/
│   ├── Wonky Donkey [SDeQT9zCvi4].mp4
│   ├── Wonky Donkey [SDeQT9zCvi4].nfo
│   └── Wonky Donkey [SDeQT9zCvi4]-poster.jpg
└── Potter Puppet Pals: The Mysterious Ticking Noise [Tx1XIm6q4r4]/
    ├── Potter Puppet Pals: The Mysterious Ticking Noise [Tx1XIm6q4r4].mp4
    ├── Potter Puppet Pals: The Mysterious Ticking Noise [Tx1XIm6q4r4].nfo
    └── Potter Puppet Pals: The Mysterious Ticking Noise [Tx1XIm6q4r4]-poster.jpg
```

### Title resolution

```python
# After extract_info:
yt_title = info['title']                          # always from yt-dlp
display_title = custom_title or yt_title          # user override or yt-dlp title
sanitized = yt_dlp.utils.sanitize_filename(display_title)
folder_name = f"{sanitized} [{info['id']}]"
```

`yt_dlp.utils.sanitize_filename()` handles illegal filesystem characters. Use it directly —
do not reimplement. The unsanitized `display_title` and `yt_title` are used in NFO content.

### Staging

Downloads are assembled in `{STAGING_DIR}/{download_id}/` (using the DB row ID as the staging
subfolder to avoid collisions). Once the MP4, NFO, and poster are all in place inside that
staging subfolder, the entire folder is moved atomically to the final category path:

```python
staging_folder = Path(STAGING_DIR) / str(download_id)
final_folder   = Path(category_path) / folder_name

# After all files written to staging_folder:
shutil.move(str(staging_folder), str(final_folder))
```

`shutil.move` on the same filesystem is a rename (atomic). If staging and the category path
are on different filesystems, it falls back to copy+delete — acceptable, but note in README
that keeping staging on the same mount as the Jellyfin library is preferred.

Staging subfolders are cleaned up on failure too (`shutil.rmtree(staging_folder, ignore_errors=True)`).
On app startup, any leftover staging subfolders from a previous crash are deleted before
re-queuing pending downloads.

### NFO format

Jellyfin movie-style NFO. Generated from yt-dlp's info dict after download completes.

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>Wonky Donkey</title>
  <originaltitle>Wonky Donkey</originaltitle>
  <plot>Full video description here...</plot>
  <year>2009</year>
  <premiered>2009-03-12</premiered>
  <dateadded>2024-11-01</dateadded>
  <studio>ChannelNameHere</studio>
  <uniqueid type="youtube" default="true">SDeQT9zCvi4</uniqueid>
  <id>SDeQT9zCvi4</id>
  <source>WEB-DL</source>
  <genre>Comedy</genre>
  <tag>funny</tag>
  <tag>music</tag>
</movie>
```

Field mappings from yt-dlp info dict:
- `display_title` (custom_title if set, else `info['title']`) → `<title>`
- `info['title']` → `<originaltitle>` always (preserves the real YouTube title)
- `info['description']` → `<plot>` (strip/truncate at 4000 chars if needed)
- `info['upload_date'][:4]` → `<year>` (YYYYMMDD → YYYY)
- `info['upload_date']` formatted as YYYY-MM-DD → `<premiered>`
- current date formatted as YYYY-MM-DD → `<dateadded>`
- `info.get('channel') or info.get('uploader', '')` → `<studio>`
- `info['id']` → `<uniqueid>` and `<id>`
- `info.get('categories', [])` → one `<genre>` per entry
- `info.get('tags', [])` → one `<tag>` per entry (limit to first 20)

For non-YouTube sources, `uniqueid type` should be the extractor name:
`info.get('extractor_key', 'generic').lower()`

---

## yt-dlp Integration

Use yt-dlp as a Python library (`import yt_dlp`). Do not shell out.

### Download flow

```python
# Step 1: Extract info only (no download) — populate DB record title/id early
with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
    info = ydl.extract_info(url, download=False)
    # Update DB record with title and video_id

# Step 2: Resolve display title and build paths
yt_title     = info['title']
display_title = custom_title or yt_title
sanitized    = yt_dlp.utils.sanitize_filename(display_title)
folder_name  = f"{sanitized} [{info['id']}]"
staging_dir  = Path(STAGING_DIR) / str(download_id)
staging_dir.mkdir(parents=True, exist_ok=True)

# Step 3: Full download into staging dir
ydl_opts = {
    'format': FORMAT_STRING,  # see below
    'outtmpl': {
        'default':    str(staging_dir / f'{folder_name}.%(ext)s'),
        'thumbnail':  str(staging_dir / f'{folder_name}-poster'),
    },
    'writethumbnail': True,
    'postprocessors': [
        {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
    ],
    'download_archive': f'{category_path}/.ytdl-archive.txt',
    'merge_output_format': 'mp4',
    'progress_hooks': [progress_hook],
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])

# Step 4: Generate NFO into staging dir
write_nfo(info, display_title, staging_dir / f'{folder_name}.nfo')

# Step 5: Verify expected files exist in staging before moving
assert (staging_dir / f'{folder_name}.mp4').exists()
assert (staging_dir / f'{folder_name}.nfo').exists()
# poster is best-effort: log warning if missing, do not fail

# Step 6: Move staging folder to final destination (atomic on same filesystem)
final_path = Path(category_path) / folder_name
shutil.move(str(staging_dir), str(final_path))
```

### Format strings

**1080p (default):**
```
bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best
```

**Best available (user-toggled per download):**
```
bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best
```

### Archive file

- Location: `{category_path}/.ytdl-archive.txt`
- Created automatically by yt-dlp on first write
- If yt-dlp raises `DownloadError` containing "has already been recorded in the archive",
  mark the download record status as `skipped` with `skipped_reason = "already in archive"`
- The archive check happens inside yt-dlp — no need to pre-check manually

### Progress hook

```python
def progress_hook(d):
    if d['status'] == 'downloading':
        # update DB: percent, speed (optional, nice to have)
        pass
    elif d['status'] == 'finished':
        # file downloaded, post-processors haven't run yet
        pass
    elif d['status'] == 'error':
        # mark DB record failed
        pass
```

### Async execution

yt-dlp is synchronous. Run inside the background worker using:
```python
await asyncio.get_event_loop().run_in_executor(None, blocking_download_fn)
```

---

## Database

SQLite at `{DATA_DIR}/ytdl.db`. Use `CREATE TABLE IF NOT EXISTS` on startup. No migrations
framework needed for v1.

### Journal mode

Use the default SQLite journal mode (`DELETE`, not WAL). This app has a single writer (the
asyncio download worker). WAL mode offers no benefit here and complicates backups by
producing `.db-wal` and `.db-shm` sidecar files. With the default journal mode, restic can
safely snapshot `{DATA_DIR}/ytdl.db` at any time — SQLite guarantees the file is in a
consistent committed state between transactions, and the download worker is the only writer.

### Schema

```sql
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS downloads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT NOT NULL,
    category_id         INTEGER REFERENCES categories(id),
    category_name       TEXT NOT NULL,   -- denormalized: survives category rename/delete
    category_path       TEXT NOT NULL,   -- denormalized
    custom_title        TEXT,            -- user-supplied override; NULL = use yt-dlp title
    status              TEXT NOT NULL DEFAULT 'pending',
    quality             TEXT NOT NULL DEFAULT '1080p',   -- '1080p' | 'best'
    requested_by_email  TEXT NOT NULL,
    requested_by_name   TEXT NOT NULL,
    requested_at        TEXT NOT NULL DEFAULT (datetime('now')),
    started_at          TEXT,
    completed_at        TEXT,
    title               TEXT,            -- resolved display title (custom or yt-dlp), populated after extract_info
    yt_title            TEXT,            -- always the raw yt-dlp title, populated after extract_info
    video_id            TEXT,            -- populated after extract_info
    final_path          TEXT,            -- absolute path to the completed folder
    error_msg           TEXT,
    skipped_reason      TEXT
);
```

### Status values

| Status | Meaning |
|---|---|
| `pending` | In queue, not started |
| `downloading` | yt-dlp is actively working |
| `done` | Completed successfully |
| `failed` | yt-dlp error |
| `skipped` | URL was already in the archive file |

---

## Background Download Worker

A single asyncio task processes downloads serially from an in-process `asyncio.Queue`.

### Startup recovery (in FastAPI `lifespan` handler)

```python
# 1. Clean up any leftover staging subfolders from a previous crash
staging_root = Path(STAGING_DIR)
if staging_root.exists():
    for child in staging_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)

# 2. Reset any crash-interrupted downloads
await db.execute("UPDATE downloads SET status='pending', started_at=NULL WHERE status='downloading'")

# 3. Re-enqueue all pending items in order
rows = await db.fetch("SELECT id FROM downloads WHERE status='pending' ORDER BY id ASC")
for row in rows:
    await download_queue.put(row['id'])
```

### Worker loop

```python
async def download_worker():
    while True:
        download_id = await download_queue.get()
        record = await db.get_download(download_id)
        if record is None or record['status'] != 'pending':
            continue
        await process_download(record)
```

### process_download steps

1. Mark record `status = 'downloading'`, set `started_at`
2. Call `extract_info(url, download=False)`, update record with `title` (resolved display
   title), `yt_title` (raw), and `video_id`
3. Create staging subdirectory at `{STAGING_DIR}/{download_id}/`
4. Run full yt-dlp download into staging dir in executor
5. On success:
   a. Generate NFO into staging dir
   b. Verify MP4 and NFO exist in staging; log warning if poster missing
   c. `shutil.move` staging folder to `{category_path}/{folder_name}`
   d. Mark `status = 'done'`, set `completed_at`, set `final_path`
6. On `yt_dlp.utils.DownloadError` with "already in archive": mark `status = 'skipped'`,
   clean up staging dir
7. On any other exception: mark `status = 'failed'`, store `error_msg`, clean up staging dir

---

## Authentication

PocketID OIDC. Use `authlib.integrations.starlette_client` for the OIDC flow.

### Flow

1. Unauthenticated request → redirect to `/auth/login`
2. `/auth/login` → redirect to PocketID authorization endpoint (authlib handles discovery
   from `{OIDC_ISSUER_URL}/.well-known/openid-configuration`)
3. PocketID → redirect to `/auth/callback?code=...&state=...`
4. App exchanges code → validates ID token → stores user info in signed session cookie
5. Session cookie contains: `{sub, email, name, is_admin}`

### Admin determination

On login, check if `email` is in `ADMIN_EMAILS` (env var, comma-separated list). Store
`is_admin = True/False` in the session. No per-request DB lookup needed.

### Session

Use `starlette.middleware.sessions.SessionMiddleware` with `SECRET_KEY`. Session is stored
client-side in a signed cookie (itsdangerous). Store only: `sub`, `email`, `name`, `is_admin`.

### Required OIDC scopes

`openid profile email`

### Route protection

Create a FastAPI dependency `get_current_user` that reads the session cookie and returns the
user dict, or raises `HTTPException(302)` redirecting to `/auth/login`. Create a second
dependency `require_admin` that calls `get_current_user` and raises `403` if not admin.

---

## API Endpoints

### Auth routes
```
GET  /auth/login      — initiate OIDC redirect
GET  /auth/callback   — handle OIDC callback, set session, redirect to /
POST /auth/logout     — clear session, redirect to /auth/login
```

### Page routes (HTML, Jinja2 templates)
```
GET /          — main page: submit form + active queue section
GET /history   — paginated download history
GET /admin     — category management (admin only)
```

### JSON API routes

```
POST   /api/downloads
  Body: { url: str, category_id: int, quality: "1080p"|"best", custom_title: str|null }
  Returns: download record (201)
  Errors: 400 if URL is currently pending or downloading
          400 if category_id doesn't exist
  Side effect: enqueues the download

GET    /api/queue
  Returns: HTML partial (for HTMX polling) of pending + downloading items
  Used by: hx-get on the queue section, every 3 seconds

GET    /api/downloads?page=1&per_page=20&status=
  Returns: JSON paginated history
  Admins see all; regular users see only their own

DELETE /api/downloads/{id}
  Cancels a pending download (removes from DB + queue)
  Allowed: admin, or the user who requested it
  Error: 409 if status is not pending

GET    /api/categories
  Returns: JSON list of all categories

POST   /api/categories          (admin only)
  Body: { name: str, path: str, description: str|null }
  Validates: path exists and is writable
  Returns: created category (201)

PUT    /api/categories/{id}     (admin only)
  Body: same as POST
  Validates: path exists and is writable

DELETE /api/categories/{id}     (admin only)
  Error: 409 if any pending or downloading downloads reference it
```

---

## Frontend

### General

- Jinja2 templates extending a `base.html`
- Pico CSS via CDN (`https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css`)
  — provides a clean, mobile-first, no-class-required stylesheet
- HTMX via CDN for dynamic behavior (no page reloads, no full JS framework)
- Navigation bar: "ytdl-web" logo | Queue | History | Admin (admin only) | Logout
- All pages are usable on a phone screen without horizontal scrolling

### Main page (`/`)

```
[ URL input field — full width, large, type=url                              ]
[ Custom title (optional) — placeholder: "Leave blank to use video's title"  ]
[ Category dropdown ]         [ Quality: 1080p ○  Best ○ ]
[ Download button ]

─── Active Downloads ──────────────────────────────────
(this section refreshes every 3 seconds via HTMX polling)
Title / URL          Category    Requested by  Status     [✕]
─────────────────────────────────────────────────────────
```

Custom title note: shown as a secondary/helper field, not prominently. The placeholder text
makes it clear it's optional. If filled in, the resolved title shown in the queue and history
is the custom title (with `[id]` appended).

HTMX on the queue section:
```html
<div id="queue-section"
     hx-get="/api/queue"
     hx-trigger="every 3s"
     hx-target="#queue-section"
     hx-swap="innerHTML">
```

The `/api/queue` endpoint returns a rendered HTML partial (not JSON) for the queue rows.
Stop polling when queue is empty (use `hx-trigger="every 3s [document.querySelector('.queue-item')]"`
or always poll — the partial is cheap).

Status badges use Pico CSS's `data-tooltip` and `<mark>` element, or simple colored spans:
- pending → muted
- downloading → blue/primary
- done → green
- failed → red
- skipped → yellow

### History page (`/history`)

Table columns: Title | Category | Quality | Requested by | Requested | Completed | Status

- Paginated at 20/page with prev/next links
- Status filter `<select>` at top (triggers a normal GET with `?status=` query param)
- Clicking a title (when status=done) shows the filename in a tooltip or small expand

### Admin page (`/admin`)

Two sections:

**Category list** — table with columns: Name | Path | Description | Actions (Edit / Delete)
- Delete shows a `<dialog>` confirm (Pico CSS native dialog) before POSTing
- Edit opens an inline form (HTMX `hx-get` swap)

**Add category form**
```
[ Name ]  [ Filesystem Path ]  [ Description (optional) ]  [ Add ]
```
- On submit, server validates path exists + writable; returns inline error if not
- Success reloads the category list via HTMX

---

## Configuration

All config via environment variables (12-factor). The NixOS module sets non-secret vars
directly and loads secrets from `environmentFile`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATA_DIR` | Yes | — | Directory for `ytdl.db`. This is the only path restic needs to back up. |
| `STAGING_DIR` | No | `{DATA_DIR}/staging` | Temp dir for in-progress downloads. Keep on same filesystem as Jellyfin library for atomic moves. Not backed up — incomplete downloads are re-queued on startup. |
| `SECRET_KEY` | Yes | — | 32+ random chars for session signing |
| `OIDC_ISSUER_URL` | Yes | — | PocketID base URL (e.g. `https://id.example.com`) |
| `OIDC_CLIENT_ID` | Yes | — | OIDC client ID |
| `OIDC_CLIENT_SECRET` | Yes | — | OIDC client secret (keep in environmentFile) |
| `OIDC_REDIRECT_URI` | Yes | — | Full callback URL (e.g. `https://ytdl.example.com/auth/callback`) |
| `ADMIN_EMAILS` | Yes | — | Comma-separated list of admin email addresses |
| `PORT` | No | `8000` | HTTP port |
| `LOG_LEVEL` | No | `info` | uvicorn log level |

---

## Project Structure

```
ytdl-web/
├── flake.nix
├── flake.lock
├── pyproject.toml
├── ytdl_web/
│   ├── __init__.py
│   ├── main.py          # FastAPI app factory, lifespan handler, middleware setup
│   ├── auth.py          # OIDC flow routes, session helpers, dependencies
│   ├── db.py            # aiosqlite helpers, schema init, CRUD functions
│   ├── models.py        # Pydantic request/response models
│   ├── worker.py        # asyncio download queue, process_download, startup recovery
│   ├── ytdlp.py         # yt-dlp wrapper: extract_info, download, NFO generation
│   ├── nfo.py           # NFO XML generation from yt-dlp info dict
│   └── templates/
│       ├── base.html    # nav, CDN links, flash messages
│       ├── index.html   # submit form + queue section
│       ├── queue_partial.html   # HTMX-returned partial for queue rows
│       ├── history.html
│       ├── admin.html
│       └── login.html   # minimal page shown if OIDC redirect fails
├── nix/
│   └── module.nix
└── README.md
```

---

## NixOS Flake

### Outputs

```nix
{
  packages.${system}.default    # the Python app package
  nixosModules.default          # the NixOS service module
}
```

### Python package (`flake.nix`)

Build with `buildPythonApplication`. Dependencies:
- `fastapi`
- `uvicorn` (with `standard` extras for production)
- `aiosqlite`
- `jinja2`
- `authlib`
- `httpx` (authlib async transport)
- `itsdangerous`
- `python-multipart` (FastAPI form parsing)
- `yt-dlp`

### NixOS module options (`nix/module.nix`)

```nix
options.services.ytdl-web = {
  enable = lib.mkEnableOption "ytdl-web video download service";

  port = lib.mkOption {
    type = lib.types.port;
    default = 8000;
    description = "Port to listen on";
  };

  dataDir = lib.mkOption {
    type = lib.types.path;
    default = "/var/lib/ytdl-web";
    description = "Directory for SQLite database. Back this up with restic.";
  };

  stagingDir = lib.mkOption {
    type = lib.types.path;
    default = "/var/lib/ytdl-web/staging";
    description = ''
      Temporary directory for in-progress downloads. For atomic moves, set this
      to a path on the same filesystem as your Jellyfin library. This directory
      is ephemeral — do not back it up.
    '';
  };

  user = lib.mkOption {
    type = lib.types.str;
    default = config.services.jellyfin.user;
    defaultText = lib.literalExpression "config.services.jellyfin.user";
    description = "User to run the service as. Defaults to the Jellyfin service user so it inherits write access to the media library.";
  };

  group = lib.mkOption {
    type = lib.types.str;
    default = config.services.jellyfin.group;
    defaultText = lib.literalExpression "config.services.jellyfin.group";
    description = "Group to run the service as. Defaults to the Jellyfin service group.";
  };

  environmentFile = lib.mkOption {
    type = lib.types.path;
    description = ''
      Path to a file containing secrets as KEY=VALUE pairs.
      Must include: SECRET_KEY, OIDC_CLIENT_SECRET
    '';
  };

  settings = {
    oidcIssuerUrl = lib.mkOption {
      type = lib.types.str;
      description = "PocketID base URL";
    };
    oidcClientId = lib.mkOption {
      type = lib.types.str;
    };
    oidcRedirectUri = lib.mkOption {
      type = lib.types.str;
      description = "Full OIDC callback URL";
    };
    adminEmails = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Email addresses that receive admin privileges";
    };
    logLevel = lib.mkOption {
      type = lib.types.enum ["debug" "info" "warning" "error"];
      default = "info";
    };
  };
};
```

### systemd service (`nix/module.nix` config section)

```nix
config = lib.mkIf cfg.enable {
  # No user/group creation — runs as the existing jellyfin user which already
  # has write access to the media library.

  systemd.services.ytdl-web = {
    description = "ytdl-web video download service";
    wantedBy = ["multi-user.target"];
    after = ["network.target"];

    serviceConfig = {
      User = cfg.user;
      Group = cfg.group;
      WorkingDirectory = cfg.dataDir;
      StateDirectory = "ytdl-web";          # creates /var/lib/ytdl-web if needed
      StateDirectoryMode = "0750";
      EnvironmentFile = cfg.environmentFile;
      ExecStart = "${package}/bin/ytdl-web";
      Restart = "on-failure";
      RestartSec = "5s";
    };

    environment = {
      DATA_DIR    = cfg.dataDir;
      STAGING_DIR = cfg.stagingDir;
      PORT        = toString cfg.port;
      OIDC_ISSUER_URL  = cfg.settings.oidcIssuerUrl;
      OIDC_CLIENT_ID   = cfg.settings.oidcClientId;
      OIDC_REDIRECT_URI = cfg.settings.oidcRedirectUri;
      ADMIN_EMAILS = lib.concatStringsSep "," cfg.settings.adminEmails;
      LOG_LEVEL    = cfg.settings.logLevel;
    };

    path = [ pkgs.ffmpeg ];
  };
};
```

---

## Implementation Notes

1. **Database init**: Call schema `CREATE TABLE IF NOT EXISTS` statements in the FastAPI
   `lifespan` startup handler before the download worker starts. Set
   `PRAGMA journal_mode=DELETE` explicitly on each connection to be safe.

2. **Restic backup**: restic only needs to target `DATA_DIR` (just `ytdl.db`). The staging
   dir is ephemeral and should be excluded. Add to your restic config:
   `--exclude "{STAGING_DIR}"`. If `STAGING_DIR` is inside `DATA_DIR` (the default), use
   `--exclude "{DATA_DIR}/staging"`. The DB is safe to snapshot mid-run with default journal
   mode since SQLite guarantees committed consistency between transactions.

3. **Custom title sanitization**: Use `yt_dlp.utils.sanitize_filename(display_title)` for
   the folder/file name. Store the pre-sanitization `custom_title` (if provided) and
   `yt_title` in the DB. The NFO `<title>` gets the unsanitized display title; `<originaltitle>`
   always gets the raw yt-dlp title regardless of whether a custom title was set.

4. **Staging on same filesystem**: The README should note that for atomic moves,
   `STAGING_DIR` should be on the same mount as the Jellyfin library paths. On nixnuc with
   `/orico/jellyfin/...`, a good default would be `/orico/jellyfin/staging` rather than
   `/var/lib/ytdl-web/staging`. The NixOS module default is safe but not optimal — document
   this tradeoff.

5. **OIDC discovery**: authlib's `starlette_client` fetches
   `{OIDC_ISSUER_URL}/.well-known/openid-configuration` automatically. Register the client
   with `oauth.register(name='pocketid', ...)`.

6. **yt-dlp archive + skipped detection**: Wrap the `ydl.download()` call. yt-dlp raises
   `yt_dlp.utils.ExistingVideoReached` or logs a message — check the exact exception/log
   behavior and set status to `skipped` accordingly. An alternative: check if the video ID
   appears in the archive file before downloading.

7. **NFO generation timing**: Generate the NFO file *after* `ydl.download()` returns
   successfully, using the info dict from the earlier `extract_info` call. This avoids
   writing a partial NFO if the download fails. Both go to staging before any move.

8. **HTMX queue partial**: `/api/queue` returns `text/html` (a rendered Jinja2 partial), not
   JSON. This keeps the JS footprint at zero for the live update feature.

9. **Category path validation**: In POST/PUT `/api/categories`, call
   `os.path.isdir(path) and os.access(path, os.W_OK)` and return a 400 with a descriptive
   error message if it fails. Do not silently accept bad paths.

10. **final_path in DB**: After `shutil.move`, store the absolute path of the completed
    folder (not the individual file) in `downloads.final_path`. The history page can display
    the folder name from this.

11. **Pico CSS dialogs**: For delete confirmation use the native HTML `<dialog>` element which
    Pico CSS styles automatically. Use `htmx.find('#confirm-dialog').showModal()` on delete
    button click.

12. **URL deduplication**: Before enqueuing, check
    `SELECT id FROM downloads WHERE url=? AND status IN ('pending','downloading')` and return
    400 if found.

13. **pyproject.toml entry point**: Define a `[project.scripts]` entry
    `ytdl-web = "ytdl_web.main:run"` where `run()` calls `uvicorn.run(app, ...)` with config
    from env vars.
