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
    _has_imdb_search_link,
    _has_imdb_title_link,
    _is_tv_title,
    _ptn_search_title,
    _search_title_candidates,
    _series_search_title,
    _strip_release_noise,
    _title_matches,
    _title_year,
    _upgrade_imdb_anchor,
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
            ("Runner 2026 1080p AMZN WEB-DL DDP5 1 H 264-KyoGo", "Runner (2026)"),
            ("The Departed 2006 DDP5.1 1080p BluRay x264-AMIABLE", "The Departed (2006)"),
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


class TestSiteTypeSuffix:
    """Romanian cinema sites bolt the media type onto the year ("Mutiny (2026)
    Film"). Search must see the title alone; a title genuinely ending in a word
    like "Film" (no year in front of it) must be left alone."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Mutiny (2026) Film", "Mutiny"),
            ("DC (2026) Film", "DC"),
            ("UNABOMBER (2026) Film", "UNABOMBER"),
            ("Some Show (2024) Serie", "Some Show"),
            # No year before the word -> it is part of the title.
            ("The Ultimate Film", "The Ultimate Film"),
            ("Film", "Film"),
        ],
    )
    def test_type_word_after_year_is_dropped(self, raw, expected):
        assert _clean_search_title(raw) == expected


class TestTitleMatchesSlug:
    """A slugified feed title must validate against the spaced TMDb title.

    "the-fix" and "The Fix" are the same film; the dash is punctuation, not a
    word boundary that changes the title.
    """

    def test_hyphen_slug_matches_spaced_title(self):
        assert _title_matches("The Fix", "the-fix") is True

    def test_different_titles_do_not_match(self):
        assert _title_matches("Monster High", "Monster: The Lizzie Borden Story") is False


class TestTitleGuardSpellingVariants:
    """The guard closes spelling gaps between a feed name and its TMDb record.

    "Minions & Monsters" and "Minions.And.Monsters" are one film, as are
    "The Fantastic 4: First Steps" and "The.Fantastic.Four.First.Steps".
    Without the "&"->"and" and digit->word folds those real releases fail the
    guard and stay unenriched, while genuinely different titles ("About Time"
    vs "Time", "Hotel Tehran" vs "The Fix") must keep failing it.
    """

    @pytest.mark.parametrize(
        "tmdb,feed",
        [
            ("Minions & Monsters", "Minions.And.Monsters.2026.2160p.UHD.BluRay.REMUX-GRP"),
            (
                "The Fantastic 4: First Steps",
                "The.Fantastic.Four.First.Steps.2025.4K.HEVC.10bit.DV.GRP",
            ),
            ("2 Fast 2 Furious", "2.Fast.2.Furious.2003.1080p.WEB-DL"),
            ("8 Mile", "Eight.Mile.2002.1080p.BluRay"),
            ("The Fix", "the-fix"),
        ],
    )
    def test_spelling_gaps_match(self, tmdb, feed):
        assert _title_matches(tmdb, feed) is True

    @pytest.mark.parametrize(
        "tmdb,feed",
        [
            ("About Time", "Time.2021.S03.1080p.iP.WEB-DL.AAC2.0.H.264-playWEB"),
            ("Hotel Tehran", "The.Fix.2026.2160p.NOW.WEB-DL.DD+5.1.H.265-playWEB"),
            ("Monster High", "Monster: The Lizzie Borden Story"),
        ],
    )
    def test_different_titles_still_do_not_match(self, tmdb, feed):
        assert _title_matches(tmdb, feed) is False


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
            "The odyssey bluray or webrip date?",
            "When is the 4K bluray of Dune coming out?",
            "Guys, any bluray release date for Longlegs yet?",
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
            # A title that merely contains "date" is not a request without the
            # question mark or opener: the opener requirement protects it.
            "First Date (2026) 1080p WEB-DL x264-GRP",
            "A Date with Danger (2024) 2160p ATV WEB-DL",
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


class TestVisionTagVariants:
    """The HDR alternative has to cover the "p" spelling too.

    ``hdr10?(?:plus)?`` matched "HDR10+" but not "HDR10p", so the token the
    release tag was spelled with -- "HDR10p" -- survived into the TMDb query
    and the title was searched as "Movie HDR10p".
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Movie.2026.2160p.HDR10p.WEB-DL", "Movie"),
            ("Movie.2026.2160p.HDR10Plus.WEB-DL", "Movie"),
            ("Movie.2026.2160p.HDR10.WEB-DL", "Movie"),
            ("Movie.2026.2160p.HDR.WEB-DL", "Movie"),
        ],
    )
    def test_hdr_spellings_are_noise(self, raw, expected):
        assert _clean_search_title(raw) == expected


class TestScreenerTag:
    def test_screener_is_stripped(self):
        assert _clean_search_title("Some.Movie.2026.1080p.SCREENER.x264") == "Some Movie"


class TestTagWithCounter:
    """A release tag that carries a counter survives a bare-word boundary.

    `\bproper\b` did not match "PROPER2", so the counter reached TMDb
    ("Fight Club PROPER2") and the lookup found nothing. Frame-rate tags
    ("60FPS") had no rule at all.
    """

    def test_second_release_counter_is_stripped(self):
        raw = (
            "Fight.Club.1999.PROPER2.BluRay.1080p.DTS-HD.MA.5.1.AVC."
            "HYBRID.REMUX-FraMeSToR"
        )
        assert _clean_search_title(raw) == "Fight Club"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Some.Movie.2026.1080p.REPACK2.WEB-DL", "Some Movie"),
            ("Some.Movie.2026.1080p.60FPS.WEB-DL", "Some Movie"),
            ("Some.Movie.2026.1080p.WEB-DL.24FPS", "Some Movie"),
        ],
    )
    def test_counter_and_frame_rate_are_noise(self, raw, expected):
        assert _clean_search_title(raw) == expected

    def test_title_word_with_no_counter_is_not_eaten(self):
        # "Internal" was deliberately kept as a title word, not a release tag.
        assert (
            _clean_search_title("Internal Affairs 2015 1080p BluRay x264-GRP")
            == "Internal Affairs (2015)"
        )


class TestFrameInterpolationTag:
    """RIFE, and the "AI" modifier scene groups pair it with, are tags.

    "HEVC+AI.RIFE" left "AI RIFE" in the query, and "BACKROOMS AI RIFE" matched
    no TMDb title -- so the item got no poster.
    """

    def test_ai_rife_pair_is_removed(self):
        raw = (
            "BACKROOMS.2026.2160P.60FPS.REMUX.HEVC+AI.RIFE.DV.HDR10."
            "DDP.5.1.ATMOS-WOODROWSPOO69"
        )
        assert _clean_search_title(raw) == "BACKROOMS"

    def test_bare_rife_is_removed(self):
        assert _clean_search_title("Some.Movie.2026.2160p.RIFE.WEB-DL") == "Some Movie"


class TestNowSourceTag:
    """NOW is a source tag when a web marker follows it, a word otherwise.

    "The.Fix.2026.2160p.NOW.WEB-DL.DD+5.1.H.265-playWEB" cleaned to
    "The Fix NOW", which matched no TMDb title, so the item shipped with no
    poster. Uniquely among source tags, "now" is also an ordinary title word
    ("Apocalypse Now"), so it is stripped only in the source position -- with a
    web tag next, and nothing but separators in between.
    """

    def test_now_source_tag_is_stripped(self):
        raw = "The.Fix.2026.2160p.NOW.WEB-DL.DD+5.1.H.265-playWEB"
        assert _clean_search_title(raw) == "The Fix"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Some.Movie.2026.NOW.WEBRip.x264-GRP", "Some Movie"),
            ("Some.Movie.2026.1080p.NOW.WEB-DL.DDP5.1.H.264-GRP", "Some Movie"),
        ],
    )
    def test_now_before_a_web_marker_is_noise(self, raw, expected):
        assert _clean_search_title(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # No web marker anywhere near the word: keep it.
            (
                "Apocalypse.Now.1979.2160p.UHD.BluRay.REMUX.HEVC.TrueHD.7.1.Atmos-GRP",
                "Apocalypse Now",
            ),
            # A web marker is present, but a year/resolution sits between it and
            # the word -- not the source position.
            (
                "Apocalypse.Now.1979.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "Apocalypse Now",
            ),
            # A title that is literally the word, with release noise after it.
            ("Now.2026.1080p.WEB-DL.x264-GRP", "Now"),
        ],
    )
    def test_now_title_word_survives(self, raw, expected):
        assert _clean_search_title(raw) == expected


class TestPtnPrimaryTitle:
    """parse-torrent-title leads; the hand-written passes are the fallback."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The source tag the hand-written pass only learned about later.
            ("The.Fix.2026.2160p.NOW.WEB-DL.DD+5.1.H.265-playWEB", "The Fix"),
            # A year inside the title: PTN keeps it in one pass.
            (
                "2001.A.Space.Odyssey.1968.2160p.UHD.BluRay.REMUX-GRP",
                "2001 A Space Odyssey",
            ),
            ("Blade.Runner.2049.2021.1080p.BluRay.x264", "Blade Runner 2049"),
            ("Class.of.1984.1982.1080p.BluRay.x264-GRP", "Class of 1984"),
            # Words the hand-written tag list used to eat.
            ("She Did It (2026)", "She Did It"),
            ("Final Destination Bloodlines (2025)", "Final Destination Bloodlines"),
            (
                "Mission: Impossible - The Final Reckoning (2025)",
                "Mission: Impossible - The Final Reckoning",
            ),
            # Scene-group residues the hand-written pass left behind.
            (
                "Avatar.The.Way.of.Water.2022.IMAX.1080p.60FPS.AV1.WEB-DL.MrStrange",
                "Avatar The Way of Water",
            ),
            (
                "Spider-Man.Brand.New.Day.2026.IMAX.2160p.CORE.WEB-DL.DDP5.1.HDR.H.265-SPiLNO",
                "Spider-Man Brand New Day",
            ),
            # Episode markup: PTN separates the episode name from the series.
            ("9-1-1.S07E01.1080p.WEB.h264-GRP", "9-1-1"),
            ("The Great British Bake Off S17E01 Cake Week 1080p ALL4 WEB-DL AAC2 0 H 264-RAWR", "The Great British Bake Off"),
        ],
    )
    def test_ptn_title(self, raw, expected):
        assert _ptn_search_title(raw) == expected

    def test_year_only_result_is_rejected(self):
        # PTN reads "DC (2026)" as the title "2026" with no year; that is not a
        # usable query, so the hand-written pass gets this shape.
        assert _ptn_search_title("DC (2026)") == ""

    def test_ptn_leads_candidates(self):
        candidates = _search_title_candidates(
            "The.Fix.2026.2160p.NOW.WEB-DL.DD+5.1.H.265-playWEB", is_tv=False
        )
        assert candidates[0] == "The Fix"

    def test_year_in_title_kept_by_both_pass_orders(self):
        candidates = _search_title_candidates(
            "2001.A.Space.Odyssey.1968.2160p.UHD.BluRay.REMUX-GRP", is_tv=False
        )
        assert candidates[0] == "2001 A Space Odyssey"
        # The hand-written pass strips the title's own year, so it differs and
        # is appended for the case where the PTN query matched nothing.
        assert "2001 A Space Odyssey" in candidates


class TestSeasonOnlyMarkerIsTv:
    """A bare Sxx season marker (no episode number) is a TV signal.

    Season packs drop the episode number ("Time.2021.S03.1080p.…"). Without this
    the item took the movie path: PTN's "Time" searched TMDb's film index,
    returned "About Time" (2013), the _title_matches guard rejected it, and the
    item was skipped even though TMDb has the series with a poster.
    """

    def test_is_tv_title_season_only(self):
        assert _is_tv_title("Time.2021.S03.1080p.iP.WEB-DL.AAC2.0.H.264-playWEB") is True

    def test_is_tv_title_episode_markers(self):
        assert _is_tv_title("The Gentlemen 2024 S02E03 1080p AMZN WEB-DL") is True
        assert _is_tv_title("MobLand 2x5 2160p") is True

    def test_is_tv_title_resolution_tags_are_not_seasons(self):
        assert _is_tv_title("The.Fix.2026.2160p.NOW.WEB-DL.playWEB") is False
        assert _is_tv_title("Movie.2026.S3X.1080p.WEB-DL") is False

    def test_tv_path_leads_with_ptn_series_name(self):
        assert _search_title_candidates(
            "Time.2021.S03.1080p.iP.WEB-DL.AAC2.0.H.264-playWEB", is_tv=True
        )[0] == "Time"
        assert _search_title_candidates(
            "The.Expanse.S06.2160p.WEB-DL.H.265-GROUP", is_tv=True
        )[0] == "The Expanse"


class TestLanguageTagOnlyAfterTitle:
    """A language tag is noise after the title, a title word otherwise."""

    def test_english_after_the_title_is_dropped(self):
        # "Awarapan 2 … Hindi ESub" is a release; both tags go.
        assert _clean_search_title("Awarapan.2.2019.Hindi.English.1080p") == "Awarapan 2"

    def test_english_word_in_a_title_survives(self):
        # No release digits, so the name is a plain title and no language pass runs.
        assert _clean_search_title("English") == "English"
        assert _clean_search_title("The English Patient") == "The English Patient"


class TestSceneGroupInsideParenthesis:
    """A group tag wrapped in a trailing parenthesis still has to go.

    "Toy Story 5 2026 (… English - HONE)" puts the "-HONE" behind a ")" the
    first SCENE_GROUP_RE pass cannot see, so the group name leaked into the
    query.
    """

    def test_parenthesised_release_tail_is_removed(self):
        raw = "Toy Story 5 2026 (2160p MA WEB-DL Hybrid H265 DV HDR DDP 5.1 English - HONE)"
        assert _clean_search_title(raw) == "Toy Story 5"


class TestSceneReleasesSizeSuffix:
    """r/SceneReleases hangs "(resolution; size)" off its release lines.

    Flattening the bracket exposed the resolution to the noise pass but left
    the size behind as "; 19 1 GiB", and that residue trailed the "~GROUP" tag
    so the tail-only tilde strip missed it too. The query built from
    "Spider-Man.Brand.New.Day.2026.…~COD3D (2160p; 19.1 GiB)" then read
    "Spider Man Brand New Day iNTERNAL ~COD3D ; 19 1 GiB", matched no TMDb
    title, and the item was skipped with no poster.
    """

    def test_size_suffix_is_removed_whole(self):
        raw = (
            "Spider-Man.Brand.New.Day.2026.iNTERNAL.4K.HEVC.10bit.HDR.Dual."
            "Layer.IMAX.HYBRID.Dolby.TrueHD.7.1~COD3D (2160p; 19.1 GiB)"
        )
        assert _clean_search_title(raw) == "Spider Man Brand New Day iNTERNAL"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Some.Movie.2026.1080p.WEB-DL (1080p; 4.2 GiB)", "Some Movie"),
            ("Some.Movie.2026.1080p.WEB-DL (2160p; 12 GB)", "Some Movie"),
            ("Some.Movie.2026.1080p.WEB-DL (19.1 GiB)", "Some Movie"),
            ("Some.Movie.2026.1080p.WEB-DL (2160p)", "Some Movie"),
        ],
    )
    def test_metadata_suffix_variants(self, raw, expected):
        assert _clean_search_title(raw) == expected

    def test_parenthesis_that_is_part_of_the_title_is_kept(self):
        # "(500)" names no resolution or size, so it is not the metadata tail.
        assert _clean_search_title("(500) Days of Summer") == "500 Days of Summer"


class TestTitleYear:
    """The release year a name carries is the *last* year-like token.

    A scene name closes with its release year, and a title that is itself a
    year ("1917 2019 …") puts that first, so the last token is the release year
    in both shapes.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Blade.Runner.2049.2021.1080p.BluRay.x264", "2021"),
            ("1917.2019.1080p.BluRay.x264", "2019"),
            ("Oppenheimer.2023.1080p.BluRay.x264", "2023"),
            ("A Prophet", None),
            ("", None),
        ],
    )
    def test_last_year_like_token(self, raw, expected):
        assert _title_year(raw) == expected


class TestImdbLinkForms:
    """The two IMDb link shapes are distinguished, and search links upgrade."""

    _SEARCH = '<a href="https://www.imdb.com/find?q=X&amp;s=tt"><b>IMDb</b></a>'
    _TITLE = '<a href="https://www.imdb.com/title/tt28498219/"><b>IMDb</b></a>'

    def test_search_link_detected_not_title_link(self):
        assert _has_imdb_search_link(self._SEARCH) is True
        assert _has_imdb_title_link(self._SEARCH) is False

    def test_title_link_detected_not_search_link(self):
        assert _has_imdb_title_link(self._TITLE) is True
        assert _has_imdb_search_link(self._TITLE) is False

    def test_upgrade_rewrites_href_only(self):
        assert _upgrade_imdb_anchor(self._SEARCH, "tt28498219") == self._TITLE

    def test_upgrade_handles_trailing_slash_find(self):
        # CinemaCity ships /find/?q=…&ttype=ft, not the /find?q=… we emit.
        upgraded = _upgrade_imdb_anchor(
            '<a href="https://www.imdb.com/find/?q=X&ttype=ft">IMDb</a>', "tt1"
        )
        assert upgraded == '<a href="https://www.imdb.com/title/tt1/">IMDb</a>'

    def test_nothing_to_upgrade_is_a_no_op(self):
        assert _upgrade_imdb_anchor(self._TITLE, "tt1") == self._TITLE
        assert _upgrade_imdb_anchor("", "tt1") == ""
        assert _upgrade_imdb_anchor(self._SEARCH, "") == self._SEARCH
