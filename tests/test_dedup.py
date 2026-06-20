from __future__ import annotations

import json
from pathlib import Path

from core.dedup import DedupStore


def test_filter_new_returns_only_new_urls(tmp_path: Path) -> None:
    store = DedupStore.load(tmp_path / "cache.json")
    first = store.filter_new("demo", ["https://x/a", "https://x/b"])
    second = store.filter_new("demo", ["https://x/a", "https://x/c"])

    assert list(first) == ["https://x/a", "https://x/b"]
    assert list(second) == ["https://x/c"]


def test_load_save_roundtrip_preserves_order(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store = DedupStore.load(path)
    store.filter_new("demo", ["https://x/1", "https://x/2", "https://x/3"])
    store.save()

    reloaded = DedupStore.load(path)
    assert list(reloaded.data["demo"].keys()) == [
        "https://x/1",
        "https://x/2",
        "https://x/3",
    ]


def test_filter_new_caps_per_site_to_max(tmp_path: Path) -> None:
    store = DedupStore.load(tmp_path / "cache.json", max_per_site=5)
    store.filter_new("demo", [f"https://x/{i}" for i in range(10)])

    urls = list(store.data["demo"].keys())
    assert len(urls) == 5
    # Oldest entries (0..4) are evicted; most recent survive.
    assert urls == [f"https://x/{i}" for i in range(5, 10)]


def test_save_writes_only_capped_entries(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store = DedupStore.load(path, max_per_site=3)
    store.filter_new("demo", [f"https://x/{i}" for i in range(6)])
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["demo"] == [f"https://x/{i}" for i in range(3, 6)]
