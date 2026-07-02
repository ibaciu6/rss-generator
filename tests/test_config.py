from pathlib import Path

import pytest

from core.config import Config, SiteConfig, load_config


def test_load_config_example(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  sitefilme:
    url: "https://sitefilme.com/"
    method: "playwright"
    item_selector: "//article"
    title_selector: ".//h2/a/text()"
    link_selector: ".//h2/a/@href"
    description_selector: ".//p/text()"
    feed_file: "sitefilme.xml"
    fallback_urls:
      - "https://www.sitefilme.com/"
    blocked_final_hosts:
      - "56.com"
    allowed_final_hosts:
      - "sitefilme.com"
    allow_empty_title: true
    detail_method: "http"
    detail_title_selector: "//h1/text()"
    detail_description_selector: "//meta[@name='description']/@content"
    max_items: 24
""",
        encoding="utf-8",
    )

    cfg: Config = load_config(cfg_path)
    assert len(cfg.sites) == 1
    site = cfg.sites[0]
    assert site.name == "sitefilme"
    assert site.url == "https://sitefilme.com/"
    assert site.method == "playwright"
    assert site.feed_file == "sitefilme.xml"
    assert site.fallback_urls == ["https://www.sitefilme.com/"]
    assert site.blocked_final_hosts == ["56.com"]
    assert site.allowed_final_hosts == ["sitefilme.com"]
    assert site.allow_empty_title is True
    assert site.detail_method == "http"
    assert site.detail_title_selector == "//h1/text()"
    assert site.detail_description_selector == "//meta[@name='description']/@content"
    assert site.max_items == 24


def test_load_config_required_content_marker_groups(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  demo:
    url: "https://example.com/"
    method: "http"
    item_selector: "//a"
    title_selector: "text()"
    link_selector: "@href"
    required_content_marker_groups:
      - ["a", "b"]
      - ["c"]
""",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    site = cfg.sites[0]
    assert site.required_content_marker_groups == (("a", "b"), ("c",))


def test_load_config_normalizes_httpx_method(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  hackernews:
    url: "https://news.ycombinator.com/"
    method: "httpx"
    item_selector: "//tr"
    title_selector: ".//a/text()"
    link_selector: ".//a/@href"
""",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.sites[0].method == "http"


def test_production_sites_yaml_has_trailer_and_imdb_without_quoted_youtube_query() -> None:
    cfg = load_config(Path("config/sites.yaml"))
    for site in cfg.sites:
        blob = f"{site.description_selector or ''} {site.detail_description_selector or ''}"
        assert "youtube.com/results" in blob, site.name
        assert "imdb.com/find" in blob, site.name
        assert "search_query=%22" not in blob, f"{site.name}: drop literal quotes around title in YouTube search_query"


# ── Validation tests ────────────────────────────────────────────


def test_site_config_validates_empty_name() -> None:
    with pytest.raises(ValueError, match="Site name cannot be empty"):
        SiteConfig(
            name="  ",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
        )


def test_site_config_validates_empty_url() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        SiteConfig(
            name="example",
            url="",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
        )


def test_site_config_validates_url_format() -> None:
    with pytest.raises(ValueError, match="Must start with http"):
        SiteConfig(
            name="example",
            url="ftp://bad.example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
        )


def test_site_config_validates_empty_selectors() -> None:
    with pytest.raises(ValueError, match="Item selector cannot be empty"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
        )


def test_site_config_validates_negative_max_items() -> None:
    with pytest.raises(ValueError, match="max_items must be positive"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            max_items=-5,
        )


def test_site_config_validates_invalid_language() -> None:
    with pytest.raises(ValueError, match="Invalid language format"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            language="english",
        )


def test_site_config_validates_invalid_title_transform() -> None:
    with pytest.raises(ValueError, match="title_transform must be None or"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            title_transform="invalid_style",
        )


def test_site_config_validates_invalid_pages() -> None:
    with pytest.raises(ValueError, match="pages must be at least 1"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            pages=0,
        )


def test_load_config_empty_yaml_returns_empty_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text("", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.sites == []


def test_load_config_no_sites_key_returns_empty_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text("other_key: value", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.sites == []
