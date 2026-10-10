from pathlib import Path

import pytest

from core.config import Config, SiteConfig, load_config, resolve_feed_files


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


def test_keep_titles_is_read_from_the_site_config(tmp_path: Path) -> None:
    """uindex publishes the torrent name exactly as the tracker writes it
    ("Crooked Miles (2026) [720p] [WEBRip]"). `load_config` builds SiteConfig
    from explicit kwargs rather than `**cfg`, so a field that is never passed
    silently reads as its default -- the flag must actually reach the site."""
    base = """
sites:
  uindex:
    url: "https://uindex.org/"
    method: "http"
    item_selector: "//tr"
    title_selector: ".//a/text()"
    link_selector: ".//a/@href"
    feed_file: "uindex.xml"
"""
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(base, encoding="utf-8")
    assert load_config(cfg_path).sites[0].keep_titles is False
    cfg_path.write_text(base + "    keep_titles: true\n", encoding="utf-8")
    assert load_config(cfg_path).sites[0].keep_titles is True


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


def test_load_config_accepts_rss_method_without_xpath_selectors(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  scenereleases:
    url: "https://www.reddit.com/r/SceneReleases/.rss"
    method: "rss"
    feed_file: "scenereleases.xml"
    category: "torrents"
    language: "en"
    max_items: 25
""",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert len(cfg.sites) == 1
    site = cfg.sites[0]
    assert site.method == "rss"
    assert site.feed_file == "scenereleases.xml"
    assert site.max_items == 25


def test_load_config_rejects_unknown_method(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  demo:
    url: "https://example.com/"
    method: "requests"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid method: requests"):
        load_config(cfg_path)


def test_load_config_rejects_unknown_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  demo:
    url: "https://example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    max_itemz: 24
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown configuration key"):
        load_config(cfg_path)


def test_load_config_rejects_duplicate_feed_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sites.yaml"
    cfg_path.write_text(
        """
sites:
  first:
    url: "https://first.example/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "shared.xml"
  second:
    url: "https://second.example/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "shared.xml"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate feed_file"):
        load_config(cfg_path)


def test_production_sites_yaml_has_trailer_and_imdb_without_quoted_youtube_query() -> None:
    from scripts.enrich_feeds import _resolve_mode

    cfg = load_config(Path("config/sites.yaml"))
    for site in cfg.sites:
        # Native RSS/Atom feeds carry their own descriptions; no XPath selectors.
        # JSON API feeds (TMDB, etc.) have trailer/IMDb links added in the parser,
        # not in the XPath selectors.
        if site.method == "rss" or site.json_item_path:
            continue
        blob = f"{site.description_selector or ''} {site.detail_description_selector or ''}"
        if not blob.strip():
            # Streaming feeds (watchtv, pontv) ship no description selector at
            # all: the streaming enricher builds the poster and the trailer/
            # IMDb links itself, with the year in the query. Article feeds can
            # be selector-less too (cazanul): the feed URL is an RSS document,
            # so generation reads the site excerpt and the article enricher
            # fetches the body. Either way a selector-less site must be one the
            # enricher covers, never an oversight.
            assert _resolve_mode(site.category, site.enhance_mode) in (
                "streaming",
                "article",
            ), site.name
            continue
        assert "youtube.com/results" in blob, site.name
        assert "imdb.com/find" in blob, site.name
        assert "search_query=%22" not in blob, f"{site.name}: drop literal quotes around title in YouTube search_query"


# ── Validation tests ────────────────────────────────────────────


def test_production_sources_fetch_a_url_that_serves_that_format() -> None:
    """Regressions that silently dropped feeds from the published set.

    ``d0e5f421`` set hackread to ``method: rss`` while ``url`` stayed on the
    homepage, so the engine parsed HTML as RSS and hackread vanished; ``e65b4343``
    replaced atlantic-movies' TMDb API endpoint with the website to silence
    gitleaks, so the JSON fetch had no JSON to read. Neither raises anything a
    reader would notice -- the feed is simply absent from the OPML.
    """
    cfg = load_config(Path("config/sites.yaml"))
    sites = {site.name: site for site in cfg.sites}

    hackread = sites["hackread"]
    assert hackread.method == "rss"
    assert hackread.url.endswith("/feed/"), hackread.url

    # No URL in config may carry an api_key parameter -- literal or placeholder:
    # that text is what gitleaks flags, and when it sits on site.url it is what
    # the OPML/index write as the Source link. Keys are injected at request time
    # from api_key_env instead.
    for site in cfg.sites:
        for url in [site.url, *site.fallback_urls]:
            assert "api_key=" not in url, f"{site.name}: {url}"

    for name, site_url, key_env in [
        ("atlantic-movies", "https://www.themoviedb.org/", "TMDB_API_KEY"),
        ("atlantic-tv", "https://www.themoviedb.org/tv", "TMDB_API_KEY"),
    ]:
        site = sites[name]
        # site.url is what generate_index writes as the OPML/index Source link:
        # the main site address, no endpoint, no key.
        assert site.url == site_url, site.url
        assert site.api_key_env == key_env, site.api_key_env
        fetch_urls = [site.url, *site.fallback_urls]
        # A website URL cannot be parsed as JSON, so the API endpoint must be
        # reachable from somewhere in the site's fetch URLs.
        assert any("api.themoviedb.org/3/" in url for url in fetch_urls), fetch_urls

    # Sources that never produce a feed were removed outright.
    for name in ("bingebang-movies", "bingebang-tv", "noctratv-movies", "noctratv-tv"):
        assert name not in sites, name


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


def test_site_config_rejects_unsafe_feed_file() -> None:
    with pytest.raises(ValueError, match=r"simple \.xml filename"):
        SiteConfig(
            name="example",
            url="https://example.com/",
            method="http",
            item_selector="//article",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            feed_file="nested/feed.xml",
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


# ---- resolve_feed_files -----------------------------------------------------
# Shared by the post-generation stages (enrich, fix) so `--site` accepts the
# same inputs the generator already does.

def _two_site_config() -> Config:
    return Config(
        sites=[
            SiteConfig(
                name="ghacks",
                url="https://www.ghacks.com/",
                method="rss",
                item_selector="//item",
                title_selector="./title",
                link_selector="./link",
                feed_file="ghacks.xml",
            ),
            SiteConfig(
                name="showrss",
                url="https://showrss.info/",
                method="rss",
                item_selector="//item",
                title_selector="./title",
                link_selector="./link",
                feed_file="custom-showrss-name.xml",
            ),
        ]
    )


def test_resolve_feed_files_no_filter_returns_every_feed() -> None:
    matched, unmatched = resolve_feed_files(_two_site_config(), None)
    assert matched == {"ghacks.xml", "custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_empty_filter_returns_every_feed() -> None:
    """An empty --site list is the same as passing none: a full sweep."""
    matched, unmatched = resolve_feed_files(_two_site_config(), [])
    assert matched == {"ghacks.xml", "custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_matches_site_name() -> None:
    matched, unmatched = resolve_feed_files(_two_site_config(), ["ghacks"])
    assert matched == {"ghacks.xml"}
    assert unmatched == []


def test_resolve_feed_files_matches_feed_file_with_xml_suffix() -> None:
    """`--site ghacks.xml` must work, not be reported as a typo."""
    matched, unmatched = resolve_feed_files(_two_site_config(), ["ghacks.xml"])
    assert matched == {"ghacks.xml"}
    assert unmatched == []


def test_resolve_feed_files_matches_feed_file_that_differs_from_name() -> None:
    """A site may be targeted by name even when feed_file was renamed."""
    matched, unmatched = resolve_feed_files(_two_site_config(), ["showrss"])
    assert matched == {"custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_matches_feed_file_stem() -> None:
    matched, unmatched = resolve_feed_files(_two_site_config(), ["custom-showrss-name"])
    assert matched == {"custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_multiple_sites_accumulate() -> None:
    matched, unmatched = resolve_feed_files(_two_site_config(), ["ghacks", "showrss"])
    assert matched == {"ghacks.xml", "custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_reports_unmatched_request() -> None:
    """A typo must be distinguishable from a disabled site, not silently empty."""
    matched, unmatched = resolve_feed_files(_two_site_config(), ["ghacks", "typo"])
    assert matched == {"ghacks.xml"}
    assert unmatched == ["typo"]


def test_resolve_feed_files_unmatched_when_nothing_known() -> None:
    matched, unmatched = resolve_feed_files(_two_site_config(), ["nope"])
    assert matched == set()
    assert unmatched == ["nope"]


def test_resolve_feed_files_ignores_blank_requests() -> None:
    """`--site ''` must not narrow the run to nothing."""
    matched, unmatched = resolve_feed_files(_two_site_config(), ["", "  "])
    assert matched == {"ghacks.xml", "custom-showrss-name.xml"}
    assert unmatched == []


def test_resolve_feed_files_deduplicates_repeated_requests() -> None:
    matched, _ = resolve_feed_files(_two_site_config(), ["ghacks", "ghacks.xml"])
    assert matched == {"ghacks.xml"}


class TestDuplicateKeysAreRejected:
    """`yaml.safe_load` keeps the last of two identical keys and says nothing.

    Two sites in config/sites.yaml carried a second `removals:` block left over
    from an edit, so `dedupe-images` and `head-meta` were silently dropped for
    hackread and darknet-the-darkside. The file parsed cleanly, the pipeline
    ran, and two removal modules simply never fired.
    """

    def test_a_duplicate_key_is_an_error_not_a_silent_override(self, tmp_path):
        cfg = tmp_path / "sites.yaml"
        cfg.write_text(
            "sites:\n"
            "  a:\n"
            "    url: https://example.com/\n"
            "    feed_file: a.xml\n"
            "    removals:\n"
            "    - cosmetic-filters\n"
            "    removals:\n"
            "    - two\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate key 'removals'"):
            load_config(cfg)

    def test_the_error_names_the_line(self, tmp_path):
        cfg = tmp_path / "sites.yaml"
        cfg.write_text(
            "sites:\n  a:\n    removals: [x]\n    removals: [y]\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match=r"sites\.yaml:4"):
            load_config(cfg)

    def test_the_shipped_config_has_no_duplicate_keys(self):
        cfg = Path(__file__).resolve().parent.parent / "config" / "sites.yaml"
        if not cfg.is_file():
            pytest.skip("config/sites.yaml not present")
        load_config(cfg)  # raises on any duplicate

    def test_distinct_keys_are_unaffected(self, tmp_path):
        cfg = tmp_path / "sites.yaml"
        cfg.write_text(
            "sites:\n"
            "  a:\n"
            "    url: https://example.com/\n"
            "    feed_file: a.xml\n"
            "    method: rss\n"
            "    removals:\n"
            "    - cosmetic-filters\n"
            "    max_items: 5\n",
            encoding="utf-8",
        )
        assert load_config(cfg).sites[0].removals == ["cosmetic-filters"]
        assert load_config(cfg).sites[0].max_items == 5
