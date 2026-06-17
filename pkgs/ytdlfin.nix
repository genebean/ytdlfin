{ pkgs, pythonDeps }:
pkgs.python3.pkgs.buildPythonApplication {
  pname = "ytdlfin";
  version = "0.1.0";
  pyproject = true;

  src = ../.;

  build-system = [ pkgs.python3.pkgs.hatchling ];

  dependencies = pythonDeps pkgs.python3.pkgs;

  # ffmpeg must be on PATH for yt-dlp to mux video and audio streams.
  makeWrapperArgs = [ "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg ]}" ];

  meta = {
    description = "Self-hosted video downloader for Jellyfin";
    mainProgram = "ytdlfin";
  };
}
