"""Tests for the static reader that GitHub Pages serves (scripts/generate_reader.py).

The published reader is the *same page* the local reader serves, with one block
swapped: the data layer. So most of what matters here is that the swap is
surgical -- if anything outside the sentinels changed, the two readers would
drift and the deployed UI would quietly stop matching ``./scripts/start_reader.sh``.

The read-state tests drive the page's own JavaScript under node, because
``syncReadState`` is the function that decides whether a read mark survives. It
is the one piece of the shared page that behaves *differently* between the two
backends, and it is the piece with no Python equivalent to lean on.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts import local_reader as lr
from scripts.generate_reader import (
    ADAPTER_BEGIN,
    ADAPTER_END,
    MANIFEST_FILE,
    OUTPUT_FILE,
    STATIC_ADAPTER,
    _item_guids,
    build_manifest,
    generate_reader,
    swap_adapter,
)

FEED_A = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
    "<channel><title>Alpha</title><link>https://a.example/</link>"
    "<item><title>One</title><link>https://a.example/1</link>"
    "<guid>g1</guid><pubDate>Wed, 03 Dec 2025 07:30:24 +0000</pubDate>"
    "<description>&lt;p&gt;body one&lt;/p&gt;</description></item>"
    "<item><title>Two</title><link>https://a.example/2</link>"
    "<guid>g2</guid>"
    "<description>&lt;p&gt;body two&lt;/p&gt;</description></item>"
    "</channel></rss>"
)

FEED_B = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Beta</title><link>https://b.example/</link>'
    "<item><title>Only</title><link>https://b.example/1</link></item>"
    "</channel></rss>"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


@pytest.fixture
def feeds(tmp_path: Path) -> Path:
    d = tmp_path / "feeds"
    d.mkdir()
    (d / "alpha.xml").write_text(FEED_A, encoding="utf-8")
    (d / "beta.xml").write_text(FEED_B, encoding="utf-8")
    return d


def _page_from(html: str) -> str:
    # IGNORECASE because tag names are: HTML is case-insensitive, so a future
    # <SCRIPT> must not turn the match silently empty.
    match = re.search(r"<script>(.*?)</script>", html, re.S | re.I)
    assert match, "the reader page must carry exactly one inline script"
    return match.group(1)


def _sides(page: str) -> tuple[str, str]:
    """The page split around the adapter sentinels: shared UI, then adapter."""
    return page[: page.index(ADAPTER_BEGIN)], page[page.index(ADAPTER_END) + len(ADAPTER_END) :]


# --------------------------------------------------------------------------
# The swap
# --------------------------------------------------------------------------


class TestAdapterSwap:
    def test_sentinels_exist_exactly_once_in_the_shared_page(self):
        assert lr.HTML_PAGE.count(ADAPTER_BEGIN) == 1
        assert lr.HTML_PAGE.count(ADAPTER_END) == 1

    def test_sentinels_are_closed_comments(self):
        """An unterminated ``/* ADAPTER:BEGIN`` is closed by the first ``*/``
        *inside* the adapter, which comments out the whole data layer. That is
        valid JavaScript and only fails at runtime, with a confusing
        "ADAPTER is not defined" in the browser. Both ends must be closed."""
        for sentinel in (ADAPTER_BEGIN, ADAPTER_END):
            assert sentinel.startswith("/*") and sentinel.endswith("*/"), sentinel
        # Nothing may follow a sentinel on its own line: that is what turns it
        # back into an unterminated comment.
        for sentinel in (ADAPTER_BEGIN, ADAPTER_END):
            assert f"{sentinel} -" not in lr.HTML_PAGE
            assert f"{sentinel} *" not in lr.HTML_PAGE

    def test_sentinels_appear_nowhere_else_in_the_page(self):
        """A stray mention in a comment would give swap_adapter a second match
        and silently truncate the page."""
        for sentinel in (ADAPTER_BEGIN, ADAPTER_END):
            assert lr.HTML_PAGE.count(sentinel) == 1, sentinel

    def test_everything_outside_the_sentinels_is_untouched(self):
        """The whole point: the published reader is the local reader."""
        before_head, before_tail = _sides(lr.HTML_PAGE)
        after_head, after_tail = _sides(swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER))
        assert before_head == after_head
        assert before_tail == after_tail

    def test_the_static_adapter_actually_replaces_the_local_one(self):
        swapped = swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER)
        assert "api?list" not in swapped
        assert "api?feed" not in swapped
        assert "feeds/manifest.json" in swapped
        assert "RAW_FEED_BASE + encodeURIComponent(file)" in swapped

    def test_the_local_page_still_uses_its_api(self):
        assert "api?list" in lr.HTML_PAGE
        assert "api?feed" in lr.HTML_PAGE

    def test_sentinels_are_preserved_exactly_once_after_the_swap(self):
        swapped = swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER)
        assert swapped.count(ADAPTER_BEGIN) == 1
        assert swapped.count(ADAPTER_END) == 1

    def test_the_swap_is_idempotent(self):
        """Re-running on an already-swapped page must be a no-op, so the
        generator can be run twice without compounding the change."""
        once = swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER)
        assert swap_adapter(once, STATIC_ADAPTER) == once


# --------------------------------------------------------------------------
# The generated files
# --------------------------------------------------------------------------


class TestGenerateReader:
    def test_writes_both_files(self, feeds: Path, tmp_path: Path):
        out = tmp_path / "reader.html"
        manifest = feeds / "manifest.json"
        n_feeds, n_items = generate_reader(feeds_dir=feeds, output_file=out, manifest_file=manifest)
        assert out.is_file()
        assert manifest.is_file()
        assert (n_feeds, n_items) == (2, 3)

    def test_refuses_to_build_without_feeds(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SystemExit, match="No feeds"):
            generate_reader(
                feeds_dir=empty,
                output_file=tmp_path / "reader.html",
                manifest_file=tmp_path / "manifest.json",
            )

    def test_output_is_a_complete_html_document(self, feeds: Path, tmp_path: Path):
        out = tmp_path / "reader.html"
        generate_reader(feeds_dir=feeds, output_file=out, manifest_file=feeds / "manifest.json")
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        # The UI the local reader has, so the deployed page is not a stripped copy.
        for marker in ('id="sidebar"', 'id="feed-tree"', 'id="news"', 'id="panel"',
                       'id="mode-seg"', 'id="search"', 'id="clear-read"', 'id="refresh"'):
            assert marker in html, marker

    def test_both_manifest_and_page_land_where_the_adapter_looks(self, feeds: Path, tmp_path: Path):
        """The page fetches ``feeds/manifest.json`` relative to *its own* URL, so
        the manifest has to be a sibling of reader.html under a ``feeds/``
        directory -- which is exactly the Pages layout."""
        site = tmp_path / "site"
        (site / "feeds").mkdir(parents=True)
        (site / "feeds" / "alpha.xml").write_text(FEED_A, encoding="utf-8")
        generate_reader(
            feeds_dir=site / "feeds",
            output_file=site / "reader.html",
            manifest_file=site / "feeds" / "manifest.json",
        )
        assert (site / "reader.html").is_file()
        assert (site / "feeds" / "manifest.json").is_file()
        assert (site / "feeds" / "alpha.xml").is_file()


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


class TestManifest:
    def test_shape(self, feeds: Path):
        manifest = build_manifest(feeds)
        assert set(manifest) == {"generated", "folders"}
        assert manifest["folders"]
        for group in manifest["folders"]:
            assert set(group) == {"folder", "feeds"}
            for entry in group["feeds"]:
                assert set(entry) == {
                    "file",
                    "name",
                    "title",
                    "unread_count",
                    "item_count",
                    "guids",
                }

    def test_carries_no_token(self, feeds: Path):
        """The local token is mtime_ns-size. On Pages every feed is rewritten
        every hour, so all 60 tokens would move hourly and read state would be
        wiped continuously -- the published reader would be unusable. Omitting
        it makes syncReadState keep the marks and prune instead."""
        entries = [f for g in build_manifest(feeds)["folders"] for f in g["feeds"]]
        assert entries
        for entry in entries:
            assert "token" not in entry

    def test_guids_are_in_feed_order_and_match_the_items(self, feeds: Path):
        entries = [f for g in build_manifest(feeds)["folders"] for f in g["feeds"]]
        alpha = next(e for e in entries if e["file"] == "alpha.xml")
        assert alpha["guids"] == ["g1", "g2"]
        assert alpha["item_count"] == 2

    def test_guids_fall_back_to_link_then_title(self, tmp_path: Path):
        (tmp_path / "x.xml").write_text(
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><title>t1</title><link>https://x/1</link></item>"
            "<item><link>https://x/2</link></item>"
            "<item><title>t3</title></item>"
            "</channel></rss>",
            encoding="utf-8",
        )
        assert _item_guids(tmp_path / "x.xml") == ["https://x/1", "https://x/2", "t3"]

    def test_counts_come_from_the_feed_not_from_the_guid_list(self, tmp_path: Path):
        (tmp_path / "x.xml").write_text(
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><guid>a</guid></item><item><guid>b</guid></item>"
            "</channel></rss>",
            encoding="utf-8",
        )
        entry = build_manifest(tmp_path)["folders"][0]["feeds"][0]
        assert entry["item_count"] == 2
        assert entry["unread_count"] == 2
        assert len(entry["guids"]) == 2

    def test_unparseable_feed_still_gets_an_entry(self, tmp_path: Path):
        """A feed that will not parse still belongs in the tree, so the reader
        can show it and report the error when it is opened. Dropping it would
        make a broken feed look like a removed one."""
        (tmp_path / "broken.xml").write_text("<rss><channel><item>", encoding="utf-8")
        entries = [f for g in build_manifest(tmp_path)["folders"] for f in g["feeds"]]
        assert [e["file"] for e in entries] == ["broken.xml"]
        assert entries[0]["guids"] == []

    def test_is_json_serialisable(self, feeds: Path):
        json.dumps(build_manifest(feeds))

    def test_is_far_smaller_than_the_feeds_it_describes(self, feeds: Path):
        """Point of the manifest: one small request instead of parsing ~17 MB."""
        total = sum(p.stat().st_size for p in feeds.glob("*.xml"))
        size = len(json.dumps(build_manifest(feeds), separators=(",", ":")).encode())
        assert size < total


# --------------------------------------------------------------------------
# The page's own read-state logic, run under node
# --------------------------------------------------------------------------


def _run_node(source: str) -> str:
    result = subprocess.run(
        ["node", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _read_state_harness(body: str) -> str:
    """Wrap the page's persistence + syncReadState block in a node harness.

    That region is self-contained -- it only touches ``localStorage`` and
    ``KEY`` -- so it can be lifted out and driven directly, which is the only
    way to test the decision this page makes about a reader's read marks.
    """
    start = lr.HTML_PAGE.index("/* ---------- persistence ---------- */")
    end = lr.HTML_PAGE.index("/* ---------- helpers ---------- */")
    return textwrap.dedent(
        f"""
        const store = {{}};
        global.localStorage = {{
          getItem: (k) => (k in store ? store[k] : null),
          setItem: (k, v) => {{ store[k] = String(v); }},
        }};
        const KEY = 'localreader.read.';
        {lr.HTML_PAGE[start:end]}

        function feed(file, token, guids) {{
          const f = {{ file: file }};
          if (token !== undefined) f.token = token;
          if (guids !== undefined) f.guids = guids;
          return f;
        }}
        function marks() {{ return [...READ].sort(); }}

        {body}
        """
    )


@requires_node
class TestReadStateUnderNode:
    def test_a_local_feed_whose_token_moved_is_reset(self):
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                saveSeen({ 'a.xml': 'old' });
                const n = syncReadState([feed('a.xml', 'new')]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 1, "marks": []}

    def test_a_local_feed_whose_token_held_keeps_its_marks(self):
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                saveSeen({ 'a.xml': 'same' });
                const n = syncReadState([feed('a.xml', 'same')]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}

    def test_an_unstamped_feed_keeps_its_marks_across_a_sync(self):
        """The published reader's case. There is no token, so there is nothing
        to compare against and nothing to reset: a deploy must not cost the
        reader their read marks."""
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                saveSeen({ 'a.xml': 'whatever' });
                const n = syncReadState([feed('a.xml', undefined, ['g1'])]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}

    def test_stamped_and_unstamped_feeds_together_keep_everything(self):
        """Mixed lists are possible if a backend only stamps some feeds. The
        unstamped one has already proven it cannot do this safely, so the whole
        sync degrades to 'keep' rather than resetting half the library."""
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); READ.add(guidKey('b.xml', 'g2'));
                saveSet(READ);
                saveSeen({ 'a.xml': 'stale', 'b.xml': 'stale' });
                const n = syncReadState([feed('a.xml', 'fresh'), feed('b.xml', undefined, ['g2'])]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1", "b.xml::g2"]}

    def test_items_that_aged_out_are_pruned(self, feeds: Path):
        """The reason the manifest carries guids at all: an item that fell off
        the end of the feed must not keep a key in localStorage forever."""
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); READ.add(guidKey('a.xml', 'gone'));
                saveSet(READ);
                const n = syncReadState([feed('a.xml', undefined, ['g1'])]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}

    def test_keys_for_a_feed_that_no_longer_exists_are_pruned(self):
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); READ.add(guidKey('gone.xml', 'g9'));
                saveSet(READ);
                const n = syncReadState([feed('a.xml', undefined, ['g1'])]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}

    def test_first_sync_records_tokens_without_wiping_marks(self):
        """Upgrading the page must not silently clear everyone's read state."""
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                const n = syncReadState([feed('a.xml', 'tok')]);
                console.log(JSON.stringify({
                  reset: n, marks: marks(), seen: JSON.parse(localStorage.getItem('localreader.seen')),
                }));
                """
            )
        )
        assert json.loads(out) == {
            "reset": 0,
            "marks": ["a.xml::g1"],
            "seen": {"a.xml": "tok"},
        }

    def test_an_empty_feed_list_is_a_no_op(self):
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                const n = syncReadState([]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}

    def test_a_null_token_counts_as_unstamped(self):
        """`f.token == null`, not a truthiness test: a backend that emits an
        empty string must not be read as a changed build."""
        out = _run_node(
            _read_state_harness(
                """
                READ.add(guidKey('a.xml', 'g1')); saveSet(READ);
                saveSeen({ 'a.xml': 'old' });
                const n = syncReadState([feed('a.xml', '', ['g1'])]);
                console.log(JSON.stringify({ reset: n, marks: marks() }));
                """
            )
        )
        assert json.loads(out) == {"reset": 0, "marks": ["a.xml::g1"]}


@requires_node
class TestSidebarCounts:
    """``unreadFor`` decides every number in the sidebar, and the two backends
    feed it very different things. Getting it wrong does not crash anything --
    it just shows a wrong number, which is why it needs its own test.
    """

    @staticmethod
    def _harness(body: str) -> str:
        """Lift guidKey, countUnread and unreadFor out of the page with a stub
        STATE and READ, so the counting can be driven directly."""
        page = lr.HTML_PAGE
        guid = page[page.index("function guidKey") : page.index("function feedOfKey")]
        count = page[page.index("function countUnread") : page.index("function feedItems")]
        unread = page[page.index("function unreadFor") : page.index("function renderTree")]
        return textwrap.dedent(
            f"""
            const STATE = {{ feeds: {{}} }};
            let READ = new Set();
            {guid}
            {count}
            {unread}

            function meta(over) {{ return Object.assign(
              {{ file: 'a.xml', unread_count: 3, guids: ['g1', 'g2', 'g3'] }}, over); }}

            {body}
            """
        )

    def test_a_loaded_feed_is_counted_from_its_items(self):
        out = _run_node(
            self._harness(
                "STATE.feeds['a.xml'] = { file: 'a.xml',"
                " items: [{guid: 'g1'}, {guid: 'g2'}, {guid: 'g3'}] };"
                "READ = new Set(['a.xml::g1']);"
                "console.log(unreadFor(meta({})));"
            )
        )
        assert out == "2"

    def test_an_unloaded_feed_is_counted_against_the_reader(self):
        """The deployed manifest's `unread_count` is the item count -- the build
        server has no idea what this reader read. The guid list is what makes
        the count real."""
        out = _run_node(
            self._harness(
                "READ = new Set(['a.xml::g1', 'a.xml::g3']);"
                "console.log(unreadFor(meta({})));"
            )
        )
        assert out == "1", "must be 1 unread, not the manifest's 3"

    def test_a_manifest_count_is_never_reported_as_unread(self):
        """The failure this guards: the sidebar said 1074 unread with an
        article already read, because it summed the manifest's item counts."""
        out = _run_node(
            self._harness(
                "READ = new Set(['a.xml::g1', 'a.xml::g2']);"
                "console.log(unreadFor(meta({ unread_count: 999 })));"
            )
        )
        assert out == "1"

    def test_a_backend_with_neither_guids_nor_items_falls_back(self):
        """A TOC entry with no guid list -- the local API's shape -- must fall
        back to the count it was given rather than reporting nothing."""
        out = _run_node(
            self._harness(
                "console.log(unreadFor(meta({ guids: undefined, unread_count: 7 })));"
            )
        )
        assert out == "7"

    def test_a_feed_with_no_guids_left_reads_as_caught_up(self):
        out = _run_node(
            self._harness("console.log(unreadFor(meta({ guids: [] })));")
        )
        assert out == "0"

    def test_a_loaded_feed_wins_over_the_guid_list(self):
        """The items are the live truth once the feed is open; a guid list read
        before an item was added would be stale for that feed."""
        out = _run_node(
            self._harness(
                "STATE.feeds['a.xml'] = { file: 'a.xml',"
                " items: [{guid: 'g1'}, {guid: 'g2'}, {guid: 'g3'}, {guid: 'g4'}] };"
                "console.log(unreadFor(meta({})));"
            )
        )
        assert out == "4"

    def test_a_guid_containing_the_key_separator_is_matched_whole(self):
        """Keys are `file::guid`, and a guid may itself contain '::' -- a
        scheme-relative link, say. A naive split would look up the wrong half."""
        out = _run_node(
            self._harness(
                "READ = new Set(['a.xml::https://x/1::frag']);"
                "console.log(unreadFor(meta({ guids: ['https://x/1::frag', 'g2'] })));"
            )
        )
        assert out == "1"


@requires_node
class TestStaticAdapterUnderNode:
    """The static data layer, run against real feed XML with a DOM shim.

    ``DOMParser``/``innerHTML`` are stubbed well enough for these feeds' shape.
    The point is not to reimplement a browser but to check that the browser-side
    parser extracts the same items, ids and text the Python parser does.
    """

    @staticmethod
    def _harness(feeds_dir: Path, body: str) -> str:
        """Reproduce the Pages layout exactly: ``feeds_dir`` stands for the
        site's ``feeds/`` directory and the page sits one level above it,
        fetching ``feeds/<file>`` relative to its own URL. Anything else and a
        wrong path in the adapter would still pass."""
        if not (feeds_dir / "manifest.json").is_file():
            (feeds_dir / "manifest.json").write_text(
                json.dumps(build_manifest(feeds_dir)), encoding="utf-8"
            )
        page = swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER)
        script = _page_from(page)
        adapter = script[
            script.index("const RAW_FEED_BASE") : script.index(
                "/* ---------- persistence ---------- */"
            )
        ]
        return textwrap.dedent(
            f"""
            const fs = require('fs');
            const path = require('path');
            // The page is served from the site root, one level above feeds/, and
            // fetches 'feeds/<file>' relative to its own URL. Reproduce that
            // here so a change to either path fails the test rather than
            // quietly working in one place and 404ing on Pages.
            const site = {json.dumps(str(feeds_dir.parent.resolve()))};
            function el(name) {{
              const e = {{ localName: name, prefix: '', children: [], textContent: '' }};
              const byTag = (t) => {{
                const o = []; (function w(n) {{ for (const c of n.children) {{ if (c.localName === t) o.push(c); w(c); }} }})(e);
                return o;
              }};
              e.getElementsByTagName = byTag;
              e.getElementsByTagNameNS = (ns, t) => byTag(t).filter(c => c.prefix);
              e.querySelector = (s) => (s === 'parsererror' ? byTag(s)[0] || null : null);
              return e;
            }}
            function parseXml(text) {{
              const doc = el('#document'); const stack = [doc];
              const re = /<(\\/?)([A-Za-z_][\\w.:-]*)((?:\\s+[\\w.:-]+\\s*=\\s*"[^"]*")*)\\s*(\\/?)>|([^<]+)/g;
              let m;
              while ((m = re.exec(text)) !== null) {{
                const [, close, name, , self, run] = m;
                if (run !== undefined) {{
                  if (run.trim()) stack[stack.length - 1].textContent += run
                    .replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"')
                    .replace(/&#39;/g,"'").replace(/&amp;/g,'&');
                  continue;
                }}
                if (close) {{ if (stack.length > 1) stack.pop(); continue; }}
                const e = el(name); const i = name.indexOf(':');
                if (i > 0) {{ e.localName = name.slice(i + 1); e.prefix = name.slice(0, i); }}
                stack[stack.length - 1].children.push(e);
                if (!self) stack.push(e);
              }}
              doc.documentElement = doc.children[0] || doc;
              return doc;
            }}
            global.DOMParser = class {{ parseFromString(t) {{ return parseXml(t); }} }};
            global.document = {{
              createElement: () => {{
                let backing = '';
                return {{
                  set innerHTML(v) {{ backing = String(v).replace(/<[^>]+>/g, ' ').replace(/&lt;/g,'<')
                    .replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'")
                    .replace(/&nbsp;/g,' ').replace(/&amp;/g,'&'); }},
                  get textContent() {{ return backing; }},
                }};
              }},
            }};
            global.fetch = async (url) => {{
              // Absolute URLs (raw.githubusercontent.com) are served from the
              // local feeds/ directory under the site root.
              const rel = url.startsWith('http') ? url.slice(url.indexOf('/feeds/')) : url;
              const p = path.join(site, rel.replace(/^\\.\\//, ''));
              try {{
                const t = fs.readFileSync(p, 'utf8');
                return {{ ok: true, status: 200, text: async () => t, json: async () => JSON.parse(t) }};
              }} catch (e) {{
                return {{ ok: false, status: 404, text: async () => '', json: async () => ({{}}) }};
              }}
            }};
            {adapter}
            globalThis.ADAPTER = ADAPTER;
            (async () => {{
              {body}
            }})().catch(e => {{ console.error('FAILED: ' + e.message); process.exit(1); }});
            """
        )

    def test_toc_returns_the_manifest_folders(self, feeds: Path):
        manifest = feeds / "manifest.json"
        manifest.write_text(json.dumps(build_manifest(feeds)), encoding="utf-8")
        out = _run_node(
            self._harness(
                feeds,
                "const f = await ADAPTER.toc();"
                "console.log(JSON.stringify(f.map(g => g.folder + ':' + g.feeds.length)));",
            )
        )
        names = json.loads(out)
        assert names
        assert all(":" in n for n in names)

    def test_a_feed_parses_into_the_same_items_python_finds(self, feeds: Path):
        out = _run_node(
            self._harness(
                feeds,
                "const f = await ADAPTER.feed('alpha.xml');"
                "console.log(JSON.stringify({"
                "  count: f.item_count,"
                "  guids: f.items.map(i => i.guid),"
                "  titles: f.items.map(i => i.title),"
                "  dates: f.items.map(i => i.date),"
                "  snippets: f.items.map(i => i.snippet),"
                "  descs: f.items.map(i => i.desc_html),"
                "}));",
            )
        )
        got = json.loads(out)
        want = lr.parse_feed(feeds / "alpha.xml")
        assert got["count"] == len(want["items"])
        assert got["guids"] == [i["guid"] for i in want["items"]]
        assert got["titles"] == [i["title"] for i in want["items"]]
        assert got["dates"] == [i["date"] for i in want["items"]]
        assert got["snippets"] == [i["snippet"] for i in want["items"]]
        assert got["descs"] == [i["desc_html"] for i in want["items"]]

    def test_content_encoded_wins_over_description(self, tmp_path: Path):
        """Python prefers content:encoded; the browser must too, or the reader
        would show the short teaser instead of the article on a third of items."""
        feeds = tmp_path / "feeds"
        feeds.mkdir()
        (feeds / "enc.xml").write_text(
            '<?xml version="1.0"?><rss version="2.0" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
            "<item><guid>g</guid><title>t</title>"
            "<description>SHORT TEASER</description>"
            "<content:encoded>FULL ARTICLE BODY</content:encoded>"
            "</item></channel></rss>",
            encoding="utf-8",
        )
        out = _run_node(
            self._harness(
                feeds,
                "const f = await ADAPTER.feed('enc.xml');"
                "console.log(JSON.stringify({d: f.items[0].desc_html, s: f.items[0].snippet}));",
            )
        )
        got = json.loads(out)
        want = lr.parse_feed(feeds / "enc.xml")["items"][0]
        assert "FULL ARTICLE BODY" in got["d"]
        assert "SHORT TEASER" not in got["d"]
        assert got["s"] == want["snippet"]

    def test_bold_labels_match_the_python_extractor(self, tmp_path: Path):
        feeds = tmp_path / "feeds"
        feeds.mkdir()
        (feeds / "tags.xml").write_text(
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><guid>g</guid><title>t</title><description>"
            "<b>Trailer</b> <b>IMDb</b> <b>Trailer</b>"
            "</description></item></channel></rss>",
            encoding="utf-8",
        )
        out = _run_node(
            self._harness(
                feeds,
                "const f = await ADAPTER.feed('tags.xml');"
                "console.log(JSON.stringify(f.items[0].tags));",
            )
        )
        assert json.loads(out) == lr.parse_feed(feeds / "tags.xml")["items"][0]["tags"]

    def test_a_missing_feed_raises_a_readable_error(self, feeds: Path):
        out = _run_node(
            self._harness(
                feeds,
                "try { await ADAPTER.feed('nope.xml'); console.log('NO ERROR'); }"
                "catch (e) { console.log('ERROR: ' + e.message); }",
            )
        )
        assert "ERROR" in out
        assert "404" in out

    def test_manifest_guids_line_up_with_parsed_items(self, feeds: Path):
        """The two halves of the static build are generated separately (manifest
        in Python, items in the browser). If they disagree, the sidebar's counts
        and the read-state pruning both go wrong."""
        (feeds / "manifest.json").write_text(json.dumps(build_manifest(feeds)), encoding="utf-8")
        out = _run_node(
            self._harness(
                feeds,
                "const folders = await ADAPTER.toc();"
                "const bad = [];"
                "for (const g of folders) for (const e of g.feeds) {"
                "  const f = await ADAPTER.feed(e.file);"
                "  if (f.items.length !== e.guids.length) bad.push(e.file + ' length');"
                "  f.items.forEach((it, i) => { if (it.guid !== e.guids[i]) bad.push(e.file + '[' + i + ']'); });"
                "}"
                "console.log(JSON.stringify({feeds: folders.reduce((n,g)=>n+g.feeds.length,0), bad: bad}));",
            )
        )
        got = json.loads(out)
        assert got["feeds"] == 2
        assert got["bad"] == []


# --------------------------------------------------------------------------
# The deploy contract
# --------------------------------------------------------------------------


class TestDeployWiring:
    """The reader is only useful if the workflow actually ships it. index.html
    links to reader.html, so a missing copy is a 404 on the page's main call to
    action -- invisible in tests, obvious to a visitor."""

    @staticmethod
    def _workflow() -> str:
        return (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "update.yml"
        ).read_text(encoding="utf-8")

    def test_ci_generates_the_reader(self):
        assert "python scripts/generate_reader.py" in self._workflow()

    def test_ci_copies_the_page_into_the_artifact(self):
        assert "cp reader.html .site/" in self._workflow()

    def test_reader_is_built_after_the_feeds_it_describes(self):
        wf = self._workflow()
        assert wf.index("generate_feeds.py") < wf.index("generate_reader.py")

    def test_both_outputs_land_in_the_published_layout(self):
        """reader.html goes beside feeds/ at the site root and the manifest
        inside feeds/ -- which is what the page's relative fetches assume, and
        what the artifact's `cp -R feeds` already ships."""
        assert MANIFEST_FILE.name == "manifest.json"
        assert MANIFEST_FILE.parent.name == "feeds"
        assert OUTPUT_FILE.name == "reader.html"
        assert OUTPUT_FILE.parent == MANIFEST_FILE.parent.parent

    def test_both_outputs_are_gitignored_build_artifacts(self):
        """Rebuilt on every CI run, so committing one would only leave a stale
        copy in the repository between deploys."""
        result = subprocess.run(
            ["git", "check-ignore", "-v", str(OUTPUT_FILE), str(MANIFEST_FILE)],
            cwd=str(OUTPUT_FILE.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert str(OUTPUT_FILE.name) in result.stdout
        assert str(MANIFEST_FILE.name) in result.stdout

    def test_the_index_links_to_the_reader(self):
        index = (
            Path(__file__).resolve().parent.parent / "scripts" / "generate_index.py"
        ).read_text(encoding="utf-8")
        assert "href='reader.html'" in index
        assert "btn-reader" in index


@requires_node
class TestThePageScriptParses:
    """The whole page is one <script>; if it fails to parse, everything dies.

    d0e5f421 added a close button whose inline onclick used unescaped single
    quotes inside a single-quoted JS string. The browser rejected the entire
    script at parse time, so the reader sat on "Loading feeds…" forever while
    every other test kept passing -- nothing had ever executed the page.
    ``node --check`` is the cheapest thing that would have caught it.
    """

    @staticmethod
    def _scripts(page: str) -> list[str]:
        return re.findall(r"<script>(.*?)</script>", page, re.S)

    def _assert_parses(self, page: str, where: str, tmp_path: Path) -> None:
        scripts = self._scripts(page)
        assert scripts, f"no <script> block found in {where}"
        for i, body in enumerate(scripts):
            js = tmp_path / f"{i}.js"
            js.write_text(body, encoding="utf-8")
            res = subprocess.run(
                ["node", "--check", str(js)], capture_output=True, text=True
            )
            assert res.returncode == 0, f"{where} script block {i}: {res.stderr}"

    def test_local_page(self, tmp_path: Path) -> None:
        self._assert_parses(lr.HTML_PAGE, "local reader page", tmp_path)

    def test_published_page(self, tmp_path: Path) -> None:
        # reader.html is a build artifact (.gitignore) written by
        # generate_reader.py at deploy time, so CI checks out no copy of it.
        # Build the exact page the deploy step will write -- same call -- and
        # syntax-check that, instead of depending on a file that is not there.
        self._assert_parses(
            swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER),
            "published reader page",
            tmp_path,
        )
