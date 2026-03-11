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

