from __future__ import annotations

from pathlib import Path
from typing import List
from urllib.parse import urlparse

import anyio

from core.config import Config, SiteConfig
from core.dedup import DedupStore
from core.feed import generate_failure_rss, generate_rss
from core.logging_utils import get_logger
from scraper.fetcher import Fetcher
from scraper.parser import ParsedItem, Parser


logger = get_logger(__name__)

GENERIC_BLOCKED_CONTENT_MARKERS = (
    "just a moment...",
    "attention required!",
    "cf-error-details",
    "performing security verification",
    "security service to protect against malicious bots",
    "error code 522",
    "error code: 521",
    "web server is down",
    "origin is unreachable",
    "connection timed out",
)


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
            result = await self._fetch_site(site, fetcher)
            items = self._parser.parse_items(
                result.content,
                item_selector=site.item_selector,
                title_selector=site.title_selector,
                link_selector=site.link_selector,
                description_selector=site.description_selector,
                date_selector=site.date_selector,
                allow_empty_title=site.allow_empty_title,
            )
            items = self._deduplicate_items(items)
            if site.max_items:
                items = items[: site.max_items]

            if site.detail_title_selector or site.detail_description_selector:
                items = await self._enrich_items(site, items, fetcher)

            items = [item for item in items if item.title and item.link]
            if not items:
                raise ValueError("No items parsed from validated content")

            # Record seen URLs for dedup; feed contains all items from this run.
            list(dedup.filter_new(site.name, [item.link for item in items]))

            output_path = self._feeds_dir / site.feed_file
            generate_rss(
                items,
                site_name=site.name,
                site_url=site.url,
                category=site.category,
                output_path=output_path,
            )
            self._remove_legacy_sidecar_outputs(output_path)
            logger.info("site.done", site=site.name, items=len(items))
        except Exception as exc:  # noqa: BLE001
            self._write_failure_feed(site, str(exc))
            logger.error("site.error", site=site.name, error=str(exc))

    async def _fetch_site(self, site: SiteConfig, fetcher: Fetcher):
        last_error: Exception | None = None
        urls = [site.url, *site.fallback_urls]
        for url in urls:
            try:
                return await fetcher.fetch(
                    url,
                    method=site.method,
                    validator=lambda result: self._validate_fetch_result(
                        site,
                        result.url,
                        result.content,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("site.fetch_candidate_failed", site=site.name, url=url, error=str(exc))
                last_error = exc

        raise RuntimeError(f"All fetch candidates failed for {site.name}") from last_error

    def _validate_fetch_result(self, site: SiteConfig, final_url: str, content: str) -> None:
        host = urlparse(final_url).netloc.lower()
        blocked_hosts = {host_name.lower() for host_name in site.blocked_final_hosts}
        allowed_hosts = {host_name.lower() for host_name in site.allowed_final_hosts}

        if blocked_hosts and host in blocked_hosts:
            raise ValueError(f"Blocked final host {host}")
        if allowed_hosts and host not in allowed_hosts:
            raise ValueError(f"Unexpected final host {host}")

        lowered = content.lower()
        markers = [*GENERIC_BLOCKED_CONTENT_MARKERS, *site.blocked_content_markers]
        for marker in markers:
            if marker.lower() in lowered:
                raise ValueError(f"Blocked content marker detected: {marker}")

    async def _enrich_items(
        self,
        site: SiteConfig,
        items: List[ParsedItem],
        fetcher: Fetcher,
    ) -> List[ParsedItem]:
        enriched_items: List[ParsedItem] = []
        detail_method = site.detail_method or site.method

        for item in items:
            title = item.title
            description = item.description

            if title and (description or not site.detail_description_selector):
                enriched_items.append(item)
                continue

            try:
                detail = await fetcher.fetch(
                    item.link,
                    method=detail_method,
                    validator=lambda result: self._validate_fetch_result(
                        site,
                        result.url,
                        result.content,
                    ),
                )

                if not title and site.detail_title_selector:
                    title = self._parser.extract_first(detail.content, site.detail_title_selector) or title
                if site.detail_description_selector:
                    description = (
                        self._parser.extract_first(detail.content, site.detail_description_selector)
                        or description
                    )
                await anyio.sleep(0.25)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "site.detail_enrichment_failed",
                    site=site.name,
                    link=item.link,
                    error=str(exc),
                )

            enriched_items.append(
                ParsedItem(
                    title=title,
                    link=item.link,
                    description=description,
                    pub_date=item.pub_date,
                )
            )

        return enriched_items

    def _write_failure_feed(self, site: SiteConfig, error_message: str) -> None:
        rss_path = self._feeds_dir / site.feed_file
        generate_failure_rss(
            site_name=site.name,
            site_url=site.url,
            output_path=rss_path,
            error_message=error_message,
        )
        self._remove_legacy_sidecar_outputs(rss_path)

    def _remove_legacy_sidecar_outputs(self, rss_path: Path) -> None:
        atom_path = rss_path.with_suffix(".atom.xml")
        if atom_path.exists():
            atom_path.unlink()
            logger.info("site.output_removed", path=str(atom_path))

    def _deduplicate_items(self, items: List[ParsedItem]) -> List[ParsedItem]:
        seen_links: set[str] = set()
        deduplicated: List[ParsedItem] = []
        for item in items:
            if item.link in seen_links:
                continue
            seen_links.add(item.link)
            deduplicated.append(item)
        return deduplicated
