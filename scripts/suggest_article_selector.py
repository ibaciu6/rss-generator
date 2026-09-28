#!/usr/bin/env python3
"""Suggest a `detail_article_selector` for a site, from a real page.

The article enricher can only fetch the full body if it knows which element
holds it. Without a selector it falls back to a generic heuristic, and on
several sites that heuristic returns *less* text than the RSS excerpt the feed
already had -- so the emptiness check refuses the result and the item keeps its
snippet. That is the whole reason articles come out truncated, and it is
invisible in the run output.

This fetches a page and ranks the elements that actually contain the prose, so
the selector is chosen from the markup rather than guessed. Read-only.
"""
from __future__ import annotations

import argparse
import re
import sys

import httpx
from bs4 import BeautifulSoup

STRIP = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
# Blocks whose text is chrome, not the article.
CHROME_HINT = re.compile(
    r"nav|menu|sidebar|footer|header|comment|share|social|promo|newsletter|"
    r"subscribe|related|promo|widget|banner|cookie|advert|meta|tag",
    re.I,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-chars", type=int, default=400)
    ap.add_argument("--user-agent", default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    args = ap.parse_args(argv)

    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": args.user_agent}
    ) as c:
        r = c.get(args.url)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}")
        return 1

    soup = BeautifulSoup(r.text, "html.parser")
    # Only the non-content tags. An earlier version also removed header, aside
    # and form, which on some of these sites is exactly where the article lives
    # -- and it reported "no element held 400 characters" for a page that was
    # mostly article. Chrome is flagged by name in the output instead.
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    cands: list[tuple[int, str, str, int]] = []
    for el in soup.find_all(["div", "article", "section", "main", "td"]):
        if el.find(["div", "article", "section", "main"]):
            continue  # a wrapper around other candidates; not the article itself
        text = el.get_text(" ", strip=True)
        if len(text) < args.min_chars:
            continue
        ident = ""
        if el.get("id"):
            ident = "#" + el["id"]
        cls = ".".join((el.get("class") or [])[:2])
        if cls:
            ident += "." + cls.replace(" ", ".")
        imgs = len(el.find_all("img"))
        cands.append((len(text), ident or el.name, el.name, imgs))

    seen: set[str] = set()
    print(f"{args.url}  ({r.status_code}, {len(r.text)} bytes)\n")
    print(f'{"text":>7} {"imgs":>5}  selector')
    print("-" * 78)
    for n, sel, _tag, imgs in sorted(cands, key=lambda x: -x[0]):
        if sel in seen:
            continue
        seen.add(sel)
        flag = "  (chrome-ish name)" if CHROME_HINT.search(sel) else ""
        print(f"{n:7} {imgs:5}  {sel}{flag}")
        if len(seen) >= args.top:
            break
    if not cands:
        print(f"no element held at least {args.min_chars} characters of text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
