from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Callable, Optional

import anyio
import cloudscraper
import httpx
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
        self._proxy_url = getenv("RSS_GENERATOR_PROXY_URL")
        self._playwright_available = self._detect_playwright()
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
            proxy=self._proxy_url,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        method: str = "http",
        validator: Optional[Callable[[FetchResult], None]] = None,
    ) -> FetchResult:
        """
        Fetch a URL using the configured strategy with fallback.
        If `validator` raises, the next strategy is attempted.
        """
        logger.info("fetch.start", url=url, method=method)
        strategies = self._build_strategy_chain(method)

        last_error: Optional[Exception] = None
        for strategy in strategies:
            try:
                result = await strategy(url)
                if validator is not None:
                    validator(result)
                logger.info("fetch.success", url=url, strategy=strategy.__name__)
                return result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fetch.strategy_failed",
                    url=url,
                    strategy=strategy.__name__,
                    error=self._format_error(exc),
                )
                last_error = exc

        detail = self._format_error(last_error) if last_error is not None else "unknown error"
        raise FetchError(f"Failed to fetch {url!r}: {detail}") from last_error

    def _build_strategy_chain(self, method: str):
        chain = []
        if method in {"http", "httpx"}:
            chain = [self._fetch_http, self._fetch_cloudscraper, self._fetch_playwright]
        elif method == "cloudscraper":
            chain = [self._fetch_cloudscraper, self._fetch_http, self._fetch_playwright]
        elif method == "playwright":
            chain = [self._fetch_playwright, self._fetch_http, self._fetch_cloudscraper]
        else:
            chain = [self._fetch_http, self._fetch_cloudscraper, self._fetch_playwright]
        if self._playwright_available:
            return chain
        return [strategy for strategy in chain if strategy.__name__ != "_fetch_playwright"]

    @staticmethod
    def _detect_playwright() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def _format_error(cls, exc: Exception) -> str:
        if isinstance(exc, RetryError):
            last_attempt = exc.last_attempt
            if last_attempt is not None:
                inner = last_attempt.exception()
                if inner is not None:
                    return cls._format_error(inner)
            return "retry attempts exhausted"

        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        response_url = getattr(response, "url", None)
        if status_code is not None:
            if response_url is not None:
                return f"HTTP {status_code} from {response_url}"
            return f"HTTP {status_code}"

        return str(exc)

    @staticmethod
    def _looks_like_browser_challenge(content: str) -> bool:
        lowered = content.lower()
        markers = (
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
        return any(marker in lowered for marker in markers)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    async def _fetch_http(self, url: str) -> FetchResult:
        resp = await self._client.get(url)
        resp.raise_for_status()
        return FetchResult(url=str(resp.url), content=resp.text, status_code=resp.status_code)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    async def _fetch_cloudscraper(self, url: str) -> FetchResult:
        # cloudscraper is synchronous; run in thread.
        def _run() -> FetchResult:
            session = cloudscraper.create_scraper()
            for key, value in BROWSER_HEADERS.items():
                session.headers[key] = value
            if self._proxy_url:
                session.proxies = {"http": self._proxy_url, "https": self._proxy_url}
            resp = session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return FetchResult(url=str(resp.url), content=resp.text, status_code=resp.status_code)

        try:
            return await anyio.to_thread.run_sync(_run)
        except RetryError as exc:  # pragma: no cover - defensive
            raise exc

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(2))
    async def _fetch_playwright(self, url: str) -> FetchResult:
        def _run() -> FetchResult:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                launch_kwargs = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if self._proxy_url:
                    launch_kwargs["proxy"] = {"server": self._proxy_url}
                browser = p.chromium.launch(**launch_kwargs)
                # Use en-US locale so sites (e.g. sitefilme.com) don't serve
                # a different regional version (e.g. Chinese 56.com).
                context = browser.new_context(
                    locale="en-US",
                    viewport={"width": 1366, "height": 768},
                    user_agent=BROWSER_HEADERS["User-Agent"],
                    extra_http_headers=BROWSER_HEADERS,
                )
                context.add_init_script(
                    """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""
                )
                page = context.new_page()
                # Use 'load' instead of 'domcontentloaded' to give Cloudflare more time to initialize
                response = page.goto(url, wait_until="load", timeout=int(self._timeout * 1000))
                
                # Wait longer for Cloudflare challenges (up to 20s total with 5s increments)
                for _ in range(4):
                    content = page.content()
                    if not self._looks_like_browser_challenge(content):
                        # Extra wait for dynamic content to render after bypass
                        page.wait_for_timeout(2000)
                        content = page.content()
                        break
                    logger.info("fetch.playwright.waiting_for_challenge", url=url)
                    page.wait_for_timeout(5000)
                
                content = page.content()
                status_code = response.status if response else 200
                final_url = page.url
                
                # Check if we still have a challenge after waiting
                if self._looks_like_browser_challenge(content):
                    logger.warning("fetch.playwright.challenge_unsolved", url=url)
                
                context.close()
                browser.close()
                return FetchResult(url=final_url, content=content, status_code=status_code)

        return await anyio.to_thread.run_sync(_run)
