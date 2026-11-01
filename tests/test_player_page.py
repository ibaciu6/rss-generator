"""The player page's metadata row, driven in a real browser.

`tests/test_player_page_link.py` proves the *href* the feeds publish. This proves
what the page built from it, and it is the only guard against the bug the row
exists to avoid: whether a site renders inside a third-party iframe is not
something this page can negotiate, so a refused frame is a blank player with a
correctly built URL. Measured 2026-10-28:

- TMDB and EpGuides render.
- IMDb answers a framed request with its AWS WAF challenge, which loops
  `202 -> challenge.js -> verify -> 202` and ends on "Max challenge attempts
  exceeded" -- while the same request top-level passes.
- youtube.com sends `x-frame-options: SAMEORIGIN`.

So IMDb and Trailer must be new-tab links and EpGuides/TMDB must be iframe
buttons. Asserting the DOM the page builds is also how the query-string
allowlists get a negative case: a hostile `epguides=` must not produce a button.

The page is loaded from `file://` with every network request aborted -- the
assertions are about the elements and the URLs assigned to `player.src`, not
about what those servers answer.
"""
from __future__ import annotations

import functools
import importlib.util
from pathlib import Path
from urllib.parse import urlparse

import pytest

PLAYER_PAGE = Path(__file__).resolve().parents[1] / "player" / "index.html"


def _browser_available() -> bool:
    """Chromium is an optional extra here: the suite must still run (skipped)
    where playwright's browser binary was never downloaded."""
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:  # pragma: no cover - depends on the machine, not the code
        return False
    return True


requires_chromium = pytest.mark.skipif(
    not _browser_available(), reason="playwright chromium is not installed"
)


@functools.lru_cache(maxsize=1)
def _page_url() -> str:
    return PLAYER_PAGE.resolve().as_uri()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.route(
        "http://**", lambda route: route.abort()
    )  # the iframe's embed servers
    page.route("https://**", lambda route: route.abort())
    yield page
    page.close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def _open(page, query: str) -> None:
    page.goto(f"{_page_url()}?{query}", wait_until="domcontentloaded")
    page.wait_for_selector("#externalLinks > *")


def _row(page) -> list[tuple[str, str]]:
    """The metadata row as ``[(tag, label), ...]`` — tag says which kind."""
    return page.eval_on_selector_all(
        "#externalLinks > *", "els => els.map(e => [e.tagName, e.textContent.trim()])"
    )


def _sequence(page) -> list[str]:
    """The row as ``['button:EpGuides', 'a:IMDb ↗', ...]`` — the grouping is the
    contract, so the tags and the labels are asserted together."""
    return [f"{tag.lower()}:{label}" for tag, label in _row(page)]


def _src(page) -> str:
    return page.evaluate("document.getElementById('player').src")


def _labels(page, tag: str) -> list[str]:
    return [label for t, label in _row(page) if t.lower() == tag]


def _manual(page, query: str):
    """Open the page for the manual-lookup assertions. A bare URL renders no
    metadata row, so this waits on the form instead of ``#externalLinks``."""
    page.goto(f"{_page_url()}?{query}", wait_until="domcontentloaded")
    page.wait_for_selector("#manual")


def _row_shown(page) -> bool:
    """The page-URL row takes a line of its own, so it is only put there when it
    has something to hold: a TMDb page in the frame."""
    return page.is_visible(".manual-page")


def _tmdb(page) -> None:
    """Frame the TMDb page, which is what brings the row back. A grab only ever
    comes from a TMDb page, so this is the step the viewer takes first."""
    page.click("#externalLinks > button:text-is('TMDB')")
    page.wait_for_selector(".manual-page:not([hidden])")


def _await_hint(page, text: str) -> str:
    """Wait until the row says exactly `text`, and return what it says.

    A click can still be in flight when the assertion runs — the clipboard read
    and the frame's locate question are both async — so waiting on the words
    beats a fixed sleep, which is what CI proved by failing one of these.
    """
    page.wait_for_function(
        "t => document.getElementById('manualHint').textContent === t", arg=text
    )
    return page.inner_text("#manualHint")


def _clipboard(page, text: str) -> None:
    """What "Get the TMDb id" falls back to when the page-URL field is empty.
    Chromium only grants `clipboard-read` to an origin it trusts and these pages
    load from `file://`, so the method is replaced rather than permitted — the
    handler is unchanged either way."""
    page.evaluate(
        "text => { navigator.clipboard.readText = () => Promise.resolve(text); }", text
    )


def _navigate_inside_frame(page) -> None:
    """What the page observes when the viewer clicks through inside the frame.

    Measured 2026-11-01 against themoviedb.org: a navigation inside a
    cross-origin frame leaves `src` at the address the embedder assigned and
    `contentWindow.location.href` throws SecurityError, so the only thing that
    crosses the boundary is the frame's `load` event, with no address attached.
    Dispatching that event is exactly the signal the page gets, without the
    network.

    The page counts the one load its own `src` assignment causes and takes every
    later one for the viewer's, so an event dispatched while that first load is
    still outstanding is counted as the page's own. The dispatch therefore
    repeats until the row reports the move — a browser sends one event per
    navigation anyway.
    """
    for _ in range(8):
        page.evaluate(
            "document.getElementById('player').dispatchEvent(new Event('load'))"
        )
        if page.inner_text("#manualHint"):
            return
        page.wait_for_timeout(250)
    raise AssertionError("the page never reported the frame as moved")


TV = (
    "tmdb=156933&imdb=tt17677860&type=tv&season=1&episode=8"
    "&epguides=PresumedInnocent&title=Presumed%20Innocent%20%282024%29"
)
MOVIE = "tmdb=575265&imdb=tt9603208&type=movie&title=Rogue%20Mission%20%282025%29"


@requires_chromium
class TestMetadataRow:
    def test_a_series_groups_the_framable_entries_first(self, page):
        _open(page, TV)

        assert _sequence(page) == [
            "button:EpGuides",
            "button:TMDB",
            "button:TMDb Trailer",
            "a:IMDb ↗",
            "a:Trailer ↗",
        ]

    def test_a_movie_gets_no_epguides_entry_at_all(self, page):
        _open(page, MOVIE)

        assert _sequence(page) == [
            "button:TMDB",
            "button:TMDb Trailer",
            "a:IMDb ↗",
            "a:Trailer ↗",
        ]

    def test_the_episode_button_frames_the_episode_page(self, page):
        _open(page, TV)
        page.click("#externalLinks > button:text-is('TMDB')")

        assert _src(page) == "https://www.themoviedb.org/tv/156933/season/1/episode/8"

    def test_the_epguides_button_frames_the_resolved_slug(self, page):
        _open(page, TV)
        page.click("#externalLinks > button:text-is('EpGuides')")

        assert _src(page) == "https://epguides.com/PresumedInnocent/"

    def test_an_underscored_slug_still_frames(self, page):
        """The resolver's own slugs carry underscores — `DoctorWho_2005`,
        `Office_US` — and an allowlist that rejects them silently demotes the
        show to the Google-CSE new-tab search."""
        _open(page, TV.replace("epguides=PresumedInnocent", "epguides=DoctorWho_2005"))
        page.click("#externalLinks > button:text-is('EpGuides')")

        assert _src(page) == "https://epguides.com/DoctorWho_2005/"

    def test_imdb_never_reaches_the_iframe(self, page):
        """The blank-player regression this row exists for: IMDb's bot challenge
        cannot complete in a frame, so its entry must stay a new-tab link."""
        _open(page, TV)
        imdb = page.locator("#externalLinks a[href*='imdb.com']")

        assert imdb.get_attribute("target") == "_blank"
        assert imdb.get_attribute("rel") == "noopener noreferrer"
        assert imdb.get_attribute("href") == "https://www.imdb.com/title/tt17677860/"
        page.click("#externalLinks > button:text-is('TMDB')")
        assert "imdb.com" not in _src(page)

    def test_an_unresolved_slug_falls_back_to_the_site_search_link(self, page):
        _open(page, TV.replace("&epguides=PresumedInnocent", ""))

        # The search link cannot be framed, so it loses the first group's place
        # rather than moving the iframe buttons after it.
        assert _sequence(page) == [
            "a:EpGuides ↗",
            "button:TMDB",
            "button:TMDb Trailer",
            "a:IMDb ↗",
            "a:Trailer ↗",
        ]

    def test_a_hostile_slug_builds_no_button_and_stays_encoded(self, page):
        """Negative case for the query-string allowlist: the payload is not a
        slug, so it must not become an iframe src, and a payload in the title
        stays percent-encoded wherever it is used."""
        payload = "%3Cscript%3Eboom%3C%2Fscript%3E"
        _open(
            page,
            f"tmdb=1&imdb=tt2&type=tv&season=1&episode=2"
            f"&epguides={payload}&title={payload}",
        )

        # No EpGuides button: the row's TMDb entries carry no slug at all.
        assert _labels(page, "button") == ["TMDB", "TMDb Trailer"]
        page.click("#externalLinks > button:text-is('TMDB')")
        assert _src(page) == "https://www.themoviedb.org/tv/1/season/1/episode/2"

        hrefs = page.eval_on_selector_all(
            "#externalLinks a", "els => els.map(e => e.href)"
        )
        assert all("<script>" not in href for href in hrefs)
        # The payload reaches only the two links that quote the title, encoded.
        with_payload = [href for href in hrefs if payload in href]
        assert all("<script>" not in href for href in hrefs)
        assert len(with_payload) == 2

    def test_the_active_state_is_shared_across_both_rows(self, page):
        _open(page, TV)
        page.click("#externalLinks > button:text-is('EpGuides')")

        assert page.locator(".server-btn.active").all_text_contents() == ["EpGuides"]

        page.click("#serverButtons > button:nth-child(2)")

        second = page.locator("#serverButtons > button:nth-child(2)")
        assert page.locator(".server-btn.active").all_text_contents() == [
            second.text_content()
        ]

    def test_the_info_bar_shows_the_folded_title_and_the_episode(self, page):
        _open(page, TV)

        assert (
            page.inner_text("#info") == "Presumed Innocent (2024) • S1E8 • TMDB:156933"
        )


def _fields(page) -> dict[str, str]:
    return page.evaluate(
        "() => Object.fromEntries([...document.getElementById('manual').elements]"
        ".filter(e => e.name).map(e => [e.name, e.value]))"
    )


@requires_chromium
class TestManualLookup:
    def test_the_form_is_prefilled_from_the_current_query(self, page):
        """The form mirrors the URL rather than starting blank, so Load replays
        what the page is already playing."""
        _manual(page, TV)

        assert _fields(page) == {
            "tmdb": "156933",
            "imdb": "tt17677860",
            "type": "tv",
            "season": "1",
            "episode": "8",
            "pageurl": "",
        }

    def test_a_bare_page_still_offers_the_form(self, page):
        _manual(page, "")

        assert _fields(page)["tmdb"] == ""
        assert page.inner_text("#manualHint") == ""

    def test_the_episode_fields_follow_the_type(self, page):
        _manual(page, TV)
        assert page.is_visible("#manualEpisode")
        _manual(page, MOVIE)

        assert not page.is_visible("#manualEpisode")

    def test_picking_tv_reveals_the_episode_fields(self, page):
        _manual(page, MOVIE)
        page.select_option("#manual select[name='type']", "tv")

        assert page.is_visible("#manualEpisode")

    def test_submitting_navigates_to_what_it_types(self, page):
        _manual(page, "")
        page.fill("#manual input[name='tmdb']", "211288")
        page.select_option("#manual select[name='type']", "tv")
        page.fill("#manual input[name='season']", "2")
        page.fill("#manual input[name='episode']", "5")
        page.click("#manual button[type='submit']")
        page.wait_for_url(lambda url: "season=2" in url)

        assert page.url.split("?", 1)[1] == "tmdb=211288&type=tv&season=2&episode=5"
        assert page.inner_text("#info") == "TV • S2E5 • TMDB:211288"

    def test_an_id_less_submit_refuses_and_says_why(self, page):
        """Without an id there is nothing to build a server URL from, and the
        page would reload into its own "no parameters" state. The hint is the
        only answer."""
        _manual(page, "")
        page.click("#manual button[type='submit']")

        assert page.inner_text("#manualHint") == "Enter a TMDb or IMDb id."
        assert "tmdb=" not in page.url

    def test_a_non_numeric_id_is_not_a_navigation(self, page):
        """Negative case: the ids are constrained where they are entered, so a
        typed payload never reaches the query string the iframe URLs are built
        from. Chromium blocks the submit on validation instead."""
        _manual(page, "")
        page.fill("#manual input[name='tmdb']", "211288")
        page.fill("#manual input[name='imdb']", "tt<script>")
        page.click("#manual button[type='submit']")

        assert page.evaluate(
            "document.querySelector(\"#manual input[name='imdb']\").validity.patternMismatch"
        )
        assert "script" not in page.url

    def test_a_manual_imdb_only_lookup_reaches_the_player(self, page):
        _manual(page, "")
        page.fill("#manual input[name='imdb']", "tt9603208")
        page.click("#manual button[type='submit']")
        page.wait_for_url(lambda url: "imdb=" in url)

        assert page.url.split("?", 1)[1] == "imdb=tt9603208&type=movie"
        # TMDB is offered even with no TMDb id to frame: it points at the browse
        # list, the page "Get the TMDb id" then reads an id out of.
        assert _sequence(page) == ["button:TMDB", "a:IMDb ↗"]
        page.click("#externalLinks > button:text-is('TMDB')")
        assert _src(page) == "https://www.themoviedb.org/movie"

    def test_a_junk_season_never_reaches_the_query_string(self, page):
        """Negative case for the second layer. Season and episode keep their
        values when the type is flipped back to Movie, and a control that is not
        rendered is not one the browser will validate — so the submit handler
        checks the same allowlists `getParams()` reads with, and refuses."""
        _manual(page, MOVIE.replace("type=movie", "type=tv&season=1&episode=2"))
        payload = '1"><img src=x onerror=alert(1)>'
        page.fill("#manual input[name='season']", payload)
        page.select_option("#manual select[name='type']", "movie")
        url_at_load = page.url

        page.click("#manual button[type='submit']")

        assert page.url == url_at_load, "the junk marker was submitted"
        assert "onerror" not in page.url

        # And the handler's own refusal, with validation bypassed: dispatching the
        # event directly is the only way to reach the JS layer deterministically,
        # because the browser may block the click before the handler runs.
        page.evaluate(
            "document.getElementById('manual')"
            ".dispatchEvent(new Event('submit', {cancelable: true}))"
        )

        assert page.url == url_at_load
        assert page.inner_text("#manualHint") != ""

    def test_the_title_is_not_carried_onto_new_ids(self, page):
        """The header text and the trailer search both come from `title`, and it
        belongs to whatever the ids name. Replay it after swapping the TMDb id
        and the page plays one show under another's name."""
        _manual(page, TV)
        page.fill("#manual input[name='tmdb']", "211288")
        page.click("#manual button[type='submit']")
        page.wait_for_url(lambda url: "tmdb=211288" in url)

        assert "title=" not in page.url and "epguides=" not in page.url
        assert page.inner_text("#info") == "TV • S1E8 • TMDB:211288"
        assert _sequence(page) == [
            "button:TMDB",
            "button:TMDb Trailer",
            "a:IMDb ↗",
        ]


@requires_chromium
class TestDefaultBrowse:
    """Opened with nothing to play, the page is a TMDb browser rather than a
    blank iframe: the movie list frames, TMDB is the active button, and the
    server row stays out because no server can build an embed without an id."""

    def _bare(self, page):
        page.goto(_page_url(), wait_until="domcontentloaded")
        page.wait_for_selector("#externalLinks > *")

    def test_the_frame_opens_on_the_tmdb_movie_list(self, page):
        self._bare(page)

        assert page.is_visible("#player")
        assert _src(page) == "https://www.themoviedb.org/movie"

    def test_only_the_tmdb_button_is_offered_and_it_is_active(self, page):
        self._bare(page)

        assert _sequence(page) == ["button:TMDB"]
        assert page.evaluate(
            "document.querySelector('#externalLinks > button.active').textContent.trim()"
        ) == "TMDB"
        assert page.eval_on_selector_all(
            "#serverButtons button", "els => els.length"
        ) == 0

    def test_the_new_tab_fallback_offers_the_same_page(self, page):
        """A frame that is refused looks identical to a blank one, so the
        escape hatch has to point at what the frame was given."""
        self._bare(page)
        fallback = page.locator("#openFallback")

        assert fallback.is_visible()
        assert fallback.get_attribute("href") == "https://www.themoviedb.org/movie"

    def test_the_info_line_says_what_to_do_next(self, page):
        self._bare(page)

        assert page.inner_text("#info") == (
            'Browse TMDb, then use the page URL box to load it here'
        )


@requires_chromium
class TestTmdbTrailer:
    def test_a_movie_frames_its_tmdb_trailer_list(self, page):
        _open(page, MOVIE)
        page.click("#externalLinks > button:text-is('TMDb Trailer')")

        assert _src(page) == (
            "https://www.themoviedb.org/movie/575265/videos?active_nav_item=Trailers"
        )

    def test_a_series_frames_the_tv_path(self, page):
        _open(page, TV)
        page.click("#externalLinks > button:text-is('TMDb Trailer')")

        assert _src(page) == (
            "https://www.themoviedb.org/tv/156933/videos?active_nav_item=Trailers"
        )

    def test_no_trailer_button_without_a_tmdb_id(self, page):
        """The path needs an id of TMDb's own: an IMDb-only item has none, and a
        `/movie/tt…/videos` URL is not a page."""
        page.goto(f"{_page_url()}?imdb=tt9603208&type=movie", wait_until="domcontentloaded")
        page.wait_for_selector("#externalLinks > *")

        assert "TMDb Trailer" not in _labels(page, "button")


@requires_chromium
class TestGetTheTmdbId:
    """A TMDb page address becomes player parameters. The framed page's own
    address cannot be read — the same-origin policy hides a cross-origin
    frame's location from its embedder — so the address is pasted into the page
    URL field, and an empty field falls back to the clipboard. The one thing
    that does cross the boundary is the frame's `load` event, which is how a
    viewer's own click inside the frame is noticed: the address the page handed
    over stops being treated as the page on screen, so a click can never read
    the landing page's id back out."""

    def test_the_button_is_named_for_the_id_it_brings(self, page):
        """"Grab from page" promised the address of the page in the frame, which
        this page cannot see. The label names what the click produces: the TMDb id
        read out of the URL in the box above it."""
        _manual(page, TV)
        _tmdb(page)

        assert page.inner_text("#grabPage") == "Get the TMDb id"

    @pytest.mark.parametrize(
        "clip, expected",
        [
            ("https://www.themoviedb.org/movie/1058424", "tmdb=1058424&type=movie"),
            (
                "https://www.themoviedb.org/movie/1101412-the-magic-brush",
                "tmdb=1101412&type=movie",
            ),
            ("https://www.themoviedb.org/tv/1416", "tmdb=1416&type=tv"),
            (
                "https://www.themoviedb.org/tv/1416-grey-s-anatomy",
                "tmdb=1416&type=tv",
            ),
            (
                "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23",
                "tmdb=1416&type=tv&season=23",
            ),
            (
                "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23/episode/3",
                "tmdb=1416&type=tv&season=23&episode=3",
            ),
        ],
    )
    def test_a_page_address_reads_into_parameters(self, page, clip, expected):
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, clip)

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert page.url.split("?", 1)[1] == expected

    def test_it_replaces_the_id_already_loaded(self, page):
        """The reported bug: with a TMDb id in the field, Grab used to leave it
        there, because Enter in the address field submitted the lookup form
        instead of grabbing."""
        _manual(page, "tmdb=550&type=movie")
        _tmdb(page)
        _clipboard(
            page, "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23/episode/3"
        )

        page.click("#grabPage")
        page.wait_for_url(lambda url: "season=23" in url)

        assert _fields(page) == {
            "tmdb": "1416",
            "imdb": "",
            "type": "tv",
            "season": "23",
            "episode": "3",
            "pageurl": "",
        }
        assert _src(page) == "https://cinesrc.st/embed/tv/1416?s=23&e=3"

    def test_a_season_page_loads_without_an_episode(self, page):
        """Half a marker is deliberate here: TMDb has season landing pages, and
        the manual form refuses one only because the feeds never write it."""
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "season=" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1416&type=tv&season=23"

    def test_the_address_box_and_grab_are_a_row_of_their_own(self, page):
        """The box carries a whole TMDb path, so sharing the ids row pushed the
        lookup off the header. Below the ids and beside its own button is the
        layout; the widths are what the box needs to stay readable."""
        _manual(page, TV)
        _tmdb(page)
        box = page.locator("#manual input[name='pageurl']").bounding_box()
        grab = page.locator("#grabPage").bounding_box()
        load = page.locator("#manual button[type='submit']").bounding_box()

        assert box["y"] > load["y"] + load["height"] - 1
        assert abs(grab["y"] - box["y"]) < box["height"]
        assert box["width"] > 200

    def test_a_grab_clears_every_field_the_page_was_opened_with(self, page):
        """The resurfaced bug: the id field kept the previous title's values, so a
        later click on Load replayed them. Grab drops all of them and enters only
        what the address says — including the IMDb id and the season pair, which
        the address never mentions."""
        _manual(page, f"{TV}&servers=vidlink")
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://www.themoviedb.org/movie/1058424")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert _fields(page) == {
            "tmdb": "1058424",
            "imdb": "",
            "type": "movie",
            "season": "",
            "episode": "",
            "pageurl": "",
        }

    def test_load_defers_to_the_address_in_the_box(self, page):
        """Two ways of naming the same title, and the box is the fresher one: a
        click on Load with a stale id field and an address in the box must not
        reload the stale id."""
        _manual(page, "tmdb=550&type=movie")
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://www.themoviedb.org/tv/1416/season/23")

        page.click("#manual button[type='submit']")
        page.wait_for_url(lambda url: "season=23" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1416&type=tv&season=23"

    @pytest.mark.parametrize(
        "clip, hint",
        [
            ("https://example.com/movie/1", "Not a TMDb movie or TV page URL."),
            ("", "Nothing to read — paste or copy a TMDb page URL first."),
        ],
    )
    def test_a_non_tmdb_clipboard_says_so_and_does_not_move(self, page, clip, hint):
        """Negative case: an address from anywhere else must not become an id,
        and a refusal must not reload the page the viewer is watching."""
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, clip)
        url_before = page.url

        page.click("#grabPage")
        page.wait_for_function(
            "hint => document.getElementById('manualHint').textContent === hint",
            arg=hint,
        )

        assert page.url == url_before
        # The clipboard is a source the viewer cannot otherwise see, so what it
        # held lands in the field — a stale address becomes a visible one. Nothing
        # here moves the frame, so the assignment's own load is the only one and
        # the box keeps what the click put in it.
        assert _fields(page)["pageurl"] == clip

    def test_a_stale_clipboard_names_the_page_it_already_plays(self, page):
        """The reported stall: the viewer browses on to another title and clicks
        Grab, but the clipboard still holds the address copied for the previous
        one, so the same id loads again and again and the button looks dead. The
        address is now in the field and the hint says what happened."""
        _manual(page, "tmdb=1058424&type=movie")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")
        url_before = page.url

        page.click("#grabPage")

        assert _await_hint(page, "Already loaded: movie/1058424") == (
            "Already loaded: movie/1058424"
        )
        assert page.url == url_before
        assert _fields(page)["tmdb"] == "1058424"
        assert _fields(page)["pageurl"] == (
            "https://www.themoviedb.org/movie/1058424-the-magic-brush"
        )

    def test_a_different_address_still_loads_from_the_clipboard(self, page):
        """The other side of the same check: only an address that names the
        current parameters is refused as a no-op."""
        _manual(page, "tmdb=1058424&type=movie")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/tv/1668-friends")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1668" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1668&type=tv"

    def test_a_lookalike_host_is_not_tmdb(self, page):
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, "https://themoviedb.org.tv.evil.test/movie/666")
        url_before = page.url

        page.click("#grabPage")

        assert _await_hint(page, "Not a TMDb movie or TV page URL.") == (
            "Not a TMDb movie or TV page URL."
        )
        assert page.url == url_before

    def test_the_field_is_read_before_the_clipboard(self, page):
        """The field is the reliable path: it needs no clipboard permission and
        no focus dance, and it is what a viewer reaches for when the framed page
        will not hand over its own address."""
        _manual(page, "")
        _tmdb(page)
        page.fill(
            "#manual input[name='pageurl']",
            "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23/episode/3",
        )

        page.click("#grabPage")
        page.wait_for_url(lambda url: "episode=3" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1416&type=tv&season=23&episode=3"
        assert page.inner_text("#manualHint") == ""

    def test_enter_in_the_field_grabs_instead_of_submitting_the_lookup(self, page):
        """The reported bug: the field sits inside the lookup form, so Enter used
        to submit that form — reloading on the id already in the field and
        dropping the URL just pasted."""
        _manual(page, "tmdb=550&type=movie")
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://www.themoviedb.org/movie/1058424")

        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1058424&type=movie"

    def test_a_field_value_that_is_not_an_address_is_refused(self, page):
        """Negative case for the field path, which is unvalidated text: only the
        host and the digit allowlists in `parseTmdbPageUrl` decide what loads, and
        a refusal leaves the row empty rather than on the old title."""
        _manual(page, "tmdb=550&type=movie")
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://example.com/movie/1")
        url_before = page.url

        page.click("#grabPage")

        assert _await_hint(page, "Not a TMDb movie or TV page URL.") == (
            "Not a TMDb movie or TV page URL."
        )
        assert page.url == url_before
        assert _fields(page)["tmdb"] == ""

    def test_a_grab_with_nothing_to_read_empties_the_lookup(self, page):
        """The reported case: the box is empty and the clipboard is off-limits, so
        there is nothing to grab. An id left standing in the field is what made
        the button look broken, so Grab clears the row and says why it stopped."""
        _manual(page, TV)
        _tmdb(page)
        _clipboard(page, "")
        url_before = page.url

        page.click("#grabPage")

        assert _await_hint(
            page, "Nothing to read — paste or copy a TMDb page URL first."
        ) == "Nothing to read — paste or copy a TMDb page URL first."
        assert page.url == url_before
        assert _fields(page) == {
            "tmdb": "",
            "imdb": "",
            "type": "movie",
            "season": "",
            "episode": "",
            "pageurl": "",
        }
        assert not page.is_visible("#manualEpisode")

    def test_a_bare_field_falls_back_to_the_clipboard(self, page):
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1058424&type=movie"

    def test_a_navigation_inside_the_frame_empties_the_address_box(self, page):
        """The row cannot follow the frame, so it stops vouching for what it
        holds: an address typed for the page the viewer has left is dropped the
        moment the frame loads somewhere else."""
        _manual(page, TV)
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://www.themoviedb.org/movie/999")

        _navigate_inside_frame(page)

        assert _fields(page)["pageurl"] == ""
        assert page.inner_text("#manualHint") == (
            "The frame moved - copy or paste the address of the page you are on."
        )

    def test_the_page_the_frame_started_on_is_refused_after_a_navigation(self, page):
        """The reported bug: the clipboard still holds the address copied for the
        landing page, and the button used to load it again and again. Once the
        frame has moved, that address is named for what it is instead."""
        _manual(page, "tmdb=1058424&type=movie")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")
        _navigate_inside_frame(page)
        url_before = page.url

        page.click("#grabPage")
        page.wait_for_function(
            "document.getElementById('manualHint').textContent"
            ".startsWith('That is the page the frame started on')"
        )

        assert page.url == url_before
        assert _fields(page)["tmdb"] == ""
        # What the box holds afterwards is deliberately not asserted: the click
        # writes the refused address into it, and a load that arrives later — the
        # ones `_navigate_inside_frame` fired, the frame's own error page — clears
        # it again, because an address for a page the frame has left is evidence
        # of nothing. Both orders are correct, and CI is the one that is slow
        # enough to see the other one.

    def test_a_later_frame_load_does_not_replace_the_answer(self, page):
        """The refusal is about the state the viewer just asked about; a load that
        arrives after it (a slow redirect, the frame's own error page) must not
        take the words away. CI caught this as a flaky assertion, not as a bug."""
        _manual(page, TV)
        _tmdb(page)
        page.fill("#manual input[name='pageurl']", "https://example.com/movie/1")
        page.click("#grabPage")
        page.wait_for_function(
            "document.getElementById('manualHint').textContent"
            " === 'Not a TMDb movie or TV page URL.'"
        )

        # Three loads, so at least one is the viewer's whatever the frame's own
        # assignment still owes the counter.
        for _ in range(3):
            page.evaluate(
                "document.getElementById('player').dispatchEvent(new Event('load'))"
            )
            page.wait_for_timeout(250)

        assert page.inner_text("#manualHint") == "Not a TMDb movie or TV page URL."

    def test_a_movie_page_reached_inside_the_frame_loads_its_own_id(self, page):
        """The other side of that check, and what the button is for: the copy made
        after the viewer navigated is the page they are on, so it loads."""
        _manual(page, TV)
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")
        _navigate_inside_frame(page)

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1058424&type=movie"

    def test_a_tv_page_reached_inside_the_frame_keeps_its_season_and_episode(
        self, page
    ):
        _manual(page, MOVIE)
        _tmdb(page)
        _clipboard(
            page, "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23/episode/3"
        )
        _navigate_inside_frame(page)

        page.click("#grabPage")
        page.wait_for_url(lambda url: "episode=3" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1416&type=tv&season=23&episode=3"

    @pytest.mark.parametrize(
        "clip",
        [
            "https://www.themoviedb.org/search?query=dune",
            "https://www.themoviedb.org/person/6384",
            "https://www.themoviedb.org/movie",
        ],
    )
    def test_a_tmdb_page_with_no_id_says_so_instead_of_keeping_the_old_one(
        self, page, clip
    ):
        """A TMDb page that is not a title page is a different answer from "that
        is not TMDb", and neither one may leave the previous id in the row."""
        _manual(page, TV)
        _tmdb(page)
        _clipboard(page, clip)
        _navigate_inside_frame(page)
        url_before = page.url

        page.click("#grabPage")
        # The click answers asynchronously (clipboard read, or the frame's own
        # timeout), so wait for its words: a fixed sleep passed locally and raced
        # on CI. A later frame load must not replace them either — that is what
        # made this test the one that caught the hint clobbering.
        page.wait_for_function(
            "document.getElementById('manualHint').textContent"
            " === 'That TMDb page has no movie or TV id.'"
        )

        assert page.url == url_before
        assert _fields(page)["tmdb"] == ""

    def test_the_players_own_page_is_not_a_navigation_of_the_viewers(self, page):
        """Negative case for the load counter: the load of the address the page
        assigned belongs to the page, so clicking the same button again must not
        report the frame as moved."""
        _manual(page, TV)
        _tmdb(page)
        page.wait_for_timeout(300)
        _tmdb(page)
        page.wait_for_timeout(300)

        assert page.inner_text("#manualHint") == ""
        assert _src(page) == "https://www.themoviedb.org/tv/156933/season/1/episode/8"

    def test_a_grab_resets_every_parameter_the_page_was_opened_with(self, page):
        """A feed URL, then TMDb, then a different title: only what the grabbed
        page says survives. `imdb`, `epguides`, `title` and `servers` are not
        carried over — they name the old item, the row rebuilds without them, and
        the header stops calling a movie by the previous show's name."""
        _manual(page, f"{TV}&servers=vidlink")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1058424&type=movie"
        assert _fields(page) == {
            "tmdb": "1058424",
            "imdb": "",
            "type": "movie",
            "season": "",
            "episode": "",
            "pageurl": "",
        }
        assert _sequence(page) == ["button:TMDB", "button:TMDb Trailer"]
        assert page.inner_text("#info") == "Movie • TMDB:1058424"

    def test_grabbing_an_episode_page_from_a_movie_resets_the_other_direction(
        self, page
    ):
        """Same reset with the types flipped, including the episode fields the
        movie item had left empty."""
        _manual(page, MOVIE)
        _tmdb(page)
        _clipboard(
            page, "https://www.themoviedb.org/tv/1416-grey-s-anatomy/season/23/episode/3"
        )

        page.click("#grabPage")
        page.wait_for_url(lambda url: "episode=3" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1416&type=tv&season=23&episode=3"
        assert _fields(page)["imdb"] == ""
        assert page.is_visible("#manualEpisode")


@requires_chromium
class TestPageRowVisibility:
    """The page-URL row takes a line of its own, so it spends the ones it does not
    have. It belongs to the frame: it comes back when the player puts a TMDb page
    in there, and stays out when the frame holds an embed."""

    def test_a_media_server_leaves_the_row_out(self, page):
        _open(page, TV)

        assert not _row_shown(page)
        assert _src(page) == "https://cinesrc.st/embed/tv/156933?s=1&e=8"

    def test_the_tmdb_button_brings_the_row_back(self, page):
        _open(page, TV)

        _tmdb(page)

        assert _row_shown(page)
        assert _src(page) == "https://www.themoviedb.org/tv/156933/season/1/episode/8"

    def test_the_row_is_empty_rather_than_claiming_to_be_the_frame(self, page):
        """The box takes a TMDb page the viewer typed, pasted or copied into it. It
        never shows the frame's own address, because the same-origin policy keeps a
        cross-origin frame's location from its embedder — a box that looked like an
        address bar would go on showing the page from five clicks ago."""
        _open(page, TV)
        _tmdb(page)

        assert _fields(page)["pageurl"] == ""

    def test_the_trailer_button_shows_it_too(self, page):
        """The other button that frames TMDb, so the row follows it the same way."""
        _open(page, MOVIE)
        assert not _row_shown(page)

        page.click("#externalLinks > button:text-is('TMDb Trailer')")
        page.wait_for_selector(".manual-page:not([hidden])")

        assert _src(page) == (
            "https://www.themoviedb.org/movie/575265/videos?active_nav_item=Trailers"
        )
        assert _row_shown(page)

    def test_a_page_that_is_not_tmdb_leaves_the_row_out(self, page):
        """Negative case: EpGuides frames like any embed, but there is nothing for
        "Get the TMDb id" to parse out of it."""
        _open(page, TV)

        page.click("#externalLinks > button:text-is('EpGuides')")

        assert _src(page) == "https://epguides.com/PresumedInnocent/"
        assert not _row_shown(page)

    def test_a_server_button_hides_the_row_again(self, page):
        _open(page, TV)
        _tmdb(page)

        page.click("#serverButtons > button:nth-child(1)")

        assert not _row_shown(page)

    def test_browse_mode_shows_the_row(self, page):
        """No ids, so no embed: the frame opens on TMDb's movie list and the row is
        there for the very first grab."""
        _open(page, "")

        assert _row_shown(page)
        assert _src(page) == "https://www.themoviedb.org/movie"


def _server_buttons(page) -> list[str]:
    return page.eval_on_selector_all(
        "#serverButtons button", "els => els.map(e => e.textContent.trim())"
    )


# The same film as MOVIE with the IMDb id left out: everything the feed could
# resolve but the id the two IMDb-slot providers need.
MOVIE_NO_IMDB = "tmdb=575265&type=movie&title=Rogue%20Mission%20%282025%29"


@requires_chromium
class TestImdbSlotServers:
    """VidSrc and VidAPI put the id in the IMDb slot of their embed path. A bare
    TMDb number only reaches those sites as `tt<tmdb>` — a TMDb id wearing an
    IMDb prefix — and the report was that it does not resolve there, so the two
    sit at the end of the row and are not offered at all without an IMDb id."""

    def test_the_imdb_slot_servers_come_last(self, page):
        _open(page, TV)

        assert _server_buttons(page)[-2:] == ["VidSrc", "VidAPI"]

    def test_the_rest_of_the_row_keeps_its_order(self, page):
        """Negative case for the reorder: moving two servers must not shuffle the
        eight that a TMDb id serves on its own."""
        _open(page, TV)

        assert _server_buttons(page) == [
            "CineSrc",
            "VidSrc.buzz",
            "VidLink",
            "VEmbed",
            "VidNest",
            "VidFast",
            "2Embed",
            "MedMedia03",
            "VidSrc",
            "VidAPI",
        ]

    def test_a_title_with_only_a_tmdb_id_offers_neither(self, page):
        _open(page, MOVIE_NO_IMDB)

        buttons = _server_buttons(page)
        assert "VidSrc" not in buttons
        assert "VidAPI" not in buttons
        assert len(buttons) == 8

    def test_the_server_that_remains_first_still_plays(self, page):
        """The trimmed row is not just shorter — it still loads its own first
        entry, which is what the frame shows on arrival."""
        _open(page, MOVIE_NO_IMDB)

        assert page.locator(".server-btn.active").all_text_contents() == ["CineSrc"]
        assert _src(page) == "https://cinesrc.st/embed/movie/575265"

    def test_an_imdb_id_brings_both_back(self, page):
        _open(page, f"{MOVIE_NO_IMDB}&imdb=tt9603208")

        assert _server_buttons(page)[-2:] == ["VidSrc", "VidAPI"]
        assert len(_server_buttons(page)) == 10

    def test_asking_only_for_an_imdb_server_without_an_imdb_id_plays_nothing(self, page):
        """`?servers=` is the e2e override, and an override that survives no more
        than the ids is the empty row the page already knows how to hide — no
        buttons, no frame, no half-built embed."""
        _manual(page, f"{MOVIE_NO_IMDB}&servers=vidapi")

        assert _server_buttons(page) == []
        assert not page.is_visible("#player")


# The reporter userscript, injected into the fake TMDb pages below the way a
# userscript manager would inject it, and read from the file the page documents.
REPORTER = (
    Path(__file__).resolve().parents[1] / "player" / "tmdb-frame-reporter.user.js"
).read_text()

# Every fake page links to the others, so a test can click through the frame the
# way a viewer does. `#push` moves the address without a page load — the case no
# load-event trick can see, and the reason the button asks the frame instead.
FAKE_TMDB_BODY = (
    '<a id="movie" href="/movie/550-fight-club">movie</a>'
    '<a id="tv" href="/tv/1416-greys-anatomy/season/23/episode/3">episode</a>'
    '<a id="person" href="/person/287-brad-pitt">person</a>'
    '<a id="plain" href="/plain">no reporter here</a>'
    "<button id=\"push\" onclick=\"history.pushState({}, '', '/tv/1668-friends')\">"
    "push</button>"
)


def _fake_tmdb(page, *, reporter: bool = True) -> None:
    """Answer the frame's TMDb requests with small pages on the *real* origin,
    which is the point: the player trusts nothing else. `/plain` never carries
    the reporter, standing in for a page it cannot run on."""

    def handle(route):
        path = urlparse(route.request.url).path
        script = f"<script>{REPORTER}</script>" if reporter and path != "/plain" else ""
        route.fulfill(
            status=200,
            content_type="text/html",
            body=f"<!doctype html><title>{path}</title>{script}{FAKE_TMDB_BODY}",
        )

    page.route("https://www.themoviedb.org/**", handle)


def _reporter_frame(page):
    return next(
        f for f in page.frames if f.url.startswith("https://www.themoviedb.org")
    )


def _browse(page, *, reporter: bool = True) -> None:
    """Open the bare player, whose frame starts on TMDb's movie list."""
    _fake_tmdb(page, reporter=reporter)
    _manual(page, "")
    page.wait_for_selector(".manual-page:not([hidden])")
    page.frame_locator("#player").locator("#movie").wait_for()
    if reporter:
        page.wait_for_function("frameReporterSeen === true")


def _go(page, link: str, path: str) -> None:
    """Click a link inside the frame and wait until the frame has arrived."""
    page.frame_locator("#player").locator(f"#{link}").click()
    frame = _reporter_frame(page)
    frame.wait_for_url(f"**{path}")
    frame.wait_for_selector("#movie")


def _query(page) -> str:
    return page.url.split("?", 1)[1] if "?" in page.url else ""


@requires_chromium
class TestGetTheTmdbIdFromTheFrame:
    """With the reporter userscript inside the frame, the button reads the page
    the viewer is on now. The frame is asked at click time, so an answer cannot
    be the page the viewer started on, and a page with no id says so."""

    def test_it_returns_the_page_in_the_frame_not_the_one_it_started_on(self, page):
        _browse(page)
        _go(page, "movie", "/movie/550-fight-club")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=550&type=movie"

    def test_an_episode_page_gives_series_season_and_episode(self, page):
        _browse(page)
        _go(page, "tv", "/tv/1416-greys-anatomy/season/23/episode/3")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=1416&type=tv&season=23&episode=3"

    def test_an_address_changed_without_a_page_load_is_the_current_one(self, page):
        """The case the load signal cannot see: `pushState` moves the address and
        fires no `load` at all. Asking the frame at click time is unaffected."""
        _browse(page)
        page.frame_locator("#player").locator("#push").click()
        _reporter_frame(page).wait_for_url("**/tv/1668-friends")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=1668&type=tv"

    def test_a_page_with_no_id_says_so_and_does_not_move(self, page):
        _browse(page)
        _go(page, "person", "/person/287-brad-pitt")
        url_before = page.url

        page.click("#grabPage")
        page.wait_for_function(
            "document.getElementById('manualHint').textContent !== ''"
        )

        assert page.inner_text("#manualHint") == (
            "No movie or TV id on this TMDb page (/person/287-brad-pitt)."
        )
        assert page.url == url_before
        fields = _fields(page)
        assert (fields["tmdb"], fields["imdb"], fields["season"], fields["episode"]) == (
            "",
            "",
            "",
            "",
        )

    def test_the_id_follows_every_hop(self, page):
        """No id on one page must not leave anything behind for the next."""
        _browse(page)
        _go(page, "person", "/person/287-brad-pitt")
        page.click("#grabPage")
        page.wait_for_function(
            "document.getElementById('manualHint').textContent !== ''"
        )

        _go(page, "movie", "/movie/550-fight-club")
        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=550&type=movie"

    def test_a_clipboard_left_over_is_not_read_when_the_frame_answers(self, page):
        _browse(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")
        _go(page, "tv", "/tv/1416-greys-anatomy/season/23/episode/3")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=1416&type=tv&season=23&episode=3"

    def test_the_frame_answer_beats_the_box_too(self, page):
        """The box exists because the frame could not be read; with a reporter it
        is the frame that answers, and the box is only where the answer shows."""
        _browse(page)
        page.fill("#manual input[name='pageurl']", "https://www.themoviedb.org/movie/1058424")
        _go(page, "movie", "/movie/550-fight-club")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=550&type=movie"

    def test_a_frame_that_stops_answering_returns_no_id_at_all(self, page):
        """The negative case for staleness: a reporter was seen, then the frame
        went somewhere it cannot run. The old clipboard must not stand in."""
        _browse(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424-the-magic-brush")
        _go(page, "plain", "/plain")
        url_before = page.url

        page.click("#grabPage")
        # The click answers after the frame's timeout, so wait for its own words
        # rather than for "some hint" — a refusal of any kind would say nothing
        # about which one arrived.
        page.wait_for_function(
            "document.getElementById('manualHint').textContent"
            ".startsWith('The TMDb frame did not answer')"
        )

        assert page.url == url_before
        assert _fields(page)["tmdb"] == ""

    def test_without_the_reporter_the_clipboard_path_is_unchanged(self, page):
        _browse(page, reporter=False)
        _clipboard(page, "https://www.themoviedb.org/tv/1668-friends")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=" in url)

        assert _query(page) == "tmdb=1668&type=tv"
        assert page.evaluate("frameReporterSeen") is False

    def test_a_message_that_is_not_from_the_frame_is_ignored(self, page):
        """Same shape, wrong sender: the player itself cannot vouch for TMDb."""
        _browse(page, reporter=False)

        page.evaluate(
            "window.postMessage({type: 'rss-player:location', id: 1,"
            " href: 'https://www.themoviedb.org/movie/666'}, '*')"
        )
        page.wait_for_timeout(300)

        assert page.evaluate("frameReporterSeen") is False


@requires_chromium
class TestReporterAnswersOnlyThePlayer:
    """The reporter hands a page's address to whoever embeds TMDb and asks, so it
    answers only the origins it lists."""

    ASK = (
        '<iframe id="f" src="https://www.themoviedb.org/movie/550-fight-club"></iframe>'
        "<script>window.replies = [];"
        "addEventListener('message', e => replies.push(e.data));"
        "f.onload = () => f.contentWindow.postMessage("
        "{type: 'rss-player:locate', id: 7}, 'https://www.themoviedb.org');</script>"
    )

    def _ask_from(self, page, origin: str):
        _fake_tmdb(page)
        page.route(
            f"{origin}/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=self.ASK
            ),
        )
        page.goto(f"{origin}/", wait_until="load")
        page.wait_for_timeout(500)
        return page.evaluate("window.replies")

    def test_a_listed_origin_gets_the_frames_address(self, page):
        """The control: without it the refusal below would pass on a dead script."""
        assert self._ask_from(page, "http://localhost:8000") == [
            {
                "type": "rss-player:location",
                "id": 7,
                "href": "https://www.themoviedb.org/movie/550-fight-club",
            }
        ]

    def test_any_other_origin_gets_nothing(self, page):
        assert self._ask_from(page, "http://evil.test") == []


TAMPERMONKEY_URL = "https://www.tampermonkey.net/"
REPORTER_URL = (
    "https://raw.githubusercontent.com/ibaciu6/rss-generator/main/player/"
    "tmdb-frame-reporter.user.js"
)


@requires_chromium
class TestReporterInstallLinks:
    """The row that asks for a pasted address also offers the way out of pasting:
    the userscript manager and the script itself, on the box's own line."""

    def test_both_links_sit_beside_the_box_and_leave_the_page(self, page):
        _manual(page, TV)
        _tmdb(page)

        links = page.eval_on_selector_all(
            ".manual-page a",
            "els => els.map(e => ({href: e.href, target: e.target, rel: e.rel, text: e.textContent.trim()}))",
        )

        assert [(item["href"], item["target"], item["rel"]) for item in links] == [
            (TAMPERMONKEY_URL, "_blank", "noopener noreferrer"),
            (REPORTER_URL, "_blank", "noopener noreferrer"),
        ]
        # The arrow is what marks them as leaving the page, like the row of
        # new-tab entries above.
        assert [item["text"] for item in links] == [
            "Tampermonkey \u2197",
            "Reporter script \u2197",
        ]

        box = page.locator("#manual input[name='pageurl']").bounding_box()
        grab = page.locator("#grabPage").bounding_box()
        for link_id in ("#tampermonkeyLink", "#reporterLink"):
            link = page.locator(link_id).bounding_box()
            assert abs(link["y"] - box["y"]) < box["height"], "moved off the box's line"
            assert link["x"] > grab["x"] + grab["width"] - 1, "not after the button"

    def test_the_row_hides_them_with_itself(self, page):
        """They belong to the row, so a frame holding an embed takes them away
        with it rather than leaving two orphaned install links in the header."""
        _open(page, TV)

        assert not page.is_visible("#reporterLink")
        assert not page.is_visible("#tampermonkeyLink")

    def test_the_reporter_link_is_the_scripts_own_install_url(self, page):
        """Two places name where the script lives — the anchor and the
        userscript's `@downloadURL` — and an install button pointing at anything
        else is a dead button nobody notices."""
        _manual(page, TV)
        _tmdb(page)

        declared = next(
            line.split(None, 2)[2].strip()
            for line in REPORTER.splitlines()
            if line.startswith("// @downloadURL")
        )

        assert page.get_attribute("#reporterLink", "href") == declared
        assert declared == REPORTER_URL
