"""Tests for the --site filter on the generation engine."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.engine import GenerationEngine


def _cfg(*sites):
    return SimpleNamespace(sites=list(sites))


def _site(name: str, feed_file: str | None = None, enabled: bool = True):
    return SimpleNamespace(name=name, feed_file=feed_file or f"{name}.xml", enabled=enabled)


def _engine(cfg, only=None) -> GenerationEngine:
    return GenerationEngine(
        config=cfg,
        cache_path=Path("data/cache.json"),
        feeds_dir=Path("feeds"),
        only_sites=only,
    )


@pytest.fixture
def cfg():
    return _cfg(
        _site("alpha"),
        _site("beta"),
        _site("showrss"),
        _site("gamma", enabled=False),
    )


class TestSiteFilter:
    def test_no_filter_selects_every_enabled_site(self, cfg):
        selected, unmatched = _engine(cfg)._select_sites()
        assert [s.name for s in selected] == ["alpha", "beta", "showrss"]
        assert unmatched == []

    def test_filters_to_one_site(self, cfg):
        selected, unmatched = _engine(cfg, ["alpha"])._select_sites()
        assert [s.name for s in selected] == ["alpha"]
        assert unmatched == []

    def test_accepts_feed_file_with_or_without_xml(self, cfg):
        for value in ("showrss", "showrss.xml", "  showrss.xml  "):
            selected, unmatched = _engine(cfg, [value])._select_sites()
            assert [s.name for s in selected] == ["showrss"], value
            assert unmatched == [], f"{value} wrongly reported unmatched"

    def test_accepts_multiple_sites(self, cfg):
        selected, _ = _engine(cfg, ["alpha", "showrss"])._select_sites()
        assert sorted(s.name for s in selected) == ["alpha", "showrss"]

    def test_reports_only_genuinely_unknown_names(self, cfg):
        selected, unmatched = _engine(cfg, ["alpha", "ghost", "phantom.xml"])._select_sites()
        assert [s.name for s in selected] == ["alpha"]
        assert unmatched == ["ghost", "phantom.xml"]

    def test_never_selects_a_disabled_site(self, cfg):
        selected, unmatched = _engine(cfg, ["gamma"])._select_sites()
        assert selected == []
        assert unmatched == ["gamma"]

    def test_empty_list_means_no_filter(self, cfg):
        selected, unmatched = _engine(cfg, [])._select_sites()
        assert len(selected) == 3
        assert unmatched == []


class TestRunRejectsEmptySelection:
    @pytest.mark.asyncio
    async def test_raises_when_nothing_matches(self, cfg):
        with pytest.raises(ValueError, match="No enabled sites"):
            await _engine(cfg, ["does-not-exist"]).run()

    @pytest.mark.asyncio
    async def test_raises_when_config_is_all_disabled(self):
        empty = _cfg(_site("only", enabled=False))
        with pytest.raises(ValueError, match="No enabled sites"):
            await _engine(empty).run()
