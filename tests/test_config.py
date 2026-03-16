from pathlib import Path

from core.config import Config, load_config


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
