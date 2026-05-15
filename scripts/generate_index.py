#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
OUTPUT_OPML = REPO_ROOT / "feeds.opml"

GITHUB_PAGES_FEED_BASE = "https://ibaciu6.github.io/rss-generator"
INOREADER_FEED_PREFIX = "https://www.inoreader.com/search/feeds/"

_EPISODE_CATEGORIES = frozenset({"episodes", "updates"})
_TVSHOW_CATEGORIES = frozenset({"tvshows"})


@dataclass(frozen=True)
class FeedInfo:
    site: SiteConfig
    href: str
    status: str
    last_build_date: str  # "YYYY-MM-DD HH:MM" or "" when unknown
    items_count: int
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
        "    .table-wrap { margin-top: 24px; padding: 22px 24px 24px; }",
        "    .table-wrap + .table-wrap { margin-top: 32px; }",
        "    .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }",
        "    h2.section-title { margin: 0 0 14px; font-size: 1.15rem; font-weight: 700; color: var(--text); letter-spacing: 0.02em; line-height: 1.35; }",
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
        "    .col-updated { white-space: nowrap; color: var(--muted); font-size: 0.88rem; }",
        "    .lang-badge { display: inline-block; padding: 1px 6px; margin-left: 4px; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; border-radius: 4px; background: var(--accent-soft); color: var(--accent); vertical-align: middle; }",
        "    .note { margin-top: 16px; padding: 14px 16px; }",
        "    code { font-family: 'SFMono-Regular', 'Menlo', monospace; }",
        "    @media (max-width: 640px) {",
        "      main { padding: 24px 14px 40px; }",
        "      .hero { padding: 20px; }",
        "      th, td { padding: 12px 14px; }",
        "    }",
        "  </style>",
        "</head>",
        "<body>",
        "  <main>",
        "    <section class='hero'>",
        "      <h1>RSS Generator</h1>",
        "      <p class='lede'><a href='https://github.com/ibaciu6/rss-generator' rel='noopener noreferrer' target='_blank'>github.com/ibaciu6/rss-generator</a> &middot; <a href='feeds.opml' download>Download OPML</a></p>",
        "    </section>",
    ]

    episode_feeds = [f for f in feeds_info if _is_episode_category(f.site)]
    tvshow_feeds = [f for f in feeds_info if _is_tvshow_category(f.site)]
    movie_feeds = [f for f in feeds_info if not _is_episode_category(f.site) and not _is_tvshow_category(f.site)]

    if movie_feeds:
        html_lines.extend(_feed_section_html("Movies", movie_feeds))
    if tvshow_feeds:
        html_lines.extend(_feed_section_html("TV Shows", tvshow_feeds))
    if episode_feeds:
        html_lines.extend(_feed_section_html("Episodes", episode_feeds))

    html_lines.extend(
        [
            "  </main>",
            "</body>",
            "</html>",
        ]
    )

    output_file.write_text("\n".join(html_lines), encoding="utf-8")
    print(
        f"Generated {output_file} with {len(feeds_info)} feeds "
        f"({len(movie_feeds)} Movies, {len(tvshow_feeds)} TV Shows, {len(episode_feeds)} Episodes)."
    )

    _write_opml(movie_feeds, tvshow_feeds, episode_feeds)


def _is_episode_category(site: SiteConfig) -> bool:
    c = (site.category or "").strip().lower()
    return c in _EPISODE_CATEGORIES


def _is_tvshow_category(site: SiteConfig) -> bool:
    c = (site.category or "").strip().lower()
    return c in _TVSHOW_CATEGORIES


def _write_opml(
    movie_feeds: list[FeedInfo],
    tvshow_feeds: list[FeedInfo],
    episode_feeds: list[FeedInfo],
) -> None:
    from xml.sax.saxutils import escape as xml_escape

    sections = [
        ("Online-Movies-RO", [f for f in movie_feeds if f.site.language == "ro"]),
        ("Online-Movies-EN", [f for f in movie_feeds if f.site.language == "en"]),
        ("Online-Episodes-RO", episode_feeds),
        ("Online-TV-Series-EN", tvshow_feeds),
    ]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head><title>FMHY Streaming Feeds</title></head>",
        "  <body>",
    ]
    for folder, feeds in sections:
        if not feeds:
            continue
        lines.append(f'    <outline text="{xml_escape(folder)}" title="{xml_escape(folder)}">')
        for f in feeds:
            if not f.has_feed:
                continue
            absolute_feed = f"{GITHUB_PAGES_FEED_BASE.rstrip('/')}/{f.href.lstrip('/')}"
            lines.append(
                f'      <outline type="rss" text="{xml_escape(f.site.display_name or f.site.name)}" '
                f'title="{xml_escape(f.site.display_name or f.site.name)}" '
                f'xmlUrl="{xml_escape(absolute_feed)}" '
                f'htmlUrl="{xml_escape(f.site.url)}"/>'
            )
        lines.append("    </outline>")
    lines.extend(["  </body>", "</opml>"])
    OUTPUT_OPML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {OUTPUT_OPML} ({sum(len(f) for _, f in sections if f)} feeds).")


def _feed_row_lines(feed: FeedInfo, section_title: str = "") -> list[str]:
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
    lang_badge = ""
    if feed.site.language == "en":
        lang_badge = " <span class='lang-badge'>EN</span>"
    elif feed.site.language == "ro":
        lang_badge = " <span class='lang-badge'>RO</span>"
    else:
        lang_badge = f" <span class='lang-badge'>{escape(feed.site.language.upper())}</span>"
    return [
        "          <tr>",
        f"            <td>{escape(_site_display_name(feed.site, section_title))}{lang_badge}</td>",
        f"            <td>{rss_cell}</td>",
        f"            <td>{inoreader_cell}</td>",
        f"            <td class='status {status_class}'>{escape(feed.status)}</td>",
        f"            <td class='col-updated'>{escape(feed.last_build_date) or '—'}</td>",
        f"            <td>{feed.items_count}</td>",
        f"            <td><a href='{escape(feed.site.url)}'>Source</a></td>",
        "          </tr>",
    ]


def _feed_section_html(title: str, feeds: list[FeedInfo]) -> list[str]:
    lines: list[str] = [
        "    <section class='table-wrap'>",
        f"      <h2 class='section-title'>{escape(title)}</h2>",
        "      <div class='table-scroll'>",
        "      <table>",
        "        <thead>",
        "          <tr>",
        "            <th>Site</th>",
        "            <th>RSS</th>",
        "            <th>Inoreader</th>",
        "            <th>Status</th>",
        "            <th>Updated</th>",
        "            <th>Items</th>",
        "            <th>Source</th>",
        "          </tr>",
        "        </thead>",
        "        <tbody>",
    ]
    for feed in sorted(feeds, key=lambda f: _site_display_name(f.site, title).lower()):
        lines.extend(_feed_row_lines(feed, title))
    lines.extend(
        [
            "        </tbody>",
            "      </table>",
            "      </div>",
            "    </section>",
        ]
    )
    return lines


def _get_feed_info(site: SiteConfig, feeds_dir: Path) -> FeedInfo:
    feed_path = feeds_dir / site.feed_file
    href = f"feeds/{site.feed_file}"
    fallback_title = _site_display_name(site, "")

    if not feed_path.exists() or feed_path.stat().st_size == 0:
        return FeedInfo(
            site=site,
            href=href,
            status="Missing",
            last_build_date="",
            items_count=0,
            has_feed=False,
        )

    try:
        root = ET.parse(feed_path).getroot()
    except ET.ParseError:
        return FeedInfo(
            site=site,
            href=href,
            status="Invalid XML",
            last_build_date="",
            items_count=0,
            has_feed=True,
        )

    channel = root.find("channel")
    if channel is None:
        return FeedInfo(
            site=site,
            href=href,
            status="Invalid XML",
            last_build_date="",
            items_count=0,
            has_feed=True,
        )

    title = _safe_text(channel.findtext("title"), fallback_title)
    items_count = len(channel.findall("item"))
    status = "Unavailable" if is_failure_feed_title(title) else "Available"
    last_build_date = _parse_feed_date(channel.findtext("lastBuildDate"))

    return FeedInfo(
        site=site,
        href=href,
        status=status,
        last_build_date=last_build_date,
        items_count=items_count,
        has_feed=True,
    )


def _site_display_name(site: SiteConfig, title: str) -> str:
    return site.display_name or site.name


def _site_display_name(site: SiteConfig, section_title: str = "") -> str:
    raw = site.display_name or _display_name(site.name)
    # Strip redundant category suffix when it matches the section header
    section_lower = section_title.strip().lower()
    raw_lower = raw.strip().lower()
    redundants = []
    if section_lower == "movies":
        redundants = ["movies"]
    elif section_lower in ("tv shows", "tv"):
        redundants = ["tv shows", "tv", "series", "shows", "episodes"]
    elif section_lower == "episodes":
        redundants = ["episodes"]
    for redundant in redundants:
        if raw_lower.endswith(f" {redundant}"):
            raw = raw[: -(len(redundant) + 1)].strip()
            raw_lower = raw.lower()
    return raw


def _parse_feed_date(raw: str | None) -> str:
    """Parse an RFC 2822 feed date into a compact ``YYYY-MM-DD HH:MM UTC`` string."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw.strip()).astimezone(UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


def _safe_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = " ".join(value.split())
    return cleaned or fallback


if __name__ == "__main__":
    generate_index()
