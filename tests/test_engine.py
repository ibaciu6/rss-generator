import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

from core.config import Config, SiteConfig
from core.engine import GenerationEngine
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
                        '[{"date_gmt":"2026-03-16T04:56:18","link":"https://sitefilme.com/post-a/",'
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
                            '[{"date_gmt":"2026-03-16T04:56:18",'
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
    raise RuntimeError("challenge page")


def test_run_deletes_the_feed_when_a_site_fails_every_pass(tmp_path, monkeypatch) -> None:
    """After the retry pass the feed is removed, not replaced by a placeholder.

    A hard-down site gets no file at all, so generate_index skips it and it
    drops out of feeds.opml and the index.
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
