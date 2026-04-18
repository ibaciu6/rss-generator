from __future__ import annotations

import random
from dataclasses import dataclass
from os import getenv
from typing import Callable, Optional

import anyio
import cloudscraper
import httpx
from tenacity import RetryError, retry, stop_after_attempt, wait_random_exponential

from core.logging_utils import get_logger


logger = get_logger(__name__)

# Modern, realistic desktop user agents. Kept recent to avoid UA-based bot blocks.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
]


def _get_random_headers() -> dict:
    """Generate headers with a random user agent to avoid fingerprinting."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


# Default headers (used for httpx client init, will be overridden per-request)
BROWSER_HEADERS = _get_random_headers()


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
        proxy_url = (getenv("RSS_GENERATOR_PROXY_URL") or "").strip()
        self._proxy_url = proxy_url or None
        self._playwright_available = self._detect_playwright()
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
            proxy=self._proxy_url,
        )
        # Cap concurrent Playwright launches to 1. With multiple sites scraped
        # in parallel each could fall back to Chromium; launching several at
        # once on a GitHub runner (2 CPU / 7 GB) triggered OOM-like timeouts.
        self._playwright_limiter = anyio.CapacityLimiter(1)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        method: str = "http",
        validator: Optional[Callable[[FetchResult], None]] = None,
        playwright_wait_selector: Optional[str] = None,
    ) -> FetchResult:
        """
        Fetch a URL using the configured strategy with fallback.
        If `validator` raises, the next strategy is attempted.
        """
        logger.info("fetch.start", url=url, method=method)
        strategies = self._build_strategy_chain(method, playwright_wait_selector)

        last_error: Optional[Exception] = None
        for strategy in strategies:
            try:
                result = await strategy(url)
                if validator is not None:
                    validator(result)
                logger.info(
                    "fetch.success",
                    url=url,
                    final_url=result.url,
                    status_code=result.status_code,
                    strategy=strategy.__name__,
                )
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

    def _build_strategy_chain(
        self,
        method: str,
        playwright_wait_selector: Optional[str] = None,
    ):
        async def fetch_playwright(url: str) -> FetchResult:
            return await self._fetch_playwright(url, playwright_wait_selector)

        chain: list = []
        if method in {"http", "httpx"}:
            chain = [self._fetch_http, self._fetch_cloudscraper, fetch_playwright]
        elif method == "cloudscraper":
            chain = [self._fetch_cloudscraper, self._fetch_http, fetch_playwright]
        elif method == "playwright":
            chain = [fetch_playwright, self._fetch_http, self._fetch_cloudscraper]
        else:
            chain = [self._fetch_http, self._fetch_cloudscraper, fetch_playwright]
        if self._playwright_available:
            return chain
        return [strategy for strategy in chain if strategy is not fetch_playwright]

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
        return any(marker in lowered for marker in markers)

    @retry(wait=wait_random_exponential(multiplier=1, min=1, max=15), stop=stop_after_attempt(3))
    async def _fetch_http(self, url: str) -> FetchResult:
        # Add random delay before request (0.5-2s jitter)
        await anyio.sleep(random.uniform(0.5, 2.0))
        headers = _get_random_headers()
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        return FetchResult(url=str(resp.url), content=resp.text, status_code=resp.status_code)

    @retry(wait=wait_random_exponential(multiplier=1, min=1, max=15), stop=stop_after_attempt(3))
    async def _fetch_cloudscraper(self, url: str) -> FetchResult:
        # cloudscraper is synchronous; run in thread.
        def _run() -> FetchResult:
            import time
            # Random delay before request (0.5-3s jitter)
            time.sleep(random.uniform(0.5, 3.0))
            
            session = cloudscraper.create_scraper(
                browser={
                    "browser": "chrome",
                    "platform": random.choice(["windows", "darwin"]),
                    "mobile": False,
                }
            )
            headers = _get_random_headers()
            for key, value in headers.items():
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

    @retry(wait=wait_random_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(2))
    async def _fetch_playwright(
        self,
        url: str,
        playwright_wait_selector: Optional[str] = None,
    ) -> FetchResult:
        def _run() -> FetchResult:
            import time
            from playwright.sync_api import sync_playwright

            time.sleep(random.uniform(1.0, 4.0))

            with sync_playwright() as p:
                launch_kwargs: dict = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if self._proxy_url:
                    launch_kwargs["proxy"] = {"server": self._proxy_url}
                browser = p.chromium.launch(**launch_kwargs)

                viewport_width = random.choice([1366, 1440, 1536, 1920])
                viewport_height = random.choice([768, 900, 864, 1080])
                headers = _get_random_headers()

                context = browser.new_context(
                    locale="en-US",
                    viewport={"width": viewport_width, "height": viewport_height},
                    user_agent=headers["User-Agent"],
                    extra_http_headers=headers,
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
                nav_timeout_ms = max(25000, min(90000, int(self._timeout * 2500)))
                response = page.goto(url, wait_until="load", timeout=nav_timeout_ms)

                for _ in range(4):
                    content = page.content()
                    if not self._looks_like_browser_challenge(content):
                        page.wait_for_timeout(2000)
                        content = page.content()
                        break
                    logger.info("fetch.playwright.waiting_for_challenge", url=url)
                    page.wait_for_timeout(5000)

                if playwright_wait_selector:
                    try:
                        page.wait_for_selector(
                            playwright_wait_selector,
                            timeout=20000,
                            state="attached",
                        )
                        page.wait_for_timeout(1500)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "fetch.playwright.wait_selector_timeout",
                            url=url,
                            selector=playwright_wait_selector,
                            error=str(exc),
                        )

                content = page.content()
                status_code = response.status if response else 200
                final_url = page.url

                if self._looks_like_browser_challenge(content):
                    logger.warning("fetch.playwright.challenge_unsolved", url=url)

                context.close()
                browser.close()
                return FetchResult(url=final_url, content=content, status_code=status_code)

        # Serialize Playwright launches: 1 concurrent Chromium max.
        return await anyio.to_thread.run_sync(
            _run, limiter=self._playwright_limiter
        )
