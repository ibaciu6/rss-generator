#!/usr/bin/env python3
"""Local Inoreader-style reader for reviewing the generated RSS feeds.

Serves a single-page web UI on localhost that reads feeds/*.xml directly (no
TMDB key, no network scraping, no CI dependency) and groups feeds in folders
the same way Inoreader shows them (Online-Movies-RO / Online-Movies-EN /
Online-Episodes-RO / Online-Torrents).

Run:
    scripts/local_reader.py [PORT]
    scripts/start_reader.sh     # preferred entry point (background + verify)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"
CONFIG_FILE = REPO_ROOT / "config" / "sites.yaml"

FOLDER_BY_CAT_LANG = {
    ("movies", "ro"): "Online-Movies-RO",
    ("movies", "en"): "Online-Movies-EN",
    ("episodes", "ro"): "Online-Episodes-RO",
    ("updates", "ro"): "Online-Episodes-RO",
    ("torrents", "en"): "Online-Torrents",
    ("torrents", "ro"): "Online-Torrents",
}
FOLDER_FALLBACK = "Other"

def _folder_from_filename(file_name: str) -> str:
    """Infer a folder for orphan feeds (not in config) from their file name."""
    lower = file_name.lower()
    if "episod" in lower or "seriale" in lower or "tv" in file_name:
        return "Online-Episodes-RO"
    if "movies" in lower or "filme" in lower or "film" in lower:
        return "Online-Movies-EN"
    return FOLDER_FALLBACK


def _load_site_names() -> dict[str, tuple[str, str]]:
    """Map feed_file -> (display_name, folder_name)."""
    out: dict[str, tuple[str, str]] = {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return out
    for site in (data.get("sites") or {}).values():
        feed_file = site.get("feed_file")
        if not feed_file:
            continue
        cat = site.get("category", "").lower()
        lang = site.get("language", "").lower()
        folder = FOLDER_BY_CAT_LANG.get((cat, lang), FOLDER_FALLBACK)
        out[feed_file] = (site.get("display_name") or feed_file, folder)
    return out


def _feed_date(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw.strip())
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def parse_feed(path: Path) -> dict:
    site_names = _load_site_names()
    display_name, folder = site_names.get(
        path.name, (path.stem.replace("-", " ").title(), _folder_from_filename(path.name))
    )
    items: list[dict] = []
    feed_title, feed_link, feed_desc = display_name, "", ""
    try:
        root = ET.parse(path).getroot()
        channel = root.find("channel")
        if channel is None:
            channel = root
        for tag in ("title", "link", "description"):
            el = channel.find(tag)
            if el is not None and el.text:
                if tag == "title":
                    feed_title = el.text.strip()
                elif tag == "link":
                    feed_link = el.text.strip()
                else:
                    feed_desc = el.text.strip()
        for item in channel.findall("item"):
            def _txt(name: str) -> str:
                el = item.find(name)
                return el.text.strip() if el is not None and el.text else ""

            guid = _txt("guid") or _txt("link")
            desc = _txt("description")
            enc = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if enc is not None and enc.text:
                desc = enc.text.strip()
            items.append(
                {
                    "title": _txt("title"),
                    "link": _txt("link"),
                    "date": _feed_date(_txt("pubDate")),
                    "guid": guid,
                    "desc_html": desc,
                    "snippet": plain_text(desc),
                    "tags": extract_tags(desc),
                }
            )
    except Exception:
        pass
    return {
        "file": path.name,
        "name": display_name,
        "folder": folder,
        "title": feed_title,
        "link": feed_link,
        "description": feed_desc,
        "items": items,
    }


def extract_tags(desc_html: str) -> list[str]:
    """Pull visible <b>-bold link labels (e.g. 'Trailer', 'IMDb') like Inoreader's short-links row."""
    tags = []
    for match in re.findall(r"<b[^>]*>(.*?)</b>", desc_html, re.S | re.I):
        label = html.unescape(re.sub(r"<[^>]+>", "", match)).strip()
        if label and label not in tags:
            tags.append(label)
    return tags


def plain_text(desc_html: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", desc_html))
    return " ".join(text.split())


def iter_feed_files() -> list[Path]:
    return sorted(FEEDS_DIR.glob("*.xml"))


def build_toc() -> list[dict]:
    toc: dict[str, list[dict]] = {}
    for path in iter_feed_files():
        feed = parse_feed(path)
        toc.setdefault(feed["folder"], []).append(
            {
                "file": feed["file"],
                "name": feed["name"],
                "title": feed["title"],
                "unread_count": len(feed["items"]),
                "item_count": len(feed["items"]),
            }
        )
    return [
        {"folder": folder, "feeds": feeds}
        for folder, feeds in sorted(toc.items(), key=lambda kv: kv[0])
    ]


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feed Reader</title>
<style>
  :root {
    --bg: #f5f1e8; --panel: #fffaf0; --text: #1f2933;
    --muted: #52606d; --line: #d9cbb2; --accent: #9f3a16;
    --accent-soft: #f7d7c8; --active: #3584e4; --sidebar-w: 250px;
    --unread: #c92a2a;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; font-family: Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text);
    display: flex; height: 100vh; overflow: hidden;
  }
  aside {
    width: var(--sidebar-w); min-width: var(--sidebar-w);
    background: var(--panel); border-right: 1px solid var(--line);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .brand { padding: 12px 14px 8px; border-bottom: 1px solid var(--line); }
  .brand h1 { margin: 0; font-size: 1.15rem; font-family: Georgia, serif; }
  .brand .sub { font-size: 0.72rem; color: var(--muted); margin-top: 2px; }
  #feed-tree { flex: 1; overflow-y: auto; padding: 4px 6px 12px; }
  .folder { margin-top: 6px; }
  .folder-toggle {
    display: flex; align-items: center; gap: 4px; width: 100%;
    padding: 6px 8px; font-size: 0.8rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted);
    background: transparent; border: none; cursor: pointer; border-radius: 6px;
    font-family: inherit;
  }
  .folder-toggle:hover { background: var(--accent-soft); }
  .folder-toggle .caret { width: 12px; transition: transform 0.12s; }
  .folder-toggle .folder-count { margin-left: auto; font-weight: 400; opacity: 0.7; }
  .folder.collapsed .caret { transform: rotate(-90deg); }
  .folder.collapsed .feed-item { display: none; }
  .feed-item {
    display: flex; width: 100%; text-align: left; align-items: center; gap: 8px;
    padding: 5px 8px 5px 22px; font-size: 0.82rem; font-family: inherit;
    border: none; border-radius: 5px; background: transparent; color: var(--text);
    cursor: pointer; margin-bottom: 1px;
    white-space: nowrap; overflow: hidden;
  }
  .feed-item:hover { background: var(--accent-soft); }
  .feed-item.active { background: var(--active); color: #fff; }
  .feed-item .unread {
    min-width: 16px; height: 16px; padding: 0 4px; font-size: 0.68rem; line-height: 16px;
    text-align: center; border-radius: 9px; background: var(--unread); color: #fff;
    flex-shrink: 0;
  }
  .feed-item.active .unread { background: rgba(255,255,255,0.25); }
  .feed-item .fname { overflow: hidden; text-overflow: ellipsis; }
  .feed-item .dots { margin-left: auto; color: var(--text); flex-shrink: 0; letter-spacing: 1px; }
  .feed-item.active .dots { color: #fff; }

  main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  .toolbar {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 10px 16px; border-bottom: 1px solid var(--line); background: var(--panel);
  }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  .seg button {
    border: none; background: transparent; font-size: 0.78rem; padding: 5px 12px;
    cursor: pointer; font-family: inherit; color: var(--text);
  }
  .seg button.on { background: var(--accent); color: #fff; }
  #search {
    flex: 1; min-width: 140px; max-width: 320px; font-size: 0.8rem;
    padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px;
    background: var(--bg); font-family: inherit;
  }
  .toolbar .spacer { flex: 1; }
  .btn {
    font-size: 0.78rem; padding: 5px 10px; border: 1px solid var(--line);
    border-radius: 6px; background: var(--bg); cursor: pointer; font-family: inherit; color: var(--text);
  }
  .btn:hover { border-color: var(--accent); }

  #art-pane { flex: 1; display: flex; overflow: hidden; min-height: 0; }
  #news { flex: 1; overflow-y: auto; padding: 10px 12px 20px; min-width: 0; }
  #panel { width: 46%; min-width: 380px; border-left: 1px solid var(--line); overflow-y: auto; }
  @media (max-width: 900px) { #panel { display: none; } }

  .art {
    display: flex; gap: 10px; padding: 8px 10px; border-radius: 8px; cursor: pointer;
    border-bottom: 1px solid var(--line);
  }
  .art:hover { background: var(--accent-soft); }
  .art.dot { border-left: 3px solid var(--unread); }
  .art .bd { flex: 1; min-width: 0; }
  .art .title { font-size: 0.9rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .art.visited .title { color: var(--muted); }
  .art .src-date { font-size: 0.72rem; color: var(--muted); display: flex; gap: 8px; flex-wrap: wrap; }
  .art .snippet { font-size: 0.78rem; color: var(--muted); margin-top: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .art .tags { margin-top: 4px; display: flex; gap: 6px; flex-wrap: wrap; }
  .tag {
    font-size: 0.68rem; padding: 1px 7px; border-radius: 10px;
    background: var(--accent-soft); color: var(--accent); text-decoration: none;
  }
  .empty, .loader { text-align: center; padding: 60px 20px; color: var(--muted); font-size: 0.9rem; }
  .error { padding: 30px; color: var(--unread); text-align: center; }

  #panel { background: var(--panel); padding: 16px; }
  .panel-title { font-size: 1.05rem; font-weight: 700; margin: 0 0 4px; font-family: Georgia, serif; }
  .panel-title a { color: var(--text); text-decoration: none; }
  .panel-close { float: right; border: 1px solid var(--line); background: var(--bg); border-radius: 6px; cursor: pointer; font-size: 0.75rem; padding: 3px 8px; }
  .panel-meta { font-size: 0.75rem; color: var(--muted); margin-bottom: 10px; }
  .panel-desc { font-size: 0.9rem; line-height: 1.55; overflow-wrap: anywhere; }
  .panel-desc img { max-width: 100%; max-height: 380px; width: auto; height: auto; object-fit: contain; display: block; border-radius: 6px; margin: 8px 0; }
  .panel-desc a { color: var(--accent); }
</style>
</head>
<body>
<aside>
  <div class="brand">
    <h1>Feed Reader</h1>
    <div class="sub" id="aside-sub">loading…</div>
  </div>
  <div id="feed-tree"></div>
</aside>
<main>
  <div class="toolbar">
    <div class="seg" id="mode-seg">
      <button data-mode="unread" class="on" id="mode-unread">Unread</button>
      <button data-mode="all" id="mode-all">All</button>
    </div>
    <input id="search" type="search" placeholder="Search articles…">
    <div class="spacer"></div>
    <button class="btn" id="refresh">Refresh feeds</button>
  </div>
  <div id="art-pane">
    <div id="news"><div class="loader">Loading feeds…</div></div>
    <div id="panel"></div>
  </div>
</main>
<script>
const API = 'api?list';
const STATE = { toc: null, mode: 'unread', query: '', current: null, feeds: {}, art: {} };
const KEY = 'localreader.read.';

/* ---------- persistence ---------- */
function readSet() { try { return new Set(JSON.parse(localStorage.getItem(KEY + 'set') || '[]')); } catch (e) { return new Set(); } }
function saveSet(s) { localStorage.setItem(KEY + 'set', JSON.stringify([...s])); }
let READ = readSet();

function guidKey(feedFile, guid) { return feedFile + '::' + guid; }

/* ---------- helpers ---------- */
function h(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
function escAttr(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function relTime(dateStr) {
  if (!dateStr) return '';
  const t = new Date(dateStr.replace(' ', 'T') + 'Z');
  if (isNaN(t)) return dateStr;
  const sec = (Date.now() - t) / 1000;
  if (sec < 60) return 'now';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  if (sec < 604800) return Math.floor(sec / 86400) + 'd';
  return dateStr.slice(0, 10);
}
function countUnread(feed) {
  return feed.items.filter(i => !READ.has(guidKey(feed.file, i.guid || i.link || i.title))).length;
}
function feedItems(feed) {
  return feed.items.map(i => ({ ...i, read: READ.has(guidKey(feed.file, i.guid || i.link || i.title)) }));
}

/* ---------- sidebar ---------- */
function renderTree() {
  const tree = document.getElementById('feed-tree');
  tree.innerHTML = '';
  let totalUnread = 0;
  for (const group of STATE.toc) {
    const folder = document.createElement('div');
    folder.className = 'folder';
    const fU = group.feeds.reduce((a, f) => a + f.unread_count, 0);
    totalUnread += fU;
    const toggle = document.createElement('button');
    toggle.className = 'folder-toggle';
    toggle.innerHTML = '<span class="caret">▼</span>' + h(group.folder) +
      '<span class="folder-count">' + (fU || '') + '</span>';
    toggle.onclick = () => folder.classList.toggle('collapsed');
    folder.appendChild(toggle);
    for (const fmeta of group.feeds) {
      const feed = STATE.feeds[fmeta.file];
      const un = feed ? countUnread(feed) : fmeta.unread_count;
      const nItems = feed ? feed.items.length : fmeta.item_count;
      const btn = document.createElement('button');
      btn.className = 'feed-item' + (STATE.current === fmeta.file ? ' active' : '');
      btn.innerHTML =
        (un ? '<span class="unread">' + un + '</span>' : '') +
        '<span class="fname">' + h(fmeta.name) + '</span>' +
        (nItems ? '<span class="dots">•••</span>' : '');
      btn.onclick = () => { selectFeed(fmeta.file); };
      folder.appendChild(btn);
    }
    tree.appendChild(folder);
  }
  document.getElementById('aside-sub').textContent = totalUnread + ' unread / ' +
    Object.keys(STATE.feeds).length + ' feeds';
}

async function loadFeedAny(file) {
  if (STATE.feeds[file]) return STATE.feeds[file];
  const res = await fetch('api?feed=' + encodeURIComponent(file));
  if (!res.ok) throw new Error('feed ' + file + ' failed');
  STATE.feeds[file] = await res.json();
  return STATE.feeds[file];
}

async function selectFeed(file) {
  document.querySelectorAll('.feed-item').forEach(b => b.classList.remove('active'));
  STATE.current = file;
  try { await loadFeedAny(file); } catch (e) { return; }
  const btn = [...document.querySelectorAll('.feed-item')].find(b => b.textContent.includes(STATE.feeds[file].name));
  if (btn) btn.classList.add('active');
  renderNews();
}

/* ---------- news list ---------- */
function renderNews() {
  const news = document.getElementById('news');
  if (!STATE.current) { news.innerHTML = '<div class="empty">Select a feed from the sidebar.</div>'; return; }
  const feed = STATE.feeds[STATE.current];
  if (!feed) { news.innerHTML = '<div class="loader">Loading…</div>'; return; }
  let items = feedItems(feed);
  if (STATE.mode === 'unread') items = items.filter(i => !i.read);
  const q = STATE.query.toLowerCase();
  if (q) items = items.filter(i => (i.title + ' ' + i.snippet).toLowerCase().includes(q));
  if (!items.length) {
    news.innerHTML = '<div class="empty">' + (STATE.mode === 'unread' && q === '' ? 'All caught up — you are up to date.' : 'No articles match.') + '</div>';
    return;
  }
  news.innerHTML = '';
  for (const it of items) {
    const row = document.createElement('div');
    row.className = 'art' + (it.read ? ' visited' : ' dot');
    row.innerHTML =
      '<div class="bd">' +
        '<div class="title">' + h(it.title) + '</div>' +
        '<div class="src-date"><span>' + h(feed.name) + '</span><span>' + h(relTime(it.date)) + ' · ' + h(it.date) + '</span></div>' +
        (it.snippet ? '<div class="snippet">' + h(it.snippet.slice(0, 220)) + '</div>' : '') +
        (it.tags.length ? '<div class="tags">' + it.tags.map(t => '<a class="tag" href="#" onclick="event.stopPropagation();return false;">' + h(t) + '</a>').join('') + '</div>' : '') +
      '</div>';
    row.onclick = () => { markRead(feed, it, row); openPanel(feed, it); };
    news.appendChild(row);
  }
}

function markRead(feed, it, row) {
  READ.add(guidKey(feed.file, it.guid || it.link || it.title));
  saveSet(READ);
  row.classList.add('visited');
  row.classList.remove('dot');
  renderTree();
  const sidebarBtn = [...document.querySelectorAll('.feed-item')].find(b => b.textContent.includes(feed.name));
  const badge = sidebarBtn && sidebarBtn.querySelector('.unread');
  if (badge) { const n = countUnread(feed); badge.textContent = n || ''; badge.style.display = n ? '' : 'none'; }
}

function openPanel(feed, it) {
  const panel = document.getElementById('panel');
  panel.innerHTML =
    '<button class="panel-close" onclick="document.getElementById(\\\'panel\\\').innerHTML=\\\'\\\'">✕</button>' +
    '<h2 class="panel-title">' + (it.link ? '<a href="' + escAttr(it.link) + '" target="_blank" rel="noreferrer">' + h(it.title) + '</a>' : h(it.title)) + '</h2>' +
    '<div class="panel-meta"><a href="' + escAttr(feed.link) + '" target="_blank" rel="noreferrer">' + h(feed.title) + '</a> · ' + h(it.date) + '</div>' +
    '<div class="panel-desc">' + (it.desc_html || h(it.snippet)) + '</div>';
}

/* ---------- mode / search ---------- */
document.getElementById('mode-seg').addEventListener('click', (e) => {
  if (!e.target.dataset.mode) return;
  STATE.mode = e.target.dataset.mode;
  document.querySelectorAll('#mode-seg button').forEach(b => b.classList.toggle('on', b.dataset.mode === STATE.mode));
  renderNews();
});
document.getElementById('search').addEventListener('input', (e) => { STATE.query = e.target.value; renderNews(); });
document.getElementById('refresh').addEventListener('click', () => boot(true));

/* ---------- boot ---------- */
async function boot(reload) {
  const news = document.getElementById('news');
  if (reload) news.innerHTML = '<div class="loader">Refreshing…</div>';
  try {
    const res = await fetch('api?list' + (reload ? '&t=' + Date.now() : ''));
    const data = await res.json();
    STATE.toc = data.folders;
    if (reload) STATE.feeds = {};
    const first = data.folders[0] && data.folders[0].feeds[0];
    if (reload || !STATE.current) STATE.current = first ? first.file : null;
    renderTree();
    if (STATE.current) await selectFeed(STATE.current);
    else news.innerHTML = '<div class="empty">No feeds found in the feeds/ directory.<br>Run: PYTHONPATH=. python3 scripts/generate_feeds.py</div>';
  } catch (e) {
    news.innerHTML = '<div class="error">' + h(e.message) + '</div>';
  }
}
boot(false);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/reader"):
            self._send(200, "text/html; charset=utf-8", HTML_PAGE)
        elif parsed.path == "/api":
            self._handle_api()
        else:
            self._send(404, "text/plain", "not found")

    def _handle_api(self) -> None:
        from urllib.parse import parse_qs

        qs = parse_qs(urlparse(self.path).query)
        if "list" in urlparse(self.path).query.split("&"):
            self._send(200, "application/json", json.dumps({"folders": build_toc()}))
            return
        feed_file = qs.get("feed", [None])[0]
        if not feed_file:
            self._send(400, "application/json", json.dumps({"error": "missing feed"}))
            return
        path = (FEEDS_DIR / feed_file).resolve()
        if not str(path).startswith(str(FEEDS_DIR.resolve())) or not path.is_file():
            self._send(404, "application/json", json.dumps({"error": "feed not found"}))
            return
        self._send(200, "application/json", json.dumps(parse_feed(path)))

    def _send(self, code: int, ctype: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print(f"[reader] {time.strftime('%H:%M:%S')} {self.address_string()} - {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Inoreader-style feed reader")
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()

    if not (FEEDS_DIR / "uindex-movies.xml").exists():
        print("No feeds found yet. Generate them first:")
        print(f"    PYTHONPATH=. python3 {REPO_ROOT / 'scripts' / 'generate_feeds.py'}")
        return 1

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://localhost:{args.port}/reader"
    print(f"\nServing feed reader at {url}")
    print("Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())