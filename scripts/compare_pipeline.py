#!/usr/bin/env python3
"""Run the pipeline locally, snapshotting the feeds after every stage, and
compare each stage against the one before it and against what CI published.

A local run that "passes" says very little: the published output is the only
faithful view of what readers get, because a local run sees a different page
variant and a residential IP rather than a datacenter one. But comparing a
local run to the published copy is only useful if you can see *where* the two
diverge, so this keeps a full copy of `feeds/` after each stage and reports,
per feed and per stage, exactly what changed.

    stages:  0 = as found (the published copy, when --pull ran)
             1 = after generate_feeds   2 = after enrich_feeds
             3 = after fix_feeds

Usage:
    PYTHONPATH=. python scripts/compare_pipeline.py --pull          # git first
    PYTHONPATH=. python scripts/compare_pipeline.py --pull --run --report
    PYTHONPATH=. python scripts/compare_pipeline.py --report-only

Everything is written under `--work` (default `logs/pipeline`) and nothing in
`feeds/` is treated as precious: `--run` regenerates it, exactly as CI does.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from itertools import pairwise
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEEDS = REPO / "feeds"
STAGES = ["0-published", "1-generate", "2-enrich", "3-fix"]
STRIP = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def text_len(html: str) -> int:
    return len(WS.sub(" ", TAGS.sub(" ", STRIP.sub(" ", html or ""))).strip())


def load_feed(path: Path) -> dict:
    """A feed reduced to what can be compared: per item, title and body length."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {"items": {}, "error": True}
    channel = root.find("channel")
    items = {}
    for item in (channel if channel is not None else root).findall("item"):
        title = (item.findtext("title") or "").strip()
        body = item.findtext("description") or ""
        enc = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        if enc is not None and enc.text:
            body = enc.text
        items[title] = {
            "chars": text_len(body),
            "imgs": body.count("<img"),
            "embeds": len(re.findall(r"<(iframe|video|audio)[\\s>/]", body, re.I)),
            "shortcodes": len(re.findall(r"\[/?su_[a-z_]+", body, re.I)),
        }
    return {"items": items}


def snapshot(src: Path, dst: Path) -> int:
    if dst.exists():
        shutil.rmtree(dst)
    if not src.exists():
        return 0
    shutil.copytree(src, dst)
    return len(list(dst.glob("*.xml")))


def _local_env() -> dict[str, str]:
    """The environment a local run needs, including the gitignored ``.env``.

    ``start_reader.sh`` sources ``.env`` before running the pipeline, and CI
    injects the same names from secrets. A harness that only forwards
    ``os.environ`` therefore runs a *different* pipeline than the one the
    project ships: with no ``TMDB_API_KEY`` every streaming feed is silently
    skipped, and a comparison then reports "the local build lost the posters"
    when nothing was broken at all. Parse ``.env`` here rather than sourcing
    it, so no value is ever echoed or executed.
    """
    env = {**os.environ, "PYTHONPATH": "."}
    dotenv = REPO / ".env"
    if not dotenv.is_file():
        return env
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if name and name not in os.environ:
            env[name] = value
    return env


def run_step(name: str, extra: list[str], log: Path) -> tuple[int, str]:
    cmd = [sys.executable, f"scripts/{name}", *extra]
    proc = subprocess.run(
        cmd, cwd=REPO, capture_output=True, text=True, env=_local_env()
    )
    tail = "\n".join((proc.stdout or "").splitlines()[-12:])
    log.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    return proc.returncode, tail


# Enrichment skips a whole feed with `items=0` and no error when a key it
# needs is absent, so a missing TMDB_API_KEY looks exactly like "nothing
# needed enriching". The published copy always has the key, so a local run
# without it produces a diff full of phantom losses. Called out here rather
# than left for the reader to infer from the item counts.
_KEYLESS = re.compile(r"items=0\s*$", re.M)


def check_enrich_log(log: Path) -> list[str]:
    """Feeds enrich skipped outright, and whether the cause is a missing key."""
    if not log.is_file():
        return []
    text = log.read_text(encoding="utf-8", errors="replace")
    skipped = [
        m.group(1)
        for m in re.finditer(r"\[\d+/\d+\]\s+(\S+)\s+\([^)]*\).*?items=0\s*$", text, re.M)
    ]
    if skipped:
        key = "TMDB_API_KEY" if not os.environ.get("TMDB_API_KEY") else None
        if not key:
            key = "TMDB_API_KEY"
        print(
            f"   !! {len(skipped)} feed(s) skipped as keyless ({', '.join(skipped[:6])}"
            f"{'...' if len(skipped) > 6 else ''}) -- is TMDB_API_KEY in .env?"
        )
    return skipped


def describe(a: dict, b: dict) -> tuple[str, int, int, int, int, int]:
    """``(verdict, grew, shrank, item_delta, chars_before, chars_after)``.

    ``chars_*`` is summed over the items the two snapshots share, so a feed
    that gained four fresh headlines is not reported as if it lost 40% of its
    text. Judging a stage by total length alone is what makes a comparison
    unreadable: every run adds and drops items, and the totals move for reasons
    that have nothing to do with the stage under test.
    """
    ai, bi = a.get("items", {}), b.get("items", {})
    grew = shrank = 0
    chars_a = chars_b = 0
    for title in ai.keys() & bi.keys():
        chars_a += ai[title]["chars"]
        chars_b += bi[title]["chars"]
        if bi[title]["chars"] > ai[title]["chars"]:
            grew += 1
        elif bi[title]["chars"] < ai[title]["chars"]:
            shrank += 1
    delta = len(bi) - len(ai)
    verdict = "same" if (grew, shrank, delta) == (0, 0, 0) else "changed"
    return verdict, grew, shrank, delta, chars_a, chars_b


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default="logs/pipeline")
    ap.add_argument("--pull", action="store_true", help="fetch the CI-published feeds first")
    ap.add_argument("--run", action="store_true", help="run generate/enrich/fix")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--report", action="store_true", help="print the stage-by-stage diff")
    ap.add_argument("--sites", nargs="*", help="restrict the run to these feed files")
    args = ap.parse_args(argv)

    work = REPO / args.work
    work.mkdir(parents=True, exist_ok=True)

    if args.pull:
        print("== pulling the CI-published feeds ==")
        rc, tail = run_step("pull_published_feeds.py", [], work / "pull.log")
        print(f"   rc={rc} {tail.strip().splitlines()[-1] if tail.strip() else ''}")

    if args.run:
        # The baseline is the one snapshot that cannot be regenerated from
        # feeds/: it only exists because something pulled it off the deployed
        # site. A second `--run` without `--pull` would quietly overwrite it
        # with the local build, and every later comparison would be the local
        # build against itself -- reported as agreement. Refuse instead.
        if (work / STAGES[0]).is_dir() and not args.pull:
            print(
                f"refusing to run: {work / STAGES[0]} already exists but --pull was not\n"
                f"  given, so it is the deployed copy and this run would overwrite it.\n"
                f"  Re-pull it, or use --work <other dir> for a run that has no baseline."
            )
            return 2
        snapshot(FEEDS, work / STAGES[0])
        # Recorded, not assumed: stage 0 is the *published* copy only when a
        # pull happened in this invocation. Reading a leftover local snapshot as
        # "what GitHub Actions produced" is the one mistake this tool exists to
        # prevent, so the label follows the facts rather than the filename.
        (work / "provenance.json").write_text(
            json.dumps(
                {
                    "stage_0": "published" if args.pull else "whatever was in feeds/",
                    "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%S") if args.pull else None,
                    "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sites": args.sites or "all",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        for name, stage, extra in (
            ("generate_feeds.py", STAGES[1], []),
            ("enrich_feeds.py", STAGES[2], []),
            ("fix_feeds.py", STAGES[3], []),
        ):
            step_args = list(extra)
            if args.sites:
                step_args += ["--site", *args.sites]
            print(f"== {name} ==")
            stage_log = work / f"{stage}.log"
            rc, tail = run_step(name, step_args, stage_log)
            print(f"   rc={rc}")
            for line in tail.splitlines()[-4:]:
                print(f"   {line}")
            if name == "enrich_feeds.py":
                check_enrich_log(stage_log)
            snapshot(FEEDS, work / stage)

    if args.report or not (args.run or args.pull):
        dirs = [work / s for s in STAGES if (work / s).is_dir()]
        if len(dirs) < 2:
            print(f"not enough snapshots in {work}/ to compare")
            return 1
        prov = {}
        if (work / "provenance.json").is_file():
            prov = json.loads((work / "provenance.json").read_text(encoding="utf-8"))
        if prov:
            print(
                f"\nrun {prov.get('run_at')} | stage 0 = {prov.get('stage_0')}"
                f" | scope = {prov.get('sites')}"
            )
        print()
        print(f'{"stage":14} {"feeds":>6} {"items":>6} {"chars":>9} {"imgs":>6} {"embeds":>7} {"shortcodes":>11}')
        print("-" * 74)
        for d in dirs:
            feeds = sorted(d.glob("*.xml"))
            totals = Counter()
            for f in feeds:
                snap = load_feed(f)
                for v in snap.get("items", {}).values():
                    totals["items"] += 1
                    totals["chars"] += v["chars"]
                    totals["imgs"] += v["imgs"]
                    totals["embeds"] += v["embeds"]
                    totals["shortcodes"] += v["shortcodes"]
            print(
                f"{d.name:14} {len(feeds):6} {totals['items']:6} {totals['chars']:9} "
                f"{totals['imgs']:6} {totals['embeds']:7} {totals['shortcodes']:11}"
            )

        print()
        for a, b in pairwise(dirs):
            print(f"== {a.name} -> {b.name} ==")
            changed = []
            for fa in sorted(a.glob("*.xml")):
                fb = b / fa.name
                sa, sb = load_feed(fa), load_feed(fb if fb.exists() else Path("/nonexistent"))
                if not fb.exists():
                    print(f"   {fa.stem:36} GONE")
                    continue
                verdict, grew, shrank, delta, ca, cb = describe(sa, sb)
                if verdict != "same":
                    changed.append((fa.stem, grew, shrank, delta, ca, cb))
            for stem, grew, shrank, delta, ca, cb in changed:
                pct = f"{cb*100//max(1,ca)}%" if ca else "-"
                print(
                    f"   {stem:36} {grew:3} fuller {shrank:3} shorter "
                    f"{delta:+4} items  chars {ca} -> {cb} ({pct})"
                )
            if not changed:
                print("   nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
