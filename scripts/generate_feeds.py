from __future__ import annotations

import sys
from pathlib import Path

# Running this file directly puts scripts/ on sys.path instead of the repo
# root, so `from core.cli import main` fails. Put the root first so the
# documented `python scripts/generate_feeds.py` works with or without
# PYTHONPATH, and so a container CMD needs no wrapper.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The import must follow the path fix above.
from core.cli import main

if __name__ == "__main__":
    # Forward argv so `--site <name>` reaches the parser; the sibling
    # onboard_site.py wrapper does the same.
    raise SystemExit(main(["generate", *sys.argv[1:]]))
