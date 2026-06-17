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

      # Dev-only Python dependencies (not included in the production package).
      devPythonDeps =
        ps: with ps; [
          pytest
          pytest-cov
        ];

      ourPkgs =
        system:
        import ./pkgs {
          pkgs = pkgsFor system;
          inherit pythonDeps;
        };

      mkDevShell =
        pkgs:
        let
          p = ourPkgs pkgs.system;
        in
        pkgs.mkShell {
          packages = [
            # Full Python environment with all runtime + dev dependencies.
            (pkgs.python3.withPackages (ps: pythonDeps ps ++ devPythonDeps ps))
            # System tools available to yt-dlp at runtime.
            pkgs.ffmpeg
            pkgs.yt-dlp
            # Developer tooling — pre-commit and the hooks it runs locally.
            pkgs.pre-commit
            pkgs.deadnix
            pkgs.nixfmt-tree
            # Image tools for resizing docs/static assets.
            pkgs.imagemagick
            p.run-tests
            p.docs-serve
            p.check-sri
          ];

          shellHook = ''
            # Make the local package importable without installing it.
            export PYTHONPATH="$PWD:$PYTHONPATH"
            docs-serve() { ytdlfin-docs-serve "$@"; }
            test() { ytdlfin-test "$@"; }
            if [[ $- == *i* ]]; then
              echo "ytdlfin dev shell"
              echo ""
              echo "  test [pytest-args]                      run the test suite"
              echo "  docs-serve [port]                       serve docs on http://localhost:4000"
              echo "  ytdlfin-check-sri                       verify CDN SRI hashes in HTML files"
              echo "  uvicorn ytdlfin.main:app --reload       start the app"
            fi
          '';
        };

    in
    {
      packages = forAllSystems (system: {
        default = (ourPkgs system).ytdlfin;
        check-sri = (ourPkgs system).check-sri;
        container = (ourPkgs system).container;
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
