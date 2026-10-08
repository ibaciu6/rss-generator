import asyncio
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.config import Config, SiteConfig
from core.engine import STALE_FEED_MAX_AGE_DAYS, GenerationEngine
from scraper.parser import ParsedItem


class _FailingFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        raise RuntimeError("challenge page")

    async def close(self) -> None:
        """run() closes the fetcher in a finally block."""


class _DummyDedup:
    def filter_new(self, site_name: str, urls):
        return urls


class _FallbackFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        if url == "https://sitefilme.com/":
            raise RuntimeError("challenge page")
        if "wp-json/wp/v2/posts" in url:
            return type(
                "FetchResult",
                (),
                {
                    "url": url,
                    "content": (
                        '[{"date_gmt":"2026-09-26T04:56:18","link":"https://sitefilme.com/post-a/",'
                        '"title":{"rendered":"Recovered Post"},"excerpt":{"rendered":"<p>Recovered</p>"}}]'
                    ),
                    "status_code": 200,
                },
            )()
        raise RuntimeError(f"unexpected url {url}")


class _HtmlRetryFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        if "wp-json/wp/v2/posts" in url or url.endswith("/feed/"):
            raise RuntimeError("unexpected fallback source")

        if method == "http":
            return type(
                "FetchResult",
                (),
                {
                    "url": url,
                    "content": "<html><body><p>No matching cards yet</p></body></html>",
                    "status_code": 200,
                },
            )()

        if method == "cloudscraper":
            return type(
                "FetchResult",
                (),
                {
                    "url": url,
                    "content": (
                        '<html><body><article class="item">'
                        '<h2>Recovered From Alternate HTML</h2>'
                        '<a href="https://example.com/recovered">Read more</a>'
                        "</article></body></html>"
                    ),
                    "status_code": 200,
                },
            )()

        raise RuntimeError(f"unexpected method {method}")


class _FallbackUrlFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        if url == "https://sitefilme.com/":
            result = type(
                "FetchResult",
                (),
                {
                    "url": "https://www.56.com/",
                    "content": "<html><body>redirected elsewhere</body></html>",
                    "status_code": 200,
                },
            )()
        elif url == "https://www.sitefilme.com/":
            result = type(
                "FetchResult",
                (),
                {
                    "url": "https://sitefilme.com/",
                    "content": (
                        '<html><body><article class="item">'
                        '<h2>Recovered From Fallback URL</h2>'
                        '<a href="https://example.com/fallback-url">Read more</a>'
                        "</article></body></html>"
                    ),
                    "status_code": 200,
                },
            )()
        else:
            raise RuntimeError(f"unexpected url {url}")

        if validator is not None:
            validator(result)
        return result


def test_deduplicate_items_by_link(tmp_path: Path) -> None:
    site = SiteConfig(
        name="sitefilme",
        url="https://sitefilme.com/",
        method="playwright",
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="sitefilme.xml",
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", tmp_path / "feeds")

    items = engine._deduplicate_items(
        [
            ParsedItem(title="A", link="https://example.com/a", description=None, pub_date=None),
            ParsedItem(title="A 2", link="https://example.com/a", description=None, pub_date=None),
            ParsedItem(title="B", link="https://example.com/b", description=None, pub_date=None),
        ]
    )

    assert [item.link for item in items] == ["https://example.com/a", "https://example.com/b"]


def test_wordpress_fallback_skips_html_listing_markers_on_json(tmp_path: Path) -> None:
    """wp-json bodies never contain HTML listing markers; do not reject valid API payloads."""

    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()

    class _Fetcher:
        async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
            if url == "https://wp-marker-mismatch.example/":
                raise RuntimeError("html failed")
            if "wp-json/wp/v2/posts" in url:
                result = type(
                    "FetchResult",
                    (),
                    {
                        "url": url,
                        "content": (
                            '[{"date_gmt":"2026-09-26T04:56:18",'
                            '"link":"https://wp-marker-mismatch.example/hello/",'
                            '"title":{"rendered":"From API"}}]'
                        ),
                        "status_code": 200,
                    },
                )()
                if validator is not None:
                    validator(result)
                return result
            raise RuntimeError(f"unexpected url {url!r}")

    site = SiteConfig(
        name="wplisting",
        url="https://wp-marker-mismatch.example/",
        method="http",
        required_content_marker_groups=(("__only_expected_in_html__",),),
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="wplisting.xml",
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _Fetcher(), _DummyDedup()))

    root = ET.parse(feeds_dir / "wplisting.xml").getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "wplisting"
    assert channel.findtext("item/title") == "From API"


def test_process_site_uses_wordpress_fallback_when_html_fails(tmp_path: Path) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "sitefilme.xml"

    site = SiteConfig(
        name="sitefilme",
        display_name="SiteFilme",
        url="https://sitefilme.com/",
        method="http",
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="sitefilme.xml",
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _FallbackFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")

    assert channel is not None
    assert channel.findtext("title") == "SiteFilme"
    assert channel.findtext("item/title") == "Recovered Post"
    assert channel.findtext("item/link") == "https://sitefilme.com/post-a/"


def test_process_site_retries_html_with_alternate_method_before_source_fallbacks(
    tmp_path: Path,
) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "sitefilme.xml"

    site = SiteConfig(
        name="sitefilme",
        display_name="SiteFilme",
        url="https://sitefilme.com/",
        method="http",
        item_selector="//article[contains(@class,'item')]",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="sitefilme.xml",
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _HtmlRetryFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")

    assert channel is not None
    assert channel.findtext("title") == "SiteFilme"
    assert channel.findtext("item/title") == "Recovered From Alternate HTML"
    assert channel.findtext("item/link") == "https://example.com/recovered"


def test_process_site_tries_fallback_url_when_primary_final_host_is_unexpected(
    tmp_path: Path,
) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "sitefilme.xml"

    site = SiteConfig(
        name="sitefilme",
        display_name="SiteFilme",
        url="https://sitefilme.com/",
        method="http",
        item_selector="//article[contains(@class,'item')]",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="sitefilme.xml",
        fallback_urls=["https://www.sitefilme.com/"],
        allowed_final_hosts=["sitefilme.com", "www.sitefilme.com"],
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _FallbackUrlFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")

    assert channel is not None
    assert channel.findtext("title") == "SiteFilme"
    assert channel.findtext("item/title") == "Recovered From Fallback URL"
    assert channel.findtext("item/link") == "https://example.com/fallback-url"


def test_validate_fetch_result_requires_content_markers(tmp_path: Path) -> None:
    site = SiteConfig(
        name="demo",
        url="https://example.com/",
        method="http",
        item_selector="//a",
        title_selector="text()",
        link_selector="@href",
        feed_file="demo.xml",
        required_content_markers=["listing-grid"],
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", tmp_path / "feeds")
    engine._validate_fetch_result(site, "https://example.com/", "<div id='listing-grid'></div>")
    try:
        engine._validate_fetch_result(site, "https://example.com/", "<html></html>")
    except ValueError as exc:
        assert "no group matched" in str(exc) or "Required content marker" in str(exc)
    else:
        raise AssertionError("expected ValueError when required marker absent")


def test_validate_fetch_result_or_groups(tmp_path: Path) -> None:
    site = SiteConfig(
        name="demo",
        url="https://example.com/",
        method="http",
        item_selector="//a",
        title_selector="text()",
        link_selector="@href",
        feed_file="demo.xml",
        required_content_marker_groups=(("alpha", "beta"), ("gamma",)),
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", tmp_path / "feeds")
    engine._validate_fetch_result(site, "https://example.com/", "<html>GAMMA only</html>")
    try:
        engine._validate_fetch_result(site, "https://example.com/", "<html>none</html>")
    except ValueError as exc:
        assert "no group matched" in str(exc)
    else:
        raise AssertionError("expected ValueError")


class _AtomRssFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        assert method == "http"
        result = type(
            "FetchResult",
            (),
            {
                "url": url,
                "content": (
                    '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                    "<title>Movie.2026.1080p.WEB-DL</title>"
                    '<link href="https://www.reddit.com/r/SceneReleases/comments/1abc/movie/"/>'
                    "<published>2026-09-15T15:46:38+00:00</published>"
                    "<updated>2026-09-15T15:46:38+00:00</updated>"
                    "<id>t3_1abc</id>"
                    "</entry></feed>"
                ),
                "status_code": 200,
            },
        )()
        if validator is not None:
            validator(result)
        return result


def test_process_site_fetches_native_rss_for_rss_method(tmp_path: Path) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "scenereleases.xml"

    site = SiteConfig(
        name="scenereleases",
        display_name="r/SceneReleases",
        url="https://www.reddit.com/r/SceneReleases/.rss",
        method="rss",
        item_selector="",
        title_selector="",
        link_selector="",
        feed_file="scenereleases.xml",
        category="torrents",
        max_items=25,
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _AtomRssFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "r/SceneReleases"
    assert channel.findtext("item/title") == "Movie.2026.1080p.WEB-DL"
    assert channel.findtext("item/link") == "https://www.reddit.com/r/SceneReleases/comments/1abc/movie/"


def test_process_site_rss_fallback_url_when_primary_is_empty(tmp_path: Path) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "scenereleases-fallback.xml"

    class _FailingPrimaryThenFallback:
        async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
            if "old.reddit.com" in url:
                result = type(
                    "FetchResult",
                    (),
                    {
                        "url": url,
                        "content": (
                            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                            "<title>Fallback.Movie.2026</title>"
                            '<link href="https://www.reddit.com/r/SceneReleases/comments/1xyz/fallback/"/>'
                            "<updated>2026-09-15T15:46:38+00:00</updated>"
                            "<id>t3_1xyz</id>"
                            "</entry></feed>"
                        ),
                        "status_code": 200,
                    },
                )()
                if validator is not None:
                    validator(result)
                return result
            raise RuntimeError("rate limited (429)")

    site = SiteConfig(
        name="scenereleases-fallback",
        url="https://www.reddit.com/r/SceneReleases/.rss",
        fallback_urls=["https://old.reddit.com/r/SceneReleases/.rss"],
        method="rss",
        item_selector="",
        title_selector="",
        link_selector="",
        feed_file="scenereleases-fallback.xml",
        category="torrents",
        max_items=25,
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _FailingPrimaryThenFallback(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("item/title") == "Fallback.Movie.2026"


def test_process_site_rss_falls_back_when_http_returns_challenge_html(tmp_path: Path) -> None:
    """An HTTP 200 HTML challenge (e.g. Substack bot wall) must fail validation
    so the fetcher retries the next strategy instead of publishing a failure feed."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "ddosecrets.xml"

    class _ChallengeThenPlaywrightFetcher:
        async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
            strategies = [
                "<html><body>Just a moment...</body></html>",
                "<html><body>Enable JavaScript to continue</body></html>",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<rss version="2.0"><channel><title>Distributed Email of Secrets</title>'
                    "<item><title>Recovered item</title>"
                    "<link>https://ddosecrets.substack.com/p/recovered</link>"
                    "<description>d</description></item></channel></rss>"
                ),
            ]
            for content in strategies:
                result = type(
                    "FetchResult",
                    (),
                    {"url": url, "content": content, "status_code": 200},
                )()
                if validator is None:
                    return result
                try:
                    validator(result)
                    return result
                except Exception:
                    continue
            raise RuntimeError("all strategies returned challenge HTML")

    site = SiteConfig(
        name="ddosecrets",
        display_name="Distributed Email of Secrets",
        url="https://ddosecrets.substack.com/feed",
        method="rss",
        item_selector="",
        title_selector="",
        link_selector="",
        feed_file="ddosecrets.xml",
        category="cyber",
        max_items=30,
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _ChallengeThenPlaywrightFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Distributed Email of Secrets"
    assert channel.findtext("item/title") == "Recovered item"


def test_candidate_rss_urls_prefers_listing_feed(tmp_path: Path) -> None:
    """Listing-specific feeds (e.g. /filme/feed/) come before the root /feed/."""

    site = SiteConfig(
        name="demo",
        url="https://example.com/filme/",
        method="http",
        item_selector="//a",
        title_selector="text()",
        link_selector="@href",
        feed_file="demo.xml",
        fallback_urls=["https://www.example.com/filme/"],
    )
    urls = GenerationEngine._candidate_rss_urls(site)
    assert urls[0] == "https://example.com/filme/feed/"
    assert urls[1] == "https://www.example.com/filme/feed/"
    # Root feed stays in the list for sites that only publish /feed/ at root.
    assert "https://example.com/feed/" in urls
    assert "https://www.example.com/feed/" in urls


def test_filmflix_config_has_listing_marker() -> None:
    from core.config import load_config

    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "sites.yaml")
    filmflix = next(s for s in cfg.sites if s.name == "filmehd-cc-filme")
    groups = filmflix.required_content_marker_groups
    assert groups
    flat = {m.lower() for g in groups for m in g}
    assert "film-name" in flat


def _site(name: str = "gone") -> SiteConfig:
    return SiteConfig(
        name=name,
        url="https://example.com/",
        method="http",
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file=f"{name}.xml",
    )


def _one_item() -> list[ParsedItem]:
    return [
        ParsedItem(
            title="Recovered",
            link="https://example.com/1",
            description="d",
            pub_date=None,
        )
    ]


def _run_engine(engine: GenerationEngine, extract, monkeypatch) -> None:
    """Drive engine.run() with the retry pause removed and a stubbed extract.

    The outcome of a failure is decided in run(), not _process_site, so the
    tests that care about it have to go through run().
    """
    monkeypatch.setattr("core.engine.RETRY_PASS_DELAY_SECONDS", 0)
    monkeypatch.setattr("core.engine.Fetcher", lambda: _FailingFetcher())
    monkeypatch.setattr(engine, "_extract_items", extract)
    asyncio.run(engine.run())


async def _always_fails(site, fetcher):
    # A source that says it is finished, not one having a bad run. These tests
    # are about the deletion path; see TestTransientVsGone for the other half.
    raise RuntimeError("native RSS fetch failed: HTTP 404 Not Found")


async def _always_challenged(site, fetcher):
    """A 200 that is a bot wall rather than the article. Transient."""
    raise RuntimeError("challenge page")


async def _always_times_out(site, fetcher):
    """Six cinema malls timing out at 240s in the same pass. Transient."""
    from core.engine import SiteResult

    return SiteResult(
        site=site.name, kind="failed", detail="Site timed out after 240s"
    )


def test_run_deletes_the_feed_when_a_site_fails_every_pass(tmp_path, monkeypatch) -> None:
    """A source that is genuinely gone leaves no file, not a placeholder.

    Deletion is now reserved for that. A site having one bad run -- a timeout,
    a bot wall, a rate limit -- keeps whatever the last good run published; see
    TestTransientVsGone, and the reason in core.engine._source_is_gone.
    """
    from core.feed import generate_rss

    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "gone.xml"
    generate_rss(
        [ParsedItem(title="Old Item", link="https://example.com/old", description="", pub_date=None)],
        site_name="Gone",
        site_url="https://example.com/",
        category="blogs",
        output_path=rss_path,
    )
    assert rss_path.exists()

    engine = GenerationEngine(Config(sites=[_site()]), tmp_path / "cache.json", feeds_dir)
    _run_engine(engine, _always_fails, monkeypatch)

    assert not rss_path.exists(), "a failed site must leave no feed file behind"


def test_run_retries_a_site_that_fails_once(tmp_path, monkeypatch) -> None:
    """A transient blip must not cost a feed."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    engine = GenerationEngine(
        Config(sites=[_site("flaky")]), tmp_path / "cache.json", feeds_dir
    )

    calls = {"n": 0}

    async def flaky(site, fetcher):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("challenge page")
        return _one_item()

    _run_engine(engine, flaky, monkeypatch)

    assert (feeds_dir / "flaky.xml").exists(), "a site that recovers on retry keeps its feed"
    assert calls["n"] == 2, "the failed site must be retried once"


def test_run_does_not_retry_a_site_that_succeeded(tmp_path, monkeypatch) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    engine = GenerationEngine(
        Config(sites=[_site("fine")]), tmp_path / "cache.json", feeds_dir
    )

    calls = {"n": 0}

    async def ok(site, fetcher):
        calls["n"] += 1
        return _one_item()

    _run_engine(engine, ok, monkeypatch)

    assert calls["n"] == 1, "a successful site must not be fetched again"
    assert (feeds_dir / "fine.xml").exists()


def test_run_removes_legacy_sidecars_for_a_dropped_feed(tmp_path, monkeypatch) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "gone.xml"
    atom_path = rss_path.with_suffix(".atom.xml")
    rss_path.write_text("stale rss", encoding="utf-8")
    atom_path.write_text("stale atom", encoding="utf-8")

    engine = GenerationEngine(Config(sites=[_site()]), tmp_path / "cache.json", feeds_dir)
    _run_engine(engine, _always_fails, monkeypatch)

    assert not rss_path.exists()
    assert not atom_path.exists()


def _dated_item(days_old: int) -> ParsedItem:
    when = datetime.now(UTC) - timedelta(days=days_old)
    return ParsedItem(
        title=f"Post {days_old}d",
        link=f"https://example.com/{days_old}d",
        description="d",
        pub_date=when,
    )


class TestStalenessRule:
    """A source that answers but has gone quiet is removed too."""

    def _engine(self, tmp_path: Path) -> GenerationEngine:
        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        site = _site("dormant")
        return GenerationEngine(
            Config(sites=[site]), tmp_path / "cache.json", feeds_dir
        )

    def test_age_is_none_when_no_item_is_dated(self):
        """The 20 streaming and cinema feeds carry no pubDate at all.

        Their only date signal is a release year in the title, so treating
        "no date" as "infinitely old" would delete every one of them while they
        serve fresh daily listings.
        """
        undated = [
            ParsedItem(title="Star Wars (2015)", link="https://x/1", description=None, pub_date=None)
        ]
        assert GenerationEngine._staleness_days(undated) is None

    def test_age_uses_the_newest_item_not_the_oldest(self):
        items = [_dated_item(400), _dated_item(3), _dated_item(90)]
        age = GenerationEngine._staleness_days(items)
        assert age is not None and age <= 3

    def test_mixed_dated_and_undated_uses_the_dated_ones(self):
        items = [_dated_item(2), ParsedItem(title="x", link="https://x/2", description=None, pub_date=None)]
        age = GenerationEngine._staleness_days(items)
        assert age is not None and age <= 2

    def test_a_fresh_feed_is_written(self, tmp_path, monkeypatch) -> None:
        engine = self._engine(tmp_path)

        async def extract(site, fetcher):
            return [_dated_item(1)]

        _run_engine(engine, extract, monkeypatch)
        assert (tmp_path / "feeds" / "dormant.xml").exists()

    def test_a_stale_feed_is_removed(self, tmp_path, monkeypatch) -> None:
        """Unreachable is not the only way to deserve deletion."""
        engine = self._engine(tmp_path)
        feeds_dir = tmp_path / "feeds"
        (feeds_dir / "dormant.xml").write_text("previous run", encoding="utf-8")

        async def extract(site, fetcher):
            return [_dated_item(STALE_FEED_MAX_AGE_DAYS + 100)]

        _run_engine(engine, extract, monkeypatch)
        assert not (feeds_dir / "dormant.xml").exists()

    def test_just_inside_the_threshold_is_kept(self, tmp_path, monkeypatch) -> None:
        feeds_dir = tmp_path / "feeds"
        engine = self._engine(tmp_path)

        async def extract(site, fetcher):
            return [_dated_item(STALE_FEED_MAX_AGE_DAYS - 5)]

        _run_engine(engine, extract, monkeypatch)
        assert (feeds_dir / "dormant.xml").exists()

    def test_just_past_the_threshold_is_removed(self, tmp_path, monkeypatch) -> None:
        feeds_dir = tmp_path / "feeds"
        engine = self._engine(tmp_path)

        async def extract(site, fetcher):
            return [_dated_item(STALE_FEED_MAX_AGE_DAYS + 5)]

        _run_engine(engine, extract, monkeypatch)
        assert not (feeds_dir / "dormant.xml").exists()

    def test_threshold_is_90_days(self) -> None:
        """Pinned deliberately, not by accident.

        At 30 days this rule deleted vedem-just (63 days quiet) and doublepulsar
        (59) — both sites alive, returning 200, publishing irregularly. Small
        sources post when they have news, not on a schedule, so a threshold tuned
        to the news blogs punishes exactly the small blogs in the list.
        """
        assert STALE_FEED_MAX_AGE_DAYS == 90

    def test_a_silent_but_reachable_source_is_kept(self, tmp_path, monkeypatch) -> None:
        """The real vedem-just case: site up, nothing published for two months."""
        feeds_dir = tmp_path / "feeds"
        engine = self._engine(tmp_path)

        async def extract(site, fetcher):
            return [_dated_item(63)]

        _run_engine(engine, extract, monkeypatch)
        assert (feeds_dir / "dormant.xml").exists()

    def test_a_stale_site_is_not_retried(self, tmp_path, monkeypatch) -> None:
        """Only a *failed* fetch is worth a second attempt. A quiet source will
        still be quiet 45 s later, and retrying it doubles the run's wall time
        for nothing."""
        engine = self._engine(tmp_path)
        calls = {"n": 0}

        async def extract(site, fetcher):
            calls["n"] += 1
            return [_dated_item(STALE_FEED_MAX_AGE_DAYS + 100)]

        _run_engine(engine, extract, monkeypatch)
        assert calls["n"] == 1


class TestFailureReportFields:
    """The CI step that builds logs/failed_feeds.txt parses feed_generation.log
    for event=="site.error" and reads payload["error"]. A renamed field turns
    the whole report into "<site>: None" without failing anything."""

    def _events(self, monkeypatch) -> list[dict]:

        captured: list[dict] = []

        class _Capture:
            def __init__(self, name, level="info"):
                pass

            def __getattr__(self, _name):
                def _log(event, **fields):
                    captured.append({"event": event, **fields})

                return _log

        import core.engine as engine_mod

        class _Logger:
            def __getattr__(self, _name):
                def _log(event, **fields):
                    captured.append({"event": event, **fields})

                return _log

        monkeypatch.setattr(engine_mod, "logger", _Logger())
        return captured

    def test_site_error_carries_an_error_field(self, tmp_path, monkeypatch) -> None:
        captured = self._events(monkeypatch)
        engine = GenerationEngine(
            Config(sites=[_site("boom")]), tmp_path / "cache.json", tmp_path / "feeds"
        )
        (tmp_path / "feeds").mkdir(exist_ok=True)
        _run_engine(engine, _always_fails, monkeypatch)

        errors = [e for e in captured if e["event"] == "site.error"]
        assert errors, "a failed site must emit site.error"
        for e in errors:
            assert e.get("error"), f"site.error has no 'error' field: {e}"
            assert e["error"]

    def test_stale_uses_its_own_field_and_does_not_emit_site_error(
        self, tmp_path, monkeypatch
    ) -> None:
        captured = self._events(monkeypatch)
        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        engine = GenerationEngine(
            Config(sites=[_site("quiet")]), tmp_path / "cache.json", feeds_dir
        )

        async def extract(site, fetcher):
            return [_dated_item(STALE_FEED_MAX_AGE_DAYS + 200)]

        _run_engine(engine, extract, monkeypatch)

        assert not [e for e in captured if e["event"] == "site.error"], (
            "a stale feed is not a generation failure and must stay out of "
            "failed_feeds.txt"
        )
        stale = [e for e in captured if e["event"] == "site.feed_stale"]
        # One event when staleness is detected, one when the file is removed.
        assert stale
        assert any(e.get("reason") for e in stale), stale


class TestTransientVsGone:
    """Whether a failed feed's published file gets unlinked is the one
    irreversible thing the pipeline does, and it used to hang on a single bad
    run. A 240s timeout is the definition of transient; deleting on it removed
    six healthy cinema feeds in one pass, and four established blogs whose
    "failed to parse RSS XML" was a bot challenge served to the datacenter
    address. All seven were back an hour later.
    """

    @pytest.mark.parametrize(
        "detail",
        [
            "Site timed out after 240s",
            "Page.goto: net::ERR_CONNECTION_REFUSED at https://www.revoblog.ro/feed/",
            "net::ERR_NAME_NOT_RESOLVED",
            "Failed to fetch 'https://www.computerblog.ro/feed': Failed to parse RSS XML",
            "HTTP 429 Too Many Requests",
            "net::ERR_CONNECTION_RESET",
            "Temporary failure in name resolution",
            "Just a moment... checking your browser before accessing",
            "net::ERR_HTTP_RESPONSE_CODE_FAILURE",
            "HTTP 403 Forbidden",
            "HTTP 500 Internal Server Error",
            "HTTP 503 Service Unavailable",
        ],
    )
    def test_a_bad_run_keeps_the_previous_feed(self, detail):
        from core.engine import _source_is_gone

        assert _source_is_gone(detail) is False, detail

    @pytest.mark.parametrize(
        "detail",
        [
            "native RSS fetch failed (https://x/feed): HTTP 404 Not Found",
            "Feed not found (404)",
            "HTTP 410 Gone",
            "the feed has been removed by the publisher",
        ],
    )
    def test_a_source_that_says_it_is_gone_is_still_dropped(self, detail):
        from core.engine import _source_is_gone

        assert _source_is_gone(detail) is True, detail

    def test_an_unrecognised_failure_is_treated_as_transient(self):
        """One-sided on purpose: being wrong this way leaves a stale feed for a
        run, and being wrong the other way deletes a healthy source."""
        from core.engine import _source_is_gone

        assert _source_is_gone("something nobody has seen before") is False
        assert _source_is_gone("") is False

    def test_a_404_inside_a_bot_wall_message_is_not_misread(self):
        """The transient patterns are checked first, so a challenge page that
        happens to mention a 404 keeps the feed."""
        from core.engine import _source_is_gone

        assert _source_is_gone("challenge page returned 404 for the asset") is False

    def test_the_engine_does_not_delete_on_a_transient_failure(self, tmp_path, monkeypatch):
        """End to end through the real loop: a timed-out site must leave its
        published file in place."""

        from core.engine import GenerationEngine

        feeds = tmp_path / "feeds"
        feeds.mkdir()
        published = feeds / "cinema.xml"
        published.write_text(
            '<?xml version="1.0"?><rss><channel><title>keep me</title>'
            "<item><title>yesterday</title></item></channel></rss>",
            encoding="utf-8",
        )
        engine = GenerationEngine(
            Config(sites=[_site("cinema")]), tmp_path / "cache.json", feeds
        )
        _run_engine(engine, _always_times_out, monkeypatch)

        assert published.exists(), "a transient failure deleted a published feed"

    def test_the_engine_keeps_the_feed_when_every_pass_is_challenged(
        self, tmp_path, monkeypatch
    ):
        """The exact case from a real run: four established blogs were dropped
        for "failed to parse RSS XML" while serving valid RSS to a residential
        IP, because the datacenter address was handed a bot wall."""
        from core.feed import generate_rss

        feeds = tmp_path / "feeds"
        feeds.mkdir()
        rss = feeds / "cinema.xml"
        generate_rss(
            [ParsedItem(title="Old", link="https://example.com/old", description="", pub_date=None)],
            site_name="Cinema",
            site_url="https://example.com/",
            category="cinema",
            output_path=rss,
        )
        engine = GenerationEngine(
            Config(sites=[_site("cinema")]), tmp_path / "cache.json", feeds
        )
        _run_engine(engine, _always_challenged, monkeypatch)
        assert rss.exists(), "a bot wall deleted a published feed"


class TestSeededFeedsSurviveTransientFailures:
    """A seeded `feeds/` is what makes the transient rule reachable at all.

    `feeds/*.xml` is gitignored, so a CI checkout starts with an empty feeds/
    and every generation begins from nothing. "Keep the last good feed on a
    transient failure" then had nothing to keep, and `_published_age_days()` --
    the valve that drops a source which is dead *and* answers 5xx -- always
    read None. Both were correct in a local run and inert in production, and
    five healthy feeds were deleted in one run for "Failed to parse RSS XML" and
    "ERR_CONNECTION_REFUSED": the two canonical transient errors.

    CI now seeds feeds/ from the deployed copy before generating. These pin that
    the seeded copy is used for the failure path and still deleted for the
    persistent one -- seeding must not become a way to keep a dead feed alive.
    """

    @staticmethod
    def _engine(tmp_path, name, *, with_feed=True, days=1):

        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        from core.config import Config
        from core.engine import GenerationEngine

        feeds = tmp_path / "feeds"
        feeds.mkdir()
        if with_feed:
            when = format_datetime(datetime.now(UTC) - timedelta(days=days), usegmt=True)
            (feeds / f"{name}.xml").write_text(
                '<?xml version="1.0"?><rss><channel><title>t</title>'
                f"<item><title>x</title><pubDate>{when}</pubDate></item>"
                "</channel></rss>",
                encoding="utf-8",
            )
        return GenerationEngine(
            Config(sites=[_site(name)]), tmp_path / "c.json", feeds
        )

    @pytest.mark.parametrize(
        "detail",
        [
            "Failed to parse RSS XML",
            "net::ERR_CONNECTION_REFUSED",
            "timed out after 240s",
            "HTTP 500 Internal Server Error",
            "rate limited",
        ],
    )
    def test_a_transient_failure_keeps_the_seeded_feed(
        self, tmp_path, detail, monkeypatch
    ):
        from core.engine import SiteResult

        # The retry pass sleeps between attempts; without this the outcome is
        # right and the suite takes four minutes longer.
        monkeypatch.setattr("core.engine.RETRY_PASS_DELAY_SECONDS", 0)
        engine = self._engine(tmp_path, "s")
        before = (tmp_path / "feeds" / "s.xml").read_bytes()

        async def _pass(sites, fetcher, dedup):
            return [
                SiteResult(site=s.name, kind="failed", detail=detail) for s in sites
            ]

        engine._run_pass = _pass
        asyncio.run(engine.run())
        assert (tmp_path / "feeds" / "s.xml").read_bytes() == before, (
            f"a transient failure ({detail!r}) discarded a healthy feed"
        )

    def test_a_persistent_failure_still_deletes_the_seeded_feed(
        self, tmp_path, monkeypatch
    ):
        from core.engine import SiteResult

        monkeypatch.setattr("core.engine.RETRY_PASS_DELAY_SECONDS", 0)
        engine = self._engine(tmp_path, "s")

        async def _pass(sites, fetcher, dedup):
            return [
                SiteResult(site=s.name, kind="failed", detail="HTTP 404 Not Found")
                for s in sites
            ]

        engine._run_pass = _pass
        asyncio.run(engine.run())
        assert not (tmp_path / "feeds" / "s.xml").exists(), (
            "seeding turned into a way to keep a dead feed alive"
        )

    def test_a_transient_failure_still_drops_a_long_dead_seeded_feed(
        self, tmp_path, monkeypatch
    ):
        """The valve that reads the published file's age only has a file to
        read because CI now seeds one. Without a seed it always read None."""
        from core.engine import SiteResult

        monkeypatch.setattr("core.engine.RETRY_PASS_DELAY_SECONDS", 0)
        engine = self._engine(tmp_path, "s", days=200)

        async def _pass(sites, fetcher, dedup):
            return [
                SiteResult(site=s.name, kind="failed", detail="HTTP 500")
                for s in sites
            ]

        engine._run_pass = _pass
        asyncio.run(engine.run())
        assert not (tmp_path / "feeds" / "s.xml").exists(), (
            "a seeded feed nobody has refreshed in 200 days was kept"
        )


def test_min_items_refuses_a_half_rendered_listing(tmp_path: Path) -> None:
    """A bot-challenged or half-hydrated page renders one card instead of the
    listing: pontv shipped a feed whose single item was "1 (2026)" linking to
    /movies/1. Publishing that replaces a healthy feed with junk, so the site
    must fail -- and a transient failure keeps the last good copy instead of
    dropping the feed."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()

    class _OneCardFetcher:
        async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
            result = type(
                "FetchResult",
                (),
                {
                    "url": url,
                    "content": (
                        '<html><body><a href="/movies/1"><div class="c">'
                        "<h3>1</h3></div></a></body></html>"
                    ),
                    "status_code": 200,
                },
            )()
            if validator is not None:
                validator(result)
            return result

    def _site(name: str, min_items: int) -> SiteConfig:
        return SiteConfig(
            name=name,
            url="https://pontv.to/movies",
            method="http",
            item_selector="//a[contains(@href, '/movies/')]",
            title_selector="normalize-space(.//h3)",
            link_selector="concat('https://pontv.to', @href)",
            feed_file=f"{name}.xml",
            min_items=min_items,
        )

    guarded = _site("pontv-guarded", 10)
    engine = GenerationEngine(
        Config(sites=[guarded]), tmp_path / "cache.json", feeds_dir
    )
    result = asyncio.run(engine._process_site(guarded, _OneCardFetcher(), _DummyDedup()))
    assert result.kind == "failed"
    assert "10 required" in (result.detail or "")
    assert not (feeds_dir / "pontv-guarded.xml").exists()

    # The same one-card page publishes fine without the guard -- it is the
    # guard, not the fetch, that rejects it.
    unguarded = _site("pontv-unguarded", 0)
    engine2 = GenerationEngine(
        Config(sites=[unguarded]), tmp_path / "cache.json", feeds_dir
    )
    result2 = asyncio.run(engine2._process_site(unguarded, _OneCardFetcher(), _DummyDedup()))
    assert result2.kind == "ok"
    assert (feeds_dir / "pontv-unguarded.xml").exists()
