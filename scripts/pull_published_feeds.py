"""Download the feeds GitHub Actions published, for reviewing the live output.

Feeds are gitignored and built in CI, so the local reader normally shows
whatever the last local run happened to produce — a different page variant, a
datacenter-blocked site, or stale items. The only faithful view of what readers
actually get is the deployed Pages artifact, and this pulls it into `feeds/`.

This is the review-side counterpart of a generation run. It is deliberately
*not* used by CI: nothing here seeds a build. Generation either rebuilds a feed
from scratch or replaces it with a failure placeholder (invariant 20), and a
published copy is never carried over into a new run.

Usage:
    PYTHONPATH=. python scripts/pull_published_feeds.py
    PYTHONPATH=. python scripts/pull_published_feeds.py --base-url https://host/prefix
    PYTHONPATH=. python scripts/pull_published_feeds.py --keep-extra
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://ibaciu6.github.io/rss-generator"
USER_AGENT = "rss-generator-pull/1.0"
_XMLURL_RE = re.compile(r'xmlUrl\s*=\s*"([^"]+\.xml)"', re.IGNORECASE)


def _fetch(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def published_feed_files(base_url: str, timeout: float) -> list[str]:
    """Feed filenames from the published OPML, which is the shipped manifest."""
    opml = _fetch(f"{base_url.rstrip('/')}/feeds.opml", timeout)
    names = {m.rsplit("/", 1)[-1] for m in _XMLURL_RE.findall(opml.decode("utf-8", "replace"))}
    if not names:
        raise SystemExit(f"no feed URLs found in {base_url}/feeds.opml")
    return sorted(names)


def _is_valid_feed(content: bytes) -> bool:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    return root.find("channel") is not None


def pull_one(base_url: str, feed_file: str, feeds_dir: Path, timeout: float) -> tuple[str, str]:
    url = f"{base_url.rstrip('/')}/feeds/{feed_file}"
    try:
        content = _fetch(url, timeout)
    except (HTTPError, URLError, OSError) as exc:
        return feed_file, f"FAILED ({exc})"
    if not _is_valid_feed(content):
        return feed_file, "FAILED (not valid RSS)"
    (feeds_dir / feed_file).write_bytes(content)
    return feed_file, f"{len(content) // 1024} KiB"


def main(argv: list[str] | None = None) -> int:
    """Replace local feeds with the published ones.

    Local feeds that the site no longer publishes are removed, so a site
    deleted from config/sites.yaml does not linger in the reader. Pass
    ``--keep-extra`` to leave them alone.
    """
    parser = argparse.ArgumentParser(
        prog="pull_published_feeds",
        description="Download the feeds GitHub Actions published into feeds/",
    )
    parser.add_argument("--base-url", default=os.environ.get("RSS_FEED_PUBLIC_BASE", DEFAULT_BASE_URL).strip())
    parser.add_argument("--feeds-dir", type=Path, default=Path("feeds"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--keep-extra", action="store_true", help="do not delete unpublished local feeds")
    args = parser.parse_args(argv)

    if not args.base_url:
        parser.error("--base-url or RSS_FEED_PUBLIC_BASE is required")

    args.feeds_dir.mkdir(parents=True, exist_ok=True)
    feed_files = published_feed_files(args.base_url, args.timeout)
    print(f"Pulling {len(feed_files)} published feeds from {args.base_url}")

    ok = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = executor.map(
            lambda f: pull_one(args.base_url, f, args.feeds_dir, args.timeout),
            feed_files,
        )
        for feed_file, status in results:
            if status.startswith("FAILED"):
                failed += 1
                print(f"  {feed_file}: {status}", file=sys.stderr)
            else:
                ok += 1
                print(f"  {feed_file}: {status}")

    if not args.keep_extra:
        published = set(feed_files)
        removed = 0
        for stale in sorted(args.feeds_dir.glob("*.xml")):
            if stale.name not in published:
                stale.unlink()
                removed += 1
        if removed:
            print(f"  removed {removed} local feed(s) that are no longer published")

    print(f"Done: {ok} pulled, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
