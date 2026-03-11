from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Set

from core.logging_utils import get_logger


logger = get_logger(__name__)


@dataclass
class DedupStore:
    path: Path
    data: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "DedupStore":
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            data = {k: set(v) for k, v in raw.items()}
        else:
            data = {}

        return cls(path=path, data=data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: sorted(v) for k, v in self.data.items()}
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)

    def filter_new(self, site_name: str, urls: Iterable[str]) -> Iterable[str]:
        seen = self.data.setdefault(site_name, set())
        new_urls = []
        for url in urls:
            if url not in seen:
                new_urls.append(url)
                seen.add(url)
        logger.info("dedup.filter", site=site_name, total=len(list(urls)), new=len(new_urls))
        return new_urls


