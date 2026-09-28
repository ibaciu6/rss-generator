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
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"
CONFIG_FILE = REPO_ROOT / "config" / "sites.yaml"

# Sentinels delimiting the data layer inside HTML_PAGE. scripts/generate_reader.py
# replaces the text between them to build the static GitHub Pages reader; both
# ends must be exact substrings of the page for that swap to be possible, and
# neither may appear anywhere else.
# Both sentinels are *closed* comments on purpose. An unterminated "/* BEGIN"
# would be closed by the first "*/" inside the adapter it marks, commenting out
# the whole data layer -- which parses as valid JS and fails only at runtime.
ADAPTER_BEGIN = "/* ADAPTER:BEGIN */"
ADAPTER_END = "/* ADAPTER:END */"

FOLDER_BY_CAT_LANG = {
    ("movies", "ro"): "Online-Movies",
    ("movies", "en"): "Online-Movies",
    ("episodes", "ro"): "Online-Episodes",
    ("updates", "ro"): "Online-Episodes",
    ("cinema", "en"): "Online-Cinema",
    ("cinema", "ro"): "Online-Cinema",
    ("releases", "en"): "Online-Torrents",
    ("releases", "ro"): "Online-Torrents",
    ("torrents", "en"): "Online-Torrents",
    ("torrents", "ro"): "Online-Torrents",
    ("cyber", "en"): "Online-Cyber",
    ("cyber", "ro"): "Online-Cyber",
    ("tech", "en"): "Online-Tech",
    ("tech", "ro"): "Online-Tech",
    ("news", "en"): "Online-News",
    ("news", "ro"): "Online-News",
    ("economy", "en"): "Online-Economy",
    ("economy", "ro"): "Online-Economy",
    ("blogs", "en"): "Online-Blogs",
    ("blogs", "ro"): "Online-Blogs",
    ("local", "en"): "Online-Local",
    ("local", "ro"): "Online-Local",
    ("education", "en"): "Online-Education",
    ("education", "ro"): "Online-Education",
    ("other", "en"): "Online-Other",
    ("other", "ro"): "Online-Other",
}
FOLDER_FALLBACK = "Other"

def _folder_from_filename(file_name: str) -> str:
    """Infer a folder for orphan feeds (not in config) from their file name."""
    lower = file_name.lower()
    if "episod" in lower or "seriale" in lower or "tv" in file_name:
        return "Online-Episodes"
    if "movies" in lower or "filme" in lower or "film" in lower:
        return "Online-Movies"
    return FOLDER_FALLBACK


# The config is read once per feed, so it is cached and invalidated on the
# file's own mtime/size. Re-parsing the ~3k-line sites.yaml for all 73 feeds
# cost 4.4s on every page load - enough to make the reader feel frozen.
_SITE_NAMES_CACHE: tuple[int, int, dict[str, tuple[str, str]]] = (0, 0, {})


def _load_site_names() -> dict[str, tuple[str, str]]:
    """Map feed_file -> (display_name, folder_name)."""
    global _SITE_NAMES_CACHE
    st = CONFIG_FILE.stat()
    if _SITE_NAMES_CACHE[:2] == (st.st_mtime_ns, st.st_size):
        return _SITE_NAMES_CACHE[2]

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
    _SITE_NAMES_CACHE = (st.st_mtime_ns, st.st_size, out)
    return out


def _feed_date(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw.strip())
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def parse_feed(path: Path, *, with_items: bool = True) -> dict:
    site_names = _load_site_names()
    display_name, folder = site_names.get(
        path.name, (path.stem.replace("-", " ").title(), _folder_from_filename(path.name))
    )
    items: list[dict] = []
    feed_title, feed_link, feed_desc = display_name, "", ""
    # Bound before the try, not inside it: a feed that fails to parse falls
    # through to the return, and an unbound local there raises
    # UnboundLocalError instead of reporting the feed as empty.
    item_count = 0
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
            if not with_items:
                # The TOC only needs a count. Skipping the per-item work matters:
                # 1208 items across 73 feeds, one body 471KB, ~2s per page load.
                item_count += 1
                continue

            def _txt(name: str, _item=item) -> str:
                el = _item.find(name)
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
        "item_count": item_count or len(items),
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


def feed_token(path: Path) -> str:
    """Cheap change stamp for a feed file, used to invalidate read state.

    ``mtime_ns`` + ``size`` rather than a content hash: this runs on every
    ``api?list`` call for all ~70 feeds, and ``stat`` is O(1) where hashing a
    few MB of embedded article bodies is not. The only cost of the imprecision
    is that a rewrite producing byte-identical output still resets that feed's
    read state - which is the documented behaviour anyway.
    """
    try:
        st = path.stat()
    except OSError:
        return "missing"
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def build_toc() -> list[dict]:
    """Table of contents for the sidebar. Every field here is cheap on purpose:
    the TOC drives every page load and every refresh, and it used to parse the
    full body of all 1208 items (~6.5s) plus re-read sites.yaml per feed."""
    toc: dict[str, list[dict]] = {}
    for path in iter_feed_files():
        feed = parse_feed(path, with_items=False)
        toc.setdefault(feed["folder"], []).append(
            {
                "file": feed["file"],
                "name": feed["name"],
                "title": feed["title"],
                "unread_count": feed["item_count"],
                "item_count": feed["item_count"],
                "token": feed_token(path),
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
    --accent-soft: #f7d7c8; --active: #3584e4;
    --sidebar-w: 260px; --panel-w: 46%;
    --head-h: 48px;
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
    /* flex-basis (not width) is what JS drives, so the column really resizes.
       max-width is only a backstop; it must stay looser than the JS clamp or
       the two rules fight and the handle detaches from the pointer. */
    flex: 0 0 var(--sidebar-w); width: var(--sidebar-w);
    min-width: 180px; max-width: 90vw;
    background: var(--panel); border-right: 1px solid var(--line);
    display: flex; flex-direction: column; overflow: hidden;
  }
  /* Drag handle between the sidebar and the article pane. It is a real flex
     item (normal flow) so it always sits exactly on the column delimiter. */
  .gutter {
    flex: 0 0 7px; position: relative; cursor: col-resize;
    background: transparent; touch-action: none; z-index: 5;
  }
  .gutter::after {
    content: ''; position: absolute; inset: 0 2px;
    background: var(--line); opacity: 0.55;
    transition: opacity 0.12s, background 0.12s;
  }
  .gutter:hover::after, .gutter.active::after { opacity: 1; background: var(--accent); }
  .gutter:focus-visible { outline: 2px solid var(--accent); outline-offset: -1px; }
  body.resizing, body.resizing * { cursor: col-resize !important; user-select: none !important; }
  body.resizing iframe, body.resizing video { pointer-events: none; }
  /* The sidebar header and the toolbar are one visual band: identical height,
     identical padding rhythm, identical bottom rule, so nothing drifts. */
  .brand {
    height: var(--head-h); flex: 0 0 var(--head-h); padding: 0 16px;
    border-bottom: 1px solid var(--line);
    display: flex; flex-direction: column; justify-content: center; overflow: hidden;
  }
  .brand h1 { margin: 0; font-size: 1.02rem; line-height: 1.25; font-family: Georgia, serif; }
  .brand .sub { font-size: 0.72rem; line-height: 1.25; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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

  main { flex: 1 1 0; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  .toolbar {
    display: flex; align-items: center; gap: 12px; flex-wrap: nowrap; overflow: hidden;
    height: var(--head-h); flex: 0 0 var(--head-h);
    padding: 0 16px; border-bottom: 1px solid var(--line); background: var(--panel);
  }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  .seg button {
    border: none; background: transparent; font-size: 0.78rem; padding: 5px 12px;
    cursor: pointer; font-family: inherit; color: var(--text);
  }
  .seg button.on { background: var(--accent); color: #fff; }
  #search {
    flex: 1 1 auto; min-width: 0; max-width: 320px; font-size: 0.8rem;
    padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px;
    background: var(--bg); font-family: inherit;
  }
  .toolbar .spacer { flex: 1 1 0; min-width: 0; }
  .btn {
    font-size: 0.78rem; padding: 5px 10px; border: 1px solid var(--line);
    border-radius: 6px; background: var(--bg); cursor: pointer; font-family: inherit; color: var(--text);
    white-space: nowrap; flex-shrink: 0;
  }
  .btn:hover { border-color: var(--accent); }

  #art-pane { flex: 1 1 auto; display: flex; overflow: hidden; min-height: 0; }
  #news { flex: 1 1 0; min-width: 200px; overflow-y: auto; padding: 10px 12px 20px; }
  #panel {
    /* flex: 0 0 var(--panel-w) — JS overrides --panel-w, never style.width.
       Backstop is looser than the JS reserve so JS is always the binding limit. */
    flex: 0 0 var(--panel-w); width: var(--panel-w);
    min-width: 300px; max-width: calc(100% - 210px);
    background: var(--panel); border-left: 1px solid var(--line);
    overflow-y: auto; padding: 16px;
  }
  @media (max-width: 860px) { #panel, #panel-gutter { display: none; } }

  .art {
    display: flex; gap: 10px; padding: 8px 10px; border-radius: 8px; cursor: pointer;
    border-bottom: 1px solid var(--line);
  }
  .art:hover { background: var(--accent-soft); }
  .art.dot { border-left: 3px solid var(--unread); }
  .art .bd { flex: 1; min-width: 0; }
  .art .title { font-size: 0.9rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .art.visited .title { color: var(--muted); }
  .empty, .loader { text-align: center; padding: 60px 20px; color: var(--muted); font-size: 0.9rem; }
  .error { padding: 30px; color: var(--unread); text-align: center; }

  .panel-title { font-size: 1.05rem; font-weight: 700; margin: 0 0 4px; font-family: Georgia, serif; }
  .panel-title a { color: var(--text); text-decoration: none; }
  .panel-close { float: right; border: 1px solid var(--line); background: var(--bg); border-radius: 6px; cursor: pointer; font-size: 0.75rem; padding: 3px 8px; }
  .panel-meta { font-size: 0.75rem; color: var(--muted); margin-bottom: 10px; }
  .panel-desc { font-size: 0.9rem; line-height: 1.55; overflow-wrap: anywhere; }
  .panel-desc img { max-width: 100%; max-height: 380px; width: auto; height: auto; object-fit: contain; display: block; border-radius: 6px; margin: 8px 0; }
  .panel-desc a { color: var(--accent); }
  #gen-note { font-size: 0.75rem; color: var(--muted); opacity: 0; transition: opacity .25s; white-space: nowrap; }
  #gen-note.show { opacity: 1; }
</style>
</head>
<body>
<aside id="sidebar">
  <div class="brand">
    <h1>Feed Reader</h1>
    <div class="sub" id="aside-sub">loading…</div>
  </div>
  <div id="feed-tree"></div>
</aside>
<div class="gutter" id="sidebar-gutter" role="separator" aria-orientation="vertical"
     tabindex="0" title="Drag to resize · double-click to reset"></div>
<main>
  <div class="toolbar">
    <div class="seg" id="mode-seg">
      <button data-mode="unread" class="on" id="mode-unread">Unread</button>
      <button data-mode="all" id="mode-all">All</button>
    </div>
    <input id="search" type="search" placeholder="Search articles…">
    <span id="gen-note" role="status" aria-live="polite"></span>
    <div class="spacer"></div>
    <button class="btn" id="clear-read" title="Mark every article unread">Reset read</button>
    <button class="btn" id="refresh">Refresh feeds</button>
  </div>
  <div id="art-pane">
    <div id="news"><div class="loader">Loading feeds…</div></div>
    <div class="gutter" id="panel-gutter" role="separator" aria-orientation="vertical"
         tabindex="0" title="Drag to resize · double-click to reset"></div>
    <div id="panel"></div>
  </div>
</main>
<script>
const STATE = { toc: null, mode: 'unread', query: '', current: null, feeds: {}, art: {} };
const KEY = 'localreader.read.';

/* ADAPTER:BEGIN */

/* Data layer, swapped by scripts/generate_reader.py for the static build.
 *
 * One page, two backends. Served locally, this file answers api?list and
 * api?feed=<file> and returns parsed JSON. On GitHub Pages there is no
 * server, so the static build replaces this object with one that reads
 * feeds/manifest.json and feeds/<file>.xml in the browser instead. Every
 * line outside the sentinels below -- markup, CSS, rendering, read state --
 * is shared, which is what keeps the two readers identical.
 *
 *   toc()          -> [{ folder, feeds: [...] }] for the sidebar
 *   feed(file)     -> the parsed feed object for one feed
 *   emptyHint      -> what to say when toc() comes back with no feeds
 *
 * A TOC entry's `token` is a build stamp used to decide whether read state
 * survived a regeneration; see syncReadState(). `guids` is the item id list,
 * used only to prune read state. A backend may omit either.
 */
const ADAPTER = {
  emptyHint: 'Run: PYTHONPATH=. python3 scripts/generate_feeds.py',
  async toc() {
    const r = await fetch('api?list');
    if (!r.ok) throw new Error('feed list failed');
    return (await r.json()).folders;
  },
  async feed(file) {
    const r = await fetch('api?feed=' + encodeURIComponent(file));
    if (!r.ok) throw new Error('feed ' + file + ' failed');
    return r.json();
  },
};
/* ADAPTER:END */

/* ---------- persistence ---------- */
function readSet() { try { return new Set(JSON.parse(localStorage.getItem(KEY + 'set') || '[]')); } catch (e) { return new Set(); } }
function saveSet(s) { localStorage.setItem(KEY + 'set', JSON.stringify([...s])); }
let READ = readSet();

const SEEN_KEY = 'localreader.seen';
function seenMap() { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || 'null'); } catch (e) { return null; } }
function saveSeen(m) { try { localStorage.setItem(SEEN_KEY, JSON.stringify(m)); } catch (e) {} }

function guidKey(feedFile, guid) { return feedFile + '::' + guid; }
function feedOfKey(k) { const i = k.indexOf('::'); return i < 0 ? '' : k.slice(0, i); }

/* Reconcile stored read state with the feed list the backend just returned.
   Returns the number of feeds whose read state was reset, so the caller can say so.

   Two independent jobs, and the order matters:

   1. Prune. Keys for feeds that no longer exist, and -- when the backend
      supplies `guids` -- keys for items that have aged out of a feed that does,
      are dropped. Without this localStorage grows without bound as feeds are
      renamed and old items fall off the end of a feed.

   2. Reset, only for stamped feeds. A `token` is a build stamp: when it moves,
      the feed was regenerated and its marks no longer refer to anything, so
      they are cleared.

   A backend that cannot stamp a build omits `token` -- the static reader does,
      because every deploy rewrites every file and all 60 tokens would move
      hourly, wiping every read mark the reader had. Such a backend keeps its
      read state and relies on pruning alone, which is both correct and what
      makes a published reader usable at all. */
function syncReadState(feeds) {
  // An empty list is "the backend has nothing to tell us", not "every feed is
  // gone". Pruning on it would wipe the whole library -- irreversibly, since
  // read marks are only re-learned by reading again. This happens for real
  // when a deploy produces a manifest with zero folders.
  if (!feeds.length) return 0;
  const live = new Set(feeds.map(f => f.file));
  // Only a feed that actually supplied `guids` can have its keys judged
  // against them. A feed without them keeps everything it has: treating "no
  // list" as "no items" would silently wipe the read marks of any feed the
  // backend could not enumerate.
  const known = new Map();
  feeds.forEach(f => { if (f.guids) known.set(f.file, new Set(f.guids)); });
  const pruned = [...READ].filter(k => {
    const file = feedOfKey(k);
    if (!live.has(file)) return true;                    // the feed is gone
    const guids = known.get(file);
    if (!guids) return false;                            // nothing to judge by
    return !guids.has(k.slice(file.length + 2));          // the item aged out
  });
  if (pruned.length) {
    const next = new Set(READ);
    pruned.forEach(k => next.delete(k));
    READ = next;
    saveSet(READ);
  }
  // A missing or empty token means "no usable build stamp" -- keep the marks.
  // Falsiness, not `== null`: a backend that emits "" is saying it cannot
  // stamp, and reading that as a changed build would wipe read state.
  if (feeds.some(f => !f.token)) return 0;

  const seen = seenMap();
  const now = {};
  feeds.forEach(f => { now[f.file] = f.token; });
  if (!seen) {
    // First run of this version: record the tokens but keep the existing marks,
    // so upgrading does not silently wipe everyone's read state.
    saveSeen(now);
    return 0;
  }
  const changed = feeds.filter(f => seen[f.file] !== f.token);
  saveSeen(now);
  if (!changed.length) return 0;
  const stale = new Set(changed.map(f => f.file));
  const keep = new Set([...READ].filter(k => !stale.has(feedOfKey(k))));
  READ = keep;
  saveSet(READ);
  return changed.length;
}

/* ---------- helpers ---------- */
function h(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
function countUnread(feed) {
  return feed.items.filter(i => !READ.has(guidKey(feed.file, i.guid || i.link || i.title))).length;
}
function feedItems(feed) {
  return feed.items.map(i => ({ ...i, read: READ.has(guidKey(feed.file, i.guid || i.link || i.title)) }));
}

/* ---------- sidebar ---------- */
/* Unread count for a feed, whether or not it has been opened.

   A loaded feed is counted from its items. An unloaded one has only what the
   backend put in the TOC, and that `unread_count` is the feed's item count --
   it cannot know what *this* reader has read. The published reader is the case
   that bites: its manifest is built at deploy time by a build server with no
   read state at all, so a sidebar summing unread_count would claim every
   article is unread forever, and never change. The guid list the manifest also
   carries is enough to do better: a guid that is not in READ is unread. */
function unreadFor(fmeta) {
  const loaded = STATE.feeds[fmeta.file];
  if (loaded) return countUnread(loaded);
  if (fmeta.guids) {
    let n = 0;
    for (const g of fmeta.guids) if (!READ.has(guidKey(fmeta.file, g))) n++;
    return n;
  }
  return fmeta.unread_count;
}

function renderTree() {
  const tree = document.getElementById('feed-tree');
  tree.innerHTML = '';
  let totalUnread = 0;
  for (const group of STATE.toc) {
    const folder = document.createElement('div');
    folder.className = 'folder';
    const fU = group.feeds.reduce((a, f) => a + unreadFor(f), 0);
    totalUnread += fU;
    const toggle = document.createElement('button');
    toggle.className = 'folder-toggle';
    toggle.innerHTML = '<span class="caret">▼</span>' + h(group.folder) +
      '<span class="folder-count">' + (fU || '') + '</span>';
    toggle.onclick = () => folder.classList.toggle('collapsed');
    folder.appendChild(toggle);
    for (const fmeta of group.feeds) {
      const un = unreadFor(fmeta);
      const btn = document.createElement('button');
      btn.className = 'feed-item' + (STATE.current === fmeta.file ? ' active' : '');
      btn.dataset.file = fmeta.file;
      btn.title = fmeta.name;
      btn.innerHTML =
        (un ? '<span class="unread">' + un + '</span>' : '') +
        '<span class="fname">' + h(fmeta.file) + '</span>';
      btn.onclick = () => { selectFeed(fmeta.file); };
      folder.appendChild(btn);
    }
    tree.appendChild(folder);
  }
  const totalFeeds = STATE.toc.reduce((n, g) => n + g.feeds.length, 0);
  document.getElementById('aside-sub').textContent = totalUnread + ' unread / ' +
    totalFeeds + ' feeds';
}

async function loadFeedAny(file) {
  if (STATE.feeds[file]) return STATE.feeds[file];
  const feed = await ADAPTER.feed(file);
  STATE.feeds[file] = feed;
  return feed;
}

async function selectFeed(file) {
  STATE.current = file;
  try { await loadFeedAny(file); } catch (e) { return; }
  // Rebuild the tree rather than toggling .active by hand: only now is the real
  // unread count of this feed known, and the tree draws the current feed as
  // active from STATE.current itself. Without this the sidebar keeps the count
  // the backend reported before the feed was parsed, so a feed you have read
  // articles in still shows itself fully unread after a reload.
  renderTree();
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
  // renderTree rebuilds the whole sidebar from unreadFor(), so the feed's badge
  // and the folder and grand totals all move here. Nothing to patch by hand.
  renderTree();
}

function openPanel(feed, it) {
  const panel = document.getElementById('panel');
  panel.innerHTML =
    '<button class="panel-close" onclick="document.getElementById(\\\'panel\\\').innerHTML=\\\'\\\'">✕</button>' +
    '<h2 class="panel-title">' + h(it.title) + '</h2>' +
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

let noteTimer = null;
function flashNote(msg) {
  const el = document.getElementById('gen-note');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => el.classList.remove('show'), 4000);
}
document.getElementById('clear-read').addEventListener('click', () => {
  READ = new Set();
  saveSet(READ);
  renderTree();
  if (STATE.current) renderNews();
  flashNote('All articles marked unread');
});

/* ---------- boot ---------- */

/* ---------- column resizing ---------- */
const WIDTH_KEY = 'localreader.widths';
const GUTTERS = [
  // `dir` is how pointer movement maps to width, and it is NOT the same for
  // both gutters -- the two columns are anchored on opposite edges:
  //
  //   sidebar: first flex item, left edge pinned. The handle sits on its
  //            RIGHT edge, so dragging right widens it  -> dir = +1
  //   panel:   last flex item but `flex: 0 0`, so its RIGHT edge is pinned.
  //            The handle sits on its LEFT edge, so dragging right NARROWS
  //            it -> dir = -1
  //
  // Getting this backwards makes the handle slide away from the cursor
  // instead of tracking it. `dir` also makes the keyboard arrows follow the
  // handle rather than the raw arrow direction.
  //
  // `min` = this column's smallest size, `reserve` = space that must remain
  // for the other column(s) so they never collapse.
  { id: 'sidebar-gutter', cssVar: '--sidebar-w', min: 180, reserve: 420, reset: 260, dir: 1 },
  { id: 'panel-gutter',   cssVar: '--panel-w',   min: 300, reserve: 220, reset: '46%', dir: -1 },
];

function readWidths() {
  try { return JSON.parse(localStorage.getItem(WIDTH_KEY) || '{}') || {}; }
  catch (e) { return {}; }
}
function writeWidths(v) {
  try { localStorage.setItem(WIDTH_KEY, JSON.stringify(v)); } catch (e) {}
}

/* Width JS should start a drag from: the *rendered* width when the column is on
   screen, so a var that ever drifted out of sync self-corrects on next drag.
   `cfg.reset` may be a %, so resolve it against the pane. */
function currentWidthPx(el, cfg) {
  const rect = el.getBoundingClientRect();
  if (rect.width > 0) return rect.width;
  const v = String(cfg.reset).trim();
  if (v.endsWith('%')) return (el.parentElement.clientWidth * parseFloat(v)) / 100;
  return parseFloat(v) || cfg.min;
}

function initGutter(cfg) {
  const gutter = document.getElementById(cfg.id);
  if (!gutter) return;
  const target = cfg.cssVar === '--sidebar-w'
    ? document.getElementById('sidebar')
    : document.getElementById('panel');
  if (!target) return;

  const root = document.documentElement;
  const maxPx = () => Math.max(cfg.min + 40, (target.parentElement.clientWidth || window.innerWidth) - cfg.reserve);
  const clamp = (px) => Math.min(Math.max(px, cfg.min), maxPx());
  const apply = (px) => root.style.setProperty(cfg.cssVar, Math.round(px) + 'px');
  const store = () => writeWidths({ ...readWidths(), [cfg.cssVar]: root.style.getPropertyValue(cfg.cssVar) });

  // Restore a saved width, re-clamped so an old/out-of-range value can't stick.
  const saved = readWidths()[cfg.cssVar];
  if (saved) {
    const n = parseFloat(saved);
    if (!isNaN(n)) apply(clamp(n));
  }

  gutter.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    const startX = e.clientX;
    // Measure the real column, not the CSS var: keeps the handle glued to the
    // pointer even if a stale stored value is wider than the pane allows.
    const startPx = currentWidthPx(target, cfg);
    gutter.classList.add('active');
    document.body.classList.add('resizing');
    try { gutter.setPointerCapture(e.pointerId); } catch (err) {}
    // cfg.dir keeps the handle glued to the pointer for both anchorings.
    const move = function (ev) { apply(clamp(startPx + cfg.dir * (ev.clientX - startX))); };
    const up = function () {
      gutter.classList.remove('active');
      document.body.classList.remove('resizing');
      gutter.removeEventListener('pointermove', move);
      gutter.removeEventListener('pointerup', up);
      gutter.removeEventListener('pointercancel', up);
      store();
    };
    gutter.addEventListener('pointermove', move);
    gutter.addEventListener('pointerup', up);
    gutter.addEventListener('pointercancel', up);
  });

  gutter.addEventListener('dblclick', function () {
    root.style.removeProperty(cfg.cssVar);
    store();
  });

  gutter.addEventListener('keydown', function (e) {
    const step = e.shiftKey ? 48 : 16;
    let px = currentWidthPx(target, cfg);
    // Arrows move the HANDLE, so they are scaled by cfg.dir too: on the panel
    // (dir -1) ArrowRight narrows, because that pushes the handle rightwards.
    if (e.key === 'ArrowLeft') px -= cfg.dir * step;
    else if (e.key === 'ArrowRight') px += cfg.dir * step;
    else if (e.key === 'Home') px = cfg.min;
    else return;
    e.preventDefault();
    apply(clamp(px));
    store();
  });
}

GUTTERS.forEach(initGutter);

async function boot(reload) {
  const news = document.getElementById('news');
  if (reload) news.innerHTML = '<div class="loader">Refreshing…</div>';
  try {
    STATE.toc = await ADAPTER.toc();
    // Before rendering: a regenerated feed's items are all unread again.
    const reset = syncReadState(STATE.toc.flatMap(g => g.feeds));
    if (reload) STATE.feeds = {};
    const first = STATE.toc[0] && STATE.toc[0].feeds[0];
    if (reload || !STATE.current) STATE.current = first ? first.file : null;
    renderTree();
    if (STATE.current) await selectFeed(STATE.current);
    else news.innerHTML = '<div class="empty">No feeds found.' +
      (ADAPTER.emptyHint ? '<br>' + h(ADAPTER.emptyHint) : '') + '</div>';
    if (reset) flashNote(reset + ' feed' + (reset === 1 ? '' : 's') + ' regenerated — read state reset');
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
    def do_GET(self) -> None:
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

    def log_message(self, fmt: str, *args) -> None:
        print(f"[reader] {time.strftime('%H:%M:%S')} {self.address_string()} - {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Inoreader-style feed reader")
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()

    if not any(FEEDS_DIR.glob("*.xml")):
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