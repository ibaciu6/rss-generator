"""Tests for Wayback mirror refresh utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

from scripts.refresh_wayback_mirrors import extract_mirror_sources, should_refresh


def _site(url: str, fallbacks: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(url=url, fallback_urls=fallbacks or [])


def test_extract_mirror_sources_returns_original_urls_replacing_mirrors():
    sites = [
        _site(
            "https://ddosecrets.substack.com/feed",
            ["https://web.archive.org/web/0id_/https://ddosecrets.substack.com/feed"],
        ),
        _site("https://example.com/rss", []),
        _site(
            "https://news.example.org/feed.xml",
            ["https://web.archive.org/web/2026id_/https://news.example.org/feed.xml"],
        ),
    ]
    assert extract_mirror_sources(sites) == [
        "https://ddosecrets.substack.com/feed",
        "https://news.example.org/feed.xml",
    ]


def test_extract_mirror_sources_ignores_non_mirror_fallbacks_and_dedups():
    sites = [
        _site(
            "https://a.example/feed",
            [
                "https://web.archive.org/web/0id_/https://a.example/feed",
                "https://mirror.example/rss",
            ],
        ),
        _site("https://b.example/feed", ["https://web.archive.org/web/0id_/https://a.example/feed"]),
    ]
    assert extract_mirror_sources(sites) == ["https://a.example/feed"]


def test_should_refresh_true_when_never_captured():
    assert should_refresh("https://a.example/feed", {}) is True


def test_should_refresh_false_when_refreshed_recently():
    state = {
        "https://a.example/feed": (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
    }
    assert should_refresh("https://a.example/feed", state) is False


def test_should_refresh_true_after_interval_elapsed():
    state = {
        "https://a.example/feed": (
            datetime.now(UTC) - timedelta(hours=12)
        ).isoformat()
    }
    assert should_refresh("https://a.example/feed", state) is True