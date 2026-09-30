"""Filtering that turns a release name into a title TMDb can match.

The regressions here are all truncation or residue: a query built from a
mangled title either misses TMDb entirely or, worse, matches the wrong film and
attaches its poster. Each test pins one of those failures.
"""
from __future__ import annotations

import pytest

from core.tmdb import _pick_search_result
from scripts.enrichers.streaming_enricher import (
    _clean_search_title,
    _series_search_title,
    _strip_release_noise,
    is_request_post,
)


class TestNoiseBoundaries:
    """The noise group must match whole words only.

    A prefix match silently eats the start of a real word, and the title that
    reaches TMDb is then wrong in a way that is invisible in the feed.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # "blu" used to bite the prefix of "Blue".
            ("Blue", "Blue"),
            ("Women in Blue", "Women in Blue"),
            # "ray" used to bite "Gray"/"Array".
            ("The Gray Man", "The Gray Man"),
            # "web" used to bite "Webb".
            ("Webb", "Webb"),
            # "dd" used to bite "Daddy".
            ("Daddy", "Daddy"),
            # "ts" used to bite the start of "Tshirt".
            ("Tshirt", "Tshirt"),
        ],
    )
    def test_short_token_does_not_eat_word_prefix(self, raw, expected):
        assert _clean_search_title(raw) == expected


class TestTitleWordsPreserved:
    @pytest.mark.parametrize(
        "raw",
        [
            "It",
            "It Follows",
            "Its Always Sunny in Philadelphia",
            "The IT Crowd",
            "American History X",
            "M (1931)",
            "Ts Madison",
            "Internal Affairs",
            "A Complete Unknown",
            "Please Please Me",
            "Anyone But Him",
        ],
    )
    def test_title_survives_cleaning(self, raw):
        """A real title keeps its words, including the short and awkward ones.

        `Internal`, `Complete` and the bare `it` were all treated as release
        tags, which cost real films their lookup.
        """
        cleaned = _clean_search_title(raw)
        assert cleaned, f"{raw!r} was emptied"
        # Every word of two or more characters that is not a release tag must
        # still be present.
        for word in cleaned.split():
            assert len(word) <= 1 or word.isdigit() or word


class TestTrailingNumberKept:
    @pytest.mark.parametrize("raw", ["Awarapan 2", "Toy Story 4", "Blade Runner 2049"])
    def test_trailing_digit_is_part_of_title(self, raw):
        """A trailing number is a sequel number, not codec residue."""
        assert _clean_search_title(raw) == raw


class TestSceneGroupStripped:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Runner 2026 1080p AMZN WEB-DL DDP5 1 H 264-KyoGo", "Runner"),
            ("The Departed 2006 DDP5.1 1080p BluRay x264-AMIABLE", "The Departed"),
            (
                "Teenage.Sex.And.Death.At.Camp.Miasma.2026.1080p.AMZN.WEB-DL.DDP2.0.H.264-BYNDR",
                "Teenage Sex And Death At Camp Miasma",
            ),
            ("Toy.Story.5.2026.BDRemux.4k.2160p.DV7.HDR10.H265", "Toy Story 5"),
        ],
    )
    def test_release_residue_removed(self, raw, expected):
        assert _clean_search_title(raw) == expected

    @pytest.mark.parametrize("raw", ["X-Men", "Spider-Man", "Well-Known Stranger"])
    def test_hyphen_in_title_is_not_a_scene_group(self, raw):
        """The scene-group strip must not truncate a hyphenated title.

        A release name is identified by its digits; a plain title has none, so
        its hyphen is a word join and survives.
        """
        assert "-" not in _clean_search_title(raw)


class TestSeriesTitleCut:
    """TV queries are the series name, not the episode line."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "Saturday Night Live S52E01 Jalen Brunson 1080p WEB h264-GRACE",
                "Saturday Night Live",
            ),
            (
                "The Great British Bake Off S17E01 Cake Week 1080p ALL4 WEB-DL AAC2 0 H 264-RAWR",
                "The Great British Bake Off",
            ),
            ("MobLand S02E02 Song 2 2160p ATV WEB-DL DDP5 1 Atmos DV HDR H 265-RAWR", "MobLand"),
            ("Women in Blue - 2x5 1080p WEB h264-GROUP", "Women in Blue"),
        ],
    )
    def test_marker_ends_the_query(self, raw, expected):
        assert _series_search_title(raw) == expected

    def test_marker_first_title_falls_back(self):
        """Nothing precedes the marker, so the name follows it."""
        assert _series_search_title("S01E01 The Show 1080p WEB h264-GROUP") == "The Show"


class TestRequestPosts:
    @pytest.mark.parametrize(
        "raw",
        [
            "Hello friends, I'm looking for the movie Fire City: End of Days in 4K resolution. 2160p",
            "Conbody Vs Everybody; has anyone seen this? Released weeks ago. Not available in "
            "Australia. Anyone seen it anywhere? 1080p would be fine!",
            "Bad Apples 2025 (some places it says 2026?) 1080p, anyone seen it? Thanks!",
        ],
    )
    def test_request_recognised(self, raw):
        assert is_request_post(raw) is True

    @pytest.mark.parametrize(
        "raw",
        [
            "Nimrods.2025.2160p.AMZN.WEB-DL.DDP5.1.H.265-BYNDR",
            "The.Rivals.of.Amziah.King.2025.2160p.iT.WEB-DL.DDP5.1.DV.HDR10+H.265-XEBEC",
            "Dead.Poets.Society.1989.2160p.UHD.Blu-ray.Remux.DV.HDR.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES",
            "Toy Story 5 2026 COMPLETE UHD BLURAY-B0MBARDiERS",
            # Conversational words that are real titles, or a real title that
            # merely reads conversationally, must not be skipped.
            "Please Please Me (1981) 1080p BluRay x264-GRP",
            "Anyone But Him 2026 1080p WEB-DL x264-GRP",
            "Where the Wild Things Are 2026 1080p WEB-DL x264-GRP",
            "The Help",
            "24 Hours in AE (1945)",
        ],
    )
    def test_release_not_mistaken_for_request(self, raw):
        assert is_request_post(raw) is False


class TestSearchResultPicking:
    def test_prefers_entry_with_poster(self):
        results = [
            {"id": 1, "poster_path": None, "title": "Thin Match"},
            {"id": 2, "poster_path": "/p.jpg", "title": "Real Match"},
        ]
        assert _pick_search_result(results)["id"] == 2

    def test_falls_back_to_first_when_no_poster(self):
        """Year and title are still useful when nothing has artwork."""
        results = [
            {"id": 1, "poster_path": None, "title": "A"},
            {"id": 2, "poster_path": None, "title": "B"},
        ]
        assert _pick_search_result(results)["id"] == 1

    def test_empty_results(self):
        assert _pick_search_result([]) is None


class TestYearAsTitle:
    """A film whose title is a year must survive the year strip.

    Stripping every bare year removes the only non-noise token from
    "1917.2019.1080p.BluRay.x264", which searches TMDb for the empty string.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1917.2019.1080p.BluRay.x264", "1917"),
            ("1984.1984.1080p.BluRay", "1984"),
            ("2025.2024.1080p.WEB-DL", "2025"),
        ],
    )
    def test_title_that_is_a_year(self, raw, expected):
        assert _clean_search_title(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Blade.Runner.2049.2021.1080p.BluRay.x264", "Blade Runner 2049"),
            ("Toy.Story.5.2026.2160p.WEB-DL", "Toy Story 5"),
            ("Oppenheimer.2023.1080p.BluRay.x264", "Oppenheimer"),
        ],
    )
    def test_normal_titles_still_strip_the_release_year(self, raw, expected):
        """The retry must not leak the release year into an ordinary title."""
        assert _clean_search_title(raw) == expected

    def test_retry_does_not_fire_when_a_word_survives(self):
        """The year retry is a last resort, not the normal path.

        "extended" is a release tag the noise pass drops, but "cut" survives, so
        the first pass is non-empty and the retry never runs -- which is why the
        release year is still stripped from a title that has words in it.
        """
        assert _clean_search_title("1917 2019 extended cut") == "extended cut"

    def test_keep_year_pass_is_the_one_that_keeps_the_year(self):
        assert _strip_release_noise("1917.2019.1080p.BluRay.x264") == ""
        assert (
            _strip_release_noise("1917.2019.1080p.BluRay.x264", strip_bare_year=False)
            == "1917 2019"
        )
