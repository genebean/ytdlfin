{ pkgs }:
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
''
