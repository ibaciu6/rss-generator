from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import anyio
import cloudscraper
import httpx
from playwright.sync_api import sync_playwright
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from core.logging_utils import get_logger


logger = get_logger(__name__)

# Headers that mimic a desktop browser in a Western locale to avoid
# geo/lang-based redirects (e.g. sitefilme.com serving 56.com to bots).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class FetchResult:
    url: str
    content: str
    status_code: int


class FetchError(Exception):
    pass


class Fetcher:
    """
    Multi‑strategy fetcher with automatic fallback:
    HTTP client -> cloudscraper -> Playwright.
    Uses browser-like headers and locale so sites that redirect by
    User-Agent or Accept-Language (e.g. sitefilme.com) serve the same
    content as a manual visit.
    """

    def __init__(self, timeout: float = 20.0):
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str, method: str = "http") -> FetchResult:
        """
        Fetch a URL using the configured strategy with fallback.
        """
        logger.info("fetch.start", url=url, method=method)
        strategies = self._build_strategy_chain(method)

        last_error: Optional[Exception] = None
        for strategy in strategies:
            try:
                html = await strategy(url)
                logger.info("fetch.success", url=url, strategy=strategy.__name__)
                return FetchResult(url=url, content=html, status_code=200)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fetch.strategy_failed",
                    url=url,
                    strategy=strategy.__name__,
                    error=str(exc),
                )
                last_error = exc

        raise FetchError(f"Failed to fetch {url!r}") from last_error

    def _build_strategy_chain(self, method: str):
        chain = []
        if method == "http":
            chain = [self._fetch_http, self._fetch_cloudscraper, self._fetch_playwright]
        elif method == "cloudscraper":
            chain = [self._fetch_cloudscraper, self._fetch_http, self._fetch_playwright]
        elif method == "playwright":
            chain = [self._fetch_playwright, self._fetch_http, self._fetch_cloudscraper]
        else:
            chain = [self._fetch_http, self._fetch_cloudscraper, self._fetch_playwright]
        return chain

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    async def _fetch_http(self, url: str) -> str:
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.text

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    async def _fetch_cloudscraper(self, url: str) -> str:
        # cloudscraper is synchronous; run in thread.
        def _run() -> str:
            session = cloudscraper.create_scraper()
            for key, value in BROWSER_HEADERS.items():
                session.headers[key] = value
            resp = session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.text

        try:
            return await anyio.to_thread.run_sync(_run)
        except RetryError as exc:  # pragma: no cover - defensive
            raise exc

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(2))
    async def _fetch_playwright(self, url: str) -> str:
        def _run() -> str:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                # Use en-US locale so sites (e.g. sitefilme.com) don't serve
                # a different regional version (e.g. Chinese 56.com).
                context = browser.new_context(
                    locale="en-US",
                    extra_http_headers=BROWSER_HEADERS,
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=int(self._timeout * 1000))
                content = page.content()
                context.close()
                browser.close()
                return content

        return await anyio.to_thread.run_sync(_run)


