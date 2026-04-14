from __future__ import annotations

import sys

from core.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["generate", *sys.argv[1:]]))

