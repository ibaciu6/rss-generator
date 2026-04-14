#!/usr/bin/env python3
"""
Probe each configured listing URL (HEAD + robots.txt), derive a conservative
hour-step cron, stagger minutes per site, then optionally rewrite ``schedule:`` in YAML.

Dry-run by default. After ``--write``, edit ``.github/workflows/update.yml`` cron
schedules manually if you want CI timing to match the suggestions.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.config import discover_site_yaml_files, load_site_yaml  # noqa: E402
from core.schedule_suggest import probe_site, suggest_cron  # noqa: E402

_SCHEDULE_LINE = re.compile(r"^schedule:\s*.+$", re.MULTILINE)


def _replace_schedule(text: str, new_cron: str) -> str:
    if _SCHEDULE_LINE.search(text):
        return _SCHEDULE_LINE.sub(f"schedule: {new_cron}", text, count=1)
    if text.endswith("\n"):
        return text + f"schedule: {new_cron}\n"
    return text + f"\nschedule: {new_cron}\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Suggest staggered GitHub cron per site from light HTTP probes.")
    p.add_argument("--config", type=Path, default=REPO / "config" / "sites", help="Sites config directory")
    p.add_argument("--write", action="store_true", help="Rewrite schedule: in each site YAML")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed (with --reshuffle, XORs into per-site randomness)",
    )
    p.add_argument(
        "--reshuffle",
        action="store_true",
        help="Use fresh random minutes each run (still unique per site within the run)",
    )
    p.add_argument("--timeout", type=float, default=12.0, help="HTTP probe timeout seconds")
    p.add_argument(
        "--no-render",
        action="store_true",
        help="After --write, skip printing the reminder about update.yml (no-op for compatibility)",
    )
    args = p.parse_args()

    rows: list[tuple[str, int, float, int, str, str]] = []
    updates: list[tuple[Path, str, str]] = []

    for path, bucket in discover_site_yaml_files(args.config):
        site = load_site_yaml(path)
        if bucket is not None:
            site = replace(site, config_bucket=bucket)
        sig = probe_site(site, timeout_s=args.timeout)
        seed: int | None = args.seed
        if args.reshuffle:
            import secrets

            salt = secrets.randbits(32) & 0xFFFFFFFF
            seed = (salt ^ (args.seed & 0xFFFFFFFF)) & 0xFFFFFFFF if args.seed is not None else salt
        cron, step = suggest_cron(site, sig, seed=seed)
        rows.append(
            (
                site.name,
                sig.status_code,
                sig.elapsed_ms,
                step,
                cron,
                path.relative_to(REPO).as_posix(),
            )
        )
        updates.append((path, cron, site.name))

    wname = max(4, len("site"), max((len(r[0]) for r in rows), default=0))
    print(f"{'site':<{wname}}  http  ms      step  cron                path")
    for name, st, ms, step, cron, rel in rows:
        print(f"{name:<{wname}}  {st:>4}  {ms:>6.0f}  {step:>4}  {cron:<18}  {rel}")

    if args.write:
        for path, cron, _ in updates:
            raw = path.read_text(encoding="utf-8")
            path.write_text(_replace_schedule(raw, cron), encoding="utf-8")
            print("Updated", path.relative_to(REPO))
        if not args.no_render:
            print(
                "Note: CI uses monolithic `.github/workflows/update.yml` schedules; "
                "per-site `schedule` in YAML is informational unless you edit `update.yml`."
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
