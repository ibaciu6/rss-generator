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


def _clipboard(page, text: str) -> None:
    """What "Grab from page" falls back to when the page-URL field is empty.
    Chromium only grants `clipboard-read` to an origin it trusts and these pages
    load from `file://`, so the method is replaced rather than permitted — the
    handler is unchanged either way."""
    page.evaluate(
        "text => { navigator.clipboard.readText = () => Promise.resolve(text); }", text
    )


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
        # list, the page "Grab from page" then reads an id out of.
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
            'Browse TMDb, then use "Grab from page" to load it here'
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
class TestGrabFromPage:
    """A TMDb page address becomes player parameters. The framed page's own
    address cannot be read — the same-origin policy hides a cross-origin
    frame's location from its embedder — so the address is pasted into the page
    URL field, and an empty field falls back to the clipboard."""

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
        page.wait_for_timeout(400)

        assert page.url == url_before
        assert page.inner_text("#manualHint") == hint
        # The clipboard is a source the viewer cannot otherwise see, so what it
        # held lands in the field — a stale address becomes a visible one.
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
        page.wait_for_timeout(400)

        assert page.url == url_before
        assert _fields(page)["tmdb"] == "1058424"
        assert _fields(page)["pageurl"] == (
            "https://www.themoviedb.org/movie/1058424-the-magic-brush"
        )
        assert page.inner_text("#manualHint") == "Already loaded: movie/1058424"

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
        page.wait_for_timeout(400)

        assert page.url == url_before
        assert page.inner_text("#manualHint") == "Not a TMDb movie or TV page URL."

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
        page.wait_for_timeout(400)

        assert page.url == url_before
        assert page.inner_text("#manualHint") == "Not a TMDb movie or TV page URL."
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
        page.wait_for_timeout(400)

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
        assert page.inner_text("#manualHint") == (
            "Nothing to read — paste or copy a TMDb page URL first."
        )

    def test_a_bare_field_falls_back_to_the_clipboard(self, page):
        _manual(page, "")
        _tmdb(page)
        _clipboard(page, "https://www.themoviedb.org/movie/1058424")

        page.click("#grabPage")
        page.wait_for_url(lambda url: "tmdb=1058424" in url)

        assert page.url.split("?", 1)[1] == "tmdb=1058424&type=movie"

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
        "Grab from page" to read out of it."""
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
