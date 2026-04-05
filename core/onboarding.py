from __future__ import annotations

import hashlib
import re
import subprocess
import textwrap
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import anyio
import httpx
from lxml import etree, html
import yaml

from core.config import FetchMethod, SiteConfig, load_config
from core.feed import generate_rss
from scraper.fetcher import FetchError, Fetcher
from scraper.parser import ParsedItem, Parser


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "sites.yaml"
DEFAULT_PREVIEW_DIR = REPO_ROOT / ".preview-feeds"
DEFAULT_WORKFLOW = "update.yml"

CANDIDATE_TAGS = {"article", "div", "li", "section"}
FETCH_METHODS: tuple[FetchMethod, ...] = ("cloudscraper", "http", "playwright")
MAX_BASE_CANDIDATES = 3
MAX_PREVIEW_ITEMS = 5
DEFAULT_CATEGORY = "updates"
DEFAULT_MAX_ITEMS = 24

GENERIC_TITLE_SELECTOR = (
    ".//h1[normalize-space()][1]/text() || "
    ".//h2[normalize-space()][1]/text() || "
    ".//h3[normalize-space()][1]/text() || "
    ".//h4[normalize-space()][1]/text() || "
    ".//a[normalize-space()][1]/text() || "
    ".//img[@alt][1]/@alt || "
    ".//img[@title][1]/@title"
)

GENERIC_LINK_SELECTOR = (
    ".//h1[1]/ancestor::a[@href][1]/@href || "
    ".//h2[1]/ancestor::a[@href][1]/@href || "
    ".//h3[1]/ancestor::a[@href][1]/@href || "
    ".//h4[1]/ancestor::a[@href][1]/@href || "
    ".//a[@href][1]/@href"
)

TITLE_EXPR = (
    "normalize-space("
    "if (string-length(normalize-space((.//h1[normalize-space()][1])[1])) > 0) "
    "then (.//h1[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//h2[normalize-space()][1])[1])) > 0) "
    "then (.//h2[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//h3[normalize-space()][1])[1])) > 0) "
    "then (.//h3[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//h4[normalize-space()][1])[1])) > 0) "
    "then (.//h4[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//a[normalize-space()][1])[1])) > 0) "
    "then (.//a[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//img[@alt][1]/@alt)[1])) > 0) "
    "then (.//img[@alt][1]/@alt)[1] "
    "else (.//img[@title][1]/@title)[1])"
)

IMAGE_EXPR = (
    "normalize-space("
    "if (string-length(normalize-space((.//img/@data-src)[1])) > 0) "
    "then (.//img/@data-src)[1] "
    "else if (string-length(normalize-space((.//img/@data-lazy-src)[1])) > 0) "
    "then (.//img/@data-lazy-src)[1] "
    "else if (string-length(normalize-space((.//img/@src)[1])) > 0) "
    "then (.//img/@src)[1] "
    "else '')"
)

SUMMARY_EXPR = (
    "normalize-space("
    "if (string-length(normalize-space((.//*["
    "contains(@class,'excerpt') or contains(@class,'summary') or contains(@class,'desc')"
    "][normalize-space()][1])[1])) > 0) "
    "then (.//*[contains(@class,'excerpt') or contains(@class,'summary') or contains(@class,'desc')]"
    "[normalize-space()][1])[1] "
    "else if (string-length(normalize-space((.//p[normalize-space()][1])[1])) > 0) "
    "then (.//p[normalize-space()][1])[1] "
    "else '')"
)


@dataclass(frozen=True)
class FetchAttempt:
    method: FetchMethod
    ok: bool
    final_url: str | None
    detail: str
    page_title: str | None = None


@dataclass(frozen=True)
class PreviewOption:
    fetch_method: FetchMethod
    final_url: str
    item_selector: str
    title_selector: str
    link_selector: str
    description_selector: str | None
    style_name: str
    item_count: int
    preview_items: tuple[ParsedItem, ...]
    score: float
    preview_path: Path | None = None


@dataclass(frozen=True)
class FetchSnapshot:
    method: FetchMethod
    final_url: str
    content: str
    page_title: str | None


def run_onboarding(
    url: str | None,
    config_path: Path = DEFAULT_CONFIG,
    workflow_name: str = DEFAULT_WORKFLOW,
    push: bool = True,
    dispatch: bool = True,
) -> int:
    site_url = _normalize_url(url or _prompt("Site URL", default="").strip())
    if not site_url:
        print("No URL provided.")
        return 1

    if push:
        _ensure_repo_ready()

    attempts, options = anyio.run(discover_preview_options, site_url)
    _print_fetch_attempts(attempts)

    if not options:
        print("\nNo viable feed previews were discovered for this page.")
        return 1

    default_slug = derive_site_slug(site_url)
    preview_dir = DEFAULT_PREVIEW_DIR / default_slug
    options = tuple(write_preview_feeds(options, preview_dir, site_url))
    _print_preview_options(options)

    choice = _prompt_choice(len(options))
    option = options[choice - 1]

    existing_sites = _load_existing_site_names(config_path)
    site_name = _prompt_unique_site_name(default_slug, existing_sites)
    display_name = _prompt("Display name", default=_display_name_from_slug(site_name)).strip()
    feed_file = _prompt("Feed file", default=f"{site_name}.xml").strip() or f"{site_name}.xml"
    category = _prompt("Category", default=DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
    max_items_text = _prompt("Max items", default=str(DEFAULT_MAX_ITEMS)).strip() or str(DEFAULT_MAX_ITEMS)
    max_items = int(max_items_text)

    config_entry = _site_config_from_option(
        site_name=site_name,
        display_name=display_name or None,
        site_url=site_url,
        option=option,
        feed_file=feed_file,
        category=category or None,
        max_items=max_items,
    )

    print("\nSelected configuration:")
    print(f"  method: {config_entry.method}")
    print(f"  item_selector: {config_entry.item_selector}")
    print(f"  title_selector: {config_entry.title_selector}")
    print(f"  link_selector: {config_entry.link_selector}")
    if config_entry.description_selector:
        print(f"  description_selector: {config_entry.description_selector}")
    print(f"  preview file: {option.preview_path}")

    if not _confirm("Write config, commit, push, and trigger feed update?", default=True):
        print("Aborted without changing config.")
        return 0

    append_site_config(config_path, config_entry)
    print(f"Wrote configuration to {config_path}")

    if push:
        commit_message = f"Add {site_name} feed configuration"
        _commit_and_push(config_path, commit_message)
        print(f"Committed and pushed: {commit_message}")

    if push and dispatch:
        run_url = dispatch_update_workflow(workflow_name)
        if run_url:
            print(f"Dispatched workflow: {run_url}")
        else:
            print("Workflow dispatched.")

    return 0


async def discover_preview_options(url: str) -> tuple[tuple[FetchAttempt, ...], tuple[PreviewOption, ...]]:
    parser = Parser()
    fetcher = Fetcher()
    attempts: list[FetchAttempt] = []
    snapshots: list[FetchSnapshot] = []
    seen_hashes: set[str] = set()

    try:
        for method in FETCH_METHODS:
            try:
                result = await fetcher.fetch(url, method=method)
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    FetchAttempt(
                        method=method,
                        ok=False,
                        final_url=None,
                        detail=str(exc),
                    )
                )
                continue

            page_title = _extract_page_title(result.content)
            attempts.append(
                FetchAttempt(
                    method=method,
                    ok=True,
                    final_url=result.url,
                    detail=f"HTTP {result.status_code}",
                    page_title=page_title,
                )
            )

            digest = hashlib.sha256(result.content.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            snapshots.append(
                FetchSnapshot(
                    method=method,
                    final_url=result.url,
                    content=result.content,
                    page_title=page_title,
                )
            )
    finally:
        await fetcher.close()

    options: list[PreviewOption] = []
    for snapshot in snapshots:
        options.extend(_discover_options_from_snapshot(snapshot, parser))

    options.sort(key=lambda option: option.score, reverse=True)
    deduped: list[PreviewOption] = []
    seen_pairs: set[tuple[str, str]] = set()
    for option in options:
        key = (option.item_selector, option.style_name)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(option)
    return tuple(attempts), tuple(deduped[: MAX_BASE_CANDIDATES * 3])


def append_site_config(config_path: Path, site: SiteConfig) -> None:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw_sites = data.setdefault("sites", {})
    if site.name in raw_sites:
        raise ValueError(f"Site {site.name!r} already exists in {config_path}")

    entry: dict[str, object] = {
        "display_name": site.display_name,
        "url": site.url,
        "method": site.method,
        "item_selector": site.item_selector,
        "title_selector": site.title_selector,
        "link_selector": site.link_selector,
        "feed_file": site.feed_file,
        "category": site.category,
        "max_items": site.max_items,
    }

    if site.description_selector:
        entry["description_selector"] = site.description_selector
    if site.date_selector:
        entry["date_selector"] = site.date_selector
    if site.fallback_urls:
        entry["fallback_urls"] = site.fallback_urls
    if site.blocked_content_markers:
        entry["blocked_content_markers"] = site.blocked_content_markers
    if site.blocked_final_hosts:
        entry["blocked_final_hosts"] = site.blocked_final_hosts
    if site.allowed_final_hosts:
        entry["allowed_final_hosts"] = site.allowed_final_hosts
    if site.allow_empty_title:
        entry["allow_empty_title"] = True
    if site.detail_method:
        entry["detail_method"] = site.detail_method
    if site.detail_title_selector:
        entry["detail_title_selector"] = site.detail_title_selector
    if site.detail_description_selector:
        entry["detail_description_selector"] = site.detail_description_selector

    raw_sites[site.name] = {key: value for key, value in entry.items() if value is not None}
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100000),
        encoding="utf-8",
    )

    # Validate what we just wrote.
    load_config(config_path)


def write_preview_feeds(
    options: Sequence[PreviewOption],
    preview_dir: Path,
    site_url: str,
) -> list[PreviewOption]:
    if preview_dir.exists():
        for path in preview_dir.glob("*.xml"):
            path.unlink()
    preview_dir.mkdir(parents=True, exist_ok=True)

    written: list[PreviewOption] = []
    for index, option in enumerate(options, start=1):
        preview_path = preview_dir / f"{index:02d}-{option.style_name}.xml"
        generate_rss(
            items=option.preview_items[:MAX_PREVIEW_ITEMS],
            site_name=f"Preview {index}: {option.style_name}",
            site_url=site_url,
            category="preview",
            output_path=preview_path,
        )
        written.append(
            PreviewOption(
                fetch_method=option.fetch_method,
                final_url=option.final_url,
                item_selector=option.item_selector,
                title_selector=option.title_selector,
                link_selector=option.link_selector,
                description_selector=option.description_selector,
                style_name=option.style_name,
                item_count=option.item_count,
                preview_items=option.preview_items,
                score=option.score,
                preview_path=preview_path,
            )
        )
    return written


def derive_site_slug(url: str) -> str:
    host = urlparse(_normalize_url(url)).netloc.lower()
    host = re.sub(r"^www\d*\.", "", host)
    host = host.split(":", 1)[0]
    if host.endswith(".co.uk"):
        host = host[:-6]
    elif host.count(".") >= 1:
        host = host.rsplit(".", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    return slug or "new-site"


def dispatch_update_workflow(workflow_name: str) -> str | None:
    owner, repo = _parse_repo_slug(_git_output(["git", "remote", "get-url", "origin"]).strip())
    token = _resolve_github_token()
    if not token:
        raise RuntimeError("No GitHub token available for workflow dispatch.")

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_name}/dispatches",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"ref": "main"},
        )
        response.raise_for_status()

        runs = client.get(
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_name}/runs",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params={"branch": "main", "event": "workflow_dispatch", "per_page": 1},
        )
        runs.raise_for_status()
        payload = runs.json()
        workflow_runs = payload.get("workflow_runs", [])
        if workflow_runs:
            return workflow_runs[0].get("html_url")
    return None


def _discover_options_from_snapshot(snapshot: FetchSnapshot, parser: Parser) -> list[PreviewOption]:
    root = _parse_html(snapshot.content)
    selectors = _candidate_item_selectors(root)

    base_options: list[PreviewOption] = []
    for selector in selectors:
        items = parser.parse_items(
            snapshot.content,
            item_selector=selector,
            title_selector=GENERIC_TITLE_SELECTOR,
            link_selector=GENERIC_LINK_SELECTOR,
        )
        items = tuple(_normalize_preview_items(items, snapshot.final_url))
        if len(items) < 3:
            continue

        has_images, has_summary = _detect_content_features(root, selector)
        score = _score_preview_items(items, has_images=has_images, has_summary=has_summary)
        if score <= 0:
            continue

        base_options.append(
            PreviewOption(
                fetch_method=snapshot.method,
                final_url=snapshot.final_url,
                item_selector=selector,
                title_selector=GENERIC_TITLE_SELECTOR,
                link_selector=GENERIC_LINK_SELECTOR,
                description_selector=None,
                style_name="basic",
                item_count=len(items),
                preview_items=items[:MAX_PREVIEW_ITEMS],
                score=score,
            )
        )

        if has_images:
            poster_selector = _build_description_selector(include_summary=False)
            poster_items = tuple(
                _normalize_preview_items(
                    parser.parse_items(
                        snapshot.content,
                        item_selector=selector,
                        title_selector=GENERIC_TITLE_SELECTOR,
                        link_selector=GENERIC_LINK_SELECTOR,
                        description_selector=poster_selector,
                    ),
                    snapshot.final_url,
                )
            )
            base_options.append(
                PreviewOption(
                    fetch_method=snapshot.method,
                    final_url=snapshot.final_url,
                    item_selector=selector,
                    title_selector=GENERIC_TITLE_SELECTOR,
                    link_selector=GENERIC_LINK_SELECTOR,
                    description_selector=poster_selector,
                    style_name="poster-links",
                    item_count=len(items),
                    preview_items=poster_items[:MAX_PREVIEW_ITEMS],
                    score=score + 0.5,
                )
            )

        if has_images or has_summary:
            rich_selector = _build_description_selector(include_summary=True)
            rich_items = tuple(
                _normalize_preview_items(
                    parser.parse_items(
                        snapshot.content,
                        item_selector=selector,
                        title_selector=GENERIC_TITLE_SELECTOR,
                        link_selector=GENERIC_LINK_SELECTOR,
                        description_selector=rich_selector,
                    ),
                    snapshot.final_url,
                )
            )
            base_options.append(
                PreviewOption(
                    fetch_method=snapshot.method,
                    final_url=snapshot.final_url,
                    item_selector=selector,
                    title_selector=GENERIC_TITLE_SELECTOR,
                    link_selector=GENERIC_LINK_SELECTOR,
                    description_selector=rich_selector,
                    style_name="poster-summary-links",
                    item_count=len(items),
                    preview_items=rich_items[:MAX_PREVIEW_ITEMS],
                    score=score + 1.0,
                )
            )

    base_options.sort(key=lambda option: option.score, reverse=True)
    deduped: list[PreviewOption] = []
    seen_pairs: set[tuple[str, str]] = set()
    for option in base_options:
        key = (option.item_selector, option.style_name)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(option)
    return deduped[: MAX_BASE_CANDIDATES * 3]


def _candidate_item_selectors(root: etree._Element) -> list[str]:
    selectors: set[str] = set()

    for parent in root.iter():
        children = [child for child in parent if isinstance(child.tag, str)]
        grouped: dict[tuple[str, tuple[str, ...]], list[etree._Element]] = {}
        for child in children:
            tag = child.tag.lower()
            classes = tuple(_class_tokens(child))
            if tag not in CANDIDATE_TAGS or not classes:
                continue
            if not child.xpath(".//a[@href]"):
                continue
            grouped.setdefault((tag, classes), []).append(child)

        for (tag, classes), nodes in grouped.items():
            if 3 <= len(nodes) <= 60:
                selectors.add(_build_selector(tag, classes))
                if len(classes) > 1:
                    selectors.add(_build_selector(tag, classes[:1]))

    by_single_class: dict[tuple[str, str], int] = {}
    for node in root.xpath("//*[@class]"):
        if not isinstance(node.tag, str):
            continue
        tag = node.tag.lower()
        if tag not in CANDIDATE_TAGS or not node.xpath(".//a[@href]"):
            continue
        for class_name in _class_tokens(node):
            by_single_class[(tag, class_name)] = by_single_class.get((tag, class_name), 0) + 1

    for (tag, class_name), count in by_single_class.items():
        if 4 <= count <= 80:
            selectors.add(_build_selector(tag, (class_name,)))

    ranked: list[tuple[int, str]] = []
    for selector in selectors:
        try:
            count = len(root.xpath(selector))
        except Exception:  # noqa: BLE001
            continue
        if 3 <= count <= 80:
            ranked.append((count, selector))

    ranked.sort(key=lambda item: (abs(item[0] - 24), -item[0], len(item[1])))
    return [selector for _, selector in ranked[:12]]


def _build_description_selector(include_summary: bool) -> str:
    summary_part = (
        f"if (string-length({SUMMARY_EXPR}) > 0) then concat('<p>', {SUMMARY_EXPR}, '</p>') else ''"
        if include_summary
        else "''"
    )
    return (
        "concat("
        f"if (string-length({IMAGE_EXPR}) > 0) then concat('<img src=\"', {IMAGE_EXPR}, "
        "'\" style=\"max-width:300px;display:block;border-radius:4px;\">') else '', "
        f"{summary_part}, "
        "'<p><a href=\"https://www.youtube.com/results?search_query=', "
        f"encode-for-uri({TITLE_EXPR}), "
        "'+preview%7Cpromo%7Ctrailer+-fake+-fan&sp=EgIYAQ%253D%253D\" target=\"_blank\" "
        "rel=\"noopener noreferrer\"><b style=\"color:#6600cc;\">Trailer</b></a><br>', "
        "'<a href=\"https://www.imdb.com/find?q=', "
        f"encode-for-uri({TITLE_EXPR}), "
        "'&s=tt\" target=\"_blank\" rel=\"noopener noreferrer\"><b style=\"color:#6600cc;\">IMDb</b></a></p>'"
        ")"
    )


def _normalize_preview_items(items: Iterable[ParsedItem], base_url: str) -> list[ParsedItem]:
    normalized: list[ParsedItem] = []
    seen_links: set[str] = set()
    for item in items:
        title = " ".join(unescape(item.title).split())
        link = urljoin(base_url, item.link)
        if not title or not link or link in seen_links:
            continue
        seen_links.add(link)
        normalized.append(
            ParsedItem(
                title=title,
                link=link,
                description=item.description,
                pub_date=item.pub_date,
            )
        )
    return normalized


def _detect_content_features(root: etree._Element, selector: str) -> tuple[bool, bool]:
    try:
        nodes = root.xpath(selector)[:MAX_PREVIEW_ITEMS]
    except Exception:  # noqa: BLE001
        return False, False

    if not nodes:
        return False, False

    image_hits = 0
    summary_hits = 0
    for node in nodes:
        if node.xpath(".//img[@src or @data-src or @data-lazy-src]"):
            image_hits += 1
        paragraph_text = _first_nonempty_text(node.xpath(".//p[normalize-space()][1]"))
        excerpt_text = _first_nonempty_text(
            node.xpath(
                ".//*[contains(@class,'excerpt') or contains(@class,'summary') or contains(@class,'desc')]"
                "[normalize-space()][1]"
            )
        )
        if paragraph_text or excerpt_text:
            summary_hits += 1

    threshold = max(1, len(nodes) // 2)
    return image_hits >= threshold, summary_hits >= threshold


def _score_preview_items(
    items: Sequence[ParsedItem],
    *,
    has_images: bool,
    has_summary: bool,
) -> float:
    if len(items) < 3:
        return 0.0

    unique_titles = len({item.title.lower() for item in items})
    avg_title_length = sum(len(item.title) for item in items) / len(items)
    score = float(len(items))
    score += unique_titles * 0.4
    score += min(avg_title_length, 80.0) / 20.0
    if has_images:
        score += 1.5
    if has_summary:
        score += 1.0
    return score


def _parse_html(content: str) -> etree._Element:
    parser = html.HTMLParser(encoding="utf-8")
    return html.fromstring(content.encode("utf-8"), parser=parser)


def _extract_page_title(content: str) -> str | None:
    try:
        root = _parse_html(content)
    except Exception:  # noqa: BLE001
        return None

    titles = root.xpath("//title/text()")
    if not titles:
        return None
    value = " ".join(str(titles[0]).split())
    return value or None


def _class_tokens(node: etree._Element) -> list[str]:
    raw = str(node.attrib.get("class", "")).strip()
    tokens = []
    for token in raw.split():
        normalized = token.strip()
        if not normalized:
            continue
        if len(normalized) > 40:
            continue
        tokens.append(normalized)
    return tokens[:3]


def _build_selector(tag: str, classes: Sequence[str]) -> str:
    parts = [
        f"contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')"
        for class_name in classes
    ]
    predicate = " and ".join(parts)
    return f"//{tag}[{predicate}]"


def _site_config_from_option(
    *,
    site_name: str,
    display_name: str | None,
    site_url: str,
    option: PreviewOption,
    feed_file: str,
    category: str | None,
    max_items: int,
) -> SiteConfig:
    return SiteConfig(
        name=site_name,
        display_name=display_name,
        url=site_url,
        method=option.fetch_method,
        item_selector=option.item_selector,
        title_selector=option.title_selector,
        link_selector=option.link_selector,
        description_selector=option.description_selector,
        feed_file=feed_file,
        category=category,
        max_items=max_items,
    )


def _print_fetch_attempts(attempts: Sequence[FetchAttempt]) -> None:
    print("\nFetch attempts:")
    for attempt in attempts:
        status = "ok" if attempt.ok else "failed"
        final_url = f" -> {attempt.final_url}" if attempt.final_url else ""
        page_title = f" | title: {attempt.page_title}" if attempt.page_title else ""
        print(f"  - {attempt.method}: {status} ({attempt.detail}){final_url}{page_title}")


def _print_preview_options(options: Sequence[PreviewOption]) -> None:
    print("\nPreview options:")
    for index, option in enumerate(options, start=1):
        print(
            f"\n[{index}] {option.style_name} | method={option.fetch_method} | "
            f"items={option.item_count} | selector={option.item_selector}"
        )
        if option.preview_path is not None:
            print(f"    preview file: {option.preview_path}")
        for item in option.preview_items[:3]:
            print(f"    - {item.title}")
            print(f"      {item.link}")
            if item.description:
                print(f"      {textwrap.shorten(_description_preview(item.description), width=140, placeholder='...')}")


def _description_preview(value: str) -> str:
    try:
        wrapper = ET.fromstring(f"<root>{value}</root>")
        text = " ".join(" ".join(wrapper.itertext()).split())
        return text or value
    except ET.ParseError:
        return " ".join(unescape(value).split())


def _prompt_choice(max_value: int) -> int:
    while True:
        raw = _prompt("Select preview", default="1").strip()
        try:
            value = int(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if 1 <= value <= max_value:
            return value
        print(f"Enter a number between 1 and {max_value}.")


def _prompt_unique_site_name(default_slug: str, existing_sites: set[str]) -> str:
    while True:
        value = _prompt("Site key", default=default_slug).strip().lower()
        value = re.sub(r"[^a-z0-9-]+", "-", value).strip("-")
        if not value:
            print("Site key cannot be empty.")
            continue
        if value in existing_sites:
            print(f"Site key {value!r} already exists.")
            continue
        return value


def _confirm(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _prompt(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ") or default


def _normalize_url(url: str) -> str:
    value = url.strip()
    if value and "://" not in value:
        value = f"https://{value}"
    return value


def _display_name_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def _first_nonempty_text(values: Sequence[object]) -> str:
    for value in values:
        if isinstance(value, etree._Element):
            text = " ".join(" ".join(value.itertext()).split())
        else:
            text = " ".join(str(value).split())
        if text:
            return text
    return ""


def _load_existing_site_names(config_path: Path) -> set[str]:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw_sites = data.get("sites", {})
    if not isinstance(raw_sites, dict):
        return set()
    return set(raw_sites)


def _ensure_repo_ready() -> None:
    _git_output(["git", "fetch", "origin"])
    status = _git_output(["git", "status", "--porcelain"]).strip()
    if status:
        raise RuntimeError("Working tree is not clean. Commit or stash local changes first.")
    behind = _git_output(["git", "rev-list", "--count", "HEAD..origin/main"]).strip()
    if behind not in {"0", ""}:
        raise RuntimeError("Local branch is behind origin/main. Sync the repo first.")


def _commit_and_push(config_path: Path, commit_message: str) -> None:
    _git_output(["git", "add", str(config_path)])
    cached = _git_output(["git", "diff", "--cached", "--name-only"]).strip()
    if not cached:
        return
    _git_output(["git", "commit", "-m", commit_message])
    _git_output(["git", "push", "origin", "main"])


def _resolve_github_token() -> str | None:
    token = _git_credential_token()
    return token or None


def _git_credential_token() -> str:
    process = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    )
    for line in process.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def _git_output(command: Sequence[str]) -> str:
    process = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return process.stdout


def _parse_repo_slug(remote_url: str) -> tuple[str, str]:
    https_match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", remote_url)
    if not https_match:
        raise RuntimeError(f"Unsupported Git remote URL: {remote_url}")
    return https_match.group("owner"), https_match.group("repo")
