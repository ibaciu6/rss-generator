#!/usr/bin/env python3
from __future__ import annotations

import sys

from core.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["onboard-site", *sys.argv[1:]]))
