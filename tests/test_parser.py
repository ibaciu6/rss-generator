import json
from pathlib import Path

import pytest
import yaml

from scraper.parser import Parser

HTML = """
<html>
  <body>
    <article>
      <h2><a href="https://example.com/a">Title A</a></h2>
      <p>Desc A</p>
    </article>
    <article>
      <h2><a href="https://example.com/b">Title B</a></h2>
      <p>Desc B</p>
    </article>
  </body>
</html>
"""


def test_parser_extracts_items() -> None:
    parser = Parser()
    items = parser.parse_items(
        HTML,
        item_selector="//article",
        title_selector=".//h2/a/text()",
        link_selector=".//h2/a/@href",
        description_selector=".//p/text()",
    )

    assert len(items) == 2
    assert items[0].title == "Title A"
    assert items[0].link == "https://example.com/a"
    assert items[0].description == "Desc A"


def test_parser_uses_fallback_selectors() -> None:
    parser = Parser()
    html = """
    <html>
      <body>
        <section class="cards">
          <article class="item">
            <a href="https://example.com/fallback" title="Fallback Title">
              <img alt="Fallback Image Title" />
            </a>
          </article>
        </section>
      </body>
    </html>
    """

    items = parser.parse_items(
        html,
        item_selector="//article[contains(@class,'missing')] || //article[contains(@class,'item')]",
        title_selector=".//h2/a/text() || .//a/@title || .//img/@alt",
        link_selector=".//div[@class='poster']/a/@href || .//a/@href",
        description_selector=".//p/text() || .//img/@alt",
    )

    assert len(items) == 1
    assert items[0].title == "Fallback Title"
    assert items[0].link == "https://example.com/fallback"
    assert items[0].description == "Fallback Image Title"


def test_parser_allows_empty_title_for_detail_enrichment() -> None:
    parser = Parser()
    html = """
    <html>
      <body>
        <article class="item">
          <a href="https://example.com/detail"></a>
          <p>Genre text</p>
        </article>
      </body>
    </html>
    """

    items = parser.parse_items(
        html,
        item_selector="//article",
        title_selector=".//h2/text()",
        link_selector=".//a/@href",
        description_selector=".//p/text()",
        allow_empty_title=True,
    )

    assert len(items) == 1
    assert items[0].title == ""
    assert items[0].link == "https://example.com/detail"
    assert items[0].description == "Genre text"


def test_parser_extracts_sitefilme_title_from_last_post_title_block() -> None:
    parser = Parser()
    html = """
    <html>
      <body>
        <div class="posts1">
          <div class="post">
            <a href="https://sitefilme.com/online/123/">
              <img src="https://sitefilme.com/image.jpg" alt="" />
            </a>
            <div class="post-title"><a href="https://sitefilme.com/online/123/"></a></div>
            <div class="post-title">Actual SiteFilme Title</div>
          </div>
        </div>
      </body>
    </html>
    """

    items = parser.parse_items(
        html,
        item_selector="//div[contains(@class,'posts1')]/div[contains(@class,'post')]",
        title_selector=(
            "normalize-space(.//div[contains(@class,'post-title')][last()])"
            " || normalize-space(.//div[contains(@class,'post-title')][1]/a/text())"
        ),
        link_selector="normalize-space(.//a[1]/@href)",
        description_selector="concat('<img src=\"', normalize-space(.//img/@src), '\">')",
    )

    assert len(items) == 1
    assert items[0].title == "Actual SiteFilme Title"
    assert items[0].link == "https://sitefilme.com/online/123/"


def test_extract_first_returns_first_matching_value() -> None:
    parser = Parser()
    html = """
    <html>
      <head><title>Ignored Title</title></head>
      <body><h1>Preferred Title</h1></body>
    </html>
    """

    value = parser.extract_first(html, "//h1/text() || //title/text()")

    assert value == "Preferred Title"


def test_extract_first_supports_xpath2_functions_on_detail_pages() -> None:
    parser = Parser()
    html = """
    <html>
      <head>
        <meta property="og:image" content="https://example.com/poster.jpg" />
      </head>
      <body>
        <h1>Example Show Sezonul 2 Episodul 4</h1>
        <span class="tobeornot">Subtitrat in Engleza</span>
        <div class="wp-content">
          <p>Example Show Sezonul 2 Episodul 4 Online Subtitrat in Romana - Episode summary text</p>
        </div>
      </body>
    </html>
    """
    selector = (
        "concat("
        "'<img src=\"', normalize-space(if (string-length(normalize-space((//meta[@property=\"og:image\"]/@content)[1])) > 0) "
        "then (//meta[@property=\"og:image\"]/@content)[1] else ''), "
        "'\">', "
        "'<p>', "
        "if (string-length(normalize-space((//span[contains(@class,\"tobeornot\")])[1])) > 0) "
        "then concat(normalize-space((//span[contains(@class,\"tobeornot\")])[1]), '<br>') else '', "
        "normalize-space(substring-after(normalize-space((//div[contains(@class,\"wp-content\")]/p[1])[1]), ' - ')), "
        "'</p>', "
        "'<a href=\"https://www.imdb.com/find?q=', "
        "encode-for-uri(substring-before(normalize-space(//h1), ' Sezonul ')), "
        "'\">IMDb</a>'"
        ")"
    )

    value = parser.extract_first(html, selector)

    assert value is not None
    assert 'https://example.com/poster.jpg' in value
    assert 'Subtitrat in Engleza' in value
    assert 'Episode summary text' in value
    assert 'Example%20Show' in value


def test_fsonline_detail_prefers_sheader_backdrop_over_sidebar_posters() -> None:
    """FSOnline episode pages omit og:image on some posts; the first .poster img is a global sidebar."""
    parser = Parser()
    # Historical detail-page XPath (feeds now use listing-only descriptions to avoid N× Playwright launches in CI).
    fsonline_episode_page_description = r"""concat('<img src="', normalize-space(if (string-length(normalize-space((//meta[@property="og:image"]/@content)[1])) > 0) then if (contains(normalize-space((//meta[@property="og:image"]/@content)[1]), ' /')) then substring-before(normalize-space((//meta[@property="og:image"]/@content)[1]), ' /') else (//meta[@property="og:image"]/@content)[1] else if (contains(normalize-space((//div[contains(@class,"sheader")])[1]/@style), 'url(')) then translate(substring-before(substring-after(normalize-space((//div[contains(@class,"sheader")])[1]/@style), 'url('), ')'), "'", '') else if (string-length(normalize-space((//div[contains(@class,"poster")][not(ancestor::div[contains(@class,"featured-shows")])]//img/@data-src)[1])) > 0) then (//div[contains(@class,"poster")][not(ancestor::div[contains(@class,"featured-shows")])]//img/@data-src)[1] else if (string-length(normalize-space((//div[contains(@class,"poster")][not(ancestor::div[contains(@class,"featured-shows")])]//img/@src)[1])) > 0) then (//div[contains(@class,"poster")][not(ancestor::div[contains(@class,"featured-shows")])]//img/@src)[1] else if (string-length(normalize-space((//div[contains(@class,"poster")]//img/@data-src)[1])) > 0) then (//div[contains(@class,"poster")]//img/@data-src)[1] else (//div[contains(@class,"poster")]//img/@src)[1]), '" alt="', normalize-space(//h1), ' Poster" style="max-width:300px;max-height:450px;width:auto;height:auto;object-fit:contain;display:block;border-radius:4px;">', '<br><a href="https://www.youtube.com/results?search_query=', encode-for-uri(normalize-space(if (contains(normalize-space(//h1), ' Sezonul ')) then substring-before(normalize-space(//h1), ' Sezonul ') else normalize-space(//h1))), '+preview%7Cpromo%7Ctrailer+-fake+-fan&sp=EgIYAQ%253D%253D" target="_blank" rel="noopener noreferrer"><b style="color:#6600cc;">Trailer</b></a><br>', '<a href="https://www.imdb.com/find?q=', encode-for-uri(normalize-space(if (contains(normalize-space(//h1), ' Sezonul ')) then substring-before(normalize-space(//h1), ' Sezonul ') else normalize-space(//h1))), '&s=tt&ttype=tv" target="_blank" rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>')"""
    html = """
    <html><head><title>Episode</title></head><body>
      <div class="sheader" style="background-image:url(https://image.tmdb.org/t/p/w1280/correctBackdrop.jpg)">
        <h1>Happy&#8217;s Place Sezonul 2 Episodul 12</h1>
      </div>
      <div class="featured-shows">
        <article class="item tvshow"><div class="poster">
          <img data-src="https://image.tmdb.org/t/p/w185/wrongSidebar.jpg" alt="Other show"/>
        </div></article>
      </div>
    </body></html>
    """
    value = parser.extract_first(html, fsonline_episode_page_description)

    assert value is not None
    assert "correctBackdrop.jpg" in value
    assert "wrongSidebar.jpg" not in value


def test_parser_extracts_items_from_rss_xml() -> None:
    parser = Parser()
    xml = """
    <rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
      <channel>
        <item>
          <title><![CDATA[Example Title]]></title>
          <link>https://example.com/a</link>
          <description><![CDATA[<p>Short description</p>]]></description>
          <pubDate>Mon, 16 Mar 2026 04:56:18 +0000</pubDate>
        </item>
      </channel>
    </rss>
    """

    items = parser.parse_rss_items(xml)

    assert len(items) == 1
    assert items[0].title == "Example Title"
    assert items[0].link == "https://example.com/a"
    assert items[0].description == "<p>Short description</p>"
    assert items[0].pub_date is not None


def test_parser_extracts_items_from_atom_feed() -> None:
    parser = Parser()
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>r/SceneReleases</title>
      <entry>
        <title>The.Rivals.of.Amziah.King.2025.2160p.iT.WEB-DL</title>
        <link href="https://www.reddit.com/r/SceneReleases/comments/1wh41ph/abc/"/>
        <published>2026-09-15T15:46:38+00:00</published>
        <updated>2026-09-15T15:46:38+00:00</updated>
        <content type="html">&amp;#32; submitted by &amp;#32; /u/mrzurba</content>
        <id>t3_1wh41ph</id>
      </entry>
    </feed>
    """

    items = parser.parse_rss_items(xml)

    assert len(items) == 1
    assert items[0].title == "The.Rivals.of.Amziah.King.2025.2160p.iT.WEB-DL"
    assert items[0].link == "https://www.reddit.com/r/SceneReleases/comments/1wh41ph/abc/"
    assert items[0].pub_date is not None


def test_parser_strips_reddit_boilerplate_from_atom_description() -> None:
    parser = Parser()
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Movie.2026.1080p</title>
        <link href="https://www.reddit.com/r/SceneReleases/comments/1abc/"/>
        <published>2026-09-15T15:46:38+00:00</published>
        <content type="html">&amp;#32; submitted by &amp;#32; &lt;a href=&quot;https://www.reddit.com/user/foo&quot;&gt; /u/foo &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/SceneReleases/comments/1abc/&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; &amp;#32; &lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/SceneReleases/comments/1abc/&quot;&gt;[comments]&lt;/a&gt;&lt;/span&gt;</content>
      </entry>
    </feed>
    """

    items = parser.parse_rss_items(xml)

    assert len(items) == 1
    assert items[0].description is None


def test_parser_extracts_items_from_wordpress_posts() -> None:
    parser = Parser()
    payload = """
    [
      {
        "date_gmt": "2026-03-16T04:56:18",
        "link": "https://example.com/post-a",
        "title": {"rendered": "Post <em>A</em>"},
        "excerpt": {"rendered": "<p>Excerpt A</p>"},
        "_embedded": {
          "wp:featuredmedia": [
            {"source_url": "https://example.com/poster.jpg"}
          ]
        }
      }
    ]
    """

    items = parser.parse_wordpress_posts(payload)

    assert len(items) == 1
    assert items[0].title == "Post A"
    assert items[0].link == "https://example.com/post-a"
    assert items[0].description == (
        '<img src="https://example.com/poster.jpg" style="max-width:220px;border-radius:4px;">'
        "<p>Excerpt A</p>"
    )
    assert items[0].pub_date is not None


def _uindex_site(cfg_path: Path, feed_file: str) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return next(
        s for s in cfg["sites"].values() if s.get("feed_file") == feed_file
    )


def test_uindex_search_queries_strip_release_metadata() -> None:
    """Torrent naming noise (res, source, codec, group) must not leak into searches."""
    parser = Parser()
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "sites.yaml"
    movies = _uindex_site(cfg_path, "uindex-movies.xml")
    tv = _uindex_site(cfg_path, "uindex-tv.xml")

    def make_row(name: str) -> str:
        return (
            '<tr><td class="sr-col-name">'
            '<a href="magnet:?xt=urn:btih:X&amp;dn=x" class="sr-magnet">m</a> '
            f'<a href="/details.php?id=1" class="sr-torrent-link">{name}</a>'
            '</td><td class="sr-col-size">1.5 GB</td></tr>'
        )

    movie_rows = "".join(
        make_row(name)
        for name in [
            "Goody Goody 2026 1080p AMZN WEB-DL DDP5 1 H 264-CHORTLE NEW",
            "Goody Goody (2026) [720p] [WEBRip]",
            "Resident Evil 2026 1080p TELESYNC MULTi x264-DKS NEW",
        ]
    )
    html = (
        f"<html><body><table class=\"top-table\"><tbody>{movie_rows}</tbody></table>"
        "</body></html>"
    )
    items = parser.parse_items(
        html,
        movies["item_selector"],
        movies["title_selector"],
        movies["link_selector"],
        movies["description_selector"],
    )

    expected_query = {
        "Goody Goody 2026 1080p AMZN WEB-DL DDP5 1 H 264-CHORTLE NEW": "Goody%20Goody%202026",
        "Goody Goody (2026) [720p] [WEBRip]": "Goody%20Goody%202026",
        "Resident Evil 2026 1080p TELESYNC MULTi x264-DKS NEW": "Resident%20Evil%202026",
    }
    assert len(items) == 3
    for item in items:
        safe = expected_query[item.title]
        desc = item.description or ""
        assert f"search_query={safe}+preview" in desc
        assert f'imdb.com/find?q={safe}&s=tt" target=' in desc
        for noise in ("1080p", "TELESYNC", "WEBRip", "AMZN", "WEB-DL", "DDP5", "CHORTLE", "x264"):
            assert noise not in desc, f"release metadata leaked into search: {desc}"

    tv_row = make_row(
        "Ted Lasso S04E07 Yes and Baby 1080p ATVP WEB-DL DDP5 1 Atmos H 264-FLUX"
    )
    tv_html = (
        f"<html><body><table class=\"top-table\"><tbody>{tv_row}</tbody></table>"
        "</body></html>"
    )
    tv_items = parser.parse_items(
        tv_html,
        tv["item_selector"],
        tv["title_selector"],
        tv["link_selector"],
        tv["description_selector"],
    )

    assert len(tv_items) == 1
    assert "search_query=Ted%20Lasso+preview" in tv_items[0].description


# ---------------------------------------------------------------------------
# TVmaze schedule parser
# ---------------------------------------------------------------------------

_TVMAZE_US_ITEM = {
    "id": 123,
    "url": "https://www.tvmaze.com/episodes/123/my-show-1x01-pilot",
    "name": "Pilot",
    "season": 1,
    "number": 1,
    "airdate": "2026-11-04",
    "airtime": "21:00",
    "airstamp": "2026-11-05T02:00:00+00:00",
    "summary": "<p>The first episode.</p>",
    "image": None,
    "show": {
        "id": 10,
        "url": "https://www.tvmaze.com/shows/10/my-show",
        "name": "My Show",
        "image": {
            "medium": "https://static.tvmaze.com/uploads/images/medium_portrait/0/1.jpg",
            "original": "https://static.tvmaze.com/uploads/images/original_untouched/0/1.jpg",
        },
    },
}

_TVMAZE_WEB_ITEM = {
    "id": 456,
    "url": "https://www.tvmaze.com/episodes/456/stream-show-3x07-the-one",
    "name": "The One",
    "season": 3,
    "number": 7,
    "airdate": "2026-11-04",
    "airtime": "",
    "airstamp": "2026-11-04T08:00:00+00:00",
    "summary": None,
    "image": None,
    "_embedded": {
        "show": {
            "id": 20,
            "url": "https://www.tvmaze.com/shows/20/stream-show",
            "name": "Stream Show",
            "image": {
                "medium": "https://static.tvmaze.com/uploads/images/medium_portrait/1/2.jpg",
            },
        }
    },
}


def test_parse_tvmaze_us_schedule_broadcast():
    """Parses /schedule (show at item.show) and builds SxEx title."""
    parser = Parser()
    items = parser.parse_tvmaze_schedule(json.dumps([_TVMAZE_US_ITEM]))
    assert len(items) == 1
    item = items[0]
    assert item.title == "My Show S01E01"
    assert item.link == "https://www.tvmaze.com/episodes/123/my-show-1x01-pilot"
    assert item.pub_date is not None
    assert item.description is not None
    assert "static.tvmaze.com" in item.description
    assert "tv" in item.categories
    assert "episode" in item.categories


def test_parse_tvmaze_web_schedule_streaming():
    """Parses /schedule/web (show at item._embedded.show) and builds SxEx title."""
    parser = Parser()
    items = parser.parse_tvmaze_schedule(json.dumps([_TVMAZE_WEB_ITEM]))
    assert len(items) == 1
    item = items[0]
    assert item.title == "Stream Show S03E07"
    assert item.link == "https://www.tvmaze.com/episodes/456/stream-show-3x07-the-one"
    assert item.pub_date is not None
    assert "static.tvmaze.com" in (item.description or "")


def test_parse_tvmaze_schedule_generic_episode_name_omitted():
    """'Episode N' episode names are not appended to the title."""
    episode = {**_TVMAZE_US_ITEM, "name": "Episode 1"}
    items = Parser().parse_tvmaze_schedule(json.dumps([episode]))
    assert len(items) == 1
    assert items[0].title == "My Show S01E01"


def test_parse_tvmaze_schedule_skips_missing_show():
    """Items without a show block are silently skipped."""
    item = {k: v for k, v in _TVMAZE_US_ITEM.items() if k != "show"}
    items = Parser().parse_tvmaze_schedule(json.dumps([item]))
    assert items == []


def test_parse_tvmaze_schedule_skips_missing_url():
    """Items with no episode URL and no show URL are skipped."""
    item = {**_TVMAZE_US_ITEM, "url": None}
    item["show"] = {k: v for k, v in _TVMAZE_US_ITEM["show"].items() if k != "url"}
    items = Parser().parse_tvmaze_schedule(json.dumps([item]))
    assert items == []


def test_parse_tvmaze_schedule_invalid_json():
    """Invalid JSON raises ParserError."""
    from scraper.parser import ParserError
    with pytest.raises(ParserError):
        Parser().parse_tvmaze_schedule("not json")


def test_parse_tvmaze_schedule_non_array_json():
    """A JSON object (not array) at root raises ParserError."""
    from scraper.parser import ParserError
    with pytest.raises(ParserError):
        Parser().parse_tvmaze_schedule(json.dumps({"results": []}))


def test_parse_tvmaze_schedule_empty_array():
    """An empty array yields no items without raising."""
    items = Parser().parse_tvmaze_schedule(json.dumps([]))
    assert items == []


def test_tvmaze_schedule_poster_url_is_escaped():
    """XSS payload in poster_url is escaped before HTML interpolation."""
    payload = json.dumps([{
        "season": 1, "number": 2, "name": "Ep", "airdate": "2026-11-04",
        "url": "https://tvmaze.com/ep",
        "show": {
            "name": "Show",
            "url": "https://tvmaze.com/show",
            "image": {"medium": 'https://example.com/img.jpg" onload="alert(1)'},
        }
    }])
    items = Parser().parse_tvmaze_schedule(payload)
    assert len(items) == 1
    assert '" onload="alert(1)' not in (items[0].description or "")


# ---------------------------------------------------------------------------
# iCal parser
# ---------------------------------------------------------------------------

_ICAL_CONTENT = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TVmaze//EN
BEGIN:VEVENT
SUMMARY:My Show S01E05 - The One
DTSTART;VALUE=DATE-TIME:20261104T210000Z
URL:https://www.tvmaze.com/episodes/123/my-show-1x05-the-one
DESCRIPTION:When the protagonist discovers a hidden secret.\n
END:VEVENT
BEGIN:VEVENT
SUMMARY:Another Show S02E10
DTSTART;VALUE=DATE-TIME:20261105T200000Z
URL:https://www.tvmaze.com/episodes/456/another-show-2x10
END:VEVENT
END:VCALENDAR
"""


def test_parse_ical_basic():
    """Parses VEVENT entries with SUMMARY, DTSTART, URL; DESCRIPTION is dropped."""
    items = Parser().parse_ical(_ICAL_CONTENT)
    assert len(items) == 2
    assert items[0].title == "My Show S01E05 - The One"
    assert items[0].link == "https://www.tvmaze.com/episodes/123/my-show-1x05-the-one"
    assert items[0].pub_date is not None
    # An iCal DESCRIPTION carries the episode name, not a body, so the parser
    # never publishes it (see Parser.parse_ical docstring).
    assert items[0].description is None
    assert items[1].title == "Another Show S02E10"
    assert items[1].description is None


def test_parse_ical_drops_episode_name_description():
    """A DESCRIPTION that is only the episode name never reaches the item.

    TVmaze's followed-shows calendar puts the episode name ("Lean Girls") in
    DESCRIPTION. Relaying it published that name as bare prose between the
    poster and the player link, so the field is discarded while the title,
    link and date are still parsed.
    """
    content = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Elsbeth (2024) - 4x06
DTSTART;VALUE=DATE-TIME:20261120T210000Z
URL:https://www.tvmaze.com/episodes/9/elsbeth-4x06
DESCRIPTION:Lean Girls
END:VEVENT
END:VCALENDAR
"""
    items = Parser().parse_ical(content)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Elsbeth (2024) - 4x06"
    assert item.link == "https://www.tvmaze.com/episodes/9/elsbeth-4x06"
    assert item.pub_date is not None
    assert item.description is None


def test_parse_ical_skips_non_vevent():
    """VEVENT-only: non-VEVENT components are ignored."""
    content = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:America/New_York
END:VTIMEZONE
BEGIN:VEVENT
SUMMARY:Test Show S01E01
DTSTART;VALUE=DATE-TIME:20261104T200000Z
URL:https://tvmaze.com/ep/1
END:VEVENT
END:VCALENDAR
"""
    items = Parser().parse_ical(content)
    assert len(items) == 1
    assert items[0].title == "Test Show S01E01"


def test_parse_ical_skips_missing_title():
    """VEVENTs without SUMMARY are skipped."""
    content = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;VALUE=DATE-TIME:20261104T200000Z
URL:https://tvmaze.com/ep/1
END:VEVENT
BEGIN:VEVENT
SUMMARY:Valid Show S01E01
DTSTART;VALUE=DATE-TIME:20261104T210000Z
URL:https://tvmaze.com/ep/2
END:VEVENT
END:VCALENDAR
"""
    items = Parser().parse_ical(content)
    assert len(items) == 1
    assert items[0].title == "Valid Show S01E01"


def test_parse_ical_skips_missing_link():
    """VEVENTs without URL or LOCATION are skipped."""
    content = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:No Link Show
DTSTART;VALUE=DATE-TIME:20261104T200000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Valid Show
DTSTART;VALUE=DATE-TIME:20261104T210000Z
URL:https://tvmaze.com/ep/1
END:VEVENT
END:VCALENDAR
"""
    items = Parser().parse_ical(content)
    assert len(items) == 1
    assert items[0].title == "Valid Show"


def test_parse_ical_empty_calendar():
    """An empty calendar yields no items."""
    content = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
    items = Parser().parse_ical(content)
    assert items == []


def test_parse_ical_invalid_content():
    """Invalid iCal content raises ParserError."""
    from scraper.parser import ParserError
    with pytest.raises(ParserError):
        Parser().parse_ical("not valid ical content at all")

