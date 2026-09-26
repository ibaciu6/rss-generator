#!/usr/bin/env bash
set -euo pipefail

PORT=${1:-8080}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Kill any previous server on the same port
lsof -ti "tcp:$PORT" 2>/dev/null | xargs -r kill 2>/dev/null || true

echo ""
echo "Serving the static site at http://localhost:$PORT"
echo "Open browser: http://localhost:$PORT/index.html"
echo "Note: the interactive reader is a separate server - use ./scripts/start_reader.sh"
echo ""

exec python3 -m http.server "$PORT" --bind 0.0.0.0 --directory "$ROOT"
