"""EpGuides linking: real pages first, a fallback that works, and repairs.

Three failure modes are covered here:

- `allshows.txt` qualifies titles the feed does not carry ("The Diplomat (US)"
  for "The Diplomat"), so the lookup indexes a bare title beside each exact one,
  and the version on air wins a bare key shared by several.
- `allshows.txt` also lags the site: a show EpGuides already has a page for is
  absent from the TSV for weeks, so the recent-adds feed is merged in.
- the fallback used to be `epguides.com/search/?q=…`, a page that never reads
  its `q`, so those items shipped a dead link -- which the "already linked"
  guard then left alone forever. Enrichment rewrites them.
"""
from __future__ import annotations

import csv
import os
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from scripts.enrichers import streaming_enricher as se

ALLSHOWS_COLUMNS = [
    "title",
    "directory",
    "tvrage",
    "TVmaze",
    "start date",
    "end date",
    "number of episodes",
    "run time",
    "network",
    "country",
    "onhiatus",
    "onhiatusdesc",
]

RECENT_ADDS_XML = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<rss version="2.0">
   <channel>
      <title>epguides.com - Recent adds all</title>
      <item>
         <link>https://epguides.com/AvatarSevenHavens/</link>
         <guid isPermaLink="true">https://epguides.com/AvatarSevenHavens/</guid>
         <title>Avatar: Seven Havens</title>
         <description>current Paramount+ (US) animated series (added on 10/8/2026)</description>
      </item>
      <item>
         <link>https://epguides.com/SevenHavens/</link>
         <title>Seven Havens</title>
         <description>alias of the same guide</description>
      </item>
   </channel>
</rss>
"""

IMDB = (
    '<a href="https://www.imdb.com/find?q=Show&amp;s=tt" target="_blank" '
    'rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>'
)


def _no_network(*_args, **_kwargs):
    raise AssertionError("the mirror must be read without a network round-trip")


def _write_allshows(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """Write an allshows.txt-shaped mirror: (title, slug, start, end) rows."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(ALLSHOWS_COLUMNS)
        for title, slug, start, end in rows:
            writer.writerow([title, slug, "", "", start, end, "", "", "", "", "", ""])
    return path


def _stale_link(query: str) -> str:
    """The anchor `_build_epguides_search_link` used to emit."""
    return (
        f'<br><a href="https://epguides.com/search/?q={quote(query, safe="")}" '
        'target="_blank" rel="noopener noreferrer">'
        '<b style="color:#6600cc;">EpGuides</b></a>'
    )


def _tv_item(query: str = "Dream") -> ET.Element:
    """An item published while the dead site-search fallback was in use."""
    item = ET.Element("item")
    ET.SubElement(item, "title").text = f"{query} S01E02"
    ET.SubElement(item, "link").text = "https://example.com/tv/1"
    text = f"{IMDB}{_stale_link(query)}<br><p>Body.</p>"
    ET.SubElement(item, "description").text = text
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = text
    return item


def _write_tv_feed(path: Path, query: str = "Dream") -> Path:
    rss = ET.Element("rss")
    channel = ET.SubElement(rss, "channel")
    channel.append(_tv_item(query))
    ET.ElementTree(rss).write(path, encoding="UTF-8", xml_declaration=True)
    return path


def _without_tmdb():
    """Silence every TMDb/IMDb hop the streaming pipeline makes after EpGuides."""
    stack = ExitStack()
    for name in (
        "find_by_imdb",
        "tv_lookup",
        "movie_lookup",
        "search_tv",
        "search_movie",
        "_fallback_lookup",
    ):
        stack.enter_context(patch.object(se, name, return_value=None))
    return stack


# --------------------------------------------------------------------------
# the bare-title index
# --------------------------------------------------------------------------


class TestBareTitleIndex:
    def test_a_title_without_its_qualifier_finds_the_page(self, tmp_path):
        """The regression the search fallback was hiding: the page existed all
        along, the lookup just asked for a key `allshows.txt` does not spell."""
        path = _write_allshows(
            tmp_path / "allshows.txt",
            [
                ("The Diplomat (US)", "Diplomat", "Apr 2023", "___ ____"),
                ("The Diplomat (UK)", "Diplomat_UK", "Feb 2023", "Apr 2023"),
            ],
        )
        mapping = se._load_epguides(path)

        assert se._find_epguides_slug("The Diplomat", mapping) == "Diplomat"

    def test_a_qualified_title_still_names_its_own_page(self, tmp_path):
        path = _write_allshows(
            tmp_path / "allshows.txt",
            [
                ("The Diplomat (US)", "Diplomat", "Apr 2023", "___ ____"),
                ("The Diplomat (UK)", "Diplomat_UK", "Feb 2023", "Apr 2023"),
            ],
        )
        mapping = se._load_epguides(path)

        assert se._find_epguides_slug("The Diplomat (UK)", mapping) == "Diplomat_UK"
        assert se._find_epguides_slug("The Diplomat (US)", mapping) == "Diplomat"

    def test_an_exact_title_never_loses_its_key_to_the_bare_index(self, tmp_path):
        """A show actually titled "The Office" outranks the bare key its
        remakes qualify themselves with."""
        path = _write_allshows(
            tmp_path / "allshows.txt",
            [
                ("The Office (US)", "Office_US", "Mar 2005", "May 2013"),
                ("The Office", "OfficePlain", "Jan 1990", "Feb 1990"),
            ],
        )
        mapping = se._load_epguides(path)

        assert se._find_epguides_slug("The Office", mapping) == "OfficePlain"
        assert se._find_epguides_slug("The Office (US)", mapping) == "Office_US"

    def test_a_shared_bare_title_prefers_the_one_on_air(self, tmp_path):
        path = _write_allshows(
            tmp_path / "allshows.txt",
            [
                ("Doctor Who (1963)", "DoctorWho", "Nov 1963", "Dec 1989"),
                ("Doctor Who (2005)", "DoctorWho_2005", "Mar 2005", "Oct 2022"),
                ("Doctor Who (2023)", "DoctorWho_2023", "Nov 2023", "___ ____"),
            ],
        )
        mapping = se._load_epguides(path)

        assert se._find_epguides_slug("Doctor Who", mapping) == "DoctorWho_2023"

    def test_with_nothing_on_air_the_newest_incarnation_wins(self, tmp_path):
        path = _write_allshows(
            tmp_path / "allshows.txt",
            [
                ("The Office (UK)", "Office_UK", "Jul 2001", "Dec 2003"),
                ("The Office (US)", "Office_US", "Mar 2005", "May 2013"),
            ],
        )
        mapping = se._load_epguides(path)

        assert se._find_epguides_slug("The Office", mapping) == "Office_US"

    def test_an_absent_mirror_is_empty_rather_than_an_error(self, tmp_path):
        """A missing local mirror must not break an offline run."""
        assert se._load_epguides(tmp_path / "nope.txt") == {}


# --------------------------------------------------------------------------
# the recent-adds feed
# --------------------------------------------------------------------------


class TestRecentAdds:
    def test_titles_resolve_to_the_page_in_the_link(self, tmp_path):
        cache = tmp_path / "epguides_recent.xml"
        cache.write_text(RECENT_ADDS_XML, encoding="utf-8")

        mapping = se._load_recent_adds(cache)

        assert mapping["avatarsevenhavens"] == "AvatarSevenHavens"
        assert mapping["sevenhavens"] == "SevenHavens"

    def test_a_missing_or_unparseable_mirror_is_empty(self, tmp_path):
        assert se._load_recent_adds(tmp_path / "absent.xml") == {}

        broken = tmp_path / "broken.xml"
        broken.write_text("<not-xml", encoding="utf-8")
        assert se._load_recent_adds(broken) == {}

    def test_allshows_entries_win_the_merge(self, tmp_path, monkeypatch):
        """The recent feed repeats a show under every alias; the curated TSV
        decides when the two disagree."""
        monkeypatch.setattr(
            se, "_recent_adds_mapping", lambda: {"thediplomat": "DiplomatRecent", "new": "New"}
        )

        merged = se._with_recent_adds({"thediplomat": "Diplomat"})

        assert merged == {"thediplomat": "Diplomat", "new": "New"}

    def test_a_fresh_mirror_is_used_without_touching_the_network(self, tmp_path, monkeypatch):
        cache = tmp_path / "epguides_recent.xml"
        cache.write_text(RECENT_ADDS_XML, encoding="utf-8")
        monkeypatch.setattr(se, "EPGUIDES_RECENT_CACHE", cache)
        monkeypatch.setattr(se.httpx, "get", _no_network)

        assert "avatarsevenhavens" in se._recent_adds_mapping()

    def test_downloads_can_be_disabled_but_the_mirror_still_reads(self, tmp_path, monkeypatch):
        cache = tmp_path / "epguides_recent.xml"
        cache.write_text(RECENT_ADDS_XML, encoding="utf-8")
        stale = cache.stat().st_mtime - se.EPGUIDES_RECENT_TTL_SECONDS - 1
        os.utime(cache, (stale, stale))
        monkeypatch.setattr(se, "EPGUIDES_RECENT_CACHE", cache)
        monkeypatch.setattr(se.httpx, "get", _no_network)
        monkeypatch.setenv("EPGUIDES_DISABLE_DOWNLOAD", "1")

        assert "avatarsevenhavens" in se._recent_adds_mapping()


# --------------------------------------------------------------------------
# the fallback link
# --------------------------------------------------------------------------


class TestFallbackLink:
    def test_it_is_the_sites_own_working_search(self):
        link = se._build_epguides_search_link("Avatar: Seven Havens")

        assert f"https://www.google.com/cse?cx={se.EPGUIDES_CSE_CX}" in link
        assert "q=Avatar%3A%20Seven%20Havens" in link
        # an href is HTML: an unescaped & would be a bare entity in the feed
        assert "&amp;q=" in link
        assert "epguides.com/search" not in link


# --------------------------------------------------------------------------
# rewriting what is already published
# --------------------------------------------------------------------------


class TestStaleLinkRepair:
    def test_the_dead_search_link_becomes_the_real_page(self):
        item = _tv_item("Dream")
        assert se._has_stale_epguides_link(item)

        assert se._attach_epguides_link(item, "Dream", {"dream": "Dream"})
        for node in (item.find("description"), item.find(se._CONTENT_ENCODED)):
            assert "epguides.com/Dream/" in (node.text or "")
            assert "epguides.com/search" not in (node.text or "")
            assert "<p>Body.</p>" in (node.text or "")
        assert not se._has_stale_epguides_link(item)
        # converges: the next run finds a link already there
        assert not se._attach_epguides_link(item, "Dream", {"dream": "Dream"})

    def test_an_unresolved_series_falls_back_to_the_working_search(self):
        item = _tv_item("Dream")

        assert se._attach_epguides_link(item, "Dream", {}, allow_fallback=True)
        assert "google.com/cse" in (item.findtext("description") or "")
        assert "epguides.com/search" not in (item.findtext("description") or "")
        assert not se._attach_epguides_link(item, "Dream", {}, allow_fallback=True)

    def test_without_a_fallback_the_stale_link_is_left_for_the_refresh_pass(self):
        """Rewriting to another search link here would stop the second pass
        from ever upgrading it to the real page."""
        item = _tv_item("Dream")

        assert not se._attach_epguides_link(item, "Dream", {})
        assert se._has_stale_epguides_link(item)

    def test_a_real_link_beside_a_stale_one_is_not_duplicated(self):
        item = _tv_item("Dream")
        real = se._build_epguides_link("Dream")
        for tag in ("description", se._CONTENT_ENCODED):
            node = item.find(tag)
            node.text = (node.text or "").replace(_stale_link("Dream"), real + _stale_link("Dream"))

        assert se._attach_epguides_link(item, "Dream", {"dream": "Dream"})
        for tag in ("description", se._CONTENT_ENCODED):
            assert (item.findtext(tag) or "").count(">EpGuides</b></a>") == 1
            assert "epguides.com/search" not in (item.findtext(tag) or "")


class TestProcessFeedRepairs:
    def test_an_item_whose_only_link_is_dead_is_recorded_as_a_miss(self, tmp_path):
        feed = _write_tv_feed(tmp_path / "tv.xml")
        misses: dict = {}

        with _without_tmdb():
            changed, _stats = se.process_feed(
                feed, epguides_mapping={}, epguides_misses=misses, is_series_feed=True
            )

        assert not changed
        assert feed in misses, "a dead link is not a linked item"

    def test_a_resolving_series_is_repaired_in_place(self, tmp_path):
        feed = _write_tv_feed(tmp_path / "tv.xml")
        misses: dict = {}

        with _without_tmdb():
            changed, stats = se.process_feed(
                feed,
                epguides_mapping={"dream": "Dream"},
                epguides_misses=misses,
                is_series_feed=True,
            )

        assert changed and stats["epguides"] == 1
        assert misses == {}
        text = feed.read_text(encoding="utf-8")
        assert "epguides.com/Dream/" in text
        assert "epguides.com/search" not in text

        # a second run finds nothing left to do (§13: enrichment is idempotent)
        with _without_tmdb():
            again, stats_again = se.process_feed(
                feed,
                epguides_mapping={"dream": "Dream"},
                epguides_misses={},
                is_series_feed=True,
            )
        assert not again and stats_again["epguides"] == 0
        assert feed.read_text(encoding="utf-8") == text
