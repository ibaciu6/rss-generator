from pathlib import Path

from core.config import Config, load_config


def test_load_config_from_sites_directory(tmp_path: Path) -> None:
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    (sites_dir / "alpha.yaml").write_text(
        """
url: "https://alpha.example/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "alpha.xml"
""",
        encoding="utf-8",
    )
    (sites_dir / "beta.yaml").write_text(
        """
url: "https://beta.example/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "beta.xml"
""",
        encoding="utf-8",
    )
    cfg = load_config(sites_dir)
    assert {s.name for s in cfg.sites} == {"alpha", "beta"}


def test_load_config_from_movies_and_series_directories(tmp_path: Path) -> None:
    sites_dir = tmp_path / "sites"
    (sites_dir / "movies").mkdir(parents=True)
    (sites_dir / "series").mkdir(parents=True)
    (sites_dir / "movies" / "m1.yaml").write_text(
        """
url: "https://movies.example/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "m1.xml"
category: movies
""",
        encoding="utf-8",
    )
    (sites_dir / "series" / "s1.yaml").write_text(
        """
url: "https://series.example/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "s1.xml"
category: episodes
""",
        encoding="utf-8",
    )
    cfg = load_config(sites_dir)
    by_name = {s.name: s for s in cfg.sites}
    assert set(by_name) == {"m1", "s1"}
    assert by_name["m1"].config_bucket == "movies"
    assert by_name["s1"].config_bucket == "series"


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


def test_production_site_configs_have_trailer_and_imdb_without_quoted_youtube_query() -> None:
    root = Path("config/sites")
    cfg = load_config(root)
    for site in cfg.sites:
        blob = f"{site.description_selector or ''} {site.detail_description_selector or ''}"
        assert "youtube.com/results" in blob, site.name
        assert "imdb.com/find" in blob, site.name
        assert "search_query=%22" not in blob, f"{site.name}: drop literal quotes around title in YouTube search_query"
