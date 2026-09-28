#!/usr/bin/env python3
"""Add `cosmetic-filters` to every article-mode site's removals list.

Done in the config rather than in code so both consumers see it for free:
enrich_feeds reads site.removals, and fix_feeds reads the same field as its
safety net (invariant 18). Wiring it into enrich_feeds alone would leave the
two paths disagreeing -- a description cleaned during enrichment would stay
clean, but one that reached a feed by another route would never be re-cleaned.

Line-based so the file keeps its formatting and key order; a yaml round-trip
would rewrite the whole document and bury a 39-line change in noise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from core.config import load_config
from scripts.enrich_feeds import _ARTICLE_CFG, CATEGORY_ENRICHMENT

MODULE = "cosmetic-filters"
SITES = Path("config/sites.yaml")

SITE_KEY = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$")
REMOVALS_KEY = re.compile(r"^    removals:\s*$")


def main() -> int:
    cfg = load_config(SITES)
    wanted = {
        s.name
        for s in cfg.sites
        if CATEGORY_ENRICHMENT.get(s.category) is _ARTICLE_CFG
        and s.enabled is not False
        and MODULE not in (s.removals or [])
    }
    if not wanted:
        print(f"nothing to do: every article-mode site already lists {MODULE}")
        return 0

    out: list[str] = []
    current: str | None = None
    added: list[str] = []
    created: list[str] = []
    pending: str | None = None  # a wanted site whose removals: key is absent

    def close_pending() -> None:
        """Give a site that had no removals: key one, holding just the module."""
        nonlocal pending
        if pending is None:
            return
        out.append("    removals:")
        out.append(f"    - {MODULE}")
        added.append(pending)
        created.append(pending)
        pending = None

    for line in SITES.read_text(encoding="utf-8").split("\n"):
        m = SITE_KEY.match(line)
        if m:
            close_pending()
            current = m.group(1)
            out.append(line)
            continue
        out.append(line)
        if REMOVALS_KEY.match(line):
            pending = None
            if current in wanted:
                # Prepend, so the generic rules run first and the per-site
                # modules still get their turn on whatever is left.
                out.append(f"    - {MODULE}")
                added.append(current)
            continue
        # A further key at the site's own indent means the block is over, so a
        # site that never declared removals: needs one appended here.
        if current in wanted and re.match(r"^    [A-Za-z_][A-Za-z0-9_.-]*:", line):
            pending = current
    close_pending()

    SITES.write_text("\n".join(out), encoding="utf-8")

    # Verify against the parsed config rather than trusting the text edit.
    check = load_config(SITES)
    have = {s.name for s in check.sites if MODULE in (s.removals or [])}
    missing, extra = wanted - have, added and set(added) - wanted
    if missing or extra:
        print(f"FAILED missing={sorted(missing)} unexpected={sorted(extra)}", file=sys.stderr)
        return 1
    print(f"added {MODULE} to {len(set(added))} article-mode sites "
          f"({len(set(created))} gained a removals: key)")
    print(f"total sites now carrying it: {len(have)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
