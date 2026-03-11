from __future__ import annotations

from html import escape
from pathlib import Path

from core.config import load_config


INDEX_PATH = Path("index.html")
CONFIG_PATH = Path("config/sites.yaml")


def _display_name(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def build_index_html() -> str:
    config = load_config(CONFIG_PATH)
    rows: list[str] = []
    for site in config.sites:
        label = escape(_display_name(site.name))
        rss_name = site.feed_file
        atom_name = Path(site.feed_file).with_suffix(".atom.xml").name
        rss_path = Path("feeds") / rss_name
        atom_path = Path("feeds") / atom_name
        rss_cell = (
            f"<a href=\"feeds/{escape(rss_name)}\">RSS</a>"
            if rss_path.exists()
            else "<span>Unavailable</span>"
        )
        atom_cell = (
            f"<a href=\"feeds/{escape(atom_name)}\">Atom</a>"
            if atom_path.exists()
            else "<span>Unavailable</span>"
        )
        source_href = escape(site.url)
        rows.append(
            "        <tr>"
            f"<td>{label}</td>"
            f"<td>{rss_cell}</td>"
            f"<td>{atom_cell}</td>"
            f"<td><a href=\"{source_href}\">Source</a></td>"
            "</tr>"
        )

    table_rows = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>RSS Generator</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5f1e8;
        --panel: #fffaf0;
        --text: #1f2933;
        --muted: #52606d;
        --line: #d9cbb2;
        --accent: #9f3a16;
        --accent-soft: #f7d7c8;
      }}

      * {{
        box-sizing: border-box;
      }}

      body {{
        margin: 0;
        font-family: Georgia, "Times New Roman", serif;
        background:
          radial-gradient(circle at top left, #fffdf6 0, #fffdf6 22%, transparent 22%),
          linear-gradient(180deg, #efe4d3 0%, var(--bg) 32%, #f8f5ef 100%);
        color: var(--text);
      }}

      main {{
        max-width: 980px;
        margin: 0 auto;
        padding: 48px 20px 64px;
      }}

      .hero {{
        background: rgba(255, 250, 240, 0.88);
        border: 1px solid var(--line);
        border-radius: 24px;
        padding: 28px;
        box-shadow: 0 18px 60px rgba(102, 79, 46, 0.08);
      }}

      h1 {{
        margin: 0 0 12px;
        font-size: clamp(2.2rem, 5vw, 3.6rem);
        line-height: 1;
        letter-spacing: -0.04em;
      }}

      p {{
        margin: 0;
        color: var(--muted);
        font-size: 1.05rem;
      }}

      .table-wrap {{
        margin-top: 24px;
        overflow-x: auto;
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 20px;
        box-shadow: 0 18px 60px rgba(102, 79, 46, 0.08);
      }}

      table {{
        width: 100%;
        border-collapse: collapse;
      }}

      th, td {{
        padding: 14px 18px;
        text-align: left;
        border-bottom: 1px solid var(--line);
      }}

      th {{
        font-size: 0.78rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
        background: rgba(247, 215, 200, 0.35);
      }}

      tr:last-child td {{
        border-bottom: 0;
      }}

      td:first-child {{
        font-weight: 700;
      }}

      a {{
        color: var(--accent);
        text-decoration: none;
      }}

      a:hover {{
        text-decoration: underline;
      }}

      .note {{
        margin-top: 16px;
        padding: 14px 16px;
        border-left: 4px solid var(--accent);
        background: rgba(247, 215, 200, 0.35);
        border-radius: 12px;
      }}

      @media (max-width: 640px) {{
        main {{
          padding: 24px 14px 40px;
        }}

        .hero {{
          padding: 20px;
        }}

        th, td {{
          padding: 12px 14px;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <h1>RSS Generator</h1>
        <p>Published RSS and Atom feeds for every configured source in this repository.</p>
      </section>
      <section class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Site</th>
              <th>RSS</th>
              <th>Atom</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
{table_rows}
          </tbody>
        </table>
      </section>
      <p class="note">The feed files live under <code>/feeds</code>. This page is generated from <code>config/sites.yaml</code>.</p>
    </main>
  </body>
</html>
"""


def main() -> int:
    INDEX_PATH.write_text(build_index_html(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
