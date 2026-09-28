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
from scripts.enrichers import article_enricher as ae

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
        name=kw.get("name", Path(feed_file).stem),
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


# --------------------------------------------------------------------------
# --site filtering
# --------------------------------------------------------------------------


class TestSiteFilter:
    """`--site NAME` must narrow a run to the named feed instead of sweeping
    every file in feeds/."""

    def _run(self, tmp_path, argv, sites):
        """Run main() with the feed dir, config and dispatch stubbed out.

        Returns (exit code, feed filenames that reached the dispatcher).
        """
        _write_feed(tmp_path / "a.xml")
        _write_feed(tmp_path / "b.xml")
        seen: list[str] = []

        def stub(path, **kwargs):
            # The real dispatcher takes several keyword args; the filter tests
            # only care about *which* feeds reached it.
            seen.append(Path(path).name)
            return False, {"items": 1}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=sites)),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", stub),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            rc = asyncio.run(ef.main(argv))
        return rc, seen

    def test_no_site_flag_processes_every_feed(self, tmp_path):
        rc, seen = self._run(tmp_path, [], [_site("a.xml", "movies"), _site("b.xml", "movies")])
        assert rc == 0
        assert seen == ["a.xml", "b.xml"]

    def test_site_flag_processes_only_that_feed(self, tmp_path):
        rc, seen = self._run(tmp_path, ["--site", "a"], [_site("a.xml", "movies"), _site("b.xml", "movies")])
        assert rc == 0
        assert seen == ["a.xml"]

    def test_site_flag_accepts_the_xml_filename(self, tmp_path):
        rc, seen = self._run(tmp_path, ["--site", "a.xml"], [_site("a.xml", "movies"), _site("b.xml", "movies")])
        assert rc == 0
        assert seen == ["a.xml"]

    def test_site_flag_is_repeatable(self, tmp_path):
        rc, seen = self._run(tmp_path, ["--site", "a", "--site", "b"], [_site("a.xml", "movies"), _site("b.xml", "movies")])
        assert rc == 0
        assert seen == ["a.xml", "b.xml"]

    def test_unknown_site_fails_instead_of_sweeping(self, tmp_path, capsys):
        """A typo must not quietly re-enrich all 70 feeds."""
        rc, seen = self._run(tmp_path, ["--site", "typo"], [_site("a.xml", "movies")])
        assert rc == 1
        assert seen == []
        assert "unknown site: typo" in capsys.readouterr().err

    def test_configured_but_ungenerated_feed_fails(self, tmp_path, capsys):
        """Targeting a site whose feed file does not exist is a real problem for
        a single-feed run, unlike in a full sweep."""
        rc, seen = self._run(tmp_path, ["--site", "gone"], [_site("gone.xml", "movies")])
        assert rc == 1
        assert seen == []
        assert "no such feed file" in capsys.readouterr().err


class TestItemCounting:
    """Every mode must report its items exactly once in the run summary."""

    def test_article_mode_is_not_double_counted(self, tmp_path, capsys):
        """Regression: the article branch added `total_items` itself *and* the
        shared post-branch line did it again, so every blog feed reported twice
        its real item count (gabriel-ursan: 9 items shown as 18)."""
        _write_feed(tmp_path / "blog.xml", items=9)
        site = _site("blog.xml", "blogs")

        async def article_stub(*args, **kwargs):
            return True, {"items": 9, "enriched": 9, "skipped": 0}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=[site])),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "_enrich_with_article_content", article_stub),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main([]))
        out = capsys.readouterr().out
        assert re.findall(r"\| (\d+) items", out) == ["9"], out
        assert "| 9 article bodies" in out

    def test_streaming_mode_is_not_double_counted(self, tmp_path, capsys):
        _write_feed(tmp_path / "movies.xml", items=7)
        site = _site("movies.xml", "movies")

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=[site])),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "process_streaming_feed", lambda p, **kw: (True, {"items": 7, "posters": 3})),
            patch.dict("os.environ", {"TMDB_API_KEY": "k"}),
        ):
            asyncio.run(ef.main([]))
        out = capsys.readouterr().out
        assert re.findall(r"\| (\d+) items", out) == ["7"], out


class TestKeptExcerptIsSurfaced:
    """The number has to reach the log, or nobody notices a feed that stopped
    producing article bodies."""

    def test_the_summary_names_the_problem_loudly(self):
        src = (Path(__file__).resolve().parent.parent / "scripts" / "enrich_feeds.py").read_text(
            encoding="utf-8"
        )
        assert "ITEMS STILL ON THEIR SITE EXCERPT" in src, (
            "the summary must surface kept_excerpt, not bury it in a counter"
        )
        assert "ON-EXCERPT=" in src, "the per-feed line must show it too"

    def test_the_stat_is_accumulated_from_the_article_mode_branch_only(self):
        """Streaming feeds have no article body to keep, so counting them would
        inflate the headline with items that are not stubs."""
        src = (Path(__file__).resolve().parent.parent / "scripts" / "enrich_feeds.py").read_text(
            encoding="utf-8"
        )
        assert 'total_kept_excerpt += stats.get("kept_excerpt", 0)' in src
        assert src.count("total_kept_excerpt +=") == 1


class TestEpguidesLinkPlacement:
    """EpGuides used to be appended to the end of the description, so it landed
    after the article prose and read as a stray link dropped at the bottom. It
    belongs with the other generated links, above the body.
    """

    IMDB = (
        '<a href="https://www.imdb.com/find?q=Show%20%282026%29&amp;s=tt" target="_blank" '
        'rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>'
    )
    TRAILER = '<a href="#"><b style="color:#6600cc;">Trailer</b></a>'

    def _out(self, body: str) -> str:
        from scripts.enrichers.streaming_enricher import (
            _build_epguides_link,
            _insert_after_imdb,
        )

        text = f'<img src="p.jpg"><br>{self.TRAILER}<br>{self.IMDB}<br>{body}'
        return _insert_after_imdb(text, _build_epguides_link("Show"))

    def test_it_lands_after_imdb_and_before_the_body(self):
        out = self._out("<p>Body text.</p>")
        order = re.findall(r">(Trailer|IMDb|EpGuides)</b>", out)
        assert order == ["Trailer", "IMDb", "EpGuides"], order
        assert out.index("EpGuides</b>") < out.index("<p>Body text.")

    def test_the_separator_is_not_doubled(self):
        """The link carries its own leading <br>; consuming the existing one as
        well left `</a><br><br><a`."""
        assert "<br><br>" not in self._out("<p>Body text.</p>")

    def test_with_no_imdb_link_it_still_ends_up_in_the_description(self):
        from scripts.enrichers.streaming_enricher import (
            _build_epguides_link,
            _insert_after_imdb,
        )

        out = _insert_after_imdb("<p>Only body.</p>", _build_epguides_link("Show"))
        assert "EpGuides</b>" in out
        assert out.startswith("<p>Only body.</p>")

    def test_a_series_link_and_the_fallback_search_link_both_place_correctly(self):
        from scripts.enrichers.streaming_enricher import (
            _build_epguides_search_link,
            _insert_after_imdb,
        )

        out = _insert_after_imdb(
            f"{self.IMDB}<br><p>Body.</p>", _build_epguides_search_link("Some Show")
        )
        assert "epguides.com" not in out  # the fallback points at a site search
        assert "google.com/cse" in out
        assert out.index("cse") < out.index("<p>Body.")



class TestAlreadyEnrichedHostMatching:
    """The streaming enricher skips an item when its description already
    carries a TMDb poster and an IMDb find link. It used to decide that with
    `"image.tmdb.org" in description` -- a substring test on a URL, which cannot
    tell a real poster from a tracking URL that merely mentions the host.

    The cost of a false match is not cosmetic: the item is skipped forever, so
    the enrichment it still needed never happens and nothing reports why.
    """

    @staticmethod
    def _poster() -> str:
        return '<img src="https://image.tmdb.org/t/p/w500/abc.jpg">'

    @staticmethod
    def _imdb() -> str:
        return '<a href="https://www.imdb.com/find?q=Some+Film&amp;s=tt">IMDb</a>'

    def test_a_real_poster_and_find_link_count_as_enriched(self):
        from scripts.enrichers import streaming_enricher as se

        blob = f"<p>{self._poster()}{self._imdb()}</p>"
        assert se._has_host(blob, "image.tmdb.org")
        assert se._has_path(blob, "imdb.com", "/find")

    def test_a_url_that_merely_mentions_the_host_does_not_count(self):
        from scripts.enrichers import streaming_enricher as se

        blob = '<img src="https://tracker.example.ro/pixel.gif?ref=image.tmdb.org">'
        assert not se._has_host(blob, "image.tmdb.org")

    def test_a_host_containing_the_name_does_not_count(self):
        from scripts.enrichers import streaming_enricher as se

        blob = '<img src="https://image.tmdb.org.evil.example/x.jpg">'
        assert not se._has_host(blob, "image.tmdb.org")

    def test_the_bare_host_is_accepted_as_well_as_www(self):
        from scripts.enrichers import streaming_enricher as se

        assert se._has_path('<a href="https://imdb.com/find?q=x">', "imdb.com", "/find")

    def test_an_imdb_title_link_is_not_a_find_link(self):
        """The guard means "we already added a search link". A direct title link
        came from the site, and counting it would skip the search link we owe."""
        from scripts.enrichers import streaming_enricher as se

        blob = '<a href="https://www.imdb.com/title/tt1234567/">IMDb</a>'
        assert not se._has_path(blob, "imdb.com", "/find")

    def test_the_sites_own_find_link_does_not_count_as_ours(self):
        """Found on real data: the Romanian cinema sites ship their own IMDb
        search as ``/find/?q=...&ttype=ft`` -- a trailing slash and a parameter
        this module never writes. Treating those as links we already added
        suppressed the trailer link on 100 items across 9 cinema feeds, and
        nothing would ever put it back."""
        from scripts.enrichers import streaming_enricher as se

        theirs = '<a href="https://www.imdb.com/find/?q=Odiseea&amp;s=tt&amp;ttype=ft">IMDb</a>'
        assert not se._has_path(theirs, "imdb.com", "/find")
        ours = '<a href="https://www.imdb.com/find?q=Odiseea&amp;s=tt">IMDb</a>'
        assert se._has_path(ours, "imdb.com", "/find")

    def test_the_host_comparison_ignores_case_and_port_and_userinfo(self):
        from scripts.enrichers import streaming_enricher as se

        assert se._has_host('<img src="https://IMAGE.TMDB.ORG/a.jpg">', "image.tmdb.org")
        assert se._has_host('<img src="https://image.tmdb.org:443/a.jpg">', "image.tmdb.org")
        assert se._has_host('<img src="https://u:p@image.tmdb.org/a.jpg">', "image.tmdb.org")

    def test_a_subdomain_of_the_wanted_host_counts(self):
        from scripts.enrichers import streaming_enricher as se

        assert se._has_host('<img src="https://static.image.tmdb.org/a.jpg">', "image.tmdb.org")

    def test_malformed_markup_does_not_raise(self):
        from scripts.enrichers import streaming_enricher as se

        for blob in ("", "no markup at all", '<img src="', "<a href='", '<img src="::::">'):
            assert se._has_host(blob, "image.tmdb.org") is False
            assert se._has_path(blob, "imdb.com", "/find") is False


class TestArticleFetchesHonourTheProxy:
    """The generate step has read `RSS_GENERATOR_PROXY_URL` since the fetcher
    was written; article enrichment did not, so setting the secret only fixed
    half the pipeline.

    The symptom is specific and was visible in a production run: hackread
    serves its *feed* over the bare host with a 200 and answers the same host's
    article pages with 403, so the feed generated fine and then every one of
    its items came back as the site's own excerpt. All ten article fetches
    failed and nothing said the address was the problem.
    """

    def test_no_proxy_configured_means_no_proxy_kwarg(self, monkeypatch):
        monkeypatch.delenv("RSS_GENERATOR_PROXY_URL", raising=False)
        assert ae._proxy_kwargs() == {}

    def test_a_configured_proxy_is_used(self, monkeypatch):
        monkeypatch.setenv("RSS_GENERATOR_PROXY_URL", "http://proxy.invalid:8080")
        assert ae._proxy_kwargs() == {"proxy": "http://proxy.invalid:8080"}
        assert ef._proxy_url() == "http://proxy.invalid:8080"

    def test_the_shared_client_is_configured_with_it(self, monkeypatch):
        """The shared AsyncClient is what every article fetch uses, so this is
        the line that has to carry the proxy -- a proxy on the one-shot
        fallback path alone would reach nothing."""
        monkeypatch.setenv("RSS_GENERATOR_PROXY_URL", "http://proxy.invalid:8080")
        assert ef._client_kwargs()["proxy"] == "http://proxy.invalid:8080"

    def test_the_shared_client_keeps_its_timeout(self, monkeypatch):
        monkeypatch.delenv("RSS_GENERATOR_PROXY_URL", raising=False)
        assert ef._client_kwargs() == {"timeout": 15.0}

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_a_blank_value_is_treated_as_unset(self, monkeypatch, raw):
        monkeypatch.setenv("RSS_GENERATOR_PROXY_URL", raw)
        assert ae._proxy_kwargs() == {}
        assert ef._client_kwargs() == {"timeout": 15.0}


class TestTheExcerptSummarySaysWhy:
    """A fetch failure and a site that has gone quiet produce the same published
    item, so the summary has to carry the reason.

    108 items once disappeared into per-feed `fetch 19` markers with nothing in
    the log to say the address was at fault. Measured directly: ghacks,
    doublepulsar and hackread answer 403 to GitHub's runners and serve normally
    from a residential address, and edu.ro times out connecting. None of that
    is a code fault, and all of it is invisible unless said.
    """

    @staticmethod
    def _summary(tmp_path, capsys, monkeypatch, *, items, kept, fetch_failed, proxy=""):
        monkeypatch.setenv("RSS_GENERATOR_PROXY_URL", proxy)
        site = SimpleNamespace(
            name="x",
            feed_file="x.xml",
            url="https://example.com/",
            category="blogs",
            kind=None,
            ad_selectors=[],
            removals=[],
            base_url="",
            display_name="X",
        )
        (tmp_path / "x.xml").write_text(
            '<?xml version="1.0"?><rss><channel><title>t</title>'
            "<item><title>i</title><link>https://example.com/a</link>"
            "<description>old</description></item></channel></rss>",
            encoding="utf-8",
        )
        stats = {"items": items, "kept_excerpt": kept, "fetch_failed": fetch_failed}

        async def fake(path, **kw):
            return False, stats

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=[site])),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "enrich_article_feed", fake),
        ):
            asyncio.run(ef.main())
        return capsys.readouterr().out

    def test_a_blocked_run_names_the_missing_proxy(self, tmp_path, capsys, monkeypatch):
        out = self._summary(
            tmp_path, capsys, monkeypatch, items=10, kept=10, fetch_failed=10
        )
        assert "RSS_GENERATOR_PROXY_URL is not set" in out, out
        assert "could not be fetched from this network" in out, out
        assert "fetched by none, in: x" in out, out

    def test_a_configured_proxy_is_reported_differently(
        self, tmp_path, capsys, monkeypatch
    ):
        out = self._summary(
            tmp_path,
            capsys,
            monkeypatch,
            items=10,
            kept=10,
            fetch_failed=10,
            proxy="http://p.invalid:1",
        )
        assert "even through the configured proxy" in out, out
        assert "is not set" not in out, out

    def test_a_partial_failure_is_not_called_a_block(
        self, tmp_path, capsys, monkeypatch
    ):
        """Not every feed that loses a fetch is blocked; calling it that would
        send the next person chasing the wrong thing."""
        out = self._summary(
            tmp_path, capsys, monkeypatch, items=10, kept=4, fetch_failed=2
        )
        assert "fetched by none" not in out, out
        assert "could not be fetched from this network" in out, out

    def test_a_clean_run_says_nothing_extra(self, tmp_path, capsys, monkeypatch):
        out = self._summary(
            tmp_path, capsys, monkeypatch, items=10, kept=0, fetch_failed=0
        )
        assert "STILL ON THEIR SITE EXCERPT" not in out, out


class TestArticleSourceReachesTheEnricher:
    """`feed_config` is built with `getattr(site, ...)` from a real
    `SiteConfig`, not from the raw YAML.

    The `article_source` key was missing from that dict while
    `ArticleEnrichConfig(article_source=site_cfg.get("article_source"))` was
    already reading it, so every site got `None` and the WordPress fallback was
    dead. It passed every unit test and a local run, because locally the *page*
    fetch succeeds and the fallback is never reached. It only showed up in
    production, as hackread still reporting `ON-EXCERPT=10(fetch 10)` and
    still appearing in the "fetched by none" list.

    A test that asserts the key exists is the whole point: the failure mode is
    silent by construction.
    """

    def test_the_key_is_in_the_config_the_enricher_receives(self, tmp_path, capsys, monkeypatch):
        site = SimpleNamespace(
            name="x",
            feed_file="x.xml",
            url="https://example.com/",
            category="cyber",
            kind=None,
            ad_selectors=[],
            removals=[],
            base_url="",
            display_name="X",
            article_source={"type": "wordpress", "api": "https://example.com/wp-json/wp/v2/posts"},
        )
        (tmp_path / "x.xml").write_text(
            '<?xml version="1.0"?><rss><channel><title>t</title>'
            "<item><title>i</title><link>https://example.com/a</link>"
            "<description>old</description></item></channel></rss>",
            encoding="utf-8",
        )
        seen: dict = {}

        async def fake(path, *, client=None, config=None, base_url=None):
            seen["config"] = config
            return False, {"items": 1, "kept_excerpt": 0, "fetch_failed": 0}

        with (
            patch.object(ef, "FEEDS_DIR", tmp_path),
            patch.object(ef, "REPO_ROOT", tmp_path),
            patch.object(ef, "load_config", return_value=SimpleNamespace(sites=[site])),
            patch.object(ef, "_feed_kinds", return_value={}),
            patch.object(ef, "enrich_article_feed", fake),
        ):
            asyncio.run(ef.main())

        capsys.readouterr()
        assert seen["config"].article_source == site.article_source, (
            "article_source never reached ArticleEnrichConfig"
        )
