"""Tests for scripts/pull_published_feeds.py (review-side helper, not CI)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.pull_published_feeds import main, published_feed_files, pull_one

BASE = "https://example.invalid/prefix"

OPML = """<?xml version="1.0"?>
<opml version="2.0"><body>
  <outline type="rss" text="A" xmlUrl="https://example.invalid/prefix/feeds/a.xml"/>
  <outline type="rss" text="B" xmlUrl="https://example.invalid/prefix/feeds/b.xml"/>
</body></opml>"""


def _rss(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{title}</title><link>https://x/</link><description>d</description>"
        "<item><title>i</title><link>https://x/1</link><description>x</description></item>"
        "</channel></rss>"
    )


def test_published_feed_files_reads_the_opml(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch", lambda url, timeout: OPML.encode()
    )
    assert published_feed_files(BASE, 5) == ["a.xml", "b.xml"]


def test_published_feed_files_rejects_an_empty_manifest(monkeypatch) -> None:
    monkeypatch.setattr("scripts.pull_published_feeds._fetch", lambda url, timeout: b"<opml/>")
    with pytest.raises(SystemExit):
        published_feed_files(BASE, 5)


def test_pull_one_writes_a_valid_feed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch", lambda url, timeout: _rss("A").encode()
    )
    name, status = pull_one(BASE, "a.xml", tmp_path, 5)
    assert name == "a.xml" and "KiB" in status
    assert ET.parse(tmp_path / "a.xml").getroot().find("channel") is not None


def test_pull_one_rejects_non_rss(tmp_path: Path, monkeypatch) -> None:
    """A captive portal or an error page must not overwrite a good local feed."""
    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch", lambda url, timeout: b"<html>nope</html>"
    )
    _, status = pull_one(BASE, "a.xml", tmp_path, 5)
    assert status.startswith("FAILED")
    assert not (tmp_path / "a.xml").exists()


def test_main_removes_feeds_no_longer_published(tmp_path: Path, monkeypatch) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "stale.xml").write_text(_rss("Stale"), encoding="utf-8")

    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch",
        lambda url, timeout: OPML.encode() if url.endswith("feeds.opml") else _rss("X").encode(),
    )
    assert main(["--base-url", BASE, "--feeds-dir", str(feeds)]) == 0
    assert sorted(p.name for p in feeds.glob("*.xml")) == ["a.xml", "b.xml"]


def test_main_keep_extra_leaves_unpublished_feeds(tmp_path: Path, monkeypatch) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "stale.xml").write_text(_rss("Stale"), encoding="utf-8")

    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch",
        lambda url, timeout: OPML.encode() if url.endswith("feeds.opml") else _rss("X").encode(),
    )
    assert main(["--base-url", BASE, "--feeds-dir", str(feeds), "--keep-extra"]) == 0
    assert (feeds / "stale.xml").exists()


def test_main_reports_failure_with_a_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    feeds = tmp_path / "feeds"
    monkeypatch.setattr(
        "scripts.pull_published_feeds._fetch",
        lambda url, timeout: OPML.encode() if url.endswith("feeds.opml") else b"<html>502</html>",
    )
    assert main(["--base-url", BASE, "--feeds-dir", str(feeds)]) == 1
