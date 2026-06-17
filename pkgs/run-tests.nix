{ pkgs }:
pkgs.writeScriptBin "ytdlfin-test" ''
  #!${pkgs.bash}/bin/bash
  cd "$(git rev-parse --show-toplevel)"
  exec pytest tests/ --cov=ytdlfin --cov-report=term-missing "$@"
''
