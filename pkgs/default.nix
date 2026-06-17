{ pkgs, pythonDeps }:
let
  ytdlfin = import ./ytdlfin.nix { inherit pkgs pythonDeps; };
in
{
  inherit ytdlfin;
  container = import ./container.nix {
    inherit pkgs;
    package = ytdlfin;
  };
  check-sri = import ./check-sri.nix { inherit pkgs; };
  run-tests = import ./run-tests.nix { inherit pkgs; };
  docs-serve = import ./docs-serve.nix { inherit pkgs; };
}
