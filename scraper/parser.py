from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Iterable, List, Optional
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from lxml import html, etree
import elementpath

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
        allow_empty_title: bool = False,
    ) -> List[ParsedItem]:
        try:
            # Handle potential encoding issues and parse
            parser = html.HTMLParser(encoding='utf-8')
            root = html.fromstring(html_content.encode('utf-8'), parser=parser)
        except Exception as exc:
            logger.error("parser.html_parse_failed", error=str(exc))
            raise ParserError("Failed to parse HTML") from exc

        items_nodes = self._select_nodes(root, item_selector)

        parsed_items: List[ParsedItem] = []
        for node in items_nodes:
            try:
                title_parts = self._select_values(node, title_selector)
                link_parts = self._select_values(node, link_selector)

                if not link_parts:
                    continue

                title = self._normalize_text(str(title_parts[0])) if title_parts else ""
                link = self._normalize_text(str(link_parts[0]))

                if not title and not allow_empty_title:
                    continue

                description: Optional[str] = None
                if description_selector:
                    desc_parts = self._select_values(node, description_selector)
                    if desc_parts:
                        # For description, we might want to keep the raw HTML if it's complex
                        description = "".join(map(str, desc_parts)).strip()
                
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
            except Exception as e:
                logger.warning("parser.item_extraction_failed", error=str(e))
                continue

        logger.info("parser.items_parsed", count=len(parsed_items))
        return parsed_items

    def parse_rss_items(self, xml_content: str) -> List[ParsedItem]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            logger.error("parser.rss_parse_failed", error=str(exc))
            raise ParserError("Failed to parse RSS XML") from exc

        parsed_items: List[ParsedItem] = []
        content_tag = "{http://purl.org/rss/1.0/modules/content/}encoded"

        for node in root.findall(".//item"):
            title = self._normalize_text(self._html_to_text(node.findtext("title") or ""))
            link = self._normalize_text(node.findtext("link") or node.findtext("guid") or "")
            description = node.findtext(content_tag) or node.findtext("description")
            pub_date_text = self._normalize_text(node.findtext("pubDate") or "")
            pub_date = self._try_parse_date(pub_date_text) if pub_date_text else None

            if not title or not link:
                continue

            parsed_items.append(
                ParsedItem(
                    title=title,
                    link=link,
                    description=description.strip() if description else None,
                    pub_date=pub_date,
                )
            )

        logger.info("parser.rss_items_parsed", count=len(parsed_items))
        return parsed_items

    def parse_wordpress_posts(self, json_content: str) -> List[ParsedItem]:
        try:
            payload = json.loads(json_content)
        except json.JSONDecodeError as exc:
            logger.error("parser.wordpress_json_parse_failed", error=str(exc))
            raise ParserError("Failed to parse WordPress JSON") from exc

        if not isinstance(payload, list):
            raise ParserError("Unexpected WordPress API payload")

        parsed_items: List[ParsedItem] = []
        for post in payload:
            if not isinstance(post, dict):
                continue

            title = self._normalize_text(
                self._html_to_text(self._wordpress_rendered_field(post.get("title")))
            )
            link = self._normalize_text(str(post.get("link") or ""))
            description = self._wordpress_description(post)
            date_value = self._normalize_text(str(post.get("date_gmt") or post.get("date") or ""))
            pub_date = self._try_parse_date(date_value) if date_value else None

            if not title or not link:
                continue

            parsed_items.append(
                ParsedItem(
                    title=title,
                    link=link,
                    description=description,
                    pub_date=pub_date,
                )
            )

        logger.info("parser.wordpress_items_parsed", count=len(parsed_items))
        return parsed_items

    def extract_first(self, html_content: str, selector: str) -> Optional[str]:
        try:
            parser = etree.HTMLParser(encoding="utf-8")
            root = etree.HTML(html_content.encode("utf-8"), parser=parser)
        except (etree.ParserError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ParserError("Failed to parse HTML") from exc

        if root is None:  # pragma: no cover - defensive
            raise ParserError("Failed to parse HTML")

        values = self._select_values(root, selector)
        if not values:
            return None
        return self._normalize_text(str(values[0]))

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
            try:
                # Try XPath 2.0 via elementpath first for complex logic
                values = elementpath.select(node, candidate)
                if values is not None:
                    if isinstance(values, (str, float, int)):
                        return [str(values)]
                    if isinstance(values, list) and values:
                        result = []
                        for v in values:
                            if isinstance(v, (str, etree._ElementUnicodeResult)):
                                if str(v).strip():
                                    result.append(str(v))
                            elif isinstance(v, (float, int)):
                                result.append(str(v))
                        if result:
                            return result
            except Exception:
                # Fallback to standard lxml XPath 1.0
                try:
                    values = node.xpath(candidate)
                    if values is None:
                        continue
                    if isinstance(values, (str, float, int)):
                        return [str(values)]
                    if isinstance(values, list) and values:
                        result = []
                        for v in values:
                            if isinstance(v, (etree._ElementUnicodeResult, str)):
                                if str(v).strip():
                                    result.append(str(v))
                            elif isinstance(v, (float, int)):
                                result.append(str(v))
                        if result:
                            return result
                except Exception as e:
                    logger.debug("parser.xpath_failed", selector=candidate, error=str(e))
                    continue
        return []

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(str(text).split())

    @staticmethod
    def _try_parse_date(value: str) -> Optional[datetime]:
        # Heuristic parsing using BeautifulSoup + standard formats.
        # In production we might prefer `dateutil.parser`, but we keep dependencies minimal.
        normalized = value.strip()
        if not normalized:
            return None

        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            pass

        candidates = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%d %b %Y",
            "%d %b %Y %H:%M",
            "%a, %d %b %Y %H:%M:%S %z",
        ]
        for fmt in candidates:
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _html_to_text(value: str) -> str:
        return BeautifulSoup(unescape(str(value)), "html.parser").get_text(" ", strip=True)

    @staticmethod
    def _wordpress_rendered_field(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("rendered") or "")
        return str(value or "")

    def _wordpress_description(self, post: dict) -> Optional[str]:
        excerpt = self._wordpress_rendered_field(post.get("excerpt"))
        content = self._wordpress_rendered_field(post.get("content"))
        image_url = self._wordpress_featured_image(post)

        parts: List[str] = []
        if image_url:
            parts.append(f'<img src="{image_url}" style="max-width:220px;border-radius:4px;">')
        if excerpt:
            parts.append(excerpt.strip())
        elif content:
            parts.append(content.strip())

        if not parts:
            return None
        return "".join(parts)

    @staticmethod
    def _wordpress_featured_image(post: dict) -> Optional[str]:
        embedded = post.get("_embedded")
        if not isinstance(embedded, dict):
            return None

        media_items = embedded.get("wp:featuredmedia")
        if not isinstance(media_items, list) or not media_items:
            return None

        first_media = media_items[0]
        if not isinstance(first_media, dict):
            return None

        return str(first_media.get("source_url") or "") or None
