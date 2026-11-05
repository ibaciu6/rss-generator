"""The fix stage's per-feed step switches."""
from __future__ import annotations

import pytest

from scripts.enrichers.postprocess_steps import (
    POSTPROCESS_STEPS,
    STEP_NAMES,
    known_steps,
    resolve_steps,
    step_enabled,
)

# ---- the registry -----------------------------------------------------------


def test_the_registry_names_every_switch_the_fix_stage_has() -> None:
    assert STEP_NAMES == (
        "titles",
        "images",
        "labels",
        "links",
        "layout",
        "chrome",
        "magnet",
        "separators",
    )
    assert known_steps() == list(STEP_NAMES)
    assert all(step.summary for step in POSTPROCESS_STEPS)


def test_resolution_absent_means_every_step() -> None:
    assert resolve_steps(None) == STEP_NAMES


def test_resolution_empty_list_means_no_step() -> None:
    assert resolve_steps([]) == ()


def test_resolution_normalizes_to_run_order() -> None:
    """The steps run in registry order, so a config list is a set, not a script."""
    assert resolve_steps(["layout", "images"]) == ("images", "layout")
    assert resolve_steps(["layout", "layout"]) == ("layout",)


def test_an_unknown_step_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown postprocess step"):
        resolve_steps(["layout", "nosuchstep"])


def test_step_enabled_treats_an_absent_list_as_all_enabled() -> None:
    # A direct caller that predates the switch keeps the old behaviour.
    assert step_enabled(None, "layout") is True
    assert step_enabled((), "layout") is False


# ---- the switches in the fix stage -----------------------------------------

POSTER_JPG = '<img src="https://image.tmdb.org/t/p/w500/poster.jpg">'
RAW_POSTER = '<img src="https://site.example/x.jpg">'
TRAILER = '<a href="https://x"><b>Trailer</b></a>'
TRAILER_AGAIN = '<a href="https://y"><b>Trailer</b></a>'


def _fix(desc: str, feed: str = "unknown-feed.xml", **kwargs) -> str:
    from scripts import fix_feeds as ff

    return ff.fix_description_html(desc, feed, **kwargs)


def test_an_empty_step_list_is_a_no_op() -> None:
    desc = f'{RAW_POSTER}<br>{TRAILER}{TRAILER_AGAIN}<br>Body | '
    assert _fix(desc, steps=()) == desc


def test_disabling_images_leaves_the_poster_src_and_style_alone() -> None:
    desc = f"{RAW_POSTER}<br>Body"
    out = _fix(desc, steps=("layout", "links"))
    assert out == desc
    assert 'style="' not in out


def test_disabling_layout_leaves_the_source_order_alone() -> None:
    desc = f"{POSTER_JPG}<br>{TRAILER}<br>Body"
    out = _fix(desc, steps=("images",))
    # The image is still normalized, but nothing is lifted or re-ordered: the
    # links stay where the source put them, ahead of the body.
    assert out.index("<img") < out.index("Trailer") < out.index("Body")


def test_disabling_links_keeps_the_duplicate_anchor() -> None:
    desc = f"{POSTER_JPG}<br>{TRAILER}<br>{TRAILER_AGAIN}"
    out = _fix(desc, steps=("images",))
    assert out.count("Trailer") == 2


def test_disabling_separators_keeps_the_dangling_bar() -> None:
    desc = "Body | "
    assert _fix(desc, steps=()) == desc
    assert _fix(desc, steps=("separators",)) == "Body"


def test_a_feed_can_switch_a_step_off_from_config(monkeypatch) -> None:
    import scripts.fix_feeds as ff

    monkeypatch.setitem(ff.FEED_POSTPROCESS, "quiet.xml", ())
    desc = f"{RAW_POSTER}<br>{TRAILER}{TRAILER_AGAIN} | "
    assert ff.fix_description_html(desc, "quiet.xml") == desc


def test_an_unknown_feed_runs_every_step(monkeypatch) -> None:
    """A file the config does not name keeps the historical full pass list."""
    import scripts.fix_feeds as ff

    monkeypatch.setattr(ff, "FEED_POSTPROCESS", {})
    desc = "Body | "
    assert ff.fix_description_html(desc, "not-in-config.xml") == "Body"


# ---- the title pass ---------------------------------------------------------


def test_the_title_pass_adds_the_year() -> None:
    from scripts import fix_feeds as ff

    assert ff.fix_title("Some Movie 2026", "unknown-feed.xml") == "Some Movie (2026)"


def test_disabling_titles_leaves_the_title_untouched(monkeypatch) -> None:
    import scripts.fix_feeds as ff

    monkeypatch.setitem(ff.FEED_POSTPROCESS, "quiet.xml", ())
    assert ff.fix_title("Some Movie 2026", "quiet.xml") == "Some Movie 2026"


def test_the_url_year_recovery_is_part_of_the_title_step() -> None:
    from scripts import fix_feeds as ff

    assert ff.fix_title("Some Movie", "unknown-feed.xml", "https://x/2024/9/") == (
        "Some Movie (2024)"
    )


# ---- the whole loop ---------------------------------------------------------


def test_the_loop_is_a_no_op_with_no_steps() -> None:
    from scripts import fix_feeds as ff

    desc = f'{RAW_POSTER}<br>{TRAILER}{TRAILER_AGAIN}<br>Body | '
    assert ff.apply_description_passes(desc, "unknown-feed.xml", steps=()) == desc


def test_the_loop_reaches_a_fixed_point() -> None:
    from scripts import fix_feeds as ff

    desc = (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg"><br>'
        '<a href="https://x"><b>Trailer</b></a><br>'
        '<a href="https://y"><b>Trailer</b></a><br>'
        "Body | "
    )
    once = ff.apply_description_passes(desc, "unknown-feed.xml")
    assert ff.apply_description_passes(once, "unknown-feed.xml") == once


def test_the_magnet_step_can_be_switched_off(monkeypatch) -> None:
    import scripts.fix_feeds as ff

    magnet = "magnet:?xt=urn:btih:abc"
    desc = '<img src="https://x/y.jpg"><br>Body'
    monkeypatch.setitem(ff.FEED_POSTPROCESS, "torrent.xml", ("layout",))
    out = ff.apply_description_passes(
        desc, "torrent.xml", link=magnet, steps=("layout",)
    )
    assert "Magnet" not in out
    with_magnet = ff.apply_description_passes(
        desc, "torrent.xml", link=magnet, steps=("layout", "magnet")
    )
    assert "Magnet" in with_magnet


def test_the_chrome_step_can_be_switched_off(monkeypatch) -> None:
    import scripts.fix_feeds as ff

    monkeypatch.setitem(ff.FEED_REMOVALS, "blog.xml", ("head-meta",))
    desc = "<head><meta charset='utf-8'></head><p>Body.</p>"
    without = ff.apply_description_passes(desc, "blog.xml", steps=())
    assert "<meta" in without
