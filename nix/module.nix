{ self }:

{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.ytdlfin;
  # Always use the package built from the same flake revision as the module.
  package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
in
{
  options.services.ytdlfin = {

    enable = lib.mkEnableOption "ytdlfin video download service";

    port = lib.mkOption {
      type = lib.types.port;
      default = 8001;
      description = "HTTP port to listen on.";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/ytdlfin";
      description = ''
        Directory for the SQLite database (ytdlfin.db).
        This is the only path restic needs to back up.
        Exclude the staging subdirectory: --exclude "{dataDir}/staging".
      '';
    };

    stagingDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/ytdlfin/staging";
      description = ''
        Temporary directory for in-progress downloads.
        For atomic moves, keep this on the same filesystem as your Jellyfin
        library. On a system where the library lives under /orico/jellyfin,
        set this to /orico/jellyfin/.ytdlfin-staging instead of the default.
        This directory is ephemeral — do not back it up.
      '';
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "ytdlfin";
      description = ''
        User to run the service as. Set to config.services.jellyfin.user to
        share file ownership with an existing Jellyfin installation and avoid
        permission issues writing to media library directories.
      '';
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "ytdlfin";
      description = ''
        Group to run the service as. Set to config.services.jellyfin.group
        if running alongside Jellyfin.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.path;
      description = ''
        Path to a file containing secrets as KEY=VALUE pairs (loaded by
        systemd as EnvironmentFile). Must include:
          SECRET_KEY=<32+ random chars for session signing>
          OIDC_CLIENT_ID=<OIDC client ID from PocketID>
          OIDC_CLIENT_SECRET=<OIDC client secret from PocketID>
      '';
    };

    settings = {
      oidcIssuerUrl = lib.mkOption {
        type = lib.types.str;
        description = "PocketID base URL, e.g. https://id.example.com";
      };

      oidcRedirectUri = lib.mkOption {
        type = lib.types.str;
        description = "Full OIDC callback URL, e.g. https://ytdlfin.example.com/auth/callback";
      };

      mediaDirectories = lib.mkOption {
        type = lib.types.listOf lib.types.path;
        default = [ ];
        description = ''
          Filesystem paths that ytdlfin needs write access to (i.e. your
          Jellyfin media library root(s)). These are added to systemd's
          ReadWritePaths so the service can move completed downloads into
          the correct category directories.

          Example: [ "/orico/jellyfin/data" ]
        '';
      };

      oidcAdminGroup = lib.mkOption {
        type = lib.types.str;
        description = ''
          PocketID group name whose members receive admin privileges.
          Members can manage categories and see all users' download history.
          Must match the group name exactly as configured in PocketID.
        '';
      };

      oidcUserGroup = lib.mkOption {
        type = lib.types.str;
        description = ''
          PocketID group name whose members have regular user access.
          Members can submit downloads and view their own history.
          Must match the group name exactly as configured in PocketID.
        '';
      };

      logLevel = lib.mkOption {
        type = lib.types.enum [
          "debug"
          "info"
          "warning"
          "error"
        ];
        default = "info";
        description = "uvicorn log level.";
      };

      trustedProxyIps = lib.mkOption {
        type = lib.types.str;
        default = "127.0.0.1";
        description = ''
          Comma-separated list of upstream proxy IPs allowed to set
          X-Forwarded-For and X-Forwarded-Proto headers, or "*" to trust all.
          Defaults to "127.0.0.1" for the standard same-host nginx deployment.
          Set to "*" to trust all proxies (acceptable for isolated LAN
          deployments behind a physical firewall).
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {

    # Create the service user/group unless the operator has overridden them to
    # use an existing account (e.g. the jellyfin user).
    users.users.${cfg.user} = lib.mkIf (cfg.user == "ytdlfin") {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.dataDir;
      description = "ytdlfin service user";
    };

    users.groups.${cfg.group} = lib.mkIf (cfg.group == "ytdlfin") { };

    systemd.tmpfiles.rules = [
      # Create dataDir and stagingDir with correct ownership before the service starts.
      "d '${cfg.dataDir}'    0750 ${cfg.user} ${cfg.group} - -"
      "d '${cfg.stagingDir}' 0750 ${cfg.user} ${cfg.group} - -"
    ];

    systemd.services.ytdlfin = {
      description = "ytdlfin video download service";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      serviceConfig = {
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.dataDir;
        EnvironmentFile = cfg.environmentFile;
        ExecStart = "${package}/bin/ytdlfin";
        Restart = "on-failure";
        RestartSec = "5s";

        # Harden the service — ytdlfin only needs access to its data and
        # library directories, not the broader filesystem.
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        # dataDir and stagingDir are always writable; add media library paths too.
        ReadWritePaths = [
          cfg.dataDir
          cfg.stagingDir
        ]
        ++ cfg.settings.mediaDirectories;
        PrivateTmp = true;
      };

      environment = {
        DATA_DIR = cfg.dataDir;
        STAGING_DIR = cfg.stagingDir;
        PORT = toString cfg.port;
        OIDC_ISSUER_URL = cfg.settings.oidcIssuerUrl;
        # OIDC_CLIENT_ID comes from environmentFile — not set here.
        OIDC_REDIRECT_URI = cfg.settings.oidcRedirectUri;
        ADMIN_GROUP = cfg.settings.oidcAdminGroup;
        USER_GROUP = cfg.settings.oidcUserGroup;
        LOG_LEVEL = cfg.settings.logLevel;
        MEDIA_DIRECTORIES = lib.concatStringsSep ":" (map toString cfg.settings.mediaDirectories);
        # Always true when deployed via NixOS module — the service is expected
        # to sit behind an HTTPS reverse proxy (e.g. nginx). Marks session
        # cookies Secure and enables correct scheme detection via proxy headers.
        HTTPS_ONLY = "true";
        TRUSTED_PROXY_IPS = cfg.settings.trustedProxyIps;
      };
    };
  };
}
