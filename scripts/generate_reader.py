#!/usr/bin/env python3
"""Build the static reader page deployed to GitHub Pages.

The local reader (``scripts/local_reader.py``) serves the same page from a
Python HTTP server with a JSON API behind it. GitHub Pages serves static files
only, so this script renders the *same* page with its data layer replaced:

- ``reader.html`` at the repo root -- the page, byte-identical to the local one
  except for the block between the ``ADAPTER:BEGIN``/``ADAPTER:END`` sentinels,
  which is swapped for a browser-side implementation.
- ``feeds/manifest.json`` -- the sidebar table of contents: folders, feed names
  and item counts, so the tree renders from one small request instead of
  parsing 17 MB of XML up front.

The published feeds are already next to the page, so the static adapter fetches
``feeds/<file>.xml`` on demand and parses it with the browser's own DOMParser.
Feed XML is served from the same origin as the page, so no CORS handling is
needed.

What deliberately differs from the local reader: the published reader carries no
``token`` per feed. The local token is ``mtime_ns-size``, which moves whenever a
feed is rewritten -- on Pages that is every hour, for every feed, so read state
would be wiped continuously. Instead the manifest carries each feed's item
``guids``, which lets :func:`syncReadState` in the shared page prune keys for
items that aged out while keeping the marks for everything still present.

Run:
    PYTHONPATH=. python scripts/generate_reader.py
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_reader import (
    ADAPTER_BEGIN,
    ADAPTER_END,
    HTML_PAGE,
    iter_feed_files,
    parse_feed,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"
OUTPUT_FILE = REPO_ROOT / "reader.html"
MANIFEST_FILE = FEEDS_DIR / "manifest.json"

# Mirrors core/feed.py's content namespace, used for the article body.
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

STATIC_ADAPTER = """const ADAPTER = {
  emptyHint: 'feeds/manifest.json lists no feeds \\u2014 run generate_feeds.py, then generate_reader.py. A missing manifest raises instead of landing here.',
  async toc() {
    const r = await fetch('feeds/manifest.json', { cache: 'no-store' });
    if (!r.ok) throw new Error('manifest.json failed (HTTP ' + r.status + ')');
    return (await r.json()).folders;
  },
  /* Parse the published feed in the browser. Only ever called for a feed the
     reader actually opens -- the full set is ~17 MB. */
  async feed(file) {
    const r = await fetch('feeds/' + encodeURIComponent(file), { cache: 'no-store' });
    if (!r.ok) throw new Error('feed ' + file + ' failed (HTTP ' + r.status + ')');
    const doc = new DOMParser().parseFromString(await r.text(), 'application/xml');
    if (doc.querySelector('parsererror')) throw new Error('feed ' + file + ' is not valid XML');
    let channel = doc.documentElement;
    if (channel.localName !== 'channel') {
      channel = doc.getElementsByTagName('channel')[0] || channel;
    }
    const text = (el, tag) => {
      const n = el.getElementsByTagName(tag)[0];
      return n ? (n.textContent || '').trim() : '';
    };
    const feed = {
      file: file,
      name: '', folder: '', title: text(channel, 'title'),
      link: text(channel, 'link'), description: text(channel, 'description'),
      items: [],
    };
    for (const item of channel.getElementsByTagName('item')) {
      const encoded = item.getElementsByTagNameNS(
        'http://purl.org/rss/1.0/modules/content/', 'encoded')[0];
      const desc = (encoded && encoded.textContent ? encoded.textContent
                    : text(item, 'description')) || '';
      const raw = text(item, 'pubDate');
      const guid = text(item, 'guid') || text(item, 'link');
      feed.items.push({
        title: text(item, 'title'),
        link: text(item, 'link'),
        date: raw ? stamp(raw) : '',
        guid: guid,
        desc_html: desc.trim(),
        snippet: plainText(desc),
        tags: boldLabels(desc),
      });
    }
    // Sort items by date, newest first
    feed.items.sort((a, b) => {
      if (!a.date && !b.date) return 0;
      if (!a.date) return 1;
      if (!b.date) return -1;
      return new Date(b.date) - new Date(a.date);
    });
    feed.item_count = feed.items.length;
    return feed;
  },
};

/* RFC 822 -> 'YYYY-MM-DD HH:MM', rendered in the offset the feed itself gave.

   Not the browser's timezone. Python's _feed_date does the same, and the two
   readers are meant to agree: using local time here would show 09:30 for an
   item stamped 07:30 +0000 to anyone east of Greenwich, and shift every date
   again for anyone west. Only numeric offsets (and Z/UT/GMT) are handled,
   which is all the feeds use; a named zone falls back to offset 0 rather than
   to a guess. */
function stamp(raw) {
  const m = /(?:([+-])(\\d{2})(\\d{2})|(?:Z|UT|GMT))\\s*$/.exec(raw.trim());
  let offsetMin = 0;
  if (m && m[1]) offsetMin = (Number(m[2]) * 60 + Number(m[3])) * (m[1] === '-' ? -1 : 1);
  const t = Date.parse(raw);
  if (Number.isNaN(t)) return '';
  const d = new Date(t + offsetMin * 60000);
  const p = (n) => String(n).padStart(2, '0');
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) +
         ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
}

/* Same two helpers as the Python side (plain_text / extract_tags), so a
   search hit matches what the local reader would have found. */
function plainText(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return (d.textContent || '').replace(/\\s+/g, ' ').trim();
}
function boldLabels(html) {
  const out = [];
  const re = /<b[^>]*>([\\s\\S]*?)<\\/b>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const label = plainText(m[1]);
    if (label && out.indexOf(label) < 0) out.push(label);
  }
  return out;
}"""


def swap_adapter(page: str, adapter: str) -> str:
    """Replace the block between the two adapter sentinels.

    Everything outside the sentinels is the shared UI and must survive
    untouched, otherwise the published reader can drift from the local one.
    """
    start = page.index(ADAPTER_BEGIN)
    end = page.index(ADAPTER_END)
    if start > end:
        raise ValueError("adapter sentinels are out of order")
    # tail starts *after* the end sentinel -- it is re-emitted below, so slicing
    # from `end` would duplicate it.
    head = page[:start]
    tail = page[end + len(ADAPTER_END) :]
    return f"{head}{ADAPTER_BEGIN}\n{adapter}\n{ADAPTER_END}{tail}"


def _item_guids(path: Path) -> list[str]:
    """Item guids of one feed, in feed order.

    Only the ids are needed -- the reader's stored read state is keyed on them
    -- so this reads the guid element and stops there rather than building the
    description, snippet and tag list for every item.
    """
    guids: list[str] = []
    try:
        channel = ET.parse(path).getroot().find("channel")
        if channel is None:
            return guids
        for item in channel.findall("item"):

            def _txt(name: str, _item=item) -> str:
                el = _item.find(name)
                return el.text.strip() if el is not None and el.text else ""

            guids.append(_txt("guid") or _txt("link") or _txt("title"))
    except ET.ParseError:
        # A feed that will not parse still belongs in the tree, so the sidebar
        # can show it and the reader can report the error when it is opened.
        return guids
    return guids


def build_manifest(feeds_dir: Path = FEEDS_DIR) -> dict:
    """The sidebar TOC: one small request instead of parsing every feed."""
    folders: dict[str, list[dict]] = {}
    for path in sorted(feeds_dir.glob("*.xml")):
        feed = parse_feed(path, with_items=False)
        folders.setdefault(feed["folder"], []).append(
            {
                "file": feed["file"],
                "name": feed["name"],
                "title": feed["title"],
                "unread_count": feed["item_count"],
                "item_count": feed["item_count"],
                # No `token` on purpose -- see the module docstring. `guids` is
                # what lets the page prune read state without wiping it.
                "guids": _item_guids(path),
            }
        )
    return {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "folders": [
            {"folder": folder, "feeds": feeds}
            for folder, feeds in sorted(folders.items(), key=lambda kv: kv[0])
        ],
    }


def generate_reader(
    feeds_dir: Path = FEEDS_DIR,
    output_file: Path = OUTPUT_FILE,
    manifest_file: Path = MANIFEST_FILE,
) -> tuple[int, int]:
    """Write reader.html and feeds/manifest.json. Returns (feeds, items)."""
    if not any(feeds_dir.glob("*.xml")):
        raise SystemExit(
            f"No feeds in {feeds_dir} (generate them first: "
            f"PYTHONPATH=. python3 scripts/generate_feeds.py)"
        )

    manifest = build_manifest(feeds_dir)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    output_file.write_text(swap_adapter(HTML_PAGE, STATIC_ADAPTER), encoding="utf-8")

    n_feeds = sum(len(f["feeds"]) for f in manifest["folders"])
    n_items = sum(f["item_count"] for f in manifest["folders"] for f in f["feeds"])
    return n_feeds, n_items


def main() -> int:
    feeds, items = generate_reader()
    print(
        f"Generated {OUTPUT_FILE} and {MANIFEST_FILE} "
        f"({feeds} feeds, {items} items, {len(list(iter_feed_files()))} feed files)."
    )
    print(
        "The page reads feeds/manifest.json and feeds/<file>.xml relative to its "
        "own URL, so keep reader.html and feeds/ side by side when deploying."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
