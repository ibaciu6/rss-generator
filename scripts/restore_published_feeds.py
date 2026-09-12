"""Restore healthy feeds from GitHub Pages before a new generation run.

Generated feeds are intentionally ignored by Git.  A scheduled runner therefore
starts empty and cannot use GenerationEngine's last-known-good protection unless
it first restores the currently published copies.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from core.config import load_config
from core.feed import is_failure_feed_title


def _is_healthy_rss(content: bytes) -> bool:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    channel = root.find("channel")
    return channel is not None and not is_failure_feed_title(channel.findtext("title"))


def _restore_one(base_url: str, feed_file: str, feeds_dir: Path) -> tuple[str, str]:
    url = f"{base_url.rstrip('/')}/feeds/{feed_file}"
    try:
        with urlopen(url, timeout=20) as response:  # noqa: S310 - fixed public Pages base
            content = response.read()
    except (OSError, URLError) as exc:
        return feed_file, f"skipped ({exc})"

    if not _is_healthy_rss(content):
        return feed_file, "skipped (not a healthy RSS feed)"

    output_path = feeds_dir / feed_file
    output_path.write_bytes(content)
    return feed_file, "restored"


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore published healthy RSS feeds")
    parser.add_argument("--config", type=Path, default=Path("config/sites.yaml"))
    parser.add_argument("--feeds-dir", type=Path, default=Path("feeds"))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("RSS_FEED_PUBLIC_BASE", "").strip(),
        help="Published site base URL (defaults to RSS_FEED_PUBLIC_BASE)",
    )
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or RSS_FEED_PUBLIC_BASE is required")

    args.feeds_dir.mkdir(parents=True, exist_ok=True)
    feed_files = [site.feed_file for site in load_config(args.config).sites if site.enabled]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(
            lambda feed_file: _restore_one(args.base_url, feed_file, args.feeds_dir),
            feed_files,
        )
        for feed_file, status in results:
            print(f"{feed_file}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
