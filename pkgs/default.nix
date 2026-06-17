{ pkgs, pythonDeps }:
{
  ytdlfin = import ./ytdlfin.nix { inherit pkgs pythonDeps; };
  check-sri = import ./check-sri.nix { inherit pkgs; };
  run-tests = import ./run-tests.nix { inherit pkgs; };
  docs-serve = import ./docs-serve.nix { inherit pkgs; };
}
