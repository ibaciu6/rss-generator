"""Content modules: defaults, resolution and the placement pass."""
from __future__ import annotations

import pytest

from scripts.enrichers.content_modules import (
    CONTENT_MODULES,
    MODULE_BY_NAME,
    air_date_html,
    compose_description,
    default_modules,
    known_modules,
    module_enabled,
    resolve_modules,
    strip_air_date,
)

POSTER = '<img src="https://image.tmdb.org/t/p/w500/poster.jpg">'
PLAY = '<a href="https://example.com/player"><b>\u25b6 Play</b></a>'
TRAILER = '<a href="https://example.com/trailer"><b>Trailer</b></a>'
IMDB = '<a href="https://www.imdb.com/title/tt1/"><b>IMDb</b></a>'
TMDB = '<a href="https://www.themoviedb.org/movie/1"><b>TMDb</b></a>'
EPGUIDES = '<a href="https://epguides.com/Show/"><b>EpGuides</b></a>'
DATE = '<p id="feed-day"><strong>Air date:</strong> 2026-11-13</p>'


# ---- the registry -----------------------------------------------------------


def test_registry_order_is_the_default_layout() -> None:
    assert [m.name for m in CONTENT_MODULES] == [
        "air-date",
        "poster",
        "play",
        "trailer",
        "imdb",
        "tmdb",
        "epguides",
    ]
    assert MODULE_BY_NAME["air-date"].slot == "lead"
    assert MODULE_BY_NAME["poster"].slot == "poster"
    assert all(
        MODULE_BY_NAME[name].slot == "tail"
        for name in ("play", "trailer", "imdb", "tmdb", "epguides")
    )


def test_known_modules_matches_the_registry() -> None:
    assert known_modules() == [m.name for m in CONTENT_MODULES]


# ---- defaults ---------------------------------------------------------------


def test_a_streaming_movie_feed_gets_the_posters_links_and_play_block() -> None:
    assert default_modules(category="movies") == (
        "air-date",
        "poster",
        "play",
        "trailer",
        "imdb",
        "tmdb",
        "epguides",
    )


def test_every_streaming_section_gets_the_play_link() -> None:
    for category in ("movies", "episodes", "series", "torrents", "releases"):
        modules = default_modules(category=category)
        assert "play" in modules, category
        assert modules.index("play") > modules.index("poster")


def test_a_series_feed_gets_the_air_date() -> None:
    assert "air-date" in default_modules(kind="series", category="series")


def test_a_torrent_feed_also_gets_the_play_link() -> None:
    modules = default_modules(category="torrents")
    assert "play" in modules
    assert modules.index("play") > modules.index("poster")


def test_an_explicit_player_server_list_enables_play_off_category() -> None:
    assert "play" in default_modules(category="movies", player_servers=("cinesrc",))


def test_an_explicitly_empty_player_server_list_disables_play() -> None:
    assert "play" not in default_modules(category="torrents", player_servers=())


def test_an_article_feed_gets_no_modules() -> None:
    assert default_modules(category="tech") == ()
    assert default_modules(category="news") == ()


def test_enhance_mode_none_disables_every_module() -> None:
    assert default_modules(category="movies", enhance_mode="none") == ()


# ---- resolution -------------------------------------------------------------


def test_absent_means_defaults() -> None:
    assert resolve_modules(None, category="movies") == default_modules(category="movies")


def test_an_empty_list_means_run_nothing() -> None:
    assert resolve_modules([], category="movies") == ()


def test_an_explicit_list_keeps_the_callers_tail_order() -> None:
    assert resolve_modules(["poster", "trailer", "play"], category="movies") == (
        "poster",
        "trailer",
        "play",
    )


def test_resolution_drops_duplicates() -> None:
    assert resolve_modules(["poster", "play", "play"], category="movies") == (
        "poster",
        "play",
    )


def test_an_unknown_module_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown content module"):
        resolve_modules(["poster", "nosuchmodule"], category="movies")


def test_module_enabled_treats_an_absent_list_as_all_enabled() -> None:
    # A direct caller that predates the registry keeps the old behaviour.
    assert module_enabled(None, "poster") is True
    assert module_enabled((), "poster") is False


# ---- the air-date paragraph -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Fri, 13 Nov 2026 00:00:00 +0000", '2026-11-13</p>'),
        ("2026-11-13", '2026-11-13</p>'),
        ("2026-11-13T10:30:00Z", '2026-11-13</p>'),
    ],
)
def test_air_date_html_accepts_rfc_and_iso(value: str, expected: str) -> None:
    assert air_date_html(value).endswith(expected)


def test_air_date_html_is_empty_without_a_date() -> None:
    assert air_date_html(None) == ""
    assert air_date_html("not a date") == ""


def test_strip_air_date_removes_the_generated_paragraph() -> None:
    desc = f"<p>Body.</p><br>{DATE}"
    assert strip_air_date(desc) == "<p>Body.</p>"


# ---- composition ------------------------------------------------------------


def test_stray_episode_title_is_removed_but_body_html_survives() -> None:
    desc = (
        f'{POSTER}<br>Fallen<br><p class="synopsis"><strong>Body:</strong> '
        'The story continues.</p><br>'
        f'{PLAY}'
    )
    out = compose_description(desc, ("air-date", "poster", "play"))

    assert "Fallen" not in out
    assert '<p class="synopsis"><strong>Body:</strong> The story continues.</p>' in out
    assert POSTER in out and PLAY in out
    assert compose_description(out, ("air-date", "poster", "play")) == out


def test_the_layout_is_date_poster_body_then_links() -> None:
    desc = f"{POSTER}<br>{PLAY}<br>Body text<br>{TRAILER}<br>{DATE}"
    out = compose_description(
        desc, ("air-date", "poster", "play", "trailer")
    )
    assert out == f"{DATE}<br>{POSTER}<br>Body text<br>{PLAY}<br>{TRAILER}"


def test_an_explicit_tail_order_is_honoured() -> None:
    desc = f"{POSTER}<br>{PLAY}<br>Body<br>{TRAILER}"
    out = compose_description(desc, ("poster", "trailer", "play"))
    assert out == f"{POSTER}<br>Body<br>{TRAILER}<br>{PLAY}"


def test_the_full_registry_order_is_play_trailer_imdb_tmdb_epguides() -> None:
    desc = (
        f"{POSTER}<br>{EPGUIDES}<br>Body<br>{TMDB}<br>{IMDB}<br>"
        f"{TRAILER}<br>{PLAY}"
    )
    out = compose_description(
        desc, ("air-date", "poster", "play", "trailer", "imdb", "tmdb", "epguides")
    )
    positions = [out.index(label) for label in ("\u25b6 Play", "Trailer", "IMDb", "TMDb", "EpGuides")]
    assert positions == sorted(positions)
    assert out.index("Body") < positions[0]


def test_a_disabled_poster_is_never_deleted() -> None:
    """Article feeds can carry site images; disabling poster stops placement,
    it does not remove what the source wrote."""
    desc = f"{POSTER}<br>Body<br>{TRAILER}"
    out = compose_description(desc, ("trailer",))
    assert out.startswith(POSTER)
    assert "Body" in out
    assert out.endswith(TRAILER)


def test_a_disabled_play_link_stays_where_the_source_put_it() -> None:
    desc = f"{POSTER}<br>{PLAY}<br>Body"
    out = compose_description(desc, ("poster",))
    assert out == desc


def test_a_disabled_air_date_retires_the_generated_paragraph() -> None:
    """The paragraph is the pipeline's own markup, not site prose, so turning
    the module off removes the copy an earlier run generated."""
    desc = f"{DATE}<br>{POSTER}<br>Body"
    out = compose_description(desc, ("poster",))
    assert "feed-day" not in out
    assert out == f"{POSTER}<br>Body"


def test_the_air_date_is_built_from_pubdate_when_missing() -> None:
    out = compose_description(
        f"{POSTER}<br>Body",
        ("air-date", "poster"),
        pub_date="Fri, 13 Nov 2026 00:00:00 +0000",
    )
    assert out.startswith('<p id="feed-day">')
    assert "2026-11-13" in out
    assert out.index("feed-day") < out.index("<img")


def test_every_player_server_label_counts_as_the_play_module() -> None:
    old = '<a href="https://cinesrc.st/x"><b>CineSrc</b></a>'
    out = compose_description(f"{POSTER}<br>Body<br>{old}", ("poster", "play"))
    assert out == f"{POSTER}<br>Body<br>{old}"


def test_an_empty_module_list_is_a_no_op() -> None:
    desc = f"<p>Intro.</p>{IMDB}{POSTER}"
    assert compose_description(desc, ()) == desc


def test_an_absent_module_list_is_a_no_op() -> None:
    desc = f"<p>Intro.</p>{IMDB}{POSTER}"
    assert compose_description(desc, None) == desc


@pytest.mark.parametrize(
    "modules",
    [
        ("air-date", "poster", "play", "trailer"),
        ("poster", "trailer", "play"),
        ("poster",),
        ("trailer",),
    ],
)
def test_composition_is_idempotent(modules: tuple[str, ...]) -> None:
    desc = (
        f"{POSTER}<br>{PLAY}<br>Body text<br>{TRAILER}<br>{DATE}"
    )
    once = compose_description(desc, modules)
    assert compose_description(once, modules) == once


def test_repeated_composition_never_accumulates_breaks() -> None:
    desc = f"{DATE}<br>{PLAY}<br>{POSTER}<br>Body<br>{TRAILER}"
    once = compose_description(desc, ("air-date", "poster", "play", "trailer"))
    assert "<br><br>" not in once
    assert not once.startswith("<br>")
    assert not once.endswith("<br>")
