from __future__ import annotations

import random
from pathlib import Path
from typing import List
from urllib.parse import urljoin, urlparse

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
    "cf-challenge",
    "checking your browser",
    "performing security verification",
    "security service to protect against malicious bots",
    "error code 522",
    "error code: 521",
    "web server is down",
    "origin is unreachable",
    "connection timed out",
)
FETCH_METHOD_ORDER = ("http", "cloudscraper", "playwright")


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
            # Shuffle sites to randomize request order across runs
            sites = list(self._config.sites)
            random.shuffle(sites)
            
            async with anyio.create_task_group() as tg:
                for idx, site in enumerate(sites):
                    # Stagger site processing with random delays to avoid burst patterns
                    # Base delay of 1-3s per site position, plus random jitter
                    stagger_delay = idx * random.uniform(1.0, 3.0)
                    tg.start_soon(self._process_site_with_delay, site, fetcher, dedup, stagger_delay)
        finally:
            await fetcher.close()
            dedup.save()
            logger.info("engine.done")

    async def _process_site_with_delay(
        self, site: SiteConfig, fetcher: Fetcher, dedup: DedupStore, delay: float
    ) -> None:
        """Process a site after an initial delay to stagger requests."""
        if delay > 0:
            logger.info("site.stagger_wait", site=site.name, delay_s=round(delay, 2))
            await anyio.sleep(delay)
        await self._process_site(site, fetcher, dedup)

    async def _process_site(self, site: SiteConfig, fetcher: Fetcher, dedup: DedupStore) -> None:
        logger.info("site.start", site=site.name, url=site.url, method=site.method)
        try:
            items = await self._extract_items(site, fetcher)
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
                site_name=self._site_title(site),
                site_url=site.url,
                category=site.category,
                output_path=output_path,
            )
            self._remove_legacy_sidecar_outputs(output_path)
            logger.info("site.done", site=site.name, items=len(items))
        except Exception as exc:  # noqa: BLE001
            self._write_failure_feed(site, str(exc))
            logger.error("site.error", site=site.name, error=str(exc))

    async def _extract_items(self, site: SiteConfig, fetcher: Fetcher) -> List[ParsedItem]:
        errors: List[str] = []

        try:
            return await self._extract_html_items(site, fetcher)
        except Exception as exc:  # noqa: BLE001
            logger.warning("site.html_parse_failed", site=site.name, error=str(exc))
            errors.append(f"HTML scrape failed: {exc}")

        rss_urls = self._candidate_rss_urls(site)
        if rss_urls:
            try:
                result = await self._fetch_candidate_urls(site, rss_urls, fetcher, source_name="RSS")
                items = self._parser.parse_rss_items(result.content)
                if items:
                    return items
                raise ValueError("No items parsed from native RSS")
            except Exception as exc:  # noqa: BLE001
                logger.warning("site.rss_fallback_failed", site=site.name, error=str(exc))
                errors.append(f"Native RSS failed: {exc}")

        wordpress_urls = self._candidate_wordpress_urls(site)
        if wordpress_urls:
            try:
                result = await self._fetch_candidate_urls(
                    site,
                    wordpress_urls,
                    fetcher,
                    source_name="WordPress API",
                )
                items = self._parser.parse_wordpress_posts(result.content)
                if items:
                    return items
                raise ValueError("No posts parsed from WordPress API")
            except Exception as exc:  # noqa: BLE001
                logger.warning("site.wordpress_fallback_failed", site=site.name, error=str(exc))
                errors.append(f"WordPress API failed: {exc}")

        raise RuntimeError("; ".join(errors))

    async def _extract_html_items(self, site: SiteConfig, fetcher: Fetcher) -> List[ParsedItem]:
        method_errors: List[str] = []
        for method in self._candidate_fetch_methods(site):
            try:
                result = await self._fetch_candidate_urls(
                    site,
                    [site.url, *site.fallback_urls],
                    fetcher,
                    source_name="HTML",
                    method=method,
                )
                items = self._parser.parse_items(
                    result.content,
                    item_selector=site.item_selector,
                    title_selector=site.title_selector,
                    link_selector=site.link_selector,
                    description_selector=site.description_selector,
                    date_selector=site.date_selector,
                    allow_empty_title=site.allow_empty_title,
                )
                if items:
                    return items
                raise ValueError("No items parsed from validated HTML")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "site.html_method_failed",
                    site=site.name,
                    method=method,
                    error=str(exc),
                )
                method_errors.append(f"{method}: {exc}")

        raise RuntimeError(
            f"All HTML methods failed for {site.name}: {'; '.join(method_errors)}"
        )

    async def _fetch_candidate_urls(
        self,
        site: SiteConfig,
        urls: List[str],
        fetcher: Fetcher,
        source_name: str,
        method: str | None = None,
    ):
        last_error: Exception | None = None
        fetch_method = method or site.method
        for url in urls:
            try:
                return await fetcher.fetch(
                    url,
                    method=fetch_method,
                    validator=lambda result: self._validate_fetch_result(
                        site,
                        result.url,
                        result.content,
                    ),
                    playwright_wait_selector=site.playwright_wait_selector,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "site.fetch_candidate_failed",
                    site=site.name,
                    source=source_name,
                    method=fetch_method,
                    url=url,
                    error=str(exc),
                )
                last_error = exc

        if last_error is None:
            raise RuntimeError(f"No {source_name} candidates were configured for {site.name}")
        raise RuntimeError(
            f"All {source_name} candidates failed for {site.name} via {fetch_method}: {last_error}"
        ) from last_error

    def _validate_fetch_result(self, site: SiteConfig, final_url: str, content: str) -> None:
        host = urlparse(final_url).netloc.lower()
        blocked_hosts = {host_name.lower() for host_name in site.blocked_final_hosts}
        allowed_hosts = {host_name.lower() for host_name in site.allowed_final_hosts}

        if blocked_hosts and host in blocked_hosts:
            raise ValueError(f"Blocked final host {host}")
        if allowed_hosts and host not in allowed_hosts:
            raise ValueError(f"Unexpected final host {host}")

        lowered = content.lower()
        for marker in site.required_content_markers:
            if str(marker).lower() not in lowered:
                raise ValueError(f"Required content marker missing: {marker}")
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
                # Random delay between detail fetches to avoid rate limiting (1-4s)
                await anyio.sleep(random.uniform(1.0, 4.0))
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
            site_name=self._site_title(site),
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

    @staticmethod
    def _site_title(site: SiteConfig) -> str:
        return site.display_name or site.name

    @staticmethod
    def _candidate_fetch_methods(site: SiteConfig) -> List[str]:
        methods: List[str] = []
        seen: set[str] = set()
        for method in (site.method, *FETCH_METHOD_ORDER):
            if method in seen:
                continue
            seen.add(method)
            methods.append(method)
        return methods

    @staticmethod
    def _candidate_rss_urls(site: SiteConfig) -> List[str]:
        return [urljoin(root_url, "feed/") for root_url in GenerationEngine._root_urls(site)]

    @staticmethod
    def _candidate_wordpress_urls(site: SiteConfig) -> List[str]:
        limit = site.max_items or 25
        return [
            urljoin(root_url, f"wp-json/wp/v2/posts?per_page={limit}&_embed=1")
            for root_url in GenerationEngine._root_urls(site)
        ]

    @staticmethod
    def _root_urls(site: SiteConfig) -> List[str]:
        roots: List[str] = []
        seen: set[str] = set()
        for raw_url in [site.url, *site.fallback_urls]:
            parsed = urlparse(raw_url)
            if not parsed.scheme or not parsed.netloc:
                continue
            root_url = f"{parsed.scheme}://{parsed.netloc}/"
            if root_url in seen:
                continue
            seen.add(root_url)
            roots.append(root_url)
        return roots
