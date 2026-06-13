# ytdlfin

Self-hosted video downloader for Jellyfin. Paste a URL, pick a category, and the file
lands in the right Jellyfin directory with NFO metadata and poster art — no manual
file management required.

## Features

- Any yt-dlp-supported URL (YouTube, Vimeo, Twitch, etc.)
- OIDC authentication via PocketID
- Download queue visible to all users
- 1080p by default; per-download toggle for best available
- NFO metadata + poster art for Jellyfin movie libraries
- Archive file per category — won't re-download the same video twice
- Admin UI for managing categories (name → filesystem path)

## Local development

```bash
nix develop
uvicorn ytdlfin.main:app --reload
```

Copy `.env.example` to `.env` and fill in your OIDC credentials, then source it before
running uvicorn.

## Required environment variables

| Variable | Description |
|---|---|
| `DATA_DIR` | Directory for the SQLite database |
| `SECRET_KEY` | 32+ random chars for session signing |
| `OIDC_ISSUER_URL` | PocketID base URL |
| `OIDC_CLIENT_ID` | OIDC client ID |
| `OIDC_CLIENT_SECRET` | OIDC client secret |
| `OIDC_REDIRECT_URI` | Full callback URL (e.g. `https://ytdlfin.example.com/auth/callback`) |
| `ADMIN_EMAILS` | Comma-separated email addresses that get admin access |

Optional: `STAGING_DIR` (default: `{DATA_DIR}/staging`), `PORT` (default: 8000),
`LOG_LEVEL` (default: info).

## Staging directory

For atomic moves (rename, not copy+delete), `STAGING_DIR` must be on the same
filesystem as your Jellyfin media library. Override the default in your NixOS config:

```nix
services.ytdlfin.stagingDir = "/orico/jellyfin/.ytdlfin-staging";
```

## NixOS deployment

Add this flake as an input in your host flake and import the module:

```nix
# flake.nix inputs
ytdlfin.url = "github:genebean/ytdlfin";

# host configuration
imports = [ ytdlfin.nixosModules.default ];

services.ytdlfin = {
  enable = true;
  user  = config.services.jellyfin.user;
  group = config.services.jellyfin.group;
  stagingDir = "/orico/jellyfin/.ytdlfin-staging";
  environmentFile = config.sops.secrets.ytdlfin-env.path;
  settings = {
    oidcIssuerUrl   = "https://id.example.com";
    oidcClientId    = "ytdlfin";
    oidcRedirectUri = "https://ytdlfin.example.com/auth/callback";
    adminEmails     = [ "you@example.com" ];
    mediaDirectories = [ "/orico/jellyfin/data" ];
  };
};
```

The `environmentFile` must contain:
```
SECRET_KEY=<32+ random chars>
OIDC_CLIENT_SECRET=<your OIDC secret>
```

## Backup

restic only needs to target `DATA_DIR`. Exclude staging:

```
--exclude "{DATA_DIR}/staging"
```

## Documentation

Full spec and user guide: <https://genebean.github.io/ytdlfin/>
