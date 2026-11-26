#!/usr/bin/env bash
# Serve the Photosphere UI against the mock Cockpit backend (tests/mock/base1),
# so you can click through it in a normal browser. Nothing touches a router.
#   tests/demo.sh [port]          127.0.0.1 only (default port 8765)
#   tests/demo.sh [port] --lan    also reachable from other devices, e.g. your phone
# The demo is all fake data and serves only the UI files, but --lan does make it
# visible to everything on your network while it runs.
set -euo pipefail

port=8765 bind=127.0.0.1
for arg in "$@"; do
  case "$arg" in
    --lan) bind=0.0.0.0 ;;
    ''|*[!0-9]*) echo "usage: $0 [port] [--lan]" >&2; exit 2 ;;
    *) port="$arg" ;;
  esac
done
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
web="$(mktemp -d)"
trap 'rm -rf "$web"' EXIT

ln -s "$repo/pkgs/photosphere" "$web/photosphere"
ln -s "$repo/tests/mock/base1" "$web/base1"
# The mock reads the real catalogs
for f in networks profiles services logwatch-models drivers; do
  ln -s "$repo/pkgs/$f.json" "$web/$f.json"
done

echo "Photosphere demo: http://127.0.0.1:$port/photosphere/index.html"
if [ "$bind" = 0.0.0.0 ]; then
  for ip in $(hostname -I 2>/dev/null || ip -4 -o addr show scope global | awk '{sub("/.*", "", $4); print $4}'); do
    case "$ip" in *:*) continue ;; esac
    echo "  on your phone:   http://$ip:$port/photosphere/index.html"
  done
  echo "  (if it doesn't load, allow TCP $port in this computer's firewall)"
fi
echo "Tabs: #overview #devices #networks #filtering #printing #hardware #remote #traffic #qos #upnp #notifications #history #advanced"
echo "Advanced tabs show after the gear button at the top right.  Ctrl-C to stop."
cd "$web"
# Like `python3 -m http.server`, but never cached, so the browser always shows the current files
python3 - "$port" "$bind" <<'PY'
import http.server, sys
class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
port, bind = int(sys.argv[1]), sys.argv[2]
print(f"Serving on {bind}:{port}", flush=True)
http.server.ThreadingHTTPServer((bind, port), NoCache).serve_forever()
PY
