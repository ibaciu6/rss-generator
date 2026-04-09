import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

from core.config import Config, SiteConfig
from core.engine import GenerationEngine
from scraper.parser import ParsedItem


class _FailingFetcher:
    async def fetch(self, url: str, method: str = "http", validator=None, **kwargs):
        raise RuntimeError("challenge page")


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


def test_process_site_removes_stale_outputs_on_failure(tmp_path: Path) -> None:
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    rss_path = feeds_dir / "sitefilme.xml"
    atom_path = feeds_dir / "sitefilme.atom.xml"
    rss_path.write_text("stale rss", encoding="utf-8")
    atom_path.write_text("stale atom", encoding="utf-8")

    site = SiteConfig(
        name="sitefilme",
        url="https://sitefilme.com/",
        method="playwright",
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        feed_file="sitefilme.xml",
    )
    engine = GenerationEngine(Config(sites=[site]), tmp_path / "cache.json", feeds_dir)

    asyncio.run(engine._process_site(site, _FailingFetcher(), _DummyDedup()))

    root = ET.parse(rss_path).getroot()
    channel = root.find("channel")

    assert rss_path.exists()
    assert not atom_path.exists()
    assert channel is not None
    assert channel.findtext("title") == "sitefilme (unavailable)"
    assert "HTML scrape failed:" in (channel.findtext("description") or "")
    assert "Native RSS failed:" in (channel.findtext("description") or "")
    assert "WordPress API failed:" in (channel.findtext("description") or "")


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
