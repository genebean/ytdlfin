# ytdlfin

Self-hosted video downloader for Jellyfin. Paste a URL, pick a category, and the file
lands in the right Jellyfin directory with NFO metadata and poster art — no manual
file management required. Authentication is handled by PocketID (OIDC) with
group-based access control.

Full documentation: <https://genebean.github.io/ytdlfin/>

## Features

- Any yt-dlp-supported URL (YouTube, Vimeo, Twitch, etc.)
- OIDC authentication via PocketID, with group-based access control
- Download queue visible to all users
- Per-download resolution picker (auto-detected from the URL; defaults to 1080p)
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

See the [spec](https://genebean.github.io/ytdlfin/reference/spec.html) for the full environment
variable reference.

## NixOS deployment

Add ytdlfin as a flake input and include the module in your `nixosConfigurations`:

```nix
# flake.nix
inputs.ytdlfin = {
  url = "github:genebean/ytdlfin";
  inputs.nixpkgs.follows = "nixpkgs";
};

nixosConfigurations.yourhost = nixpkgs.lib.nixosSystem {
  modules = [
    ytdlfin.nixosModules.default
    ./hosts/yourhost/configuration.nix
  ];
};
```

Then configure the service:

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

The `environmentFile` must supply `SECRET_KEY`, `OIDC_CLIENT_ID`, and
`OIDC_CLIENT_SECRET`. See the [spec](https://genebean.github.io/ytdlfin/reference/spec.html)
for all module options, reverse proxy setup, staging directory requirements, and
backup recommendations.

## Bugs and feedback

Open an issue at <https://github.com/genebean/ytdlfin/issues>.
