"""Tests for the single-feed full-flow orchestrator (scripts/refresh_feed.py).

The three pipeline stages used to be reachable only as whole-directory sweeps,
which made iterating on one site slow and made a bare `generate` silently throw
away the previous run's enrichment. refresh_feed.py runs get -> enrich ->
process against a named subset and reports the resulting body-text length so a
truncated article is visible without opening the XML.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import refresh_feed as rf

SITES_YAML = """\
sites:
  gabriel-ursan:
    url: "https://gabrielursan.ro/"
    method: "rss"
    category: "blogs"
    item_selector: "//item"
    title_selector: "./title"
    link_selector: "./link"
    feed_file: "gabriel-ursan.xml"
  showrss:
    url: "https://showrss.info/"
    method: "rss"
    category: "torrents"
    item_selector: "//item"
    title_selector: "./title"
    link_selector: "./link"
    feed_file: "showrss.xml"
  retired:
    url: "https://retired.example/"
    method: "rss"
    category: "blogs"
    item_selector: "//item"
    title_selector: "./title"
    link_selector: "./link"
    feed_file: "retired.xml"
    enabled: false
"""


def _repo(tmp_path: Path) -> Path:
    """Lay out a fake repo root with sites.yaml and an empty feeds/ dir."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sites.yaml").write_text(SITES_YAML, encoding="utf-8")
    (tmp_path / "feeds").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _feed(feeds_dir: Path, name: str, bodies: list[str]) -> Path:
    items = "".join(f"<item><title>i</title><description>{b}</description></item>" for b in bodies)
    path = feeds_dir / name
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>t</title>{items}</channel></rss>',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Point refresh_feed at a temp repo and record which stages ran."""
    root = _repo(tmp_path)
    calls: list[str] = []

    def site_names(argv):
        # --site is a repeated flag: `--site a b` would be a parse error in the
        # real CLIs, so the focused stage mains must be fed one flag per name.
        out = []
        it = iter(argv)
        for arg in it:
            if arg == "--site":
                out.append(next(it))
        return out

    def fake_cli(argv):
        calls.append("get")
        return 0

    async def fake_enrich(argv):
        calls.append(f"enrich:{','.join(site_names(argv))}")
        return 0

    def fake_fix(argv):
        calls.append(f"process:{','.join(site_names(argv))}")
        return 0

    monkeypatch.setattr(rf, "REPO_ROOT", root)
    monkeypatch.setattr(rf, "FEEDS_DIR", root / "feeds")
    monkeypatch.setattr(rf, "CONFIG_FILE", root / "config" / "sites.yaml")
    monkeypatch.setattr(rf, "CACHE_FILE", root / "data" / "cache.json")
    monkeypatch.setattr(rf, "cli_main", fake_cli)
    monkeypatch.setattr(rf, "enrich_main", fake_enrich)
    monkeypatch.setattr(rf, "fix_main", fake_fix)
    return root, calls


class TestStageOrder:
    def test_runs_all_three_stages_for_one_site(self, fake_repo, capsys):
        _, calls = fake_repo
        assert rf.main(["--site", "gabriel-ursan"]) == 0
        # Order matters: a bare get would discard the enrichment that follows.
        assert calls == ["get", "enrich:gabriel-ursan", "process:gabriel-ursan"]
        assert "Full flow complete" in capsys.readouterr().out

    def test_get_receives_the_site_filter(self, fake_repo, monkeypatch):
        """Generation must be scoped too, or it rewrites all 70 feeds."""
        seen: list[list[str]] = []
        monkeypatch.setattr(rf, "cli_main", lambda argv: seen.append(argv) or 0)
        rf.main(["--site", "gabriel-ursan"])
        assert seen and "--site" in seen[0] and "gabriel-ursan" in seen[0]

    def test_get_emits_one_site_flag_per_name(self, fake_repo, monkeypatch):
        """`--site <a> <b>` would bind only the first name and leave the rest as
        unrecognized positionals, so each site must get its own flag. This is
        the bug that made multi-site refresh fail inside the generate stage."""
        seen: list[list[str]] = []
        monkeypatch.setattr(rf, "cli_main", lambda argv: seen.append(argv) or 0)
        rf.main(["--site", "gabriel-ursan", "--site", "showrss"])
        assert seen
        flags = [i for i, arg in enumerate(seen[0]) if arg == "--site"]
        assert len(flags) == 2, seen[0]
        values = [seen[0][i + 1] for i in flags]
        assert values == ["gabriel-ursan", "showrss"]

    def test_multiple_sites_reach_every_stage(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["--site", "gabriel-ursan", "--site", "showrss"]) == 0
        assert calls == [
            "get",
            "enrich:gabriel-ursan,showrss",
            "process:gabriel-ursan,showrss",
        ]

    def test_site_is_required(self, fake_repo):
        """A targeted run with no target would be an accidental full sweep."""
        _, calls = fake_repo
        with pytest.raises(SystemExit) as exc:
            rf.main([])
        assert exc.value.code == 2
        assert calls == []

    def test_accepts_the_xml_filename(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["--site", "showrss.xml"]) == 0
        assert calls[1] == "enrich:showrss.xml"


class TestSkip:
    def test_skip_enrich_omits_only_that_stage(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["--site", "gabriel-ursan", "--skip", "enrich"]) == 0
        assert calls == ["get", "process:gabriel-ursan"]

    def test_skip_get_still_enriches(self, fake_repo):
        """The footgun this script guards against is get-only, so --skip get must
        still be able to re-enrich an already generated feed."""
        _, calls = fake_repo
        assert rf.main(["--site", "gabriel-ursan", "--skip", "get"]) == 0
        assert calls == ["enrich:gabriel-ursan", "process:gabriel-ursan"]

    def test_skip_rejects_an_unknown_stage(self, fake_repo):
        _, calls = fake_repo
        with pytest.raises(SystemExit) as exc:
            rf.main(["--site", "gabriel-ursan", "--skip", "nope"])
        assert exc.value.code == 2
        assert calls == []


class TestValidation:
    def test_unknown_site_fails_before_any_work(self, fake_repo, capsys):
        _, calls = fake_repo
        assert rf.main(["--site", "typo"]) == 1
        assert calls == [], "a typo must cost nothing"
        assert "unknown site: typo" in capsys.readouterr().err

    def test_disabled_site_is_rejected(self, fake_repo, capsys):
        """A disabled site never generates, so running it would fail confusingly
        in the middle of the get stage."""
        _, calls = fake_repo
        assert rf.main(["--site", "retired"]) == 1
        assert calls == []
        assert "disabled" in capsys.readouterr().err

    def test_one_bad_site_rejects_the_whole_run(self, fake_repo):
        """Partial work is worse than none: the feed would be left half-updated."""
        _, calls = fake_repo
        assert rf.main(["--site", "gabriel-ursan", "--site", "typo"]) == 1
        assert calls == []


class TestFailureReporting:
    def test_nonzero_stage_is_reported(self, fake_repo, monkeypatch, capsys):
        monkeypatch.setattr(rf, "enrich_main", lambda argv: _boom())
        assert rf.main(["--site", "gabriel-ursan"]) == 1
        out = capsys.readouterr().out
        assert "FAILED" in out and "enrich" in out

    def test_later_stages_still_run_after_a_failure(self, fake_repo, monkeypatch, capsys):
        """An enrich failure must not hide a fix failure."""
        monkeypatch.setattr(rf, "enrich_main", lambda argv: _boom())
        monkeypatch.setattr(rf, "fix_main", lambda argv: _boom())
        assert rf.main(["--site", "gabriel-ursan"]) == 1
        assert "FAILED stages: enrich, process" in capsys.readouterr().out

    def test_raising_stage_does_not_abort_the_run(self, fake_repo, monkeypatch, capsys):
        _, calls = fake_repo
        monkeypatch.setattr(rf, "enrich_main", lambda argv: _raise())
        assert rf.main(["--site", "gabriel-ursan"]) == 1
        out = capsys.readouterr().out
        assert "RuntimeError" in out
        assert "process:gabriel-ursan" in calls, "process never ran"


def _boom():
    return 1


def _raise():
    raise RuntimeError("network gone")


class TestBodyReport:
    def test_flags_items_that_are_still_bare_excerpts(self, fake_repo, capsys):
        """The whole point of the report: a ~200-char body means the site's own
        RSS excerpt shipped instead of the fetched article."""
        root, _ = fake_repo
        _feed(root / "feeds", "gabriel-ursan.xml", ["x" * 5000, "y" * 180])
        assert rf.main(["--site", "gabriel-ursan", "--skip", "get", "--skip", "enrich", "--skip", "process"]) == 0
        out = capsys.readouterr().out
        assert "1 item(s) look like bare excerpts" in out
        assert "min=180" in out

    def test_clean_feed_reports_no_warning(self, fake_repo, capsys):
        root, _ = fake_repo
        _feed(root / "feeds", "gabriel-ursan.xml", ["x" * 9000, "y" * 4000])
        rf.main(["--site", "gabriel-ursan", "--skip", "get", "--skip", "enrich", "--skip", "process"])
        assert "bare excerpts" not in capsys.readouterr().out

    def test_report_can_be_suppressed(self, fake_repo, capsys):
        root, _ = fake_repo
        _feed(root / "feeds", "gabriel-ursan.xml", ["x" * 100])
        rf.main(["--site", "gabriel-ursan", "--skip", "get", "--skip", "enrich", "--skip", "process", "--no-report"])
        assert "Body text per item" not in capsys.readouterr().out

    def test_missing_feed_is_reported_not_crashed(self, fake_repo, capsys):
        """--skip get on a never-generated feed must not raise."""
        assert rf.main(["--site", "gabriel-ursan", "--skip", "get"]) == 0
        assert "MISSING" in capsys.readouterr().out

    def test_text_len_strips_tags_and_entities(self):
        assert rf._text_len("<p>Hello &amp; goodbye</p>") == len("Hello & goodbye")
        assert rf._text_len("") == 0
        assert rf._text_len("<p>a</p><p>b</p>") == 3, "tags collapse to one space"

    def test_feed_without_items_is_handled(self, fake_repo, capsys):
        root, _ = fake_repo
        (root / "feeds" / "gabriel-ursan.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel/></rss>',
            encoding="utf-8",
        )
        rf.main(["--site", "gabriel-ursan", "--skip", "get", "--skip", "enrich", "--skip", "process"])
        assert "no items" in capsys.readouterr().out

    def test_unparseable_feed_is_reported_not_crashed(self, fake_repo, capsys):
        root, _ = fake_repo
        (root / "feeds" / "gabriel-ursan.xml").write_text("<rss><channel>", encoding="utf-8")
        rf.main(["--site", "gabriel-ursan", "--skip", "get", "--skip", "enrich", "--skip", "process"])
        assert "unparseable" in capsys.readouterr().out


class TestNameSpellings:
    """`./start.sh one ghacks` must work as well as `--site ghacks`."""

    def test_bare_name_is_accepted(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["gabriel-ursan"]) == 0
        assert calls == ["get", "enrich:gabriel-ursan", "process:gabriel-ursan"]

    def test_bare_names_and_flags_combine(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["gabriel-ursan", "--site", "showrss"]) == 0
        assert calls[1] == "enrich:gabriel-ursan,showrss"

    def test_bare_name_accepts_the_xml_filename(self, fake_repo):
        _, calls = fake_repo
        assert rf.main(["showrss.xml"]) == 0
        assert calls[1] == "enrich:showrss.xml"

    def test_no_name_at_all_is_rejected(self, fake_repo):
        _, calls = fake_repo
        with pytest.raises(SystemExit) as exc:
            rf.main(["--skip", "get"])
        assert exc.value.code == 2
        assert calls == []
