#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import quote

from core.config import SiteConfig, load_config
from core.feed import is_failure_feed_title


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "config" / "sites.yaml"
FEEDS_DIR = REPO_ROOT / "feeds"
OUTPUT_FILE = REPO_ROOT / "index.html"
# Absolute base for RSS URLs (Inoreader and other readers fetch feeds by full URL).
GITHUB_PAGES_FEED_BASE = "https://ibaciu6.github.io/rss-generator"
INOREADER_FEED_PREFIX = "https://www.inoreader.com/search/feeds/"


@dataclass(frozen=True)
class FeedInfo:
    site: SiteConfig
    title: str
    href: str
    status: str
    items_count: int
    detail: str
    has_feed: bool


def generate_index(
    config_path: Path = CONFIG_FILE,
    feeds_dir: Path = FEEDS_DIR,
    output_file: Path = OUTPUT_FILE,
) -> None:
    config = load_config(config_path)
    feeds_info = [_get_feed_info(site, feeds_dir) for site in config.sites]
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    html_lines = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "  <meta charset='UTF-8'>",
        "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
        "  <title>RSS Generator</title>",
        "  <style>",
        "    :root {",
        "      color-scheme: light;",
        "      --bg: #f5f1e8;",
        "      --panel: #fffaf0;",
        "      --text: #1f2933;",
        "      --muted: #52606d;",
        "      --line: #d9cbb2;",
        "      --accent: #9f3a16;",
        "      --accent-soft: #f7d7c8;",
        "      --ok: #1f6f43;",
        "      --warn: #8c4c00;",
        "      --error: #a61b1b;",
        "    }",
        "    * { box-sizing: border-box; }",
        "    body {",
        "      margin: 0;",
        "      font-family: Georgia, 'Times New Roman', serif;",
        "      background:",
        "        radial-gradient(circle at top left, #fffdf6 0, #fffdf6 22%, transparent 22%),",
        "        linear-gradient(180deg, #efe4d3 0%, var(--bg) 32%, #f8f5ef 100%);",
        "      color: var(--text);",
        "    }",
        "    main { max-width: 1100px; margin: 0 auto; padding: 48px 20px 64px; }",
        "    .hero, .table-wrap, .note {",
        "      background: rgba(255, 250, 240, 0.92);",
        "      border: 1px solid var(--line);",
        "      border-radius: 24px;",
        "      box-shadow: 0 18px 60px rgba(102, 79, 46, 0.08);",
        "    }",
        "    .hero { padding: 28px; }",
        "    h1 { margin: 0 0 12px; font-size: clamp(2.2rem, 5vw, 3.6rem); line-height: 1; letter-spacing: -0.04em; }",
        "    .lede, .meta { margin: 0; color: var(--muted); font-size: 1.05rem; }",
        "    .meta { margin-top: 10px; font-size: 0.95rem; }",
        "    .table-wrap { margin-top: 24px; overflow-x: auto; }",
        "    table { width: 100%; border-collapse: collapse; }",
        "    th, td { padding: 14px 18px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }",
        "    th { font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); background: rgba(247, 215, 200, 0.35); }",
        "    tr:last-child td { border-bottom: 0; }",
        "    td:first-child { font-weight: 700; min-width: 170px; }",
        "    a { color: var(--accent); text-decoration: none; }",
        "    a:hover { text-decoration: underline; }",
        "    a.btn-inoreader {",
        "      display: inline-block;",
        "      padding: 6px 12px;",
        "      font-size: 0.78rem;",
        "      font-weight: 700;",
        "      letter-spacing: 0.02em;",
        "      color: #fff;",
        "      background: #1877f2;",
        "      border-radius: 8px;",
        "      text-decoration: none;",
        "    }",
        "    a.btn-inoreader:hover { background: #145dbf; text-decoration: none; }",
        "    .inoreader-na { color: var(--muted); }",
        "    .status { font-weight: 700; }",
        "    .status-available { color: var(--ok); }",
        "    .status-unavailable { color: var(--warn); }",
        "    .status-missing, .status-invalid-xml { color: var(--error); }",
        "    .detail { min-width: 320px; color: var(--muted); }",
        "    .note { margin-top: 16px; padding: 14px 16px; }",
        "    code { font-family: 'SFMono-Regular', 'Menlo', monospace; }",
        "    @media (max-width: 640px) {",
        "      main { padding: 24px 14px 40px; }",
        "      .hero { padding: 20px; }",
        "      th, td { padding: 12px 14px; }",
        "      .detail { min-width: 220px; }",
        "    }",
        "  </style>",
        "</head>",
        "<body>",
        "  <main>",
        "    <section class='hero'>",
        "      <h1>RSS Generator</h1>",
        "      <p class='lede'>Each configured source publishes an RSS file. If scraping fails, the RSS link still returns a valid diagnostic feed instead of a 404.</p>",
        f"      <p class='meta'>Generated {escape(generated_at)}</p>",
        "    </section>",
        "    <section class='table-wrap'>",
        "      <table>",
        "        <thead>",
        "          <tr>",
        "            <th>Site</th>",
        "            <th>RSS</th>",
        "            <th>Inoreader</th>",
        "            <th>Status</th>",
        "            <th>Items</th>",
        "            <th>Details</th>",
        "            <th>Source</th>",
        "          </tr>",
        "        </thead>",
        "        <tbody>",
    ]

    for feed in feeds_info:
        status_class = f"status-{feed.status.lower().replace(' ', '-')}"
        rss_cell = (
            f"<a href='{escape(feed.href)}'>RSS</a>"
            if feed.has_feed
            else "<span aria-disabled='true'>Not available</span>"
        )
        if feed.has_feed:
            absolute_feed = f"{GITHUB_PAGES_FEED_BASE.rstrip('/')}/{feed.href.lstrip('/')}"
            inoreader_url = f"{INOREADER_FEED_PREFIX}{quote(absolute_feed, safe='')}"
            inoreader_cell = (
                f"<a class='btn-inoreader' href='{escape(inoreader_url)}' "
                f"rel='noopener noreferrer' target='_blank' "
                f"title='Preview in Inoreader, then follow'>Inoreader</a>"
            )
        else:
            inoreader_cell = "<span class='inoreader-na'>—</span>"
        html_lines.extend(
            [
                "          <tr>",
                f"            <td>{escape(_site_display_name(feed.site))}</td>",
                f"            <td>{rss_cell}</td>",
                f"            <td>{inoreader_cell}</td>",
                f"            <td class='status {status_class}'>{escape(feed.status)}</td>",
                f"            <td>{feed.items_count}</td>",
                f"            <td class='detail'>{escape(feed.detail)}</td>",
                f"            <td><a href='{escape(feed.site.url)}'>Source</a></td>",
                "          </tr>",
            ]
        )

    html_lines.extend(
        [
            "        </tbody>",
            "      </table>",
            "    </section>",
            "    <p class='note'>Feed files live under <code>feeds/</code>. Project pages use relative links here so GitHub Pages serves <code>/rss-generator/feeds/*.xml</code> correctly.</p>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )

    output_file.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"Generated {output_file} with {len(feeds_info)} feeds.")


def _get_feed_info(site: SiteConfig, feeds_dir: Path) -> FeedInfo:
    feed_path = feeds_dir / site.feed_file
    href = f"feeds/{site.feed_file}"
    fallback_title = _site_display_name(site)

    if not feed_path.exists() or feed_path.stat().st_size == 0:
        return FeedInfo(
            site=site,
            title=fallback_title,
            href=href,
            status="Missing",
            items_count=0,
            detail="Feed file has not been generated yet.",
            has_feed=False,
        )

    try:
        root = ET.parse(feed_path).getroot()
    except ET.ParseError as exc:
        return FeedInfo(
            site=site,
            title=fallback_title,
            href=href,
            status="Invalid XML",
            items_count=0,
            detail=f"Feed file could not be parsed: {exc}",
            has_feed=True,
        )

    channel = root.find("channel")
    if channel is None:
        return FeedInfo(
            site=site,
            title=fallback_title,
            href=href,
            status="Invalid XML",
            items_count=0,
            detail="Feed XML is missing the channel element.",
            has_feed=True,
        )

    title = _safe_text(channel.findtext("title"), fallback_title)
    description = _safe_text(channel.findtext("description"), "No feed details available.")
    items_count = len(channel.findall("item"))
    status = "Unavailable" if is_failure_feed_title(title) else "Available"

    return FeedInfo(
        site=site,
        title=title,
        href=href,
        status=status,
        items_count=items_count,
        detail=description,
        has_feed=True,
    )


def _display_name(name: str) -> str:
    return name.replace("-", " ").title()


def _site_display_name(site: SiteConfig) -> str:
    return site.display_name or _display_name(site.name)


def _safe_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = " ".join(value.split())
    return cleaned or fallback


if __name__ == "__main__":
    generate_index()
