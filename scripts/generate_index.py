#!/usr/bin/env python3
from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import quote

from core.config import SiteConfig, load_config


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config" / "sites"
FEEDS_DIR = REPO_ROOT / "feeds"
OUTPUT_FILE = REPO_ROOT / "index.html"
GITHUB_PAGES_FEED_BASE = "https://ibaciu6.github.io/rss-generator"
INOREADER_FEED_PREFIX = "https://www.inoreader.com/search/feeds/"

_SERIALE_CATEGORIES = frozenset({"episodes", "updates"})


def generate_index(
    config_path: Path = CONFIG_DIR,
    feeds_dir: Path = FEEDS_DIR,
    output_file: Path = OUTPUT_FILE,
) -> None:
    """
    Write a static index: links from config only (no feed health, status, or item counts).
    Regenerate when sources under ``config_path`` change.
    """
    _ = feeds_dir  # reserved for future optional checks
    config = load_config(config_path)
    sites = list(config.sites)
    filme = [s for s in sites if not _is_series_section(s)]
    seriale = [s for s in sites if _is_series_section(s)]

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
        "      <p class='lede'>Each configured source publishes an RSS file under <code>feeds/</code>. If generation fails, the feed file may still contain a short diagnostic entry instead of a 404.</p>",
        "      <p class='meta'>This page is static: it only lists sources from <code>config/sites/movies/</code> and <code>config/sites/series/</code> and is regenerated when that configuration changes (not on every feed run).</p>",
        "    </section>",
    ]

    html_lines.extend(_static_section("Filme", filme))
    html_lines.extend(_static_section("Seriale", seriale))

    html_lines.extend(
        [
            "    <p class='note'>Feed files live under <code>feeds/</code>. Project pages use relative links here so GitHub Pages serves <code>/rss-generator/feeds/*.xml</code> correctly.</p>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )

    output_file.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"Generated static {output_file} with {len(sites)} sources ({len(filme)} Filme, {len(seriale)} Seriale).")


def _is_series_section(site: SiteConfig) -> bool:
    if site.config_bucket == "series":
        return True
    if site.config_bucket == "movies":
        return False
    c = (site.category or "").strip().lower()
    return c in _SERIALE_CATEGORIES


def _static_row(site: SiteConfig) -> list[str]:
    href = f"feeds/{site.feed_file}"
    absolute_feed = f"{GITHUB_PAGES_FEED_BASE.rstrip('/')}/{href}"
    inoreader_url = f"{INOREADER_FEED_PREFIX}{quote(absolute_feed, safe='')}"
    return [
        "          <tr>",
        f"            <td>{escape(_site_display_name(site))}</td>",
        f"            <td><a href='{escape(href)}'>RSS</a></td>",
        "            <td>",
        f"              <a class='btn-inoreader' href='{escape(inoreader_url)}' ",
        "                rel='noopener noreferrer' target='_blank' ",
        "                title='Preview in Inoreader, then follow'>Inoreader</a>",
        "            </td>",
        f"            <td><a href='{escape(site.url)}'>Source</a></td>",
        "          </tr>",
    ]


def _static_section(title: str, sites: list[SiteConfig]) -> list[str]:
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
        "            <th>Source</th>",
        "          </tr>",
        "        </thead>",
        "        <tbody>",
    ]
    for site in sites:
        lines.extend(_static_row(site))
    lines.extend(
        [
            "        </tbody>",
            "      </table>",
            "      </div>",
            "    </section>",
        ]
    )
    return lines


def _display_name(name: str) -> str:
    return name.replace("-", " ").title()


def _site_display_name(site: SiteConfig) -> str:
    return site.display_name or _display_name(site.name)


if __name__ == "__main__":
    generate_index()
