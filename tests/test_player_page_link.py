"""The standalone ▶ Play link: what it carries, and how an old one is repaired.

The player page builds every embed URL from its query string, so a link that
omits the episode opens the series landing page instead of the episode, and a
year sent as its own ``year=`` parameter is a year the page never reads. Both
shapes were published, and the enricher's ``has_player`` guard means an item
that already carries a link is never revisited for a new one -- so the href is
upgraded in place. That upgrade has to converge: a guard that keeps disagreeing
with the writer would rewrite every feed, every hour, forever.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch
from urllib.parse import unquote

import pytest

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se
from scripts.enrichers.player_servers import (
    PLAYER_PAGE_BASE_URL,
    build_player_page_link,
    season_episode,
)


def _href(anchor: str) -> str:
    """The href of the anchor `build_player_page_link` returned."""
    return anchor.split('"')[1]


def _build(tmdb_id, imdb_id, media_type, title, **kwargs) -> str:
    return _href(build_player_page_link(tmdb_id, imdb_id, media_type, title, **kwargs))


class TestBuildPlayerPageLink:
    def test_no_id_builds_nothing(self):
        assert build_player_page_link(None, None, "movie", "X") == ""

    def test_movie_folds_the_year_into_the_title(self):
        href = _build(1003596, "tt21357150", "movie", "Avengers: Doomsday", year="2026")
        assert "type=movie" in href
        assert unquote(href).endswith("title=Avengers: Doomsday (2026)")
        # The page ignores a bare `year=`, and two spellings of the same fact is
        # one too many: the title is what its trailer search queries.
        assert "year=" not in href

    def test_a_title_that_already_carries_a_year_is_left_alone(self):
        href = _build(424, "tt0083650", "movie", "Blade Runner 2049", year="1984")
        assert unquote(href).endswith("title=Blade Runner 2049")

    def test_tv_episode_carries_season_and_episode(self):
        href = _build(
            291084, "tt36885662", "tv", "Best Medicine", season=1, episode=2, year="2026"
        )
        assert "type=tv&season=1&episode=2" in href
        assert "epguides=" not in href

    def test_a_resolved_epguides_slug_is_carried_and_escaped(self):
        href = _build(
            291084, "tt36885662", "tv", "Best Medicine", epguides_slug="BestMedicine_2026"
        )
        assert "&epguides=BestMedicine_2026&title=" in href

    def test_a_bare_imdb_number_is_still_a_tt_id(self):
        href = _build(None, "870973", "tv", "Celebrity Masterchef")
        assert "imdb=tt0870973" in href or "imdb=tt870973" in href
        assert "tmdb=" not in href

    def test_parameter_order_is_stable(self):
        # The upgrade rewrites published links to this exact spelling; a reorder
        # here means every link in every feed is rewritten once more.
        href = _build(
            550, "tt1000000", "tv", "The Show", season=2, episode=3, year="2024",
            epguides_slug="TheShow",
        )
        assert href == (
            f"{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv&season=2&episode=3"
            "&epguides=TheShow&title=The%20Show%20%282024%29"
        )

    def test_season_episode_reads_both_marker_spellings(self):
        assert season_episode("The Show S02E03 1080p") == (2, 3)
        assert season_episode("The Show - 4x3") == (4, 3)
        assert season_episode("The Show S03") is None
        assert season_episode("Some Movie 2026") is None


# ---------------------------------------------------------------------------
# the in-place upgrade of a published link
# ---------------------------------------------------------------------------

OLD_PLAY_LINK = (
    f'<a href="{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv&year=2024'
    '&title=The%20Show" target="_blank" rel="noopener noreferrer">'
    '<b style="color:#007bff;">&#9654; Play</b></a>'
)


def _enriched_tv_item(description: str) -> str:
    """A description an earlier run already finished: poster, IMDb title link
    and a Play link -- everything the skip guard looks for."""
    return (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
        '<br><a href="https://www.imdb.com/title/tt1000000/" target="_blank">'
        '<b style="color:#6600cc;">IMDb</b></a>'
        f"<br>{description}"
    )


def _write_feed(path, title: str, description: str) -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = "https://example.com/tv/550-the-show"
    ET.SubElement(item, "description").text = description
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def _process(path, feed_category: str = "streaming"):
    info = MovieInfo(
        poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
        title="The Show",
        year="2024",
        tmdb_id=550,
        media_type="tv",
        imdb_id="tt1000000",
    )
    with (
        patch.object(se, "tv_lookup", return_value=info),
        patch.object(se, "movie_lookup", return_value=None),
        patch.object(se, "find_by_imdb", return_value=None),
        patch.object(se, "search_tv", return_value=info),
        patch.object(se, "search_movie", return_value=None),
        patch.object(se, "_fallback_lookup", return_value=None),
    ):
        return se.process_feed(
            path, epguides_mapping={}, is_series_feed=True, feed_category=feed_category
        )


def _play_href(text: str) -> str:
    """The Play href as the browser sees it, i.e. with the XML escaping undone."""
    start = text.index(PLAYER_PAGE_BASE_URL)
    return text[start : text.index('"', start)].replace("&amp;", "&")


class TestPlayerLinkUpgrade:
    def test_an_episode_link_gains_its_parameters(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(OLD_PLAY_LINK))

        _process(feed)

        href = _play_href(feed.read_text(encoding="utf-8"))
        assert "season=2&episode=3" in href
        assert "year=" not in href
        assert unquote(href).endswith("title=The Show (2024)")

    def test_the_upgrade_is_a_no_op_the_second_time(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(OLD_PLAY_LINK))
        _process(feed)
        after_first = feed.read_text(encoding="utf-8")

        changed, _stats = _process(feed)

        assert not changed, "the guard disagrees with the writer: hourly churn"
        assert feed.read_text(encoding="utf-8") == after_first

    def test_a_season_overview_keeps_no_episode_parameters(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "The Show (2024)", _enriched_tv_item(OLD_PLAY_LINK))

        _process(feed)

        href = _play_href(feed.read_text(encoding="utf-8"))
        assert "season=" not in href and "episode=" not in href and "year=" not in href

    def test_a_current_link_is_left_entirely_alone(self, tmp_path):
        feed = tmp_path / "tv.xml"
        current = (
            f'<a href="{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv'
            '&season=2&episode=3&title=The%20Show%20%282024%29" target="_blank" '
            'rel="noopener noreferrer"><b style="color:#007bff;">&#9654; Play</b></a>'
        )
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(current))
        _process(feed)
        before = feed.read_text(encoding="utf-8")

        changed, _stats = _process(feed)

        assert not changed
        assert feed.read_text(encoding="utf-8") == before

    def test_a_freshly_written_link_matches_the_upgraded_spelling(self, tmp_path):
        """The writer and the upgrade must agree, or an item alternates between
        two spellings as each pass re-decides the href."""
        bare = tmp_path / "tv.xml"
        _write_feed(
            bare,
            "The Show S02E03",
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">',
        )
        _process(bare)
        written = _play_href(bare.read_text(encoding="utf-8"))

        old = tmp_path / "old.xml"
        _write_feed(old, "The Show S02E03", _enriched_tv_item(OLD_PLAY_LINK))
        _process(old)
        upgraded = _play_href(old.read_text(encoding="utf-8"))

        assert written == upgraded

    def test_a_run_that_missed_the_slug_keeps_the_published_one(self, tmp_path):
        """The EpGuides mirror is fetched at run time and a failed load is
        swallowed, so a miss must not strip a slug an earlier run resolved --
        that would drop the parameter, then re-add it on the next run."""
        feed = tmp_path / "tv.xml"
        with_slug = (
            f'<a href="{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv'
            '&season=2&episode=3&epguides=TheShow&title=The%20Show%20%282024%29" '
            'target="_blank" rel="noopener noreferrer">'
            '<b style="color:#007bff;">&#9654; Play</b></a>'
        )
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(with_slug))

        _process(feed)

        assert "epguides=TheShow" in _play_href(feed.read_text(encoding="utf-8"))
        changed, _stats = _process(feed)
        assert not changed, "the slug survived the rewrite but the pass still churned"

    def test_a_crafted_play_href_stays_encoded(self, tmp_path):
        """A published href is rewritten from the description's own text, so a
        title that percent-encoded markup must not come back as live markup."""
        feed = tmp_path / "tv.xml"
        payload = "%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E"
        crafted = (
            f'<a href="{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv'
            f'&year=2024&title={payload}" target="_blank" rel="noopener noreferrer">'
            '<b style="color:#007bff;">&#9654; Play</b></a>'
        )
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(crafted))

        _process(feed)

        text = feed.read_text(encoding="utf-8")
        href = _play_href(text)
        assert "<script>" not in href and '"' not in href
        assert "%22%3E%3Cscript%3E" in href, "the payload was decoded into markup"
        # ...and the parsed description, i.e. what a reader's innerHTML sees.
        description = ET.fromstring(text).findtext(".//description")
        assert "<script>alert(1)" not in description.split("href=")[1].split('"')[0]

    @pytest.mark.parametrize("field", ["tmdb", "imdb", "type"])
    def test_a_crafted_identifier_cannot_close_the_href_attribute(self, tmp_path, field):
        """The same guarantee for the tokens the upgrade copies back from the
        link it is rewriting. `parse_qs` decodes them, so a value that was
        published as `%22%3E%3Cimg …%3E` would re-enter the description as live
        markup unless the rebuild re-quotes it."""
        feed = tmp_path / "tv.xml"
        payload = "%22%3E%3Cimg%20src=x%20onerror=alert(1)%3E"
        query = {"tmdb": "550", "imdb": "tt1000000", "type": "tv"}
        query[field] = payload
        crafted = (
            f'<a href="{PLAYER_PAGE_BASE_URL}?{"&".join(f"{k}={v}" for k, v in query.items())}"'
            ' target="_blank" rel="noopener noreferrer">'
            '<b style="color:#007bff;">&#9654; Play</b></a>'
        )
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(crafted))

        _process(feed)

        text = feed.read_text(encoding="utf-8")
        # Read the description the way a reader does — XML-decode it, then parse
        # the anchor's HTML. An attribute-closing quote is the breakout.
        description = ET.fromstring(text).findtext(".//description")
        href_value, _, remainder = description.split(PLAYER_PAGE_BASE_URL, 1)[1].partition(
            '"'
        )
        assert "<" not in href_value
        assert remainder.startswith(" target="), "the payload closed the href attribute"
        assert f"{field}=%22%3E%3Cimg" in href_value, f"{field} was written back decoded"

    def test_a_percent_sign_in_a_published_title_is_not_a_re_escape(self, tmp_path):
        """`parse_qs` decodes once; decoding twice turns a title that really
        contains `%21` into `!`, so the rewrite would corrupt it — and the
        correction is a second rewrite, i.e. feed churn."""
        feed = tmp_path / "tv.xml"
        crafted = (
            f'<a href="{PLAYER_PAGE_BASE_URL}?tmdb=550&imdb=tt1000000&type=tv'
            '&title=100%2521%20Done" target="_blank" rel="noopener noreferrer">'
            '<b style="color:#007bff;">&#9654; Play</b></a>'
        )
        _write_feed(feed, "The Show S02E03", _enriched_tv_item(crafted))

        _process(feed)

        assert "title=100%2521%20Done" in _play_href(feed.read_text(encoding="utf-8"))


class TestUnresolvableItem:
    def test_an_item_that_resolves_to_nothing_never_rewrites_the_feed(self, tmp_path):
        """A ▶ Play link needs an id, and an item whose title never resolves has
        no way to stop needing the retry: the guard asks again every run, until
        TMDb's cached miss expires it for another `MISS_TTL_SECONDS`. What must
        hold is that the retry costs the *published feed* nothing — no write, no
        second copy of a link."""
        feed = tmp_path / "tv.xml"
        _write_feed(
            feed,
            "The Show S02E03",
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
            '<br><a href="https://www.imdb.com/title/tt1000000/" target="_blank">'
            '<b style="color:#6600cc;">IMDb</b></a>',
        )

        def unresolvable(path):
            with (
                patch.object(se, "tv_lookup", return_value=None),
                patch.object(se, "movie_lookup", return_value=None),
                patch.object(se, "find_by_imdb", return_value=None),
                patch.object(se, "search_tv", return_value=None),
                patch.object(se, "search_movie", return_value=None),
                patch.object(se, "_fallback_lookup", return_value=None),
            ):
                return se.process_feed(
                    path,
                    epguides_mapping={},
                    is_series_feed=True,
                    feed_category="streaming",
                )

        unresolvable(feed)
        after_first = feed.read_text(encoding="utf-8")

        changed, _stats = unresolvable(feed)

        assert not changed, "an unresolvable item still moves published bytes"
        assert feed.read_text(encoding="utf-8") == after_first
        assert PLAYER_PAGE_BASE_URL not in after_first



class TestTorrentFeedConvergence:
    def test_an_enriched_torrent_item_costs_no_lookup(self, tmp_path):
        """The short-circuit has to hold for a torrent feed too. Its exception
        there used to be a guard on the per-server label tags — anchors the
        enricher stopped writing when the player page took over building them —
        so the guard was true forever and every enriched item spent a TMDb round
        trip on every hourly run."""
        feed = tmp_path / "tv.xml"
        _write_feed(
            feed,
            "The Show S02E03",
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">',
        )
        _process(feed, feed_category="torrents")

        with (
            patch.object(se, "tv_lookup", return_value=None) as lookup,
            patch.object(se, "search_tv", return_value=None) as search,
        ):
            changed, _stats = _process(feed, feed_category="torrents")

        assert not changed
        lookup.assert_not_called()
        search.assert_not_called()
