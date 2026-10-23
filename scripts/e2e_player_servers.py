#!/usr/bin/env python3
"""End-to-end check of the player_servers feature against a live TMDb key.

Verifies the three-way contract:
  - None   → per-category defaults (cinesrc + vidsrc + vidlink for torrents)
  - []     → disable entirely for this feed
  - [...]  → explicit override
  - bad id → ValueError, not silent fallback

Generates its own un-enriched feed, so the committed feeds -- which are
already shipped with player links -- cannot mask the behaviour via the
idempotency guard.
"""
from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PLAYER_LABELS = ("CineSrc", "VidSrc", "VidLink")

CLEAN_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com/</link>
    <description>Test</description>
    <item>
      <title>Some.Movie.2026.1080p.WEB-DL</title>
      <link>https://example.com/torrent/1</link>
      <description>&lt;strong&gt;Size:&lt;/strong&gt; 2.1 GB</description>
    </item>
  </channel>
</rss>
"""


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def player_counts(description: str) -> dict[str, int]:
    return {label: description.count(f">{label}</b>") for label in PLAYER_LABELS}


def write_clean(src: Path) -> None:
    src.write_text(CLEAN_FEED, encoding="utf-8")


def run_one(src: Path, *, player_servers: tuple[str, ...] | None):
    """Call process_feed on src (in place), return (changed, stats, desc)."""
    from core.tmdb import MovieInfo
    from scripts.enrichers import streaming_enricher as se

    for k, v in load_env(REPO / ".env").items():
        os.environ.setdefault(k, v)

    info = MovieInfo(
        poster_url="https://image.tmdb.org/t/p/w342/abc.jpg",
        title="Some.Movie.2026.1080p.WEB-DL",
        year="2026",
        tmdb_id=693134,  # Dune: Part Two -- real, always resolves
        media_type="movie",
        imdb_id="tt15239678",
    )

    with (
        patch.object(se, "movie_lookup", return_value=info),
        patch.object(se, "tv_lookup", return_value=None),
        patch.object(se, "find_by_imdb", return_value=None),
        patch.object(se, "search_movie", return_value=info),
        patch.object(se, "search_tv", return_value=None),
    ):
        changed, stats = se.process_feed(
            src,
            epguides_mapping={},
            feed_category="torrents",
            keep_titles=True,
            player_servers=player_servers,
        )
    desc = ET.parse(src).getroot().find("channel").find("item").find("description").text or ""
    return changed, stats, desc


def main() -> int:
    """Run the e2e assertions."""
    errors: list[str] = []

    print("=== 1. default (None) → cinesrc + vidsrc + vidlink ===")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tf:
        src = Path(tf.name)
    try:
        write_clean(src)
        _, stats, desc = run_one(src, player_servers=None)
        print("   stats:", stats)
        print("   labels:", player_counts(desc))
        if not all(player_counts(desc)[l] == 1 for l in PLAYER_LABELS):
            errors.append(f"[1] expected all 3 labels, got {player_counts(desc)}")
        # DEFAULT_TORRENT_SERVERS order: cinesrc, vidsrc, vidlink
        order = [desc.index(f">{label}</b>") for label in ("CineSrc", "VidSrc", "VidLink")]
        if order != sorted(order):
            errors.append(f"[1] player links not in DEFAULT_TORRENT_SERVERS order: {order}")
    finally:
        os.unlink(src)

    print("\n=== 2. player_servers=[] → disabled ===")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tf:
        src = Path(tf.name)
    try:
        write_clean(src)
        _, stats, desc = run_one(src, player_servers=())
        print("   stats:", stats)
        print("   labels:", player_counts(desc))
        if not all(player_counts(desc)[l] == 0 for l in PLAYER_LABELS):
            errors.append(f"[2] expected no player labels, got {player_counts(desc)}")
    finally:
        os.unlink(src)

    print("\n=== 3. player_servers=[cinesrc] → CineSrc only ===")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tf:
        src = Path(tf.name)
    try:
        write_clean(src)
        _, stats, desc = run_one(src, player_servers=("cinesrc",))
        print("   stats:", stats)
        print("   labels:", player_counts(desc))
        if player_counts(desc)["CineSrc"] != 1:
            errors.append(f"[3] CineSrc missing: {player_counts(desc)}")
        if not all(player_counts(desc)[l] == 0 for l in ("VidSrc", "VidLink")):
            errors.append(f"[3] VidSrc/VidLink leaked: {player_counts(desc)}")
    finally:
        os.unlink(src)

    print("\n=== 4. bad server id → ValueError, not a silent fallback ===")
    try:
        from core.config import SiteConfig

        SiteConfig(
            name="test",
            url="https://example.com/",
            method="rss",
            item_selector="//item",
            title_selector="title",
            link_selector="link",
            feed_file="test.xml",
            player_servers=("cinesrc", "nosuchserver"),
        )
        errors.append("[4] nosuchserver should have raised ValueError")
    except ValueError as e:
        if "nosuchserver" not in str(e):
            errors.append(f"[4] ValueError raised but wrong message: {e}")
        print("   raised ValueError:", str(e)[:120], "✓")

    print("\n=== 5. vidsrc prefers IMDb id over TMDB id ===")
    from scripts.enrichers.player_servers import build_player_link

    link = build_player_link("vidsrc", 999999, "tt9999999", "movie", "")
    print("   ", link.replace(' target="_blank" rel="noopener noreferrer">', "…")[:80] if link else None)
    if link is None:
        errors.append("[5] vidsrc link was None with imdb_id set")
    elif "https://vidsrc.to/embed/movie/tt" not in link:
        errors.append(f"[5] vidsrc should use IMDb id in URL: {link}")

    print("\n=== 6. vidlink is TMDB-only, ignores IMDb id ===")
    link = build_player_link("vidlink", 12345, "tt9999999", "movie", "")
    print("   ", link.replace(' target="_blank" rel="noopener noreferrer">', "…")[:80] if link else None)
    if link is None:
        errors.append("[6] vidlink link was None with tmdb_id set")
    elif "tt9999999" in link:
        errors.append(f"[6] vidlink wrongly used IMDb id (TMDB-only server): {link}")

    print("\n=== 7. guard tags unique across all 7 servers ===")
    from scripts.enrichers.player_servers import PLAYER_SERVERS

    guards = {PLAYER_SERVERS[s].guard for s in PLAYER_SERVERS}
    print(f"   {len(guards)} unique guards for {len(PLAYER_SERVERS)} servers")
    if len(guards) != len(PLAYER_SERVERS):
        errors.append("[7] duplicate guard tags detected across servers")

    print("\n=== 8. TV items without episode marker skip servers with no series url ===")
    for sid in ("vidlink", "vidnest", "vidfast"):
        result = build_player_link(sid, 211288, None, "tv", "")
        if result is not None:
            errors.append(f"[8] {sid} produced a link for episode-less TV: {result}")
        print(f"   {sid:10s} -> {result}")

    print("\n=== 9. [] in sites.yaml survives load as (), not collapsed to None ===")
    import core.config as config_mod

    text = REPO.joinpath("config/sites.yaml").read_text(encoding="utf-8")
    patched = text.replace("    feed_file: uindex-movies.xml",
                           "    player_servers: []\n    feed_file: uindex-movies.xml", 1)
    tmp_cfg = Path(tempfile.mkdtemp()) / "sites.yaml"
    tmp_cfg.write_text(patched, encoding="utf-8")
    try:
        conf = config_mod.load_config(tmp_cfg)
        site = next((s for s in conf.sites if s.name == "uindex-movies"), None)
        if site is None:
            errors.append("[9] uindex-movies missing from loaded config")
        elif site.player_servers != ():
            errors.append(f"[9] [] collapsed to default instead of (): {site.player_servers!r}")
        else:
            print(f"   [] loaded as {site.player_servers!r} (not None → not defaults) ✓")
    finally:
        tmp_cfg.unlink()

    print("\n=== 10. [] survives the full CLI path (load → main() → process_feed) ===")
    import core.config as config_mod

    text = REPO.joinpath("config/sites.yaml").read_text(encoding="utf-8")
    patched = text.replace("    feed_file: uindex-movies.xml",
                           "    player_servers: []\n    feed_file: uindex-movies.xml", 1)
    tmp_cfg = Path(tempfile.mkdtemp()) / "sites.yaml"
    tmp_cfg.write_text(patched, encoding="utf-8")
    try:
        conf = config_mod.load_config(tmp_cfg)
        ps = next(
            (tuple(s.player_servers) for s in conf.sites if s.feed_file == "uindex-movies.xml"),
            None,
        )
        print("   CLI path passes player_servers =", ps)
        if ps != ():
            errors.append("[10] [] dropped by the site → feed_config step")
    finally:
        tmp_cfg.unlink()

    print()
    if errors:
        print("FAILURES:")
        for e in errors:
            print("  -", e)
        return 1
    print("All e2e assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())