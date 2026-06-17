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

      mkCheckSri =
        pkgs:
        pkgs.writeScriptBin "ytdlfin-check-sri" ''
          #!${pkgs.python3}/bin/python3
          import re, sys, hashlib, base64, urllib.request, subprocess
          from pathlib import Path

          def sri(url):
              with urllib.request.urlopen(url, timeout=15) as r:
                  return "sha384-" + base64.b64encode(hashlib.sha384(r.read()).digest()).decode()

          root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
          failed = False
          for f in sorted(root.rglob("*.html")):
              if ".git" in f.parts:
                  continue
              tags = re.findall(r'<(?:script|link)\b[^>]*>', f.read_text(), re.DOTALL | re.IGNORECASE)
              for tag in tags:
                  url_m = re.search(r'(?:src|href)=["\'](?P<url>https://cdn\.jsdelivr\.net/[^"\']+)["\']', tag)
                  hash_m = re.search(r'integrity=["\'](?P<hash>sha384-[^"\']+)["\']', tag)
                  if not url_m or not hash_m:
                      continue
                  url, expected = url_m.group("url"), hash_m.group("hash")
                  actual = sri(url)
                  if actual == expected:
                      print(f"OK   {f.relative_to(root)}: {url}")
                  else:
                      print(f"FAIL {f.relative_to(root)}: {url}")
                      print(f"     expected: {expected}")
                      print(f"     actual:   {actual}")
                      failed = True

          sys.exit(1 if failed else 0)
        '';

      mkDevShell =
        pkgs:
        let
          sriCheck = mkCheckSri pkgs;
          runTests = pkgs.writeScriptBin "ytdlfin-test" ''
            #!${pkgs.bash}/bin/bash
            cd "$(git rev-parse --show-toplevel)"
            exec pytest tests/ --cov=ytdlfin --cov-report=term-missing "$@"
          '';
          docsServe = pkgs.writeScriptBin "ytdlfin-docs-serve" ''
            #!${pkgs.python3}/bin/python3
            import http.server, os, subprocess, sys

            port = int(sys.argv[1]) if len(sys.argv) > 1 else 4000

            proj_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
            os.chdir(os.path.join(proj_root, "docs"))

            class QuietHTTPServer(http.server.ThreadingHTTPServer):
                def handle_error(self, request, client_address):
                    if sys.exc_info()[0] in (BrokenPipeError, ConnectionResetError):
                        return
                    super().handle_error(request, client_address)

            print(f"Docs available at http://localhost:{port}")
            try:
                with QuietHTTPServer(("0.0.0.0", port), http.server.SimpleHTTPRequestHandler) as httpd:
                    httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nStopped.")
          '';
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
            runTests
            docsServe
            sriCheck
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
        default = mkPackage (pkgsFor system);
        check-sri = mkCheckSri (pkgsFor system);
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
