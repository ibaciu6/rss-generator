#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON="${PYTHON:-python3}"

echo "  RSS Feed Generator"
echo "  ──────────────────"
echo "  1) Generate all feeds"
echo "  2) Enrich feeds (TMDb posters + article content)"
echo "  3) Post-process feeds"
echo "  4) Rebuild index"
echo "  5) All (full pipeline)"
read -rp "  Choice [1-5]: " cmd

case $cmd in
  1) PYTHONPATH=. "$PYTHON" scripts/generate_feeds.py ;;
  2) PYTHONPATH=. "$PYTHON" scripts/enrich_feeds.py ;;
  3) PYTHONPATH=. "$PYTHON" scripts/fix_feeds.py ;;
  4) PYTHONPATH=. "$PYTHON" scripts/generate_index.py ;;
  5)
    PYTHONPATH=. "$PYTHON" scripts/generate_feeds.py
    PYTHONPATH=. "$PYTHON" scripts/enrich_feeds.py
    PYTHONPATH=. "$PYTHON" scripts/fix_feeds.py
    PYTHONPATH=. "$PYTHON" scripts/generate_index.py
    ;;
  *) echo "Invalid" ;;
esac
