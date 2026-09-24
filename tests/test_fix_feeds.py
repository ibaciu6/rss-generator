from scripts.fix_feeds import (
    STRIP_FIELD_SETS,
    fix_description_html,
    fix_poster_style,
    strip_label_fields,
)


def test_strip_label_fields_removes_empty_uflix_labels() -> None:
    desc = (
        "<img src=\"https://image.tmdb.org/t/p/w500/p.jpg\"><br/>"
        "<strong>Episode:</strong> 6<br/>"
        "<strong>Year:</strong> 2026<br/>"
        "<strong>Genres:</strong> <br/>"
        "<strong>IMDb:</strong> <br/>"
        '<a href="https://www.youtube.com/results?search_query=x">Trailer</a>'
    )
    out = strip_label_fields(desc, "uflix-episodes.xml")
    assert "Genres" not in out
    assert "IMDb:" not in out
    assert "<strong>Episode:</strong> 6" in out
    assert "<strong>Year:</strong> 2026" in out
    assert "Trailer" in out


def test_strip_label_fields_removes_uindex_stats_but_keeps_size() -> None:
    desc = (
        "<img src=\"https://image.tmdb.org/t/p/w500/p.jpg\"><br/>"
        "<strong>Size:</strong> 1.65 GB<br/>"
        "<strong>Uploaded:</strong> 2 days ago<br/>"
        "<strong>Seeds:</strong> 1,579<br/>"
        "<strong>Leechers:</strong> 289<br/>"
        '<a href="https://www.youtube.com/results?search_query=x">Trailer</a>'
    )
    for feed in ("uindex-movies.xml", "uindex-tv.xml"):
        out = strip_label_fields(desc, feed)
        assert "<strong>Size:</strong> 1.65 GB" in out
        assert "Uploaded" not in out
        assert "Seeds" not in out
        assert "Leechers" not in out
        assert "Trailer" in out


def test_strip_label_fields_noop_for_unlisted_feed() -> None:
    desc = "<strong>Genres:</strong> Drama<br/>"
    assert strip_label_fields(desc, "other.xml") == desc
    assert "other.xml" not in STRIP_FIELD_SETS


def test_strip_label_fields_is_idempotent() -> None:
    desc = (
        "<strong>Genres:</strong> <br/>"
        "<strong>IMDb:</strong> <br/>"
        '<a href="https://x">Trailer</a>'
    )
    once = strip_label_fields(desc, "uflix-episodes.xml")
    assert strip_label_fields(once, "uflix-episodes.xml") == once


def test_fix_poster_style_applies_pinned_size() -> None:
    desc = '<img src="https://image.tmdb.org/t/p/w780/p.jpg" width="800" style="max-width:999px;">'
    out = fix_poster_style(desc)
    assert 'src="https://image.tmdb.org/t/p/w342/p.jpg"' in out
    assert "width:300px" in out
    assert "max-height:450px" in out
    assert 'width="300"' in out
    assert 'loading="lazy"' in out
    assert 'width="800"' not in out
    assert "max-width:999px" not in out
    assert "w780" not in out


def test_fix_poster_style_uses_smaller_cinema_posters(monkeypatch) -> None:
    from scripts.fix_feeds import FEED_CATEGORIES

    monkeypatch.setitem(FEED_CATEGORIES, "cinema-feed.xml", "cinema")
    out = fix_poster_style(
        '<img src="https://image.tmdb.org/t/p/w780/p.jpg">', "cinema-feed.xml"
    )
    assert "width:150px" in out
    assert "max-height:225px" in out
    assert 'width="150"' in out
    assert "w185/p.jpg" in out
    assert "w780" not in out
    assert "width:300px" not in out


def test_fix_description_html_strips_and_resizes() -> None:
    desc = (
        "<img src=\"https://image.tmdb.org/t/p/w500/p.jpg\"><br/>"
        "<strong>Episode:</strong> 6<br/>"
        "<strong>Genres:</strong> <br/>"
        "<strong>IMDb:</strong> <br/>"
    )
    out = fix_description_html(desc, "uflix-episodes.xml")
    assert "Genres" not in out
    assert "IMDb:" not in out
    assert "width:300px" in out
    assert 'width="300"' in out
