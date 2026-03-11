import asyncio
from pathlib import Path

from core.config import Config, SiteConfig
from core.engine import GenerationEngine
from scraper.parser import ParsedItem


class _FailingFetcher:
    async def fetch(self, url: str, method: str = "http"):
        raise RuntimeError("challenge page")


class _DummyDedup:
    def filter_new(self, site_name: str, urls):
        return urls


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

    assert not rss_path.exists()
    assert not atom_path.exists()


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
