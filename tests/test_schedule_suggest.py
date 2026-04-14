import random

from core.config import SiteConfig
from core.schedule_suggest import (
    ProbeSignals,
    parse_cache_max_age,
    parse_robots_crawl_delay,
    recommend_step_hours,
    staggered_hourly_cron,
    suggest_cron,
)


def _site(**kwargs: object) -> SiteConfig:
    base: dict = {
        "name": "demo",
        "url": "https://example.com/",
        "method": "http",
        "item_selector": "//a",
        "title_selector": "text()",
        "link_selector": "@href",
    }
    base.update(kwargs)
    return SiteConfig(**base)


def test_parse_cache_max_age() -> None:
    assert parse_cache_max_age(None) is None
    assert parse_cache_max_age("no-store") is None
    assert parse_cache_max_age("public, max-age=120") == 120
    assert parse_cache_max_age("Max-Age=3600") == 3600


def test_parse_robots_crawl_delay() -> None:
    body = "# hi\nUser-agent: *\nCrawl-delay: 2.5\n"
    assert parse_robots_crawl_delay(body) == 2.5
    assert parse_robots_crawl_delay("") is None


def test_recommend_step_hours_episodes_vs_movies() -> None:
    ok = ProbeSignals(200, 500.0, None, None, False)
    movies = _site(name="m", category="movies")
    eps = _site(name="e", category="episodes")
    assert recommend_step_hours(movies, ok) >= recommend_step_hours(eps, ok)


def test_recommend_step_hours_respects_errors_and_cache() -> None:
    site = _site(category="movies")
    slow = ProbeSignals(200, 9000.0, None, None, False)
    assert recommend_step_hours(site, slow) >= 5
    cached = ProbeSignals(200, 100.0, 86400, None, False)
    assert recommend_step_hours(site, cached) >= 4


def test_staggered_cron_uses_rng() -> None:
    site = _site(name="alpha")
    r1 = staggered_hourly_cron(site.name, 4, random.Random(1))
    r2 = staggered_hourly_cron(site.name, 4, random.Random(2))
    assert r1 != r2
    parts1 = r1.split()
    assert len(parts1) == 5
    assert parts1[1] == "*/4"


def test_build_rng_seed_changes_minute() -> None:
    site = _site(name="same")
    sig = ProbeSignals(200, 100.0, None, None, False)
    a, _ = suggest_cron(site, sig, seed=1)
    b, _ = suggest_cron(site, sig, seed=2)
    assert a.split()[0] != b.split()[0] or a.split()[1] != b.split()[1]
