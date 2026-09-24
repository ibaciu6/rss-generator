#!/usr/bin/env python3
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
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

# Ordered (category, section title, OPML folder) for known categories. Unknown
# categories render at the end, humanized, so new feeds surface without code edits.
_CATEGORY_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("movies", "Movies", "Online-Movies"),
    ("episodes", "Episodes", "Online-Episodes"),
    ("cinema", "Cinema", "Online-Cinema"),
    ("torrents", "Torrents", "Online-Torrents"),
    ("cyber", "Cyber Security", "Online-Cyber"),
    ("tech", "Tech", "Online-Tech"),
    ("news", "News", "Online-News"),
    ("economy", "Economy", "Online-Economy"),
    ("blogs", "Blogs", "Online-Blogs"),
    ("local", "Local", "Online-Local"),
    ("education", "Education", "Online-Education"),
    ("other", "Other", "Online-Other"),
)

# Categories folded into another group's section and OPML folder.
_CATEGORY_GROUP_ALIASES = {
    "updates": "episodes",
    "releases": "torrents",
}

_KNOWN_CATEGORIES = frozenset(cat for cat, _, _ in _CATEGORY_SECTIONS)


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
    output_opml: Path = OUTPUT_OPML,
) -> None:
    config = load_config(config_path)
    # Only enabled sites have feeds; disabled sites are excluded from the page.
    enabled_sites = [site for site in config.sites if site.enabled]
    feeds_info = [_get_feed_info(site, feeds_dir) for site in enabled_sites]
    disabled_count = len(config.sites) - len(enabled_sites)

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
        "      --ok-bg: #d9f0e1;",
        "      --warn: #8c4c00;",
        "      --warn-bg: #fce6c9;",
        "      --error: #a61b1b;",
        "      --error-bg: #f5d0d0;",
        "      --info: #0e5c8a;",
        "      --info-bg: #d0e5f5;",
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
        "    .opml-dl { margin: 14px 0 0; font-size: 0.95rem; }",
        "    .btn-opml { display: inline-block; padding: 8px 18px; font-size: 0.88rem; font-weight: 700; color: #fff; background: #b8860b; border-radius: 8px; text-decoration: none; }",
        "    .btn-opml:hover { background: #9a7209; text-decoration: none; }",
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
        "    .dashboard {",
        "      margin-top: 20px;",
        "      background: rgba(255, 250, 240, 0.92);",
        "      border: 1px solid var(--line);",
        "      border-radius: 24px;",
        "      box-shadow: 0 18px 60px rgba(102, 79, 46, 0.08);",
        "      padding: 24px 28px;",
        "    }",
        "    .dashboard h2 { margin: 0 0 16px; font-size: 1.05rem; font-weight: 700; letter-spacing: 0.02em; }",
        "    .dash-grid { display: flex; gap: 16px; flex-wrap: wrap; }",
        "    .dash-card {",
        "      flex: 1; min-width: 100px; text-align: center;",
        "      padding: 16px 14px; border-radius: 14px;",
        "      border: 1px solid var(--line);",
        "    }",
        "    .dash-card .num { font-size: 2rem; font-weight: 700; line-height: 1; }",
        "    .dash-card .lbl { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-top: 4px; }",
        "    .dash-card .num .muted-note { font-size: 0.7rem; font-weight: 400; vertical-align: middle; color: var(--muted); }",
        "    .dash-ok { background: var(--ok-bg); } .dash-ok .num { color: var(--ok); }",
        "    .dash-warn { background: var(--warn-bg); } .dash-warn .num { color: var(--warn); }",
        "    .dash-error { background: var(--error-bg); } .dash-error .num { color: var(--error); }",
        "    .dash-info { background: var(--info-bg); } .dash-info .num { color: var(--info); }",
        "    .dash-bar-wrap { margin-top: 16px; background: var(--accent-soft); border-radius: 20px; height: 8px; overflow: hidden; }",
        "    .dash-bar-fill { height: 100%; background: var(--ok); border-radius: 20px; transition: width 0.3s; }",
        "    .dash-links { margin-top: 14px; display: flex; gap: 20px; flex-wrap: wrap; align-items: center; font-size: 0.9rem; }",
        "    .dash-links a { color: var(--accent); text-decoration: none; }",
        "    .dash-links a:hover { text-decoration: underline; }",
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
        "      <p class='lede'><a href='https://github.com/ibaciu6/rss-generator' rel='noopener noreferrer' target='_blank'>github.com/ibaciu6/rss-generator</a></p>",
        "      <p class='opml-dl'><a href='feeds.opml' download class='btn-opml'>Download OPML</a> &mdash; import into Inoreader or any RSS reader</p>",
                "    </section>",
    ]

    html_lines.extend(_dashboard_html(feeds_info, enabled_count=len(enabled_sites), disabled_count=disabled_count))

    sections = _group_sections(feeds_info)
    for title, _folder, section_feeds in sections:
        if section_feeds:
            html_lines.extend(_feed_section_html(title, section_feeds))

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
        f"({', '.join(f'{title}: {len(section_feeds)}' for title, _, section_feeds in sections if section_feeds)})."
    )

    _write_opml(sections, output_opml)


def _category_key(site: SiteConfig) -> str:
    """Feeds without a category land in the Other section; aliases fold into a group."""
    category = (site.category or "other").strip().lower()
    return _CATEGORY_GROUP_ALIASES.get(category, category)


def _humanize_category(category: str) -> str:
    return " ".join(word.capitalize() for word in category.replace("-", " ").split())


def _group_sections(feeds: list[FeedInfo]) -> list[tuple[str, str, list[FeedInfo]]]:
    """Split feeds into ordered (title, OPML folder, feeds) sections.

    Known categories follow the canonical order/titles above; anything else is
    grouped under a humanized name appended in sorted order.
    """
    by_category: dict[str, list[FeedInfo]] = {}
    for feed in feeds:
        by_category.setdefault(_category_key(feed.site), []).append(feed)

    extras = sorted(c for c in by_category if c not in _KNOWN_CATEGORIES)
    ordered: list[tuple[str, str, list[FeedInfo]]] = []
    seen: set[str] = set()
    for cat in [c for c, _, _ in _CATEGORY_SECTIONS] + extras:
        if cat in seen:
            continue
        seen.add(cat)
        if cat in by_category:
            if cat in _KNOWN_CATEGORIES:
                title = next(t for c, t, _ in _CATEGORY_SECTIONS if c == cat)
                folder = next(f for c, _, f in _CATEGORY_SECTIONS if c == cat)
            else:
                title = _humanize_category(cat)
                folder = f"Online-{title.replace(' ', '-')}"
            ordered.append((title, folder, by_category[cat]))
    return ordered


def _write_opml(
    sections: list[tuple[str, str, list[FeedInfo]]],
    output_path: Path = OUTPUT_OPML,
) -> None:
    from xml.sax.saxutils import escape as xml_escape

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head><title>FMHY Streaming Feeds</title></head>",
        "  <body>",
    ]
    total = 0
    for folder, feeds in ((folder, feeds) for _title, folder, feeds in sections):
        if not feeds:
            continue
        lines.append(f'    <outline text="{xml_escape(folder)}" title="{xml_escape(folder)}">')
        for f in feeds:
            if not f.has_feed:
                continue
            total += 1
            absolute_feed = f"{GITHUB_PAGES_FEED_BASE.rstrip('/')}/{f.href.lstrip('/')}"
            lines.append(
                f'      <outline type="rss" text="{xml_escape(f.site.display_name or f.site.name)}" '
                f'title="{xml_escape(f.site.display_name or f.site.name)}" '
                f'xmlUrl="{xml_escape(absolute_feed)}" '
                f'htmlUrl="{xml_escape(f.site.url)}"/>'
            )
        lines.append("    </outline>")
    lines.extend(["  </body>", "</opml>"])
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {output_path} ({total} feeds).")


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
    return [
        "          <tr>",
        f"            <td>{escape(_site_display_name(feed.site, section_title))}</td>",
        f"            <td>{rss_cell}</td>",
        f"            <td>{inoreader_cell}</td>",
        f"            <td class='status {status_class}'>{escape(feed.status)}</td>",
        f"            <td class='col-updated'>{escape(feed.last_build_date) or '—'}</td>",
        f"            <td>{feed.items_count}</td>",
        f"            <td><a href='{escape(feed.site.url)}'>Source</a></td>",
        "          </tr>",
    ]


def _dashboard_html(feeds: list[FeedInfo], enabled_count: int, disabled_count: int) -> list[str]:
    total = len(feeds)
    available = sum(1 for f in feeds if f.status == "Available")
    unavailable = total - available
    total_items = sum(f.items_count for f in feeds)
    pct = round(available / total * 100) if total else 0
    enabled_label = f"{enabled_count}" + (f" <span class='muted-note'>(+{disabled_count} disabled)</span>" if disabled_count else "")
    return [
        "    <section class='dashboard'>",
        "      <h2>Feed Health</h2>",
        "      <div class='dash-grid'>",
        f"        <div class='dash-card dash-info'><div class='num'>{enabled_label}</div><div class='lbl'>Enabled Feeds</div></div>",
        f"        <div class='dash-card dash-ok'><div class='num'>{available}</div><div class='lbl'>Available</div></div>",
        f"        <div class='dash-card {'dash-warn' if unavailable else 'dash-ok'}'><div class='num'>{unavailable}</div><div class='lbl'>Unavailable</div></div>",
        f"        <div class='dash-card dash-info'><div class='num'>{total_items}</div><div class='lbl'>Total Items</div></div>",
        "      </div>",
        f"      <div class='dash-bar-wrap'><div class='dash-bar-fill' style='width:{pct}%'></div></div>",
        "    </section>",
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


def _site_display_name(site: SiteConfig, section_title: str = "") -> str:
    raw = site.display_name or _default_display_name(site.name)
    # Strip redundant category suffix when it matches the section header
    section_lower = section_title.strip().lower()
    raw_lower = raw.strip().lower()
    redundants = []
    if section_lower == "movies":
        redundants = ["movies"]
    elif section_lower in ("tv shows", "tv"):
        redundants = ["tv shows", "tv", "series", "shows", "episodes"]
    elif section_lower == "episodes":
        redundants = ["episodes", "tv shows", "seriale", "seasons"]
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


def _default_display_name(name: str) -> str:
    return name.replace("-", " ").replace("_", " ")


def _safe_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = " ".join(value.split())
    return cleaned or fallback


if __name__ == "__main__":
    generate_index()
