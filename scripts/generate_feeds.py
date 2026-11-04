from __future__ import annotations

import sys
from pathlib import Path

# Load environment variables from a .env file in the repository root, if present.
# This enables feeds that require authentication (e.g., TVmaze personal iCal) to
# receive their token without the user having to export it manually.
repo_root = Path(__file__).resolve().parents[1]
env_path = repo_root / ".env"
if env_path.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except Exception:
        # If python-dotenv is not installed, silently ignore – the user can set
        # the env vars manually. The rest of the script works without it.
        pass

# Running this file directly puts scripts/ on sys.path instead of the repo
# root, so `from core.cli import main` fails. Put the root first so the
# documented `python scripts/generate_feeds.py` works with or without
# PYTHONPATH, and so a container CMD needs no wrapper.
sys.path.insert(0, str(repo_root))

# The import must follow the path fix above.
from core.cli import main  # noqa: E402

if __name__ == "__main__":
    # Forward argv so `--site <name>` reaches the parser; the sibling
    # onboard_site.py wrapper does the same.
    raise SystemExit(main(["generate", *sys.argv[1:]]))
