from pathlib import Path

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


# ---- --site filtering -------------------------------------------------------


class TestSiteFilter:
    """`--site NAME` must narrow a run to the named feed instead of sweeping
    every file in feeds/."""

    @staticmethod
    def _setup(tmp_path, monkeypatch, declared, written=None):
        """Create feeds for `declared` sites, materialising only `written` files.

        Defaults to writing all of them; passing a smaller `written` list models a
        site that is configured but whose feed was never generated.
        """
        import scripts.fix_feeds as ff

        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        for name in written if written is not None else declared:
            (feeds_dir / name).write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<rss version="2.0"><channel><title>t</title></channel></rss>',
                encoding="utf-8",
            )
        config_file = tmp_path / "sites.yaml"
        config_file.write_text(
            "sites:\n"
            + "".join(
                f"  {Path(name).stem}:\n"
                f'    url: "https://example.com/"\n'
                '    method: "rss"\n'
                '    item_selector: "//item"\n'
                '    title_selector: "./title"\n'
                '    link_selector: "./link"\n'
                f'    feed_file: "{name}"\n'
                for name in declared
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)
        return feeds_dir

    def _run(self, tmp_path, monkeypatch, argv, declared=("a.xml", "b.xml"), written=None):
        import scripts.fix_feeds as ff

        self._setup(tmp_path, monkeypatch, declared, written)
        return ff.main(argv)

    def test_no_site_flag_processes_every_feed(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, [])
        out = capsys.readouterr().out
        assert "Processing 2 feeds" in out

    def test_site_flag_processes_only_that_feed(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a"])
        out = capsys.readouterr().out
        assert "Processing 1 feeds" in out

    def test_site_flag_accepts_the_xml_filename(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a.xml"])
        assert "Processing 1 feeds" in capsys.readouterr().out

    def test_site_flag_is_repeatable(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a", "--site", "b"])
        assert "Processing 2 feeds" in capsys.readouterr().out

    def test_unknown_site_fails_instead_of_sweeping(self, tmp_path, monkeypatch, capsys):
        """A typo must not quietly rewrite all 70 feeds."""
        assert self._run(tmp_path, monkeypatch, ["--site", "typo"]) == 1
        assert "unknown site: typo" in capsys.readouterr().err
        assert "Processing" not in capsys.readouterr().out

    def test_configured_but_ungenerated_feed_fails(self, tmp_path, monkeypatch, capsys):
        """Targeting a site whose feed file does not exist is a real problem for
        a single-feed run, unlike in a full sweep."""
        assert self._run(
            tmp_path, monkeypatch, ["--site", "gone"],
            declared=("a.xml", "gone.xml"), written=("a.xml",),
        ) == 1
        assert "no such feed file" in capsys.readouterr().err
