"""
Heuristics for RSS scrape cadence: probe upstream lightly, suggest hour-step crons,
and stagger minutes per site to spread load (GitHub Actions + origin).
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from core.config import FetchMethod, SiteConfig


@dataclass(frozen=True)
class ProbeSignals:
    """Lightweight signals from HEAD/GET + optional robots.txt."""

    status_code: int
    elapsed_ms: float
    cache_max_age_s: Optional[int]
    robots_crawl_delay_s: Optional[float]
    used_get_fallback: bool


def parse_cache_max_age(cache_control: str | None) -> Optional[int]:
    if not cache_control:
        return None
    m = re.search(r"max-age=(\d+)", cache_control, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_robots_crawl_delay(robots_body: str) -> Optional[float]:
    """
    Best-effort Crawl-delay (seconds) from robots.txt body (non-standard but common).
    """
    for line in robots_body.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"(?i)crawl-delay:\s*([\d.]+)\s*$", line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def recommend_step_hours(site: SiteConfig, sig: ProbeSignals) -> int:
    """
    GitHub cron hour divisor (``*/step`` in the hour field), roughly "every ~step hours".

    Higher step = less frequent (gentler on bot protection / flaky origins).
    """
    cat = (site.category or "").strip().lower()
    if cat in {"episodes", "updates"}:
        step = 3
    else:
        step = 4

    method: FetchMethod = site.method
    if method == "playwright":
        step += 2
    elif method == "cloudscraper":
        step += 1
    else:
        step = max(2, step - 1)

    if sig.status_code == 429:
        step += 4
    elif sig.status_code >= 500:
        step += 2
    elif sig.status_code == 0 or sig.status_code >= 400:
        step += 1

    if sig.elapsed_ms >= 8000:
        step += 2
    elif sig.elapsed_ms >= 4000:
        step += 1

    if sig.cache_max_age_s is not None and sig.cache_max_age_s >= 3600:
        step += 1
    if sig.cache_max_age_s is not None and sig.cache_max_age_s >= 86400:
        step += 1

    if sig.robots_crawl_delay_s is not None and sig.robots_crawl_delay_s >= 10:
        step += 2
    elif sig.robots_crawl_delay_s is not None and sig.robots_crawl_delay_s >= 1:
        step += 1

    if sig.used_get_fallback:
        step += 0

    return max(2, min(step, 12))


def staggered_hourly_cron(site_name: str, step_hours: int, rng: random.Random) -> str:
    """
    ``minute */step * * *`` UTC with a stable pseudo-random minute per site name.

    Spreads workflow starts across the hour while keeping a simple GitHub-friendly cron.
    """
    minute = rng.randint(3, 55)
    return f"{minute} */{step_hours} * * *"


def probe_site(site: SiteConfig, timeout_s: float = 10.0) -> ProbeSignals:
    """
    HEAD listing URL; on 405/501 fall back to a tiny GET. Fetches same-host robots.txt once.
    """
    url = site.url
    headers = {
        "User-Agent": "rss-generator-schedule-probe/1.0 (+https://github.com/ibaciu6/rss-generator)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    status = 0
    elapsed_ms = 0.0
    cache_max: Optional[int] = None
    used_get = False
    t0 = time.perf_counter()
    proxy = os.environ.get("RSS_GENERATOR_PROXY_URL") or None
    client_kw: dict = {"follow_redirects": True, "timeout": timeout_s, "headers": headers}
    if proxy:
        client_kw["proxy"] = proxy
    with httpx.Client(**client_kw) as client:
        try:
            r = client.head(url)
            status = r.status_code
            cache_max = parse_cache_max_age(r.headers.get("cache-control"))
            if status in (405, 501):
                used_get = True
                r2 = client.get(url, headers={**headers, "Range": "bytes=0-0"})
                status = r2.status_code
                cache_max = cache_max or parse_cache_max_age(r2.headers.get("cache-control"))
        except httpx.HTTPError:
            status = 0
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        robots_delay: Optional[float] = None
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}/", "/robots.txt")
                rb = client.get(robots_url, timeout=min(5.0, timeout_s))
                if rb.status_code == 200 and rb.text:
                    robots_delay = parse_robots_crawl_delay(rb.text)
        except httpx.HTTPError:
            pass

    return ProbeSignals(
        status_code=status,
        elapsed_ms=elapsed_ms,
        cache_max_age_s=cache_max,
        robots_crawl_delay_s=robots_delay,
        used_get_fallback=used_get,
    )


def build_rng(site_name: str, seed: int | None) -> random.Random:
    """
    Per-site RNG: same ``seed`` + different ``site_name`` ⇒ different minutes.
    """
    h = hash(site_name) % (2**32)
    if seed is None:
        return random.Random(h)
    return random.Random((int(seed) ^ h) & 0xFFFFFFFF)


def suggest_cron(site: SiteConfig, sig: ProbeSignals, seed: int | None = None) -> tuple[str, int]:
    step = recommend_step_hours(site, sig)
    rng = build_rng(site.name, seed)
    return staggered_hourly_cron(site.name, step, rng), step
