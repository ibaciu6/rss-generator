#!/usr/bin/env python3
"""One-off: split legacy ``config/sites.yaml`` into ``config/sites/<slug>.yaml`` with schedules."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
LEGACY = REPO / "config" / "sites.yaml"
OUT_DIR = REPO / "config" / "sites"

# Staggered crons (subset of previous multi-cron workflow) rotated across sites.
CRONS = [
    "7 */2 * * *",
    "23 1,5,9,13,17,21 * * *",
    "41 */3 * * *",
    "11 */4 * * *",
    "19 */5 * * *",
]


def main() -> None:
    if not LEGACY.is_file():
        raise SystemExit(f"Missing {LEGACY}")
    data = yaml.safe_load(LEGACY.read_text(encoding="utf-8")) or {}
    sites = data.get("sites")
    if not isinstance(sites, dict):
        raise SystemExit("Legacy file has no sites: mapping")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, (name, cfg) in enumerate(sorted(sites.items())):
        blob = dict(cfg)
        if "schedule" not in blob:
            blob["schedule"] = CRONS[i % len(CRONS)]
        text = yaml.safe_dump(blob, sort_keys=False, allow_unicode=True, width=100000)
        header = (
            f"# Site: {name}\n"
            f"# GitHub schedule: {blob['schedule']}\n"
            "# Poster / episode title conventions: see repo README.\n\n"
        )
        (OUT_DIR / f"{name}.yaml").write_text(header + text, encoding="utf-8")
        print("Wrote", OUT_DIR / f"{name}.yaml")
    LEGACY.unlink()
    print("Removed", LEGACY)


if __name__ == "__main__":
    main()
