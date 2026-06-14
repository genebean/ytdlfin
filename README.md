# ytdlfin

Self-hosted video downloader for Jellyfin. Paste a URL, pick a category, and the file
lands in the right Jellyfin directory with NFO metadata and poster art — no manual
file management required.

## Features

- Any yt-dlp-supported URL (YouTube, Vimeo, Twitch, etc.)
- OIDC authentication via PocketID, with group-based access control
- Download queue visible to all users
- 1080p by default; per-download toggle for best available
- NFO metadata + poster art for Jellyfin movie libraries
- Archive file per category — won't re-download the same video twice
- Admin UI for managing categories (name → filesystem path)

## Local development

```bash
cp .env.example .env   # fill in OIDC credentials
source .env
nix develop
pre-commit install     # one-time setup
uvicorn ytdlfin.main:app --reload
```

## Required environment variables

| Variable | Description |
|---|---|
| `DATA_DIR` | Directory for the SQLite database |
| `SECRET_KEY` | 32+ random chars for session signing |
| `OIDC_ISSUER_URL` | PocketID base URL |
| `OIDC_CLIENT_ID` | OIDC client ID |
| `OIDC_CLIENT_SECRET` | OIDC client secret |
| `OIDC_REDIRECT_URI` | Full callback URL (e.g. `https://ytdlfin.example.com/auth/callback`) |
| `ADMIN_GROUP` | PocketID group name for admin access |
| `USER_GROUP` | PocketID group name for regular user access |

Optional: `STAGING_DIR` (default: `{DATA_DIR}/staging`), `PORT` (default: 8000),
`LOG_LEVEL` (default: info), `HTTPS_ONLY` (set `true` behind an HTTPS proxy),
`TRUSTED_PROXY_IPS` (default: `*`).

## NixOS deployment

Add ytdlfin as a flake input and include the module in your `nixosConfigurations`:

```nix
# flake.nix
inputs.ytdlfin.url = "github:genebean/ytdlfin";

nixosConfigurations.yourhost = nixpkgs.lib.nixosSystem {
  modules = [
    ytdlfin.nixosModules.default   # bring the module into the system
    ./hosts/yourhost/configuration.nix
  ];
};
```

Then configure the service (e.g. in `configuration.nix` or a dedicated file):

```nix
services.ytdlfin = {
  enable = true;
  user  = config.services.jellyfin.user;
  group = config.services.jellyfin.group;
  stagingDir = "/orico/jellyfin/.ytdlfin-staging";
  environmentFile = config.sops.secrets.ytdlfin-env.path;
  settings = {
    oidcIssuerUrl   = "https://id.example.com";
    oidcRedirectUri = "https://ytdlfin.example.com/auth/callback";
    oidcAdminGroup  = "ytdlfin-admins";
    oidcUserGroup   = "ytdlfin-users";
    mediaDirectories = [ "/orico/jellyfin/data" ];
  };
};
```

The `environmentFile` must contain:
```
SECRET_KEY=<32+ random chars>
OIDC_CLIENT_ID=<your OIDC client ID>
OIDC_CLIENT_SECRET=<your OIDC client secret>
```

## Reverse proxy (nginx)

The NixOS module assumes nginx with TLS termination. Configure nginx to forward
the real scheme and client IP:

```nginx
location / {
  proxy_pass http://127.0.0.1:8000;
  proxy_set_header Host              $host;
  proxy_set_header X-Real-IP         $remote_addr;
  proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

The module automatically sets `HTTPS_ONLY=true` and `TRUSTED_PROXY_IPS=*`. The
default trusted-IPs wildcard is safe for LAN deployments behind a physical firewall;
override with `services.ytdlfin.settings.trustedProxyIps = "127.0.0.1"` if nginx
runs on the same host and you prefer stricter trust.

## Staging directory

For atomic moves (rename, not copy+delete), `STAGING_DIR` must be on the same
filesystem as your Jellyfin media library. Override the default in your NixOS config:

```nix
services.ytdlfin.stagingDir = "/orico/jellyfin/.ytdlfin-staging";
```

## Backup

restic only needs to target `DATA_DIR`. Exclude staging:

```
--exclude "{DATA_DIR}/staging"
```

## Documentation

Full spec and user guide: <https://genebean.github.io/ytdlfin/>
