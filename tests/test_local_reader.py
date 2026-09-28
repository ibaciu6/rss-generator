"""Tests for the local reader's server-side helpers (scripts/local_reader.py).

The read/unread marks themselves live in the browser's localStorage, so what is
testable here is the server half -- the per-feed change token exposed through
`api?list` -- plus string-level guards on the embedded page.

The client half is driven properly in ``tests/test_generate_reader.py``, which
runs the page's own ``syncReadState`` under node. That module covers the static
GitHub Pages reader, which is the same page with its data layer swapped.
"""
from __future__ import annotations

from pathlib import Path

from scripts import local_reader as lr

MINIMAL_FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>t</title><link>https://example.com/</link>'
    "<item><title>i</title><link>https://example.com/1</link></item>"
    "</channel></rss>"
)


def _feed(path: Path) -> Path:
    path.write_text(MINIMAL_FEED, encoding="utf-8")
    return path


class TestFeedToken:
    def test_token_is_stable_for_an_untouched_file(self, tmp_path):
        path = _feed(tmp_path / "a.xml")
        assert lr.feed_token(path) == lr.feed_token(path)

    def test_token_changes_when_the_file_is_rewritten(self, tmp_path):
        """This is the whole mechanism: a rewritten feed must produce a
        different token, or the client would keep stale read marks."""
        path = _feed(tmp_path / "a.xml")
        before = lr.feed_token(path)
        path.write_text(MINIMAL_FEED.replace("<title>i</title>", "<title>ii</title>"), encoding="utf-8")
        assert lr.feed_token(path) != before

    def test_token_differs_between_files_with_identical_content(self, tmp_path):
        """Two feeds can be byte-identical but must not share a token, or
        regenerating one would be invisible."""
        a = _feed(tmp_path / "a.xml")
        b = _feed(tmp_path / "b.xml")
        assert lr.feed_token(a) != lr.feed_token(b)

    def test_missing_file_yields_a_stable_marker(self, tmp_path):
        """The glob and the stat race when a feed is deleted mid-request; a
        marker beats an exception that would 500 the whole TOC."""
        assert lr.feed_token(tmp_path / "gone.xml") == "missing"
        assert lr.feed_token(tmp_path / "gone.xml") == "missing"


class TestBuildToc:
    def test_every_entry_carries_a_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lr, "FEEDS_DIR", tmp_path)
        _feed(tmp_path / "a.xml")
        _feed(tmp_path / "b.xml")
        entries = [f for g in lr.build_toc() for f in g["feeds"]]
        assert len(entries) == 2
        for entry in entries:
            assert entry["token"], entry
            assert entry["token"] != "missing"

    def test_tokens_are_unique_per_feed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lr, "FEEDS_DIR", tmp_path)
        for name in ("a.xml", "b.xml", "c.xml"):
            _feed(tmp_path / name)
        entries = [f for g in lr.build_toc() for f in g["feeds"]]
        assert len({e["token"] for e in entries}) == len(entries)

    def test_token_matches_a_direct_stat(self, tmp_path, monkeypatch):
        """Guards against the client comparing against a different derivation
        than the one build_toc publishes."""
        monkeypatch.setattr(lr, "FEEDS_DIR", tmp_path)
        path = _feed(tmp_path / "a.xml")
        st = path.stat()
        entry = next(f for g in lr.build_toc() for f in g["feeds"])
        assert entry["token"] == f"{st.st_mtime_ns:x}-{st.st_size:x}"

    def test_unread_count_still_counts_every_item(self, tmp_path, monkeypatch):
        """The token is additive; the existing counts must not shift."""
        monkeypatch.setattr(lr, "FEEDS_DIR", tmp_path)
        _feed(tmp_path / "a.xml")
        entry = next(f for g in lr.build_toc() for f in g["feeds"])
        assert entry["unread_count"] == 1
        assert entry["item_count"] == 1


class TestParseFeedItemParsing:
    """The TOC endpoint must not pay for per-item work it never uses.

    `build_toc()` runs on every page load and every refresh. Parsing all 1208
    items' HTML (one body is 471KB) plus re-reading sites.yaml per feed cost
    6.5s, which made the reader feel frozen on every refresh.
    """

    def test_with_items_false_skips_the_item_payload(self, tmp_path):
        path = _feed(tmp_path / "a.xml")
        feed = lr.parse_feed(path, with_items=False)
        assert feed["items"] == []
        assert feed["item_count"] == 1

    def test_with_items_true_parses_every_item(self, tmp_path):
        path = _feed(tmp_path / "a.xml")
        feed = lr.parse_feed(path)
        assert len(feed["items"]) == 1
        assert feed["item_count"] == 1
        assert feed["items"][0]["title"] == "i"

    def test_toc_counts_items_without_parsing_them(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lr, "FEEDS_DIR", tmp_path)
        _feed(tmp_path / "a.xml")
        toc = lr.build_toc()
        entry = toc[0]["feeds"][0]
        assert entry["item_count"] == 1
        assert entry["unread_count"] == 1
        assert "items" not in entry, "the TOC must not carry item payloads"


class TestSiteNamesCache:
    def test_repeated_calls_do_not_reparse_the_config(self, tmp_path, monkeypatch):
        """_load_site_names() used to re-read and re-parse the ~3k-line
        sites.yaml for every one of the 73 feeds: 4.4s per page load."""
        monkeypatch.setattr(lr, "CONFIG_FILE", tmp_path / "sites.yaml")
        (tmp_path / "sites.yaml").write_text(
            "sites:\n  a:\n    feed_file: a.xml\n    display_name: A\n", encoding="utf-8"
        )
        first = lr._load_site_names()
        assert first["a.xml"] == ("A", lr.FOLDER_FALLBACK)
        # Rewrite the config: the cache must notice, not serve the stale map.
        (tmp_path / "sites.yaml").write_text(
            "sites:\n  a:\n    feed_file: a.xml\n    display_name: A2\n", encoding="utf-8"
        )
        assert lr._load_site_names()["a.xml"] == ("A2", lr.FOLDER_FALLBACK)

    def test_broken_config_yields_an_empty_map_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lr, "CONFIG_FILE", tmp_path / "sites.yaml")
        (tmp_path / "sites.yaml").write_text("sites: [unclosed", encoding="utf-8")
        assert lr._load_site_names() == {}


class TestParseFeedRobustness:
    """A feed file that will not parse must degrade, not raise.

    The reader treats a broken feed as empty and keeps going, because one
    malformed file in feeds/ should not take down the whole sidebar.
    """

    def test_unparseable_xml_yields_an_empty_feed_not_an_exception(self, tmp_path):
        path = tmp_path / "broken.xml"
        path.write_text("<rss><channel><item>", encoding="utf-8")
        feed = lr.parse_feed(path)
        assert feed["items"] == []
        assert feed["item_count"] == 0
        assert feed["file"] == "broken.xml"

    def test_broken_feed_keeps_its_display_name_and_folder(self, tmp_path, monkeypatch):
        """The sidebar labels a feed from the config, so a parse failure must
        not cost the entry its name."""
        monkeypatch.setattr(lr, "CONFIG_FILE", tmp_path / "sites.yaml")
        (tmp_path / "sites.yaml").write_text(
            "sites:\n  broken:\n    feed_file: broken.xml\n    display_name: Broken\n"
            "    category: movies\n    language: ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(lr, "_SITE_NAMES_CACHE", (0, 0, {}))
        (tmp_path / "broken.xml").write_text("<rss><channel><item>", encoding="utf-8")
        feed = lr.parse_feed(tmp_path / "broken.xml")
        assert feed["name"] == "Broken"
        assert feed["folder"] == "Online-Movies"

    def test_a_feed_without_a_channel_is_still_counted(self, tmp_path):
        path = tmp_path / "root.xml"
        path.write_text(
            '<?xml version="1.0"?><rss version="2.0">'
            "<item><title>i</title></item></rss>",
            encoding="utf-8",
        )
        feed = lr.parse_feed(path, with_items=False)
        assert feed["item_count"] == 1


class TestFeedPathContainment:
    """`?feed=` names the file this server opens, and it arrives from the query
    string. A prefix test on the resolved path is not containment --
    ``/x/feeds-evil`` starts with ``/x/feeds`` -- so these pin the two things
    the handler actually has to get right.
    """

    @staticmethod
    def _serve(monkeypatch, tmp_path, feed_param):
        """Run the real handler against a fake request; return the response."""
        import json as _json

        served: dict[str, object] = {}
        feeds = tmp_path / "feeds"
        feeds.mkdir(exist_ok=True)
        (feeds / "ok.xml").write_text(
            '<?xml version="1.0"?><rss><channel><title>Ok</title>'
            "<item><title>i</title></item></channel></rss>",
            encoding="utf-8",
        )
        monkeypatch.setattr(lr, "FEEDS_DIR", feeds)

        class _H(lr.Handler):
            def __init__(self):
                pass

            def _send(self, code, ctype, body):
                served["code"] = code
                served["body"] = body

        h = _H()
        h.path = "/api?feed=" + feed_param
        h._handle_api()
        served["json"] = _json.loads(served["body"]) if served.get("body") else {}
        return served

    def test_a_real_feed_name_is_served(self, monkeypatch, tmp_path):
        served = self._serve(monkeypatch, tmp_path, "ok.xml")
        assert served["code"] == 200
        assert served["json"]["file"] == "ok.xml"

    def test_parent_traversal_is_refused(self, monkeypatch, tmp_path):
        secret = tmp_path / "secret.xml"
        secret.write_text("<rss/>", encoding="utf-8")
        for attempt in ("../secret.xml", "..%2Fsecret.xml", "a/../../secret.xml"):
            served = self._serve(monkeypatch, tmp_path, attempt)
            assert served["code"] in (400, 404), f"{attempt} was not refused"
            assert "file" not in served["json"], f"{attempt} leaked a feed"

    def test_an_absolute_path_is_refused(self, monkeypatch, tmp_path):
        served = self._serve(monkeypatch, tmp_path, "/etc/passwd")
        assert served["code"] in (400, 404)

    def test_a_sibling_directory_matching_the_prefix_is_not_reachable(
        self, monkeypatch, tmp_path
    ):
        """The old guard was str(path).startswith(str(FEEDS_DIR)), which a
        sibling directory named ``feeds-backup`` satisfies. is_relative_to
        compares components, so it does not."""
        feeds = tmp_path / "feeds"
        feeds.mkdir()
        (tmp_path / "feeds-backup").mkdir()
        (tmp_path / "feeds-backup" / "leak.xml").write_text("<rss/>", encoding="utf-8")
        monkeypatch.setattr(lr, "FEEDS_DIR", feeds)

        served: dict[str, object] = {}

        class _H(lr.Handler):
            def __init__(self):
                pass

            def _send(self, code, ctype, body):
                served["code"] = code

        h = _H()
        h.path = "/api?feed=leak.xml"
        h._handle_api()
        assert served["code"] == 404, "a file outside feeds/ was served"


class TestFeedSizeCap:
    """Feeds are built from third-party HTML, so the parser is the one place
    where a hostile input could cost memory instead of merely rendering oddly.
    """

    def test_a_feed_over_the_cap_reads_as_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lr, "MAX_FEED_BYTES", 64)
        path = tmp_path / "big.xml"
        path.write_text(
            '<?xml version="1.0"?><rss><channel><title>Big</title>'
            + "<item><title>i</title></item>" * 50
            + "</channel></rss>",
            encoding="utf-8",
        )
        assert path.stat().st_size > 64
        feed = lr.parse_feed(path)
        assert feed["items"] == []
        assert feed["item_count"] == 0

    def test_a_feed_under_the_cap_still_parses(self, tmp_path, monkeypatch):
        path = tmp_path / "small.xml"
        path.write_text(
            '<?xml version="1.0"?><rss><channel><item><title>i</title></item></channel></rss>',
            encoding="utf-8",
        )
        # The cap sits just above this file, so only the cap changed.
        monkeypatch.setattr(lr, "MAX_FEED_BYTES", path.stat().st_size)
        assert lr.parse_feed(path)["item_count"] == 1

    def test_the_real_cap_is_far_above_any_real_feed(self):
        """The cap is a guard, not a policy: if it were near the size of a real
        feed it would start deleting articles on an ordinary run."""
        biggest = max((p.stat().st_size for p in lr.FEEDS_DIR.glob("*.xml")), default=0)
        assert biggest < lr.MAX_FEED_BYTES // 20, (
            f"largest feed is {biggest} bytes against a cap of {lr.MAX_FEED_BYTES}"
        )


class TestClientWiring:
    """Cheap guards on the embedded page: these are string checks, not a DOM
    test, but they catch an accidentally deleted handler or markup id."""

    def test_page_exposes_the_token_to_the_client(self):
        assert "f.token" in lr.HTML_PAGE
        assert "syncReadState" in lr.HTML_PAGE

    def test_boot_reconciles_before_rendering(self):
        """Order matters: renderTree/selectFeed read READ, so the reset has to
        happen first or the first paint shows the stale counts."""
        boot = lr.HTML_PAGE[lr.HTML_PAGE.index("async function boot"):]
        assert boot.index("syncReadState") < boot.index("renderTree()")

    def test_reset_note_element_exists(self):
        assert 'id="gen-note"' in lr.HTML_PAGE
        assert "function flashNote" in lr.HTML_PAGE

    def test_manual_reset_button_is_wired(self):
        assert 'id="clear-read"' in lr.HTML_PAGE
        assert "getElementById('clear-read')" in lr.HTML_PAGE

    def test_seen_map_is_namespaced_apart_from_the_read_set(self):
        """Two separate keys: clearing one must not wipe the other, otherwise
        the token map would be lost and every load would look like a regen."""
        assert "localreader.seen" in lr.HTML_PAGE
        assert "localreader.read." in lr.HTML_PAGE

    def test_an_empty_feed_list_cannot_wipe_read_state(self):
        """syncReadState prunes keys for feeds that vanished, so an empty list
        would otherwise delete the entire library -- irreversibly, since read
        marks are only re-learned by reading. A deploy that produced an empty
        manifest is exactly the case this guards."""
        body = lr.HTML_PAGE[lr.HTML_PAGE.index("function syncReadState"):]
        prune = body[: body.index("if (feeds.some(f => !f.token))")]
        assert "if (!feeds.length) return 0;" in prune, (
            "the empty-list guard must come before the pruning block"
        )

    def test_a_feed_without_guids_keeps_all_of_its_marks(self):
        """Only a feed that supplied `guids` may have its keys judged against
        them. Reading an absent list as an empty one would wipe the marks of
        every feed the backend could not enumerate."""
        body = lr.HTML_PAGE[lr.HTML_PAGE.index("function syncReadState"):]
        prune = body[: body.index("if (feeds.some(f => !f.token))")]
        assert "if (!guids) return false;" in prune

    def test_the_page_has_no_fetch_outside_the_adapter(self):
        """Every request has to go through ADAPTER, or the static build would
        still try to reach the local server's api? endpoints. String checks
        rather than a DOM test, but they catch a stray hardcoded fetch."""
        start = lr.HTML_PAGE.index(lr.ADAPTER_BEGIN)
        end = lr.HTML_PAGE.index(lr.ADAPTER_END)
        outside = lr.HTML_PAGE[:start] + lr.HTML_PAGE[end:]
        assert "fetch(" not in outside, "a fetch outside the adapter will 404 on Pages"
        # ...and the adapter is where the two live calls belong.
        assert lr.HTML_PAGE[start:end].count("fetch(") == 2

    def test_the_tree_reads_unread_counts_through_unread_for(self):
        """renderTree must not sum the TOC's own `unread_count`.

        That number is the feed's item count: the local API computes it from
        parsed items, but the deployed manifest is built by a build server that
        has never seen this reader. Summing it directly leaves the sidebar
        claiming every article in the library is unread, forever."""
        tree = lr.HTML_PAGE[lr.HTML_PAGE.index("function renderTree"):]
        tree = tree[: tree.index("function loadFeedAny")]
        assert "f.unread_count" not in tree
        assert "fmeta.unread_count" not in tree
        assert tree.count("unreadFor(") == 2, "once for the folder total, once for the badge"

    def test_select_feed_rebuilds_the_tree(self):
        """A feed's real unread count only exists after it has been parsed, and
        renderTree draws the active feed from STATE.current itself, so
        selectFeed has to re-render rather than toggle .active by hand. Without
        it a feed you have read in still shows itself fully unread after a
        reload, because the tree was drawn from the pre-parse counts."""
        sel = lr.HTML_PAGE[lr.HTML_PAGE.index("async function selectFeed"):]
        sel = sel[: sel.index("/* ---------- news list")]
        assert "renderTree()" in sel
        assert "CSS.escape" not in sel, "the manual .active juggling is gone"
