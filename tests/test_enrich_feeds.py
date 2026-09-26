"""Tests for the enrichment orchestrator (scripts/enrich_feeds.py).

Covers the category -> mode routing table and the per-feed dispatch loop,
including the regressions fixed when the old enrich_posters.py monolith was
replaced by the enrichers/ package.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import enrich_feeds as ef
from scripts.enrich_feeds import (
    CATEGORY_ENRICHMENT,
    _base_url,
    _get_category_enrichment_config,
    _resolve_mode,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DISPATCHED_MODES = {"streaming", "article", "none"}


def _write_feed(path: Path, items: int = 2) -> Path:
    """Write a minimal RSS feed with `items` items."""
    body = "".join(
        f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
        for i in range(items)
    )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<rss version=\"2.0\"><channel><title>t</title>{body}</channel></rss>",
        encoding="utf-8",
    )
    return path


def _site(feed_file: str, category: str, kind: str | None = None, **kw) -> SimpleNamespace:
    return SimpleNamespace(
        feed_file=feed_file,
        category=category,
        kind=kind,
        url=kw.get("url", "https://example.com/feed"),
        enhance_mode=kw.get("enhance_mode"),
        detail_article_selector=kw.get("detail_article_selector"),
        ad_selectors=kw.get("ad_selectors", []),
    )


# --------------------------------------------------------------------------
# routing table
# --------------------------------------------------------------------------


class TestCategoryRouting:
    def test_every_category_in_sites_yaml_is_mapped_explicitly(self):
        """No site should silently fall through to the default mode."""
        from core.config import load_config

        cfg = load_config(REPO_ROOT / "config" / "sites.yaml")
        unmapped = sorted({s.category for s in cfg.sites if s.category not in CATEGORY_ENRICHMENT})
        assert unmapped == [], f"categories missing from CATEGORY_ENRICHMENT: {unmapped}"

    def test_every_configured_mode_is_actually_dispatched(self):
        """Guards against the old `catalog`/`torrent` modes, which nothing handled."""
        modes = {cfg["mode"] for cfg in CATEGORY_ENRICHMENT.values()}
        assert modes <= DISPATCHED_MODES, f"unreachable modes declared: {modes - DISPATCHED_MODES}"

    def test_cinema_sites_are_enriched(self):
        """`cinema` used to map to `catalog`, which no branch handled."""
        assert _get_category_enrichment_config("cinema")["mode"] == "streaming"

    def test_releases_site_is_enriched(self):
        """`releases` used to map to `{"mode": "none"}`, i.e. skipped."""
        assert _get_category_enrichment_config("releases")["mode"] == "streaming"

    def test_unknown_category_falls_back_to_streaming_not_none(self):
        """A new site should keep its enrichment, not silently lose it."""
        assert _get_category_enrichment_config("brand-new-category")["mode"] == "streaming"

    @pytest.mark.parametrize(
        "category,expected",
        [
            ("movies", "streaming"),
            ("episodes", "streaming"),
            ("cinema", "streaming"),
            ("torrents", "streaming"),
            ("releases", "streaming"),
            ("blogs", "article"),
            ("news", "article"),
            ("cyber", "article"),
            ("tech", "article"),
            ("education", "article"),
            ("economy", "article"),
            ("local", "article"),
        ],
    )
    def test_category_modes(self, category, expected):
        assert _get_category_enrichment_config(category)["mode"] == expected

    def test_site_enhance_mode_overrides_category(self):
        assert _resolve_mode("movies", "none") == "none"
        assert _resolve_mode("blogs", "streaming") == "streaming"

    def test_category_used_when_no_override(self):
        assert _resolve_mode("blogs", None) == "article"
        assert _resolve_mode(None, None) == "streaming"


class TestBaseUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.reddit.com/r/x/.rss", "www.reddit.com"),
            ("https://example.com", "example.com"),
            ("http://a.b.c/d/e", "a.b.c"),
            ("https://example.com:8080/x", "example.com:8080"),
            ("not-a-url", "not-a-url"),
            ("", ""),
        ],
    )
    def test_base_url(self, url, expected):
        assert _base_url(url) == expected


# --------------------------------------------------------------------------
# dispatch loop
# --------------------------------------------------------------------------


def _run_main(tmp_path: Path, sites, capsys) -> str:
    """Run enrich_feeds.main() over an empty feed dir with a fake site config."""
    with (
        patch.object(ef, "FEEDS_DIR", tmp_path),
        patch.object(ef, "REPO_ROOT", tmp_path),
        patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
        patch.object(ef, "_feed_kinds", return_value={}),
    ):
        asyncio.run(ef.main())
    return capsys.readouterr().out


class TestMainDispatch:
    def test_items_are_counted_once_per_feed(self, tmp_path, capsys):
        """Regression: `total_items` was added inside each branch AND after it,
        so every feed's items were counted twice in the run summary."""
        _write_feed(tmp_path / "a.xml", items=3)
        _write_feed(tmp_path / "b.xml", items=4)
        sites = [_site("a.xml", "movies"), _site("b.xml", "movies")]

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", return_value=(True, {"items": 3, "posters": 1})),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())
        out = capsys.readouterr().out

        summary = [ln for ln in out.splitlines() if ln.startswith("Enriched")][-1]
        # 2 feeds x 3 items reported by the stub, counted once each = 6, not 12.
        assert re.search(r"\| 6 items", summary), summary

    def test_kind_series_drives_is_series_feed(self, tmp_path, capsys):
        """Regression: series-ness was taken from the *category*, which broke
        `kind: series` on sites categorised as movies/torrents (e.g. showrss)."""
        _write_feed(tmp_path / "showrss.xml")
        sites = [_site("showrss.xml", "torrents", kind="series")]

        seen = {}

        def fake_process(path, **kw):
            seen.update(kw)
            return False, {"items": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={"showrss.xml": "series"}),
            patch.object(ef, "process_streaming_feed", side_effect=fake_process),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())

        assert seen["is_series_feed"] is True

    def test_undeclared_kind_defers_to_per_item_heuristic(self, tmp_path):
        """No `kind` in sites.yaml must pass None so process_feed can guess."""
        _write_feed(tmp_path / "x.xml")
        sites = [_site("x.xml", "movies")]
        seen = {}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(
                ef, "process_streaming_feed", side_effect=lambda p, **kw: (seen.update(kw), (False, {"items": 1}))[1]
            ),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())

        assert seen["is_series_feed"] is None

    def test_kind_movie_is_not_series(self, tmp_path):
        _write_feed(tmp_path / "m.xml")
        sites = [_site("m.xml", "movies", kind="movie")]
        seen = {}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={"m.xml": "movie"}),
            patch.object(
                ef, "process_streaming_feed", side_effect=lambda p, **kw: (seen.update(kw), (False, {"items": 1}))[1]
            ),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())

        assert seen["is_series_feed"] is False

    def test_missing_tmdb_key_skips_streaming_but_runs_article(self, tmp_path, capsys):
        """Regression: the old code printed "skipping enrichment" and then ran
        anyway, making one failing TMDb call per item with no key."""
        _write_feed(tmp_path / "movie.xml")
        _write_feed(tmp_path / "blog.xml")
        sites = [_site("movie.xml", "movies"), _site("blog.xml", "blogs")]

        streaming_calls = []
        article_calls = []

        async def fake_article(path, **kw):
            article_calls.append(path)
            return True, {"items": 1, "enriched": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", side_effect=lambda *a, **k: streaming_calls.append(a)),
            patch.object(ef, "enrich_article_feed", side_effect=fake_article),
            patch.dict("os.environ", {}, clear=True),
        ):
            asyncio.run(ef.main())
        out = capsys.readouterr().out

        assert streaming_calls == [], "streaming enrichment ran without TMDB_API_KEY"
        assert [p.name for p in article_calls] == ["blog.xml"]
        assert "WARN" in out and "TMDB_API_KEY" in out

    def test_epguides_misses_are_resolved_after_the_loop(self, tmp_path):
        """The retry-after-refresh pass must be wired to the orchestrator."""
        _write_feed(tmp_path / "s.xml")
        sites = [_site("s.xml", "episodes", kind="series")]

        def fake_process(path, epguides_misses=None, **kw):
            epguides_misses.setdefault("seeded", []).append("x")
            return False, {"items": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", side_effect=fake_process),
            patch.object(ef, "resolve_epguides_misses", return_value=4) as retry,
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())

        retry.assert_called_once()
        assert retry.call_args.args[1] == {}

    def test_per_feed_error_does_not_abort_the_run(self, tmp_path, capsys):
        _write_feed(tmp_path / "bad.xml")
        _write_feed(tmp_path / "good.xml")
        sites = [_site("bad.xml", "movies"), _site("good.xml", "blogs")]

        def boom(*a, **k):
            raise RuntimeError("network exploded")

        async def fake_article(path, **kw):
            return True, {"items": 1, "enriched": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", side_effect=boom),
            patch.object(ef, "enrich_article_feed", side_effect=fake_article),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())
        out = capsys.readouterr().out

        assert "network exploded" in out
        assert "1 errors" in out
        # the second feed still ran (progress lines print the feed stem)
        assert "good (blogs)" in out and "rich=1" in out

    def test_mode_none_leaves_the_feed_untouched(self, tmp_path):
        _write_feed(tmp_path / "n.xml")
        sites = [_site("n.xml", "movies", enhance_mode="none")]

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed") as streaming,
            patch.object(ef, "enrich_article_feed") as article,
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main())

        streaming.assert_not_called()
        article.assert_not_called()

    def test_site_article_selectors_are_wired_into_the_config(self, tmp_path):
        _write_feed(tmp_path / "b.xml")
        sites = [
            _site(
                "b.xml",
                "blogs",
                detail_article_selector="//div[@class='post']",
                ad_selectors=[".ads", ".promo"],
            )
        ]
        captured = {}

        async def fake_article(path, config=None, **kw):
            captured["config"] = config
            return True, {"items": 1, "enriched": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "enrich_article_feed", side_effect=fake_article),
            patch.dict("os.environ", {}),
        ):
            asyncio.run(ef.main())

        cfg = captured["config"]
        assert cfg.content_selectors == ["//div[@class='post']"]
        assert cfg.ad_selectors == [".ads", ".promo"]


# --------------------------------------------------------------------------
# the epguides_map=None contract, in the streaming module
# --------------------------------------------------------------------------


class TestEpguidesDefault:
    def test_process_feed_loads_the_map_when_omitted(self, tmp_path):
        """Regression: process_feed documented that it loads the map when
        omitted, but only tested it for truthiness - so passing None silently
        disabled every EpGuides link."""
        from scripts.enrichers import streaming_enricher as se

        feed = _write_feed(tmp_path / "tv.xml")
        tree = ET.parse(feed)
        channel = tree.getroot().find("channel")
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = "The Gentlemen S01E03"
        ET.SubElement(item, "link").text = "https://example.com/tv/550"
        ET.SubElement(item, "description").text = "<p>desc</p>"
        tree.write(feed, encoding="UTF-8", xml_declaration=True)

        with (
            # keys are normalized titles: _normalize_epguides_title drops spaces
            patch.object(se, "_epguides_map", return_value={"thegentlemen": "the-gentlemen-uk"}),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "movie_lookup", return_value=None),
        ):
            se.process_feed(feed)

        text = feed.read_text(encoding="utf-8")
        assert "epguides.com" in text, "no EpGuides link written when the map was omitted"
