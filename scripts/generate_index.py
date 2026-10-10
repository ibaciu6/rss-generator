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

GITHUB_PAGES_FEED_BASE = "https://raw.githubusercontent.com/ibaciu6/rss-generator/main"
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

# Column widths as percentages, shared by every section table. The tables are
# `table-layout: fixed`, so these percentages -- not the content -- decide each
# column, which is what makes the grid line up from the Movies section to
# Education.
#
# Fixed layout is not free, though, and this is the part that is easy to get
# wrong. Under the default `auto` layout a column is sized to its content and a
# table too narrow for it simply grows past its `min-width` to fit. Fixed layout
# honours the percentages instead and cannot grow, so a column narrower than its
# own widest value does not reflow: its `nowrap` text runs on past the cell and
# over the next one. So these numbers are measured, not chosen.
#
# The bar is exact. Two adjacent cells share their padding, so a cell's text
# reaches its neighbour's first glyph once it is wider than the whole cell box;
# keeping it inside its own padding needs one padding width more than that.
# _MEASURED_COLUMN_NEEDS is the widest text in each column (headers included,
# measured in Chromium) plus one desktop cell padding, and each percentage below
# is that need's share of the floor, so nothing collides at the floor or above.
# Guessing these -- which is what they were -- put Updated 4px short of its own
# timestamp at 760px, which is how a 17% column ends up painting "12:13 UTC"
# over the item count on every page narrower than about 790px.
_COLUMN_WIDTHS: tuple[tuple[str, int], ...] = (
    ("Site", 31),
    ("RSS", 6),
    ("Inoreader", 14),
    ("Status", 11),
    ("Updated", 20),
    ("Items", 8),
    ("Source", 10),
)

# The widest text in each column plus one 12px cell padding, measured in
# Chromium at the shipped font sizes and rounded up: Site is the longest
# untruncated name, Updated a "2026-10-04 02:41 UTC" stamp, and Items and Source
# are set by their letterspaced uppercase headers. Kept as data beside the
# percentages rather than folded into them so the test suite can hold the two to
# each other: content that outgrows these widths has to show up as a failing
# test, not as overlapping text nobody looks for.
_MEASURED_COLUMN_NEEDS: dict[str, int] = {
    "Site": 223,
    "RSS": 41,
    "Inoreader": 98,
    "Status": 76,
    "Updated": 145,
    "Items": 58,
    "Source": 71,
}

# The table's floor width. Unchanged from before this grid, and deliberately
# low: with the percentages above every column still fits at 760px, so a narrow
# window scrolls exactly as far as it always has. Raising the floor was the
# other way to stop the overlap, and it would have cost real estate on every
# tablet to solve a problem these percentages do not have.
_TABLE_MIN_WIDTH = 760


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
        "    .btn-reader { display: inline-block; padding: 8px 18px; font-size: 0.88rem; font-weight: 700; color: #fff; background: var(--accent); border-radius: 8px; text-decoration: none; }",
        "    .btn-reader:hover { background: #7d2d10; text-decoration: none; }",
        "    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 0; }",
        "    h1 { margin: 0 0 12px; font-size: clamp(2.2rem, 5vw, 3.6rem); line-height: 1; letter-spacing: -0.04em; }",
        "    .lede, .meta { margin: 0; color: var(--muted); font-size: 1.05rem; }",
        "    .meta { margin-top: 10px; font-size: 0.95rem; }",
        "    .table-wrap { margin-top: 24px; padding: 22px 24px 24px; }",
        "    .table-wrap + .table-wrap { margin-top: 32px; }",
        "    .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }",
        "    h2.section-title { margin: 0 0 14px; font-size: 1.15rem; font-weight: 700; color: var(--text); letter-spacing: 0.02em; line-height: 1.35; }",
        f"    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; min-width: {_TABLE_MIN_WIDTH}px; }}",
        "    th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }",
        "    th { font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); background: rgba(247, 215, 200, 0.35); white-space: nowrap; }",
        "    tr:last-child td { border-bottom: 0; }",
        "    /* The one variable-length column. Truncating rather than wrapping is",
        "       what stops a single long site name from turning its row two lines",
        "       tall; the full name rides along in the title attribute. */",
        "    td.col-site { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
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
        "      th, td { padding: 8px 10px; }",
        "    }",
        "  </style>",
        "</head>",
        "<body>",
        "  <main>",
        "    <section class='hero'>",
        "      <h1>RSS Generator</h1>",
        "      <p class='lede'><a href='https://github.com/ibaciu6/rss-generator' rel='noopener noreferrer' target='_blank'>github.com/ibaciu6/rss-generator</a></p>",
        "      <p class='lede'>Read them here, or subscribe to them in a real reader &mdash; your choice.</p>",
        "      <p class='actions'>",
        "        <a href='reader.html' class='btn-reader'>Open the reader</a>",
        "        <button type='button' class='btn-opml' onclick='downloadOpml()'>Download OPML</button>",
        "      </p>",
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
          "  <script>",
          "    function downloadOpml() {",
          "      fetch('https://raw.githubusercontent.com/ibaciu6/rss-generator/main/feeds.opml')",
          "        .then(response => response.blob())",
          "        .then(blob => {",
          "          const url = window.URL.createObjectURL(blob);",
          "          const a = document.createElement('a');",
          "          a.style.display = 'none';",
          "          a.href = url;",
          "          a.download = 'feeds.opml';",
          "          document.body.appendChild(a);",
          "          a.click();",
          "          window.URL.revokeObjectURL(url);",
          "        })",
          "        .catch(error => {",
          "          console.error('Error downloading OPML:', error);",
          "          alert('Failed to download OPML. Please try again.');",
          "        });",
          "    }",
          "  </script>",
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


def _feed_row_lines(feed: FeedInfo, label: str, full_label: str) -> list[str]:
    """One ``<tr>`` for a feed: the label to show and the full name behind it.

    Both are required rather than defaulted. A previous version fell back to
    deriving them here, which was unreachable (the only caller always passes
    both) and wrong if reached anyway: it called _site_display_name without the
    section title, so the redundant-suffix stripping never ran and a site named
    "Xfilme.ro Episodes" in the Episodes section would have been labelled
    "Xfilme.ro Episodes" in a column headed "Episodes".
    """
    status_class = f"status-{feed.status.lower().replace(' ', '-')}"
    if feed.has_feed:
        absolute_feed = f"{GITHUB_PAGES_FEED_BASE.rstrip('/')}/{feed.href.lstrip('/')}"
        rss_cell = f"<a href='{escape(absolute_feed)}'>RSS</a>"
        inoreader_url = f"{INOREADER_FEED_PREFIX}{quote(absolute_feed, safe='')}"
        inoreader_cell = (
            f"<a class='btn-inoreader' href='{escape(inoreader_url)}' "
            f"rel='noopener noreferrer' target='_blank' "
            f"title='Preview in Inoreader, then follow'>Inoreader</a>"
        )
    else:
        rss_cell = "<span aria-disabled='true'>Not available</span>"
        inoreader_cell = "<span class='inoreader-na'>—</span>"
    return [
        "          <tr>",
        # Always the full name, never a condition on it. The label is shortened
        # for 10 of today's sites and the fixed-width column ellipsises whatever
        # outgrows it, so the only way to keep a row identifiable in both cases
        # without guessing in Python which names those are is to carry the full
        # name unconditionally. Where the two match the tooltip simply repeats
        # what is already on screen.
        f"            <td class='col-site' title='{escape(full_label)}'>{escape(label)}</td>",
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
    if not feeds:
        return []
    lines: list[str] = [
        "    <section class='table-wrap'>",
        f"      <h2 class='section-title'>{escape(title)}</h2>",
        "      <div class='table-scroll'>",
        "      <table>",
        # The same colgroup in every section table. With `table-layout: fixed`
        # these percentages decide the columns outright, so a table of
        # four-character names cannot hand its spare width to the Site column
        # and leave the next section's grid misaligned with it.
        "        <colgroup>",
        *[f"          <col style='width:{pct}%'>" for _name, pct in _COLUMN_WIDTHS],
        "        </colgroup>",
        "        <thead>",
        "          <tr>",
        *[f"            <th>{escape(name)}</th>" for name, _pct in _COLUMN_WIDTHS],
        "          </tr>",
        "        </thead>",
        "        <tbody>",
    ]
    for feed, label, full_label in _labelled_rows(feeds, title):
        lines.extend(_feed_row_lines(feed, label, full_label))
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


# A site name followed by one of these is a name plus a description of itself.
# Only the head is the name: "Securelist - Information about Viruses, Hackers
# and Spam" is Securelist, and the tail is 48 characters of column.
_NAME_TAIL_SEPARATORS = (" - ", " – ", " — ", " | ", " :: ")

# Trailing words that describe the kind of site instead of identifying it.
# Longest first, so "Cybersecurity Blog" is consumed whole rather than leaving
# a dangling "Cybersecurity". Matched case-insensitively as a suffix.
#
# Note what is *not* here: a bare "news". "The Hacker News" is the name of a
# publication, and trimming the "News" leaves "The Hacker", which is a person
# who breaks into things -- a different thing entirely, and a label that reads
# as a truncation bug rather than as a name. "News" only qualifies as noise as
# part of a descriptor ("Technology News"), where the generic adjective is
# already doing the work. The same reasoning keeps "Times" and "Post" out.
_NOISE_TRAILING_WORDS = (
    "cybersecurity blog",
    "security blog",
    "technology news",
    "additions feed",
    "online security blog",
    "blog",
    "feed",
    "unpacked",
    "online",
)

# Below this, a shortened label is no longer a name -- "TV" or "Ro" identifies
# nothing -- so the original is kept instead.
_MIN_LABEL_CHARS = 3


def _short_display_name(name: str) -> str:
    """Trim a site name down to the part that identifies it.

    Presentation only. The full name stays in ``config/sites.yaml`` because it
    is also the published feed ``<title>`` and the OPML label, and shortening
    those would rename feeds for every existing subscriber to make one page's
    columns line up. Callers put the full name back in a ``title`` attribute.
    """
    label = " ".join(name.split())
    # The earliest separator in the string wins, not the first one in the
    # tuple. "Foo | Bar - Baz" contains both, and cutting at the "|" is what
    # reads as the name; cutting at the "-" would leave "Foo | Bar", which
    # still looks like a name plus something. Taking the earliest offset makes
    # the result independent of the order of _NAME_TAIL_SEPARATORS.
    offsets = [label.find(sep) for sep in _NAME_TAIL_SEPARATORS]
    offsets = [offset for offset in offsets if offset != -1]
    if offsets:
        head = label[: min(offsets)].strip()
        if len(head) >= _MIN_LABEL_CHARS:
            label = head
    # Loop rather than a single pass: "Google Online Security Blog" sheds
    # "Security Blog" and then still has "Online" left on the end.
    changed = True
    while changed:
        changed = False
        lowered = label.lower()
        for noise in _NOISE_TRAILING_WORDS:
            if lowered.endswith(" " + noise):
                trimmed = label[: len(label) - len(noise) - 1].strip()
                if len(trimmed) >= _MIN_LABEL_CHARS:
                    label = trimmed
                    changed = True
                break
    return label


def _labelled_rows(feeds: list[FeedInfo], section_title: str) -> list[tuple[FeedInfo, str, str]]:
    """Each feed with the label to show and the full name behind it, in row order.

    Shortening can make two distinct sites read the same, and two identical
    labels in one column look like a bug rather than two sites. Sites whose
    label is already their full name claim it first: they gave up nothing, so
    the one that *was* trimmed is the one that falls back to its full name.
    Without that ordering a site literally named "Foo" would lose its name to
    "Foo Blog" shortened, which is the collision backwards.
    """
    labelled: list[tuple[FeedInfo, str, str, bool]] = []
    for feed in feeds:
        full = _site_display_name(feed.site, section_title)
        # A name with nothing in it labels nothing, and load_config does not
        # police display_name, so fall back to the site key. Better a readable
        # key in the cell than a blank one.
        if not full.strip():
            full = _default_display_name(feed.site.name)
        # Compare against the whitespace-collapsed full name, not the raw one.
        # Collapsing is not shortening, and calling it shortening would let a
        # site whose display_name merely has a double space in it get its raw,
        # ugly name promoted back as the visible label by the collision fallback.
        collapsed = " ".join(full.split())
        short = _short_display_name(full)
        # Nothing to show. display_name is free text that load_config does not
        # police, so it can arrive empty, blank, or as bare separator debris
        # ("-", "::"), and a cell holding that identifies no site at all. The
        # key is the only thing left that does.
        if not any(ch.isalnum() for ch in short):
            short = _default_display_name(feed.site.name) or collapsed
        labelled.append((feed, short, full, short != collapsed))

    # Sites that gave up nothing claim their label first: they gave up nothing,
    # so the one that *was* trimmed is the one that falls back to its full name.
    claimed = {full.casefold() for _feed, _short, full, trimmed in labelled if not trimmed}
    rows: list[tuple[FeedInfo, str, str]] = []
    for feed, short, full, trimmed in labelled:
        # The fallback restores the full name, which some other row may already
        # be showing. That can only happen when two sites share a display_name,
        # in which case nothing in the names can tell them apart; showing the
        # full name twice is still better than two identical truncated labels,
        # which would read as a rendering bug.
        if trimmed and short.casefold() in claimed:
            short = full
        claimed.add(short.casefold())
        rows.append((feed, short, full))
    # Sorting on the label rather than the full name: within a section the two
    # orders differ only for the handful of sites that were shortened, and
    # those should sit with the label the reader actually sees.
    return sorted(rows, key=lambda row: row[1].lower())


def _safe_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = " ".join(value.split())
    return cleaned or fallback


if __name__ == "__main__":
    generate_index()
