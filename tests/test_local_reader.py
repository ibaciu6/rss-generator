"""Tests for the local reader's server-side helpers (scripts/local_reader.py).

The read/unread marks themselves live in the browser's localStorage, so what is
testable here is the half that decides *when* they are invalid: the per-feed
change token exposed through `api?list`. `tests/test_reader_read_state.js` drives
the client half in a real browser.
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
