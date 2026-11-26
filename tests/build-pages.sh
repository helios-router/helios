#!/usr/bin/env bash
# Build the static Photosphere demo (UI + mock backend + catalogs, sample data
# only) into a directory, ready for GitHub Pages:
#   tests/build-pages.sh <out-dir>
# The gh-pages branch holds exactly this output. To refresh it:
#   tests/build-pages.sh /tmp/pages && cd <gh-pages worktree> && rsync -a --delete --exclude .git /tmp/pages/ ./
set -euo pipefail

out="${1:?usage: $0 <out-dir>}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$out"

# Real files, not symlinks: Pages serves what's committed
rm -rf "$out/photosphere" "$out/base1"
cp -r "$repo/pkgs/photosphere" "$out/photosphere"
cp -r "$repo/tests/mock/base1" "$out/base1"
for f in networks profiles services logwatch-models drivers; do
  cp "$repo/pkgs/$f.json" "$out/$f.json"
done

# Say clearly that this is a demo with made-up data
banner='<div id="demo-banner" style="position:fixed;left:0;right:0;bottom:0;z-index:99;padding:6px 12px;font:13px system-ui,sans-serif;text-align:center;background:#f2a600;color:#1b1b1b">HeliOS demo · everything here is sample data and nothing is saved · <a href="https://github.com/helios-router/helios" style="color:inherit">source</a></div>'
python3 - "$out/photosphere/index.html" "$banner" <<'PY'
import sys
path, banner = sys.argv[1], sys.argv[2]
html = open(path).read()
if 'id="demo-banner"' not in html:
    html = html.replace("</body>", banner + "\n</body>", 1)
open(path, "w").write(html)
PY

# Landing page → the demo; no Jekyll processing
cat > "$out/index.html" <<'HTML'
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>HeliOS demo</title>
<meta http-equiv="refresh" content="0; url=photosphere/index.html#overview">
</head><body><p><a href="photosphere/index.html#overview">Open the HeliOS demo</a></p></body></html>
HTML
touch "$out/.nojekyll"
cat > "$out/README.md" <<'MD'
# HeliOS demo (GitHub Pages)

The Photosphere web UI running against a mock backend with sample data, so
anyone can click through it. Built by `tests/build-pages.sh` from the main
branch; don't edit this branch by hand.
MD
echo "Built demo in $out"
