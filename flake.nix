{
  description = "ytdlfin — self-hosted video downloader for Jellyfin";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      # Support both common Linux architectures.
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};

      # Python dependencies shared between the package and the dev shell.
      pythonDeps =
        ps: with ps; [
          fastapi
          uvicorn
          aiosqlite
          jinja2
          authlib
          httpx
          itsdangerous
          python-multipart
          yt-dlp
        ];

      mkPackage =
        pkgs:
        pkgs.python3.pkgs.buildPythonApplication {
          pname = "ytdlfin";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = [ pkgs.python3.pkgs.hatchling ];

          dependencies = pythonDeps pkgs.python3.pkgs;

          # ffmpeg must be on PATH for yt-dlp to mux video and audio streams.
          makeWrapperArgs = [ "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg ]}" ];

          meta = {
            description = "Self-hosted video downloader for Jellyfin";
            mainProgram = "ytdlfin";
          };
        };

      mkDevShell =
        pkgs:
        pkgs.mkShell {
          packages = [
            # Full Python environment with all runtime dependencies.
            (pkgs.python3.withPackages pythonDeps)
            # System tools available to yt-dlp at runtime.
            pkgs.ffmpeg
            pkgs.yt-dlp
            # Developer tooling — pre-commit and the hooks it runs locally.
            pkgs.pre-commit
            pkgs.deadnix
            pkgs.nixfmt-tree
          ];

          shellHook = ''
            # Make the local package importable without installing it.
            export PYTHONPATH="$PWD:$PYTHONPATH"
            echo "ytdlfin dev shell ready — run: uvicorn ytdlfin.main:app --reload"
          '';
        };

    in
    {
      packages = forAllSystems (system: {
        default = mkPackage (pkgsFor system);
      });

      # `nix fmt` — format all Nix files in the tree using nixfmt
      formatter = forAllSystems (system: (pkgsFor system).nixfmt-tree);

      devShells = forAllSystems (system: {
        default = mkDevShell (pkgsFor system);
      });

      # Import this module in your NixOS host flake to deploy ytdlfin.
      nixosModules.default = import ./nix/module.nix { inherit self; };

      # Minimal NixOS configuration used by CI to catch module evaluation errors
      # (missing options, type mismatches, callPackage failures) without deploying.
      nixosConfigurations.test = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.default
          {
            services.ytdlfin = {
              enable = true;
              # /dev/null is a valid path for evaluation; secrets are not read during build.
              environmentFile = "/dev/null";
              settings = {
                oidcIssuerUrl = "https://id.example.com";
                # oidcClientId is not a module option — it comes from environmentFile
                oidcRedirectUri = "https://ytdlfin.example.com/auth/callback";
                oidcAdminGroup = "ytdlfin-admins";
                oidcUserGroup = "ytdlfin-users";
              };
            };
            # Minimal stubs required for NixOS evaluation — not a bootable system.
            fileSystems."/" = {
              device = "none";
              fsType = "tmpfs";
            };
            boot.loader.grub.enable = false;
            system.stateVersion = "26.05";
          }
        ];
      };
    };
}
