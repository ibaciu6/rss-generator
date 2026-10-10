from __future__ import annotations

import os
import random
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import anyio

from core.config import Config, SiteConfig
from core.dedup import DedupStore
from core.feed import generate_rss
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

# Limit how many sites we scrape in parallel. Most work is network I/O so
# higher concurrency helps; cap is still needed because Playwright sessions
# have their own cap (CapacityLimiter in fetcher.py).
MAX_CONCURRENT_SITES = 6

# Hard per-site wall-clock cap. With 6 concurrent sites and a Playwright
# CapacityLimiter(2), a site may queue for up to ~90 s before getting a
# browser slot and then need another ~60 s to scrape → 240 s gives enough
# headroom without masking truly hung sessions.
SITE_TIMEOUT_SECONDS = 240

# Pause before retrying the sites that failed the first pass.
#
# Most failures are transient, and the first pass deliberately runs up to
# MAX_CONCURRENT_SITES sites at once behind a single proxy, so a site can lose
# a rate-limit race it would win on its own. A short pause is enough to let
# the burst settle; a second failure is treated as the site being unavailable.
#
# Only one retry pass. Seven of the 70 sites are hard-down (orange.ro serves
# HTTP 500 on five venue pages, uflix.cc/uflix.to return 522), and retrying
# those costs a full SITE_TIMEOUT_SECONDS each for nothing.
RETRY_PASS_DELAY_SECONDS = 45

# A feed whose newest item is older than this is considered dead and removed.
#
# Only feeds that actually carry publication dates can be judged. The
# streaming and cinema feeds write no `pubDate` at all — their only date signal
# is a release year in the title (`Star Wars: The Force Awakens (2015)` showing
# this week), so a naive age check would delete all 20 of them while they are
# serving fresh daily listings. No dates means no evidence of staleness, so those
# feeds are left alone.
#
# 90 days, not 30. A reachable site is not the same as a useful one, but plenty
# of sources here are small and irregular: vedem-just is a solo legal-news blog
# that posts when it has news and had published nothing for 63 days, and doublepulsar
# 59. At 30 days both were deleted while plainly alive and healthy — the reader saw
# "Not available" for a site that had simply not had a story. Three months of
# silence on a site we can still fetch and parse is the point where the feed really
# is worth dropping. A threshold tuned to the news sites punishes the small blogs.
STALE_FEED_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class SiteResult:
    """Outcome of one generation attempt.

    ``failed`` and ``stale`` both end in the feed being removed, but they are
    reported separately: a failure is "this run could not build it", a stale feed
    is "it built fine, the source has gone quiet". Only failures go into
    ``logs/failed_feeds.txt``.
    """

    site: str
    kind: str  # "ok" | "failed" | "stale"
    detail: str = ""


# Failure signatures that mean "come back next run", as opposed to "this source
# is finished". Matched case-insensitively against the failure detail.
#
# The test is deliberately one-sided: anything not positively recognised as the
# source being gone is treated as transient and the previous feed is kept. The
# cost of being wrong in that direction is a stale feed surviving one run; the
# cost of the other direction is a healthy source vanishing from the index and
# coming back an hour later, which is what deleting on a timeout produced.
_TRANSIENT_FAILURE_RE = re.compile(
    r"timed?\s*out|timeout|"
    r"ERR_CONNECTION_REFUSED|ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED|"
    r"ERR_NAME_NOT_RESOLVED|ERR_NETWORK_CHANGED|ERR_INTERNET_DISCONNECTED|"
    r"Temporary failure in name resolution|name or service not known|"
    r"connection (?:refused|reset|aborted|closed|error)|"
    r"failed to parse RSS XML|parse RSS|"
    r"rate limit|too many requests|429|"
    r"\b5\d\d\b|"
    r"bot|challenge|captcha|just a moment|checking your browser",
    re.IGNORECASE,
)

# The only signals that the source itself says it is finished. Everything else
# is assumed transient. 410 is here because it is the affirmative form of
# "gone"; 403 is deliberately not, since a 403 is far more often a bot wall
# than a publisher withdrawing a feed.
_SOURCE_GONE_RE = re.compile(
    r"\b404\b|\b410\b|\bgone\b|no longer exists|has been removed|feed (?:has been )?removed",
    re.IGNORECASE,
)


def _source_is_gone(detail: str) -> bool:
    """True when a failure means the feed is gone, not that this run was unlucky.

    Split out as a function so the rule is testable on its own: it decides
    whether a published file gets unlinked, which is the one irreversible thing
    the pipeline does.
    """
    text = detail or ""
    if _TRANSIENT_FAILURE_RE.search(text):
        return False
    return bool(_SOURCE_GONE_RE.search(text))


class GenerationEngine:
    """
    High‑level orchestration engine for generating feeds for all configured sites.
    """

    def __init__(
        self,
        config: Config,
        cache_path: Path,
        feeds_dir: Path,
        only_sites: Sequence[str] | None = None,
    ) -> None:
        self._config = config
        self._cache_path = cache_path
        self._feeds_dir = feeds_dir
        self._only_sites = set(only_sites) if only_sites else None
        self._parser = Parser()

    def _select_sites(self) -> tuple[list[SiteConfig], list[str]]:
        """Return the sites to generate, honouring an optional name/feed_file filter.

        A site matches when its ``name`` or its ``feed_file`` stem equals one of
        the requested values, so both ``showrss`` and ``showrss.xml`` work.
        Returns the selection plus the names of any unmatched requests.
        """
        enabled = [site for site in self._config.sites if site.enabled]
        if not self._only_sites:
            return enabled, []

        requested = {s.strip() for s in self._only_sites if s and s.strip()}
        # Accept the feed_file with or without its .xml suffix.
        wanted = requested | {s.removesuffix(".xml") for s in requested}
        selected = [
            site
            for site in enabled
            if site.name in wanted or Path(site.feed_file).stem in wanted
        ]
        # Report only the values the user actually typed that matched nothing,
        # so `--site showrss.xml` is not flagged just because .xml was stripped.
        matched = {s.name for s in selected} | {Path(s.feed_file).stem for s in selected}
        unmatched = [r for r in sorted(requested) if r not in matched and r.removesuffix(".xml") not in matched]
        return selected, unmatched

    async def run(self) -> None:
        # Filter out disabled sites, then apply the optional single-site filter.
        enabled_sites, unmatched = self._select_sites()
        disabled_count = len(self._config.sites) - len([s for s in self._config.sites if s.enabled])

        if disabled_count > 0 and not self._only_sites:
            disabled_names = [site.name for site in self._config.sites if not site.enabled]
            logger.info(
                "engine.disabled_sites",
                disabled_count=disabled_count,
                disabled_sites=disabled_names,
            )

        if unmatched:
            logger.warning(
                "engine.site_filter_unmatched",
                requested=sorted(self._only_sites or ()),
                unmatched=unmatched,
            )

        if not enabled_sites:
            raise ValueError(
                "No enabled sites to generate"
                + (f" matching {sorted(self._only_sites)}" if self._only_sites else "")
            )

        logger.info(
            "engine.start",
            sites=len(enabled_sites),
            total_configured=len(self._config.sites),
            max_concurrent=MAX_CONCURRENT_SITES,
        )
        dedup = DedupStore.load(self._cache_path)
        fetcher = Fetcher()
        try:
            results = await self._run_pass(enabled_sites, fetcher, dedup)

            failed = {r.site: r.detail for r in results if r.kind == "failed"}
            if failed:
                # A second, serial-ish pass for whatever failed. Most failures
                # are transient: the first pass runs up to MAX_CONCURRENT_SITES
                # sites at once behind one proxy, so a site can lose a race it
                # would win on its own. Sites that fail again are genuinely
                # unavailable, not unlucky.
                logger.info(
                    "engine.retry_pass",
                    retrying=len(failed),
                    sites=sorted(failed),
                    delay_s=RETRY_PASS_DELAY_SECONDS,
                )
                await anyio.sleep(RETRY_PASS_DELAY_SECONDS)
                failed_sites = [s for s in enabled_sites if s.name in failed]
                results = await self._run_pass(failed_sites, fetcher, dedup)

            by_name = {r.site: r for r in results}
            for site in enabled_sites:
                result = by_name.get(site.name)
                if result is None or result.kind == "ok":
                    continue
                if result.kind == "stale":
                    # The source is reachable and answered, it just has nothing
                    # recent. Removed too, but reported separately: this is not
                    # a generation failure and must not reach failed_feeds.txt.
                    self._drop_failed_feed(site, result.detail, event="site.feed_stale")
                    continue
                if not _source_is_gone(result.detail):
                    # Transient. Keep whatever the last good run published: a
                    # feed that is a few hours stale is worth far more to a
                    # reader than no feed at all, and deleting on a single bad
                    # run is how healthy sources disappear. Measured on a real
                    # run: six cinema malls all "timed out after 240s" in the
                    # same pass, and four established blogs were dropped for
                    # "failed to parse RSS XML" -- three of which serve valid
                    # RSS from a residential IP and were simply handed a bot
                    # challenge by the datacenter address. All seven came back
                    # an hour later. A 240s timeout is the definition of
                    # transient; deleting on it cannot be defended.
                    #
                    # The one thing that must still be dropped: a source that
                    # is dead *and* answers with a 5xx or a timeout would
                    # otherwise be kept forever, because the staleness check
                    # only runs on a successful generation. The feed we already
                    # hold is the only clock available -- CI checks out a fresh
                    # tree each run, so there is no cross-run state -- and if
                    # its newest item is older than the stale threshold, the
                    # source has nothing left to publish.
                    age = self._published_age_days(site)
                    if age is not None and age > STALE_FEED_MAX_AGE_DAYS:
                        self._drop_failed_feed(
                            site,
                            f"{result.detail[:160]}; last published item is {age} days old",
                            event="site.feed_stale",
                        )
                        continue
                    logger.warning(
                        "engine.transient_failure site=%s detail=%s keeping_previous",
                        site.name,
                        result.detail[:120],
                    )
                    continue
                self._drop_failed_feed(site, result.detail, event="site.error")
        finally:
            await fetcher.close()
            dedup.save()
            logger.info("engine.done")

    async def _run_pass(
        self,
        sites: Sequence[SiteConfig],
        fetcher: Fetcher,
        dedup: DedupStore,
    ) -> list[SiteResult]:
        """Generate every site once, returning one result per site."""
        if not sites:
            return []

        ordered = list(sites)
        random.shuffle(ordered)

        semaphore = anyio.Semaphore(MAX_CONCURRENT_SITES)
        results: list[SiteResult] = []

        async with anyio.create_task_group() as tg:
            for idx, site in enumerate(ordered):
                # Stagger site starts a bit to avoid bursts. Concurrency
                # itself is capped by the semaphore.
                stagger_delay = idx * random.uniform(0.5, 1.5)
                tg.start_soon(
                    self._process_site_with_delay,
                    site,
                    fetcher,
                    dedup,
                    stagger_delay,
                    semaphore,
                    results,
                )
        return results

    async def _process_site_with_delay(
        self,
        site: SiteConfig,
        fetcher: Fetcher,
        dedup: DedupStore,
        delay: float,
        semaphore: anyio.Semaphore,
        results: list[SiteResult],
    ) -> None:
        """Process a site after an initial delay; append its result."""
        if delay > 0:
            logger.info("site.stagger_wait", site=site.name, delay_s=round(delay, 2))
            await anyio.sleep(delay)
        result: SiteResult
        async with semaphore:
            with anyio.move_on_after(SITE_TIMEOUT_SECONDS) as cancel_scope:
                result = await self._process_site(site, fetcher, dedup)
            if cancel_scope.cancelled_caught:
                result = SiteResult(
                    site=site.name,
                    kind="failed",
                    detail=f"Site timed out after {SITE_TIMEOUT_SECONDS}s",
                )
                logger.warning(
                    "site.timeout",
                    site=site.name,
                    timeout_s=SITE_TIMEOUT_SECONDS,
                )
        results.append(result)

    async def _process_site(
        self, site: SiteConfig, fetcher: Fetcher, dedup: DedupStore
    ) -> SiteResult:
        """Generate one site's feed.

        The caller owns the outcome: a failure here is not yet final, because
        ``run()`` retries failed sites once more before anything is removed.
        """
        logger.info("site.start", site=site.name, url=site.url, method=site.method)
        try:
            items = await self._extract_items(site, fetcher)
            items = self._deduplicate_items(items)
            if site.max_items:
                items = items[: site.max_items]

            if site.detail_title_selector or site.detail_description_selector:
                items = await self._enrich_items(site, items, fetcher)

            items = self._filter_items(items, site)

            items = [item for item in items if item.title and item.link]
            if not items:
                raise ValueError("No items parsed from validated content")
            if site.min_items and len(items) < site.min_items:
                # A challenge page or a half-hydrated Next.js shell renders one
                # card instead of the listing. Publishing that would replace a
                # healthy feed with a single junk item; failing keeps the last
                # good copy (transient failures never drop a feed).
                raise ValueError(
                    f"Only {len(items)} items parsed; {site.min_items} required"
                )

            age = self._staleness_days(items)
            if age is not None and age > STALE_FEED_MAX_AGE_DAYS:
                newest = max(
                    i.pub_date for i in items if i.pub_date  # type: ignore[type-var]
                )
                logger.info(
                    "site.feed_stale",
                    site=site.name,
                    age_days=age,
                    newest=newest.date().isoformat(),
                    threshold_days=STALE_FEED_MAX_AGE_DAYS,
                )
                return SiteResult(
                    site=site.name,
                    kind="stale",
                    detail=(
                        f"Newest item is {age} days old "
                        f"({newest.date().isoformat()}); threshold is "
                        f"{STALE_FEED_MAX_AGE_DAYS} days"
                    ),
                )

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
            return SiteResult(site=site.name, kind="ok")
        except Exception as exc:
            logger.warning(
                "site.attempt_failed", site=site.name, error=str(exc)[:200]
            )
            return SiteResult(site=site.name, kind="failed", detail=str(exc))

    def _published_age_days(self, site: SiteConfig) -> int | None:
        """Age of the newest item in the feed file we already published.

        The safety valve for a source that is dead *and* answers with a 5xx or a
        timeout: the transient rule would otherwise keep it forever, because the
        staleness check only runs on a successful generation. Reads the file
        rather than any run history, because there is no run history to read --
        CI checks out a fresh tree, so the published file is the only record
        that survives between deployments.

        ``None`` when the file is missing, unparseable, or carries no dates.
        The streaming and cinema feeds have no ``pubDate`` at all, and "no date"
        must never read as "infinitely old".
        """
        rss_path = self._feeds_dir / site.feed_file
        if not rss_path.is_file():
            return None
        try:
            root = ET.parse(rss_path).getroot()
        except (ET.ParseError, OSError):
            return None
        channel = root.find("channel")
        if channel is None:
            return None
        dates: list[datetime] = []
        for item in channel.findall("item"):
            raw = item.findtext("pubDate")
            if not raw:
                continue
            try:
                dt = parsedate_to_datetime(raw.strip())
            except (TypeError, ValueError):
                continue
            if dt is not None:
                dates.append(dt if dt.tzinfo else dt.replace(tzinfo=UTC))
        if not dates:
            return None
        return (datetime.now(UTC) - max(dates)).days

    @staticmethod
    def _staleness_days(items: Sequence[ParsedItem]) -> int | None:
        """Age in days of the newest dated item, or ``None`` if none are dated.

        ``None`` is the important case: the streaming and cinema feeds carry no
        ``pubDate`` at all, and treating "no date" as "infinitely old" would
        delete every one of them.
        """
        dated = [i.pub_date for i in items if i.pub_date is not None]
        if not dated:
            return None
        newest = max(dated)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        return (datetime.now(UTC) - newest).days

    async def _extract_items(self, site: SiteConfig, fetcher: Fetcher) -> list[ParsedItem]:
        # Native RSS/Atom feeds (e.g. Reddit .rss) skip HTML/XPath scraping entirely.
        if site.method == "rss":
            errors: list[str] = []
            for url in [site.url, *site.fallback_urls]:
                parsed: list[ParsedItem] | None = None

                def _validate_rss(result) -> None:
                    nonlocal parsed
                    items = self._parser.parse_rss_items(result.content)
                    if not items:
                        raise ValueError("no items in native RSS")
                    parsed = items

                try:
                    await fetcher.fetch(url, method="http", validator=_validate_rss)
                    if parsed:
                        return parsed
                except Exception as exc:
                    logger.warning("site.rss_fetch_failed", site=site.name, url=url, error=str(exc))
                    errors.append(f"native RSS fetch failed ({url}): {exc}")
            raise RuntimeError("; ".join(errors))

        # JSON API feeds (e.g. TMDB API) parse JSON directly
        if site.json_item_path:
            return await self._extract_json_items(site, fetcher)

        errors: list[str] = []

        try:
            return await self._extract_html_items(site, fetcher)
        except Exception as exc:
            logger.warning("site.html_parse_failed", site=site.name, error=str(exc))
            errors.append(f"HTML scrape failed: {exc}")

        rss_urls = self._candidate_rss_urls(site)
        if rss_urls:
            try:
                result = await self._fetch_candidate_urls(
                    site,
                    rss_urls,
                    fetcher,
                    source_name="RSS",
                    require_listing_markers=False,
                )
                items = self._parser.parse_rss_items(result.content)
                if items:
                    return items
                raise ValueError("No items parsed from native RSS")
            except Exception as exc:
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
                    require_listing_markers=False,
                )
                items = self._parser.parse_wordpress_posts(result.content)
                if items:
                    return items
                raise ValueError("No posts parsed from WordPress API")
            except Exception as exc:
                logger.warning("site.wordpress_fallback_failed", site=site.name, error=str(exc))
                errors.append(f"WordPress API failed: {exc}")

        raise RuntimeError("; ".join(errors))

    async def _extract_json_items(self, site: SiteConfig, fetcher: Fetcher) -> list[ParsedItem]:
        """Extract items from a JSON API feed."""
        if not site.json_item_path:
            return []
        
        errors: list[str] = []
        for raw_url in [site.url, *site.fallback_urls]:
            # Config may reference a secret as ${NAME}; expand it only for the
            # request. The config, and everything derived from it (OPML htmlUrl,
            # index Source link), keeps the unexpanded value, so a key never
            # reaches the committed output.
            url = os.path.expandvars(raw_url)
            try:
                result = await fetcher.fetch(
                    url,
                    method="http",
                    validator=lambda result, s=site: self._validate_fetch_result(
                        s, result.url, result.content, require_listing_markers=False
                    ),
                )
                items = self._parser.parse_tmdb_json(
                    result.content,
                    item_path=site.json_item_path or "results",
                    title_field=site.json_title_field or "title",
                    link_field=site.json_link_field or "id",
                    poster_field=site.json_poster_field or "poster_path",
                    date_field=site.json_date_field or "release_date",
                    name_field=site.json_name_field or "name",
                    air_date_field=site.json_air_date_field or "first_air_date",
                    link_base=site.json_link_base or "https://www.themoviedb.org",
                )
                if items:
                    return items
                raise ValueError("No items parsed from JSON API")
            except Exception as exc:
                logger.warning("site.json_fallback_failed", site=site.name, error=str(exc))
                errors.append(f"JSON API failed ({raw_url}): {exc}")
        
        raise RuntimeError("; ".join(errors))

    async def _extract_html_items(self, site: SiteConfig, fetcher: Fetcher) -> list[ParsedItem]:
        method_errors: list[str] = []
        marker_modes: list[bool] = [True]
        if self._listing_marker_groups(site):
            marker_modes.append(False)

        for method in self._candidate_fetch_methods(site):
            for require_markers in marker_modes:
                try:
                    result = await self._fetch_candidate_urls(
                        site,
                        [site.url, *site.fallback_urls],
                        fetcher,
                        source_name="HTML",
                        method=method,
                        require_listing_markers=require_markers,
                    )
                    items = self._parser.parse_items(
                        result.content,
                        item_selector=site.item_selector,
                        title_selector=site.title_selector,
                        link_selector=site.link_selector,
                        description_selector=site.description_selector,
                        date_selector=site.date_selector,
                        allow_empty_title=site.allow_empty_title,
                        title_transform=site.title_transform,
                        category_selector=site.category_selector,
                    )
                    if items:
                        if not require_markers and self._listing_marker_groups(site):
                            logger.warning(
                                "site.listing_relaxed_markers",
                                site=site.name,
                                method=method,
                                note="accepted HTML after skipping listing marker checks (blocked/challenge rules still apply)",
                            )
                        # Multi-page: fetch pages 2..N to build a deeper pool before filtering
                        if site.pages > 1:
                            items = await self._fetch_extra_pages(
                                site, fetcher, method, require_markers, items,
                            )
                        return items
                    raise ValueError("No items parsed from validated HTML")
                except Exception as exc:
                    err = str(exc)
                    if require_markers and self._listing_marker_groups(site) and (
                        "Required content marker" in err or "no group matched" in err
                    ):
                        continue
                    logger.warning(
                        "site.html_method_failed",
                        site=site.name,
                        method=method,
                        error=err,
                    )
                    method_errors.append(f"{method}: {err}")
                    break

        raise RuntimeError(
            f"All HTML methods failed for {site.name}: {'; '.join(method_errors)}"
        )

    async def _fetch_extra_pages(
        self,
        site: SiteConfig,
        fetcher: Fetcher,
        method: str,
        require_markers: bool,
        items: list[ParsedItem],
    ) -> list[ParsedItem]:
        """Fetch pages 2..N for WordPress-style pagination and append items."""
        base = site.url.rstrip("/")
        for page_num in range(2, site.pages + 1):
            page_url = f"{base}/page/{page_num}/"
            try:
                result = await fetcher.fetch(
                    page_url,
                    method=method,
                    playwright_wait_selector=site.playwright_wait_selector,
                    playwright_scroll_to=site.playwright_scroll_to,
                )
                page_items = self._parser.parse_items(
                    result.content,
                    item_selector=site.item_selector,
                    title_selector=site.title_selector,
                    link_selector=site.link_selector,
                    description_selector=site.description_selector,
                    date_selector=site.date_selector,
                    allow_empty_title=site.allow_empty_title,
                    title_transform=site.title_transform,
                    category_selector=site.category_selector,
                )
                items.extend(page_items)
                logger.info(
                    "site.page_fetched",
                    site=site.name,
                    page=page_num,
                    items=len(page_items),
                )
            except Exception as exc:
                logger.warning(
                    "site.page_fetch_failed",
                    site=site.name,
                    page=page_num,
                    error=str(exc),
                )
        logger.info(
            "site.pages_done",
            site=site.name,
            pages=site.pages,
            total_items=len(items),
        )
        return items

    async def _fetch_candidate_urls(
        self,
        site: SiteConfig,
        urls: list[str],
        fetcher: Fetcher,
        source_name: str,
        method: str | None = None,
        *,
        require_listing_markers: bool = True,
    ):
        last_error: Exception | None = None
        fetch_method = method or site.method
        for url in urls:
            try:
                return await fetcher.fetch(
                    url,
                    method=fetch_method,
                    validator=lambda result, s=site, r=require_listing_markers: self._validate_fetch_result(
                        s,
                        result.url,
                        result.content,
                        require_listing_markers=r,
                    ),
                    playwright_wait_selector=site.playwright_wait_selector,
                    playwright_scroll_to=site.playwright_scroll_to,
                    playwright_wait_until=site.playwright_wait_until,
                )
            except Exception as exc:
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

    @staticmethod
    def _listing_marker_groups(site: SiteConfig) -> tuple[tuple[str, ...], ...]:
        if site.required_content_marker_groups:
            return site.required_content_marker_groups
        if site.required_content_markers:
            return (tuple(site.required_content_markers),)
        return ()

    def _validate_fetch_result(
        self,
        site: SiteConfig,
        final_url: str,
        content: str,
        *,
        require_listing_markers: bool = True,
    ) -> None:
        host = urlparse(final_url).netloc.lower()
        blocked_hosts = {host_name.lower() for host_name in site.blocked_final_hosts}
        allowed_hosts = {host_name.lower() for host_name in site.allowed_final_hosts}

        if blocked_hosts and host in blocked_hosts:
            raise ValueError(f"Blocked final host {host}")
        if allowed_hosts and host not in allowed_hosts:
            raise ValueError(f"Unexpected final host {host}")

        lowered = content.lower()
        if require_listing_markers:
            groups = self._listing_marker_groups(site)
            if groups:
                matched = any(
                    all(str(marker).lower() in lowered for marker in group) for group in groups
                )
                if not matched:
                    raise ValueError(
                        "Required content marker groups: no group matched "
                        f"(tried {len(groups)} group(s))"
                    )
        markers = [*GENERIC_BLOCKED_CONTENT_MARKERS, *site.blocked_content_markers]
        for marker in markers:
            if marker.lower() in lowered:
                raise ValueError(f"Blocked content marker detected: {marker}")

    async def _enrich_items(
        self,
        site: SiteConfig,
        items: list[ParsedItem],
        fetcher: Fetcher,
    ) -> list[ParsedItem]:
        enriched_items: list[ParsedItem] = []
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
                    validator=lambda result, s=site: self._validate_fetch_result(
                        s,
                        result.url,
                        result.content,
                        require_listing_markers=False,
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
            except Exception as exc:
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

    def _drop_failed_feed(
        self, site: SiteConfig, error_message: str, event: str = "site.error"
    ) -> None:
        """Delete a site feed that should not be published.

        Two reasons reach here. A site that could not be generated at all, once
        the retry pass is exhausted. Or a site that answered but whose newest
        item is older than ``STALE_FEED_MAX_AGE_DAYS`` -- the source is gone
        quiet, and a feed of last month's news is worse than no feed.

        Either way the file is deleted rather than replaced: no stale items from
        the last deployment (there is no restore path) and no placeholder.
        ``generate_index.py`` skips feeds with no file, so the site drops out of
        ``feeds.opml`` and shows as "Not available" on the index.

        The caller's ``event`` keeps the two causes distinguishable in the logs.
        """
        rss_path = self._feeds_dir / site.feed_file
        removed = False
        if rss_path.exists():
            rss_path.unlink()
            removed = True
        self._remove_legacy_sidecar_outputs(rss_path)
        log = logger.error if event == "site.error" else logger.info
        # The reason MUST land in a field named "error" for site.error: the CI
        # step that builds logs/failed_feeds.txt reads payload["error"], and
        # renaming the field silently turned the report into "<site>: None".
        detail_field = "error" if event == "site.error" else "reason"
        log(
            event,
            site=site.name,
            feed=str(rss_path),
            existed=removed,
            **{detail_field: error_message[:200]},
        )

    def _remove_legacy_sidecar_outputs(self, rss_path: Path) -> None:
        atom_path = rss_path.with_suffix(".atom.xml")
        if atom_path.exists():
            atom_path.unlink()
            logger.info("site.output_removed", path=str(atom_path))

    def _deduplicate_items(self, items: list[ParsedItem]) -> list[ParsedItem]:
        seen_links: set[str] = set()
        deduplicated: list[ParsedItem] = []
        for item in items:
            if item.link in seen_links:
                continue
            seen_links.add(item.link)
            deduplicated.append(item)
        return deduplicated

    def _filter_items(self, items: list[ParsedItem], site: SiteConfig) -> list[ParsedItem]:
        """Filter out low-quality items before feed generation.

        Applied at scrape time (before TMDb enrichment). Covers:
        - "Coming soon" titles (generic)
        - Site-specific title filter patterns (e.g. erotic keywords)
        """
        filtered: list[ParsedItem] = []
        for item in items:
            if self._is_item_filtered(item, site):
                continue
            filtered.append(item)
        return filtered

    def _is_item_filtered(self, item: ParsedItem, site: SiteConfig) -> bool:
        if item.title and "coming soon" in item.title.lower():
            logger.info("item.filtered.coming_soon", title=item.title, site=site.name)
            return True

        for pattern in site.title_filter_patterns:
            if item.title and re.search(pattern, item.title):
                logger.info(
                    "item.filtered.title_pattern",
                    title=item.title,
                    site=site.name,
                    pattern=pattern,
                )
                return True
            if item.link and re.search(pattern, item.link):
                logger.info(
                    "item.filtered.link_pattern",
                    link=item.link,
                    site=site.name,
                    pattern=pattern,
                )
                return True

        if site.blocked_categories and item.categories:
            for cat in item.categories:
                if cat in site.blocked_categories:
                    logger.info(
                        "item.filtered.category",
                        category=cat,
                        title=item.title,
                        site=site.name,
                    )
                    return True

        return False

    @staticmethod
    def _site_title(site: SiteConfig) -> str:
        return site.display_name or site.name

    @staticmethod
    def _candidate_fetch_methods(site: SiteConfig) -> list[str]:
        methods: list[str] = []
        seen: set[str] = set()
        for method in (site.method, *FETCH_METHOD_ORDER):
            if method in seen:
                continue
            seen.add(method)
            methods.append(method)
        return methods

    @staticmethod
    def _candidate_rss_urls(site: SiteConfig) -> list[str]:
        """
        Candidate native RSS URLs to try when HTML scraping fails.

        Many WordPress themes publish a per-archive feed at ``<listing>/feed/``
        (e.g. ``xfilme.ro/filme/feed/`` returns 10 movie posts while the root
        ``/feed/`` is empty). Try those listing-local feeds first, then fall
        back to ``<root>/feed/``.
        """
        candidates: list[str] = []
        seen: set[str] = set()

        for raw_url in [site.url, *site.fallback_urls]:
            parsed = urlparse(raw_url)
            if not parsed.scheme or not parsed.netloc:
                continue
            path = parsed.path or "/"
            if not path.endswith("/"):
                path = path + "/"
            listing_feed = f"{parsed.scheme}://{parsed.netloc}{path}feed/"
            if listing_feed not in seen:
                seen.add(listing_feed)
                candidates.append(listing_feed)

        for root_url in GenerationEngine._root_urls(site):
            root_feed = urljoin(root_url, "feed/")
            if root_feed not in seen:
                seen.add(root_feed)
                candidates.append(root_feed)

        return candidates

    @staticmethod
    def _candidate_wordpress_urls(site: SiteConfig) -> list[str]:
        limit = site.max_items or 25
        return [
            urljoin(root_url, f"wp-json/wp/v2/posts?per_page={limit}&_embed=1")
            for root_url in GenerationEngine._root_urls(site)
        ]

    @staticmethod
    def _root_urls(site: SiteConfig) -> list[str]:
        roots: list[str] = []
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
