from __future__ import annotations

from pathlib import Path
from typing import List

import anyio

from core.config import Config, SiteConfig
from core.dedup import DedupStore
from core.feed import generate_rss_and_atom
from core.logging_utils import get_logger
from scraper.fetcher import Fetcher
from scraper.parser import Parser


logger = get_logger(__name__)


class GenerationEngine:
    """
    High‑level orchestration engine for generating feeds for all configured sites.
    """

    def __init__(
        self,
        config: Config,
        cache_path: Path,
        feeds_dir: Path,
    ) -> None:
        self._config = config
        self._cache_path = cache_path
        self._feeds_dir = feeds_dir
        self._parser = Parser()

    async def run(self) -> None:
        logger.info("engine.start", sites=len(self._config.sites))
        dedup = DedupStore.load(self._cache_path)
        fetcher = Fetcher()
        try:
            async with anyio.create_task_group() as tg:
                for site in self._config.sites:
                    tg.start_soon(self._process_site, site, fetcher, dedup)
        finally:
            await fetcher.close()
            dedup.save()
            logger.info("engine.done")

    async def _process_site(self, site: SiteConfig, fetcher: Fetcher, dedup: DedupStore) -> None:
        logger.info("site.start", site=site.name, url=site.url, method=site.method)
        try:
            result = await fetcher.fetch(site.url, method=site.method)
            items = self._parser.parse_items(
                result.content,
                item_selector=site.item_selector,
                title_selector=site.title_selector,
                link_selector=site.link_selector,
                description_selector=site.description_selector,
                date_selector=site.date_selector,
            )
            # Record seen URLs for dedup; feed contains all items from this run.
            list(dedup.filter_new(site.name, [item.link for item in items]))

            output_path = self._feeds_dir / site.feed_file
            generate_rss_and_atom(
                items,
                site_name=site.name,
                site_url=site.url,
                category=site.category,
                output_path=output_path,
            )
            logger.info("site.done", site=site.name, items=len(items))
        except Exception as exc:  # noqa: BLE001
            logger.error("site.error", site=site.name, error=str(exc))


