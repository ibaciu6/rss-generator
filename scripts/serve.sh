#!/usr/bin/env bash
set -euo pipefail

PORT=${1:-8080}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Kill any previous server on the same port
lsof -ti "tcp:$PORT" 2>/dev/null | xargs -r kill 2>/dev/null || true

# Run feed post-processing fixes
cd "$ROOT"
PYTHONPATH=. python3 scripts/enrich_posters.py 2>&1 || true
PYTHONPATH=. python3 scripts/fix_feeds.py 2>&1

echo ""
echo "Serving RSS feeds at http://localhost:$PORT"
echo "Open browser: http://localhost:$PORT/reader.html"
echo ""

exec python3 -m http.server "$PORT" --bind 0.0.0.0
