"""Tests for the local reader's server-side helpers (scripts/local_reader.py).

The read/unread marks themselves live in the browser's localStorage, so what is
testable here is the server half -- the per-feed change token exposed through
`api?list` -- plus string-level guards on the embedded page.

The client half is driven properly in ``tests/test_generate_reader.py``, which
runs the page's own ``syncReadState`` under node. That module covers the static
GitHub Pages reader, which is the same page with its data layer swapped.
"""
from __future__ import annotations

import re
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


class TestArticleLayoutCss:
    """The panel styles the article HTML, which is whatever the site sent.

    It used to style only ``img`` and ``a``, so every other box kept the
    browser's default margins. Two of those defaults put a 40px inset on
    ``figure`` and ``blockquote``, and ``aligncenter`` -- which 191 images in
    the published feeds carry -- was honoured by nothing, so a 300px photo the
    author had centred rendered flush left inside a 534px column. These are
    string checks on the stylesheet rather than a rendering test: CI runs the
    suite before the Playwright browser is installed, so a test that needs one
    would fail there. The geometry was verified in a real browser instead.
    """

    @staticmethod
    def _css() -> str:
        """The panel's stylesheet with comments stripped.

        The comments here quote the very defaults being removed ('1em 40px',
        '190px'), so a naive rule scan reads the explanation as if it were the
        offending rule and fails on a correct stylesheet.
        """
        start = lr.HTML_PAGE.index(".panel-desc {")
        css = lr.HTML_PAGE[start : lr.HTML_PAGE.index("</style>")]
        return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def test_figure_and_blockquote_lose_the_default_40px_inset(self):
        css = self._css()
        assert ".panel-desc :is(figure, blockquote) { margin: 12px 0; }" in css, (
            "figure/blockquote keep the browser's `1em 40px`, which is the "
            "40px of dead space either side of every image and pull-quote"
        )
        # Nothing may reintroduce a horizontal margin on those two.
        for rule in css.split("}"):
            if "figure" in rule and "margin" in rule:
                assert "40px" not in rule, rule

    def test_alignment_classes_are_honoured(self):
        css = self._css()
        for cls in ("aligncenter", "alignleft", "alignright"):
            assert cls in css, f"`.{cls}` is present in feed HTML and ignored by the reader"

    def test_wp_block_image_is_centred(self):
        """WordPress centres its own image block, and 247 of them are in the
        feeds. The class sits on the <figure> in some themes and on a wrapping
        <div> in others, so both have to match."""
        css = self._css()
        assert "figure.wp-block-image" in css
        assert "div.wp-block-image" in css

    def test_explicit_alignment_wins_over_block_centring(self):
        """The ordering is load-bearing, and getting it wrong is invisible in a
        screenshot diff: `figure.alignleft` inside `div.wp-block-image` is
        centred, because the centring rule has equal specificity and came
        first. vedem-just's images are declared alignleft and rendered centred
        until this was fixed."""
        css = self._css()
        assert css.index("alignleft") > css.index("aligncenter"), (
            "alignleft must be declared after aligncenter to win the cascade"
        )
        assert css.index("alignright") > css.index("aligncenter")

    def test_alignment_uses_margins_because_block_images_ignore_text_align(self):
        css = self._css()
        assert "margin-left: auto; margin-right: auto" in css, (
            "a block-level image is positioned with auto margins, not text-align"
        )
        assert "margin-left: 0; margin-right: 0" in css, "alignleft needs its own reset"

    def test_the_image_does_not_stack_vertical_margins_inside_a_figure(self):
        """The bare-image rule sets `margin: 8px 0` and the figure sets 12px.
        Left alone those add up to a visible 20px band above and below every
        image in a figure, which is the same complaint in a different axis."""
        css = self._css()
        assert ".panel-desc figure :is(img, a) { margin-top: 0; margin-bottom: 0; }" in css


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

    def test_the_lookup_is_an_allowlist_not_a_sanitised_join(self, monkeypatch, tmp_path):
        """`?feed=` is the only thing that decides which file this server opens.

        The handler used to build `(FEEDS_DIR / feed_file).resolve()` and then
        prove the result was inside FEEDS_DIR -- reject a name with a separator,
        test containment by whole component. That was sound, and it left three
        rules to get right for one decision. It is now an allowlist: a name is
        matched against the files actually in feeds/, and anything else is not
        found. There is no join left to defend.
        """
        feeds = tmp_path / "feeds"
        feeds.mkdir(exist_ok=True)
        monkeypatch.setattr(lr, "FEEDS_DIR", feeds)
        (feeds / "ok.xml").write_text("<rss/>", encoding="utf-8")
        # A sibling directory that a str.startswith containment test would accept.
        (tmp_path / "feeds-evil").mkdir()
        (tmp_path / "feeds-evil" / "secret.xml").write_text("<rss/>", encoding="utf-8")

        assert lr._resolve_feed_path("ok.xml") is not None
        for attempt in (
            "../feeds-evil/secret.xml",
            "feeds-evil/secret.xml",
            "secret.xml",
            "/etc/passwd",
            ".",
            "..",
            "",
            "missing.xml",
        ):
            assert lr._resolve_feed_path(attempt) is None, attempt

    def test_a_name_with_a_separator_is_refused_even_if_the_tail_exists(self, monkeypatch, tmp_path):
        feeds = tmp_path / "feeds"
        feeds.mkdir(exist_ok=True)
        monkeypatch.setattr(lr, "FEEDS_DIR", feeds)
        (feeds / "ok.xml").write_text("<rss/>", encoding="utf-8")
        # `Path(name).name == name` is what rejects this: the tail is a real
        # feed, so a resolver that quietly kept the last component would serve
        # a request the caller did not name.
        assert lr._resolve_feed_path("subdir/ok.xml") is None

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


class TestBackToIndexLink:
    """The reader has to offer a way back to the feed index.

    Both readers are the same page, so the link lives in the shared
    ``HTML_PAGE`` and reaches ``reader.html`` through the adapter swap. Its
    target has to resolve in *both* deployments: on Pages ``index.html`` is a
    sibling of ``reader.html``, and the local server has to serve the generated
    index itself or the button is a dead link during development.
    """

    def test_the_page_links_back_to_the_index(self):
        assert '<a class="back" href="index.html"' in lr.HTML_PAGE, (
            "the reader has no way back to the index"
        )

    def test_the_link_is_relative_so_it_survives_the_swap(self):
        """A relative href is what makes one page correct in both deployments.

        The static build is served from the site root next to index.html; an
        absolute URL would hardcode the Pages hostname into the local reader,
        which is served from localhost on a different port.
        """
        start = lr.HTML_PAGE.index('<a class="back"')
        tag = lr.HTML_PAGE[start : lr.HTML_PAGE.index(">", start)]
        assert 'href="index.html"' in tag, tag
        assert "://" not in tag, tag

    @staticmethod
    def _back_link() -> str:
        """The back link's opening tag and its text, as written in the page."""
        start = lr.HTML_PAGE.index('<a class="back"')
        return lr.HTML_PAGE[start : lr.HTML_PAGE.index("</a>", start)]

    def test_the_link_carries_the_full_name_as_its_tooltip(self):
        """The arrow alone does not say where it goes, and "back" is ambiguous
        with browser history. The tooltip is what disambiguates it."""
        assert 'title="Back to the feed index"' in self._back_link()

    def test_the_accessible_name_is_the_visible_word_not_the_arrow(self):
        """The arrow is decoration, so it is hidden from assistive tech.

        It is tempting to reach for aria-label here, but an aria-label replaces
        the visible text as the accessible name, and WCAG 2.5.3 requires that
        name to contain the visible label. Announcing the bare arrow would also
        be useless. Hiding the arrow leaves "Feeds" as both, which is what
        voice control users say.
        """
        link = self._back_link()
        assert "aria-label" not in link, "aria-label would override the visible 'Feeds'"
        opening, _, text = link.partition(">")
        assert "aria-hidden" not in opening, "the arrow belongs to the text, not the tag"
        # The arrow carries the hiding, and "Feeds" sits outside it.
        arrow_start = text.index("<span")
        arrow_end = text.index("</span>") + len("</span>")
        assert 'aria-hidden="true"' in text[arrow_start:arrow_end]
        assert text[:arrow_start].strip() == ""
        assert text[arrow_end:].strip() == "Feeds", text

    def test_it_survives_into_the_static_build(self):
        """The published reader is built by swapping only the adapter block, so
        anything outside the sentinels has to arrive in reader.html intact."""
        from scripts.generate_reader import STATIC_ADAPTER, swap_adapter

        published = swap_adapter(lr.HTML_PAGE, STATIC_ADAPTER)
        assert '<a class="back" href="index.html"' in published

    def test_the_local_server_serves_the_index(self, monkeypatch, tmp_path):
        """Otherwise the button 404s in local preview while working on Pages."""
        monkeypatch.setattr(lr, "INDEX_FILE", tmp_path / "index.html")
        (tmp_path / "index.html").write_text("<h1>RSS Generator</h1>", encoding="utf-8")

        served: dict[str, object] = {}

        class _H(lr.Handler):
            def __init__(self):
                pass

            def _send(self, code, ctype, body):
                served["code"] = code
                served["body"] = body

        h = _H()
        h.path = "/index.html"
        h.do_GET()
        assert served["code"] == 200
        assert "RSS Generator" in str(served["body"])

    def test_a_missing_index_says_how_to_make_one(self, monkeypatch, tmp_path):
        """index.html is gitignored, so a fresh clone legitimately has none.

        The alternative is a bare 404 that reads like a broken server rather
        than an unrun build step.
        """
        monkeypatch.setattr(lr, "INDEX_FILE", tmp_path / "index.html")

        served: dict[str, object] = {}

        class _H(lr.Handler):
            def __init__(self):
                pass

            def _send(self, code, ctype, body):
                served["code"] = code
                served["body"] = body

        h = _H()
        h.path = "/index.html"
        h.do_GET()
        assert served["code"] == 404
        assert "generate_index.py" in str(served["body"])

    def test_the_index_route_takes_no_input_from_the_request(self, monkeypatch, tmp_path):
        """One fixed file, never a path assembled from the request.

        The `?feed=` handler is an allowlist because joining a query-string name
        onto a directory is the mistake that needed containing. This route must
        not reintroduce it.

        Asserting the response is *the same* for a spread of query strings is
        what makes that claim testable. Naming one parameter and checking it
        does not leak is not: an implementation that honoured some other
        parameter -- `?file=`, say -- would pass a check that only ever asked
        about `?feed=`. The canary file below is the thing that must never come
        back, whichever key is tried.
        """
        monkeypatch.setattr(lr, "INDEX_FILE", tmp_path / "index.html")
        (tmp_path / "index.html").write_text("<h1>mine</h1>", encoding="utf-8")
        (tmp_path / "secret.txt").write_text("do not serve me", encoding="utf-8")
        (tmp_path / "index.html.bak").write_text("nor me", encoding="utf-8")

        def request(path: str) -> dict[str, object]:
            served: dict[str, object] = {}

            class _H(lr.Handler):
                def __init__(self):
                    pass

                def _send(self, code, ctype, body):
                    served["code"] = code
                    served["body"] = body

            h = _H()
            h.path = path
            h.do_GET()
            return served

        baseline = request("/index.html")
        assert baseline["code"] == 200
        assert baseline["body"] == "<h1>mine</h1>"

        # Whatever the request asks for, the bytes are identical to the plain
        # route's. Probed by key name, by traversal, and by a sibling file, so
        # that a handler keyed on a parameter this test did not think of still
        # has to produce the same body.
        for path in (
            "/index.html?feed=../secret.txt",
            "/index.html?feed=secret.txt",
            "/index.html?file=../secret.txt",
            "/index.html?file=secret.txt",
            "/index.html?path=../secret.txt",
            "/index.html?name=index.html.bak",
            "/index.html?feed=index.html.bak",
            "/index.html?a=1&b=2&feed=../secret.txt",
            "/index.html?" + "x" * 2000,
            "/index.html#../secret.txt",
            "/index.html;.css",
        ):
            served = request(path)
            assert served["code"] == 200, path
            assert served["body"] == baseline["body"], f"{path} changed what was served"

        for body in (baseline["body"],):
            for canary in ("do not serve me", "nor me"):
                assert canary not in str(body)

        # A path that is not the literal route must not reach the file at all,
        # whichever way it is spelled. urlparse keeps these whole as the path.
        for path in (
            "/index.html/../secret.txt",
            "/index.html/./../../secret.txt",
            "/index.html%2f..%2fsecret.txt",
            "/./index.html",
            "/index.html.bak",
        ):
            assert request(path)["code"] == 404, path

    def test_the_brand_row_cannot_wrap_its_title(self):
        """The header has a fixed height and clips its overflow.

        The back link joined the title in one row, so the title has to
        truncate: a second line would be cut off by the 48px band rather than
        pushing it taller.
        """
        start = lr.HTML_PAGE.index(".brand h1 {")
        rule = lr.HTML_PAGE[start : lr.HTML_PAGE.index("}", start)]
        assert "white-space: nowrap" in rule
        assert "text-overflow: ellipsis" in rule
        # And the wrapper has to be allowed to shrink for that to engage.
        assert ".brand-text { min-width: 0; }" in lr.HTML_PAGE


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
