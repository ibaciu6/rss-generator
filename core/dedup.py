from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

from core.logging_utils import get_logger


logger = get_logger(__name__)

# Cap per-site URL history. Feeds only carry ~24 items at a time; once a URL
# has rolled out of every feed (weeks later) it will not come back, so keeping
# unbounded history bloats data/cache.json without benefit.
DEFAULT_MAX_URLS_PER_SITE = 500


@dataclass
class DedupStore:
    """In-memory per-site URL history backed by a JSON file.

    Uses ``OrderedDict`` so we can trim oldest entries when a site exceeds
    ``max_per_site``. Insertion order reflects "first seen" across runs.
    """

    path: Path
    data: Dict[str, "OrderedDict[str, None]"] = field(default_factory=dict)
    max_per_site: int = DEFAULT_MAX_URLS_PER_SITE

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        max_per_site: int = DEFAULT_MAX_URLS_PER_SITE,
    ) -> "DedupStore":
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            data = {k: OrderedDict((url, None) for url in v) for k, v in raw.items()}
        else:
            data = {}

        return cls(path=path, data=data, max_per_site=max_per_site)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: list(v.keys()) for k, v in self.data.items()}
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)

    def filter_new(self, site_name: str, urls: Iterable[str]) -> List[str]:
        seen = self.data.setdefault(site_name, OrderedDict())
        url_list = list(urls)
        new_urls: List[str] = []
        for url in url_list:
            if url not in seen:
                new_urls.append(url)
                seen[url] = None

        # Trim to the most recent N entries (dict preserves insertion order).
        if self.max_per_site and len(seen) > self.max_per_site:
            excess = len(seen) - self.max_per_site
            for _ in range(excess):
                seen.popitem(last=False)

        logger.info(
            "dedup.filter",
            site=site_name,
            total=len(url_list),
            new=len(new_urls),
            cached=len(seen),
        )
        return new_urls


