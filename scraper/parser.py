from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup
from lxml import etree, html

from core.logging_utils import get_logger


logger = get_logger(__name__)


@dataclass
class ParsedItem:
    title: str
    link: str
    description: Optional[str]
    pub_date: Optional[datetime]


class ParserError(Exception):
    pass


class Parser:
    """
    HTML parser using lxml (XPath) with BeautifulSoup as a helper when needed.
    """

    def parse_items(
        self,
        html_content: str,
        item_selector: str,
        title_selector: str,
        link_selector: str,
        description_selector: Optional[str] = None,
        date_selector: Optional[str] = None,
    ) -> List[ParsedItem]:
        try:
            root = html.fromstring(html_content)
        except etree.ParserError as exc:  # pragma: no cover - defensive
            raise ParserError("Failed to parse HTML") from exc

        items_nodes = self._select_nodes(root, item_selector)

        parsed_items: List[ParsedItem] = []
        for node in items_nodes:
            title_parts = self._select_values(node, title_selector)
            link_parts = self._select_values(node, link_selector)

            if not title_parts or not link_parts:
                continue

            title = self._normalize_text(title_parts[0])
            link = self._normalize_text(link_parts[0])

            description: Optional[str] = None
            if description_selector:
                desc_parts = self._select_values(node, description_selector)
                if desc_parts:
                    description = self._normalize_text(" ".join(map(str, desc_parts)))

            pub_date: Optional[datetime] = None
            if date_selector:
                date_parts = self._select_values(node, date_selector)
                if date_parts:
                    parsed_dt = self._try_parse_date(self._normalize_text(str(date_parts[0])))
                    pub_date = parsed_dt

            parsed_items.append(
                ParsedItem(
                    title=title,
                    link=link,
                    description=description,
                    pub_date=pub_date,
                )
            )

        logger.info("parser.items_parsed", count=len(parsed_items))
        return parsed_items

    @staticmethod
    def _split_selector_candidates(selector: str) -> List[str]:
        return [candidate.strip() for candidate in selector.split("||") if candidate.strip()]

    def _select_nodes(self, root: html.HtmlElement, selector: str) -> Iterable[html.HtmlElement]:
        for candidate in self._split_selector_candidates(selector):
            nodes = root.xpath(candidate)
            if nodes:
                return nodes
        return []

    def _select_values(self, node: html.HtmlElement, selector: str) -> List[str]:
        for candidate in self._split_selector_candidates(selector):
            values = node.xpath(candidate)
            if values:
                return values
        return []

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(str(text).split())

    @staticmethod
    def _try_parse_date(value: str) -> Optional[datetime]:
        # Heuristic parsing using BeautifulSoup + standard formats.
        # In production we might prefer `dateutil.parser`, but we keep dependencies minimal.
        candidates = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d %b %Y",
            "%d %b %Y %H:%M",
            "%a, %d %b %Y %H:%M:%S %z",
        ]
        for fmt in candidates:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

