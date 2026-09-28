"""Declarative cosmetic filtering, in the spirit of uBlock Origin's filter lists.

uBlock's insight that this borrows: the durable way to strip chrome is a
*declarative list of selectors* kept apart from the code, not a selector
buried in a per-site branch of the scraper. Two of its ideas carry over
directly because Soup Sieve implements the CSS that needs:

  ``:has-text(foo)``   -> ``:-soup-contains("foo")``   (Soup Sieve has it)
  domain anchoring     -> a leading ``site.com|site.ro`` prefix
  ``@@`` exceptions    -> keep an element a generic rule would otherwise drop

Two of its ideas do *not* carry over, and the reasons are specific to this
pipeline rather than to uBlock:

  ``:upward()``   uBlock can afford to drop a matched node and leave a stub.
                 A feed cannot: the match is usually the ``<a>`` and the
                 thing to remove is the list around it. Implemented here as
                 ``up=1``, escalating to the nearest block ancestor.

  content guards  In a browser tab, hiding a div that happens to be the whole
                 page is a cosmetic glitch you scroll past. Here it is a
                 published feed item that is suddenly empty, and nothing
                 reports it. Every rule is therefore applied under a budget
                 (see :func:`apply_cosmetic_filters`) and a rule that would
                 take more than ``max_text_ratio`` of the body is refused.

The rules live in ``config/cosmetic-filters.txt``: plain CSS selectors, one
per line, ``#`` comments, optional domain anchoring, optional ``up=N``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# Blocks are the elements worth escalating to. A share row is usually a list or
# a div; the point of escalating is to take the wrapper, never the document.
#
# `article` and `main` are deliberately absent. They are the content container,
# and escalating to one deletes the article: a `<button aria-label="Share on
# Facebook">` that happens to sit directly inside `<article>` has `<article>` as
# its nearest block ancestor, so `{up=1}` on the share-row rule removed an
# entire item -- found by running the real filter file over a real document,
# which is the only reason it is fixed rather than defended against in prose.
_BLOCKS = frozenset(
    {
        "div", "section", "aside", "nav", "ul", "ol", "li", "header", "footer",
        "form", "figure", "table", "blockquote",
    }
)
# Never climb out of the fragment into the document.
_NEVER_ESCALATE_TO = frozenset({"article", "main", "body", "html"})

# A rule may not take more than this share of the body's visible text. Chosen
# because the biggest legitimate target measured across the published feeds is
# a related-posts list at ~3%; sponsor-block was measured at 98% on one securelist
# item, which is the failure this cap exists to make impossible.
DEFAULT_MAX_TEXT_RATIO = 0.25

# ...but a ratio is meaningless on a short document. A 68-character body with a
# 17-character related-posts list is 25% removed, and refusing it protects
# nothing -- there is no article left to damage. So the ratio is floored, and a
# separate absolute floor guards the failure that actually matters here: a
# published item that has been emptied.
MIN_TEXT_BUDGET = 400
# A rule may never leave less than this share of the body. Checked at every
# size, not just large documents: an earlier version only applied it above 800
# characters, and a 60-character test item was therefore emptied completely by
# a single rule while every guard reported nothing wrong.
MIN_KEEP_RATIO = 0.25

# ...and may not take more than this share of the body's images, for the same
# reason. A rule that eats the article's photographs is worse than useless.
DEFAULT_MAX_IMAGE_RATIO = 0.8

# Domain anchoring, using uBlock's own `||host^` token. The obvious spelling --
# a bare `example.com selector` prefix -- is genuinely ambiguous with a
# descendant selector: `div.bar a` parses as the domain "div.bar" applied to the
# selector "a", because a class name and a hostname are both dotted words. No
# amount of shape-matching fixes that reliably, so the anchor is explicit.
_DOMAIN_ANCHOR = re.compile(r"^\|\|([A-Za-z0-9][A-Za-z0-9.\-]*(?:\|[A-Za-z0-9][A-Za-z0-9.\-]*)*)\^\s+")
# Rule options, e.g. `div.related {up=1 noimg}`.
_UP = re.compile(r"\s*up\s*[:=]\s*(\d+)")
_NOIMG = re.compile(r"\s*noimg\b")
_OPTIONS = re.compile(r"\{[^{}]*\}\s*$")


@dataclass(frozen=True)
class Rule:
    """One cosmetic filter."""

    selector: str
    domains: frozenset[str] = frozenset()  # empty = applies everywhere
    up: int = 0  # escalate to the Nth block ancestor after matching
    noimg: bool = False  # refuse if the match carries any image at all
    raw: str = ""
    exempt: bool = False  # an @@ exception: protects from generic rules

    def applies_to(self, host: str | None) -> bool:
        if not self.domains:
            return True
        if not host:
            return False
        host = host.lower()
        for d in self.domains:
            d = d.lower()
            if host == d or host.endswith("." + d):
                return True
        return False


@dataclass
class Report:
    """What a filter run did, per rule. A browser throws this away; we cannot."""

    matched: dict[str, int] = field(default_factory=dict)
    # Images and text removed, per rule. A rule that quietly deletes the
    # article's photographs is the worst outcome available here, and the
    # aggregate hides which rule did it -- rapid7 lost 60 images across 19
    # items and nothing in the report could name the culprit.
    images: dict[str, int] = field(default_factory=dict)
    chars: dict[str, int] = field(default_factory=dict)
    # The actual src of every image a rule took, so a removal can be audited
    # rather than inferred. Counting is not enough: a diff of the before/after
    # document misattributes whenever two images share a src, and a rule's
    # effect also depends on what earlier rules already removed, so a rule
    # re-run in isolation sees a different tree than it did in the real pass.
    image_srcs: dict[str, list[str]] = field(default_factory=dict)
    refused: list[tuple[str, str]] = field(default_factory=list)
    elements: int = 0
    # Empty wrappers pruned after the rules ran. Not attributable to any one
    # rule, so reported on its own.
    swept: int = 0

    def note(
        self,
        selector: str,
        n: int,
        images: int = 0,
        chars: int = 0,
        srcs: list[str] | None = None,
    ) -> None:
        self.matched[selector] = self.matched.get(selector, 0) + n
        if images:
            self.images[selector] = self.images.get(selector, 0) + images
        if chars:
            self.chars[selector] = self.chars.get(selector, 0) + chars
        if srcs:
            self.image_srcs.setdefault(selector, []).extend(srcs)
        self.elements += n

    def refuse(self, selector: str, why: str) -> None:
        self.refused.append((selector, why))


def parse_rule(line: str) -> Rule | None:
    """Parse one filter line. Returns None for blanks and comments."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return None
    exempt = line.startswith("@@")
    if exempt:
        line = line[2:].strip()

    up = 0
    noimg = False
    m = _OPTIONS.search(line)
    if m:
        opts = m.group(0)
        u = _UP.search(opts)
        if u:
            up = int(u.group(1))
        noimg = bool(_NOIMG.search(opts))
        line = line[: m.start()].strip()

    domains: frozenset[str] = frozenset()
    m = _DOMAIN_ANCHOR.match(line)
    if m:
        domains = frozenset(p.lower() for p in m.group(1).split("|"))
        line = line[m.end():].strip()

    if not line:
        return None
    return Rule(selector=line, domains=domains, up=up, noimg=noimg, raw=line, exempt=exempt)


def load_rules(path: Path) -> tuple[list[Rule], list[Rule]]:
    """Read a filter file into (active rules, exemptions)."""
    active: list[Rule] = []
    exempt: list[Rule] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        rule = parse_rule(raw)
        if rule is None:
            continue
        (exempt if rule.exempt else active).append(rule)
    return active, exempt


def _visible_text_len(soup: BeautifulSoup) -> int:
    return len(soup.get_text(" ", strip=True))


def _ancestor(node: Tag, levels: int, root: Tag) -> Tag | None:
    """The `levels`-th block ancestor of `node`, never climbing past `root`."""
    cur: Tag | None = node
    for _ in range(levels):
        nxt: Tag | None = None
        for parent in cur.parents:
            name = getattr(parent, "name", None)
            if name in _BLOCKS and parent is not root:
                nxt = parent
                break
        if nxt is None:
            return None
        cur = nxt
    return cur if cur is not node else None


def _protected(soup: BeautifulSoup, node: Tag, exempts: Iterable[Rule]) -> bool:
    """True when an @@ exception names this exact element.

    Exact match only, deliberately. An earlier version treated an exception as
    shielding its whole subtree, and `@@div.entry-content` -- the wrapper nearly
    every WordPress article sits in -- then protected every node inside the
    article, so the entire rule set silently did nothing on most feeds while
    still reporting no error. An exception means "do not remove this element",
    not "do not remove anything near it".
    """
    for rule in exempts:
        try:
            if any(el is node for el in soup.select(rule.selector)):
                return True
        except Exception:
            continue
    return False


def _measure(soup: BeautifulSoup) -> tuple[int, int]:
    """(visible text length, image count) of a parsed fragment."""
    return len(soup.get_text(" ", strip=True)), len(soup.find_all("img"))


# Wrappers worth dropping once they are empty. A rule that removes the <a>
# inside a share list leaves a bare <ul> holding nothing, and an empty block
# with margins is exactly the band of blank space around an image that this
# whole exercise started from. Restricted to elements that carried no text and
# no media *and* hold no other structural child, so a wrapper with real
# siblings inside is never touched.
_EMPTY_OK = frozenset({"ul", "ol", "div", "section", "aside", "p", "figure", "table", "tbody", "tr"})
_MEDIA = ("img", "picture", "video", "audio", "iframe", "embed", "object", "svg", "canvas")


def _sweep_empty(soup: BeautifulSoup) -> int:
    """Remove block wrappers left holding nothing. Returns how many went."""
    removed = 0
    changed = True
    while changed:
        changed = False
        for el in list(soup.find_all(_EMPTY_OK)):
            if el.name is None:  # already detached by an earlier sweep
                continue
            if el.get_text(" ", strip=True):
                continue
            if any(el.find(m) for m in _MEDIA):
                continue
            # Keep a wrapper that still holds a block child: pruning the child
            # first and re-running handles nested empties.
            if el.find(["div", "section", "ul", "ol", "table", "p", "figure"]):
                continue
            if el.find("input") or el.find("button"):
                continue
            el.decompose()
            removed += 1
            changed = True
    return removed


def apply_cosmetic_filters(
    html: str,
    rules: Iterable[Rule],
    *,
    host: str | None = None,
    exempts: Iterable[Rule] = (),
    max_text_ratio: float = DEFAULT_MAX_TEXT_RATIO,
    max_image_ratio: float = DEFAULT_MAX_IMAGE_RATIO,
    report: Report | None = None,
) -> tuple[str, Report]:
    """Apply cosmetic rules to an HTML fragment, under a content budget.

    Each rule is applied to the whole document and its effect is measured
    before the change is kept. A rule that would take more than the budget is
    rolled back and recorded in the report, because the failure it prevents --
    a published article silently emptied -- is invisible in every other signal
    a pipeline has.
    """
    soup = BeautifulSoup(html, "html.parser")
    rep = report or Report()
    live_exempts = [r for r in exempts if r.applies_to(host)]

    # Budgets come from the ORIGINAL document and stay fixed for the whole run.
    # Recomputing them against the shrinking document would let a sequence of
    # rules each take their 25% and collectively eat the article.
    total_text, total_imgs = _measure(soup)
    text_budget = max(MIN_TEXT_BUDGET, int(total_text * max_text_ratio))
    # The image budget needs a floor as well as a ratio. Measured on the real
    # feeds: rapid7's articles carry 6 images, of which 4 are the social logos
    # a rule is *supposed* to remove. A flat 50% capped that at 3 and refused
    # a correct removal; a flat 80% would let a rule take 5 of 6. So: allow the
    # larger of three images or 80%.
    img_budget = max(3, int(total_imgs * max_image_ratio))

    for rule in rules:
        if not rule.applies_to(host):
            continue
        # Measured per rule, not once. Measuring once made every rule's "lost"
        # figure cumulative from the original document, so the tag-list rule was
        # reported as taking 60 images that the share rule had already taken
        # three rules earlier.
        before_text, before_imgs = _measure(soup)
        try:
            targets = soup.select(rule.selector)
        except Exception as exc:  # one bad selector must not kill the run
            rep.refuse(rule.selector, f"invalid selector: {exc}")
            continue
        if not targets:
            continue

        # Pick the nodes to remove before touching anything, snapshot the
        # document, then apply and measure -- so a refusal is a true rollback.
        chosen: list[Tag] = []
        for el in targets:
            node = _ancestor(el, rule.up, soup) if rule.up else el
            if node is None or node.name is None:
                continue
            if any(node is c for c in chosen) or _protected(soup, node, live_exempts):
                continue
            chosen.append(node)
        if not chosen:
            # A rule matched but every match was unusable -- typically because
            # escalation hit the content container and stopped. Recording it
            # matters: the rule is in the file, it looks enabled, and it is
            # doing nothing.
            if targets:
                rep.refuse(rule.selector, "no removable target (matched only the content container)")
            continue

        if rule.noimg:
            # A text-only block -- a tag cloud, a related-posts list, an author
            # credit -- has no business containing a photograph. Measured on
            # the published feeds, `div:has(> a[href*="/tag/"])` matched a
            # container on theregister.com that also held a real article image
            # and took it with it. One image is far under the ratio budget, so
            # only a rule that says "never" catches this.
            with_imgs = [n for n in chosen if n.find("img") is not None]
            if with_imgs:
                rep.refuse(rule.selector, f"{len(with_imgs)} match(es) carry images")
                continue

        snapshot = soup.decode_contents()
        # Captured before decomposing, from the nodes this rule is about to
        # take, so the record is exact rather than inferred from a diff.
        taken_srcs = [
            img.get("src") or img.get("data-src") or ""
            for node in chosen
            for img in node.find_all("img")
        ]
        for node in chosen:
            node.decompose()
        after_text, after_imgs = _measure(soup)
        lost_text, lost_imgs = before_text - after_text, before_imgs - after_imgs
        # What is LEFT, not what went. (An earlier version computed this as
        # total - after, which is the amount removed, and the guard then
        # refused every rule that successfully stripped a footer.)
        remaining = after_text
        gutted = total_text > 0 and remaining < total_text * MIN_KEEP_RATIO
        if lost_text > text_budget or lost_imgs > img_budget or gutted:
            if gutted:
                why = (
                    f"would leave {remaining} of {total_text} chars "
                    f"({remaining * 100 // max(1, total_text)}%, floor {int(MIN_KEEP_RATIO * 100)}%)"
                )
            elif lost_text > text_budget:
                why = f"would remove {lost_text} chars, budget {text_budget}"
            else:
                why = f"would remove {lost_imgs} images, budget {img_budget}"
            soup = BeautifulSoup(snapshot, "html.parser")
            before_text, before_imgs = _measure(soup)
            rep.refuse(rule.selector, why)
            continue

        rep.note(rule.selector, len(chosen), images=lost_imgs, chars=lost_text, srcs=taken_srcs)

    swept = _sweep_empty(soup)
    rep.swept = swept

    # A module that removed nothing must return its input byte for byte.
    # Round-tripping through BeautifulSoup and back shifts a character here
    # and there -- 217 of 1065 items drifted by one byte on a second pass --
    # and since fix_feeds re-applies every site's removals each run, that
    # accumulates in the published feeds. fix_feeds.py is separately
    # non-idempotent (it re-escapes &amp; on every pass), and two silent
    # drifts in one stage is one too many to attribute later.
    if not rep.elements and not swept:
        return html, rep
    return soup.decode_contents(), rep
