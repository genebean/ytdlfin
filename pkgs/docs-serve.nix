{ pkgs }:
pkgs.writeScriptBin "ytdlfin-docs-serve" ''
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
''
