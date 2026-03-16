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
