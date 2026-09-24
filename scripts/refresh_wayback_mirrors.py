"""Refresh Wayback Machine mirrors used as RSS fallback feeds.

Some feeds (e.g. Substack) serve HTTP 403 to datacenter egress, so GitHub
Actions cannot fetch the primary URL and sites configured with a
``web.archive.org/web/<ts>id_/...`` fallback fall back to the last archived
snapshot. Run this before generation: it re-captures each mirrored feed with
Save Page Now and waits until the CDX index catches up, so the snapshot the
generation run serves is the freshest possible.

Degradation is graceful: archive.org is occasionally slow or rate-limited
(captures can take minutes to surface), so a failed refresh never fails the
pipeline - generation simply uses the last good snapshot.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.config import load_config

WAYBACK_PREFIX = "https://web.archive.org/"
CDX_PATH = "/cdx/search/cdx"
SPN_PATH = "/save/"
MIN_INTERVAL_SECONDS = 6 * 3600
WARMUP_WAIT_SECONDS = 120
CDX_POLL_INTERVAL = 10

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def extract_mirror_sources(sites: list[Any]) -> list[str]:
    """Original feed URLs behind Wayback ``id_`` mirror fallbacks, in order."""
    sources: list[str] = []
    for site in sites:
        for url in (site.url, *site.fallback_urls):
            if url.startswith(WAYBACK_PREFIX) and "id_/" in url:
                original = url.split("id_/", 1)[1]
                if original and original not in sources:
                    sources.append(original)
    return sources


def _httpget(url: str, timeout: int) -> Any:
    return urlopen(Request(url, headers={"User-Agent": _USER_AGENT}), timeout=timeout)


def newest_snapshot(original: str, timeout: int = 30) -> str | None:
    """Most recent 200-status capture timestamp for a URL, or None."""
    query = (
        f"{WAYBACK_PREFIX}{CDX_PATH}?url={original}"
        "&fl=timestamp&filter=statuscode:200&sort=reverse&limit=1"
    )
    try:
        body = _httpget(query, timeout).read().decode().strip()
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if not body:
        return None
    return body.splitlines()[0].split()[0]


def request_capture(original: str, timeout: int = 60) -> bool:
    """Trigger Save Page Now and report whether the request was accepted."""
    req = Request(
        f"{WAYBACK_PREFIX}{SPN_PATH}{original}",
        data=b"",
        method="POST",
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def should_refresh(original: str, state: dict[str, str]) -> bool:
    """True unless this source was successfully captured recently."""
    last = state.get(original)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (datetime.now(UTC) - last_dt).total_seconds() >= MIN_INTERVAL_SECONDS


def refresh_one(original: str) -> bool:
    """Re-capture a feed and wait until the index shows a newer snapshot."""
    before = newest_snapshot(original)
    if not request_capture(original):
        return False
    deadline = time.monotonic() + WARMUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(CDX_POLL_INTERVAL)
        after = newest_snapshot(original)
        if after is not None and after != before:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-capture Wayback Machine RSS fallback mirrors"
    )
    parser.add_argument("--config", type=Path, default=Path("config/sites.yaml"))
    parser.add_argument("--state", type=Path, default=Path("data/wayback_mirror_state.json"))
    args = parser.parse_args()

    config = load_config(args.config)
    sources = extract_mirror_sources(config.sites)
    if not sources:
        print("no Wayback mirror fallbacks configured")
        return 0

    state: dict[str, str] = {}
    if args.state.is_file():
        try:
            state = json.loads(args.state.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    now = datetime.now(UTC).isoformat()
    for original in sources:
        if not should_refresh(original, state):
            print(f"skip (recently refreshed): {original}")
            continue
        refreshed = refresh_one(original)
        state[original] = now
        print(f"refreshed: {original}" if refreshed else f"accepted, capture pending: {original}")

    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())