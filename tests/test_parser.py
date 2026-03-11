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
