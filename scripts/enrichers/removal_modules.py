"""Reusable removal modules for article enrichment.

Each module removes one category of chrome from a parsed article body. Feeds
reference modules by name in ``config/sites.yaml`` under ``removals:``, so a
concern is implemented once and shared across every feed that needs it.

The registry is deliberately flat: a module is a name plus a function that
takes a BeautifulSoup and returns the number of elements removed. New concerns
are added by writing one function and registering it -- no feed has to change.

Module catalogue (from the 40-site / 555-article scan):

  comments         comment section + comment form            13 sites, 65+20 markers
  akismet-notice   p.akismet_comment_form_privacy_notice      3 sites, 16 hits
  sponsor-block    text-matched partner/sponsor blocks         1 site, 8+21 hits
  emoji-images     img[src*="fbcdn.net"] (Facebook emoji)      1 site, 45 images
  related-posts    "Articole similare" / div.crp_related       2 sites
  social-share     share buttons                               2 sites
  head-meta        <meta> and <noscript> in the body           many sites, 31+30 hits
"""
from __future__ import annotations

import re
from collections.abc import Callable

from bs4 import BeautifulSoup

# A module takes a parsed body and returns how many elements it removed.
ModuleFn = Callable[[BeautifulSoup], int]

_REGISTRY: dict[str, ModuleFn] = {}


def _block_parent(node, tags: tuple[str, ...]):
    """First block-level ancestor of a text node.

    ``find_all(string=...)`` yields NavigableStrings, which have ``parents``
    but no ``parent`` -- so ``find_parent`` blows up on them. Walk ``parents``
    instead and return the first tag whose name is in ``tags``.
    """
    for parent in node.parents:
        name = getattr(parent, "name", None)
        if name in tags:
            return parent
    return None


def module(name: str):
    """Register a removal function under ``name``."""

    def wrap(fn: ModuleFn) -> ModuleFn:
        _REGISTRY[name] = fn
        return fn

    return wrap


def apply_modules(html: str, names: list[str] | tuple[str, ...]) -> str:
    """Apply the named modules to an HTML string and return the cleaned HTML.

    Modules run in the order given. Unknown names are ignored here -- config
    validation catches them at load time with a helpful message.
    """
    if not names:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for name in names:
        fn = _REGISTRY.get(name)
        if fn is not None:
            fn(soup)
    return soup.decode_contents()


def known_modules() -> list[str]:
    return sorted(_REGISTRY)


# --------------------------------------------------------------------------- #
# modules
# --------------------------------------------------------------------------- #

_COMMENT_TEXT = re.compile(
    r"comentariu|comen[t]?ează|cancel reply|lasă un comentariu", re.IGNORECASE
)


@module("comments")
def _remove_comments(soup: BeautifulSoup) -> int:
    """Comment section, comment form and the 'Comenează' links.

    The scan found comment chrome on 13 of 40 article feeds -- WordPress
    renders the whole discussion (plus the reply form) inside or beside the
    article body. Matched on the stable Romanian strings and the two classes
    WordPress uses, so it survives theme restyles.
    """
    n = 0
    # Collect first, then remove: decomposing a host detaches any sibling
    # matches find_all already handed us, and touching those raises.
    hosts = []
    for el in soup.find_all(string=_COMMENT_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    for sel in ("div.comment-respond", "div.comments-area", "ol.commentlist",
                "div#comments"):
        for el in soup.select(sel):
            el.decompose()
            n += 1
    return n


@module("akismet-notice")
def _remove_akismet(soup: BeautifulSoup) -> int:
    """The Akismet privacy notice WordPress appends to the comment form.

    One stable class on 3 feeds; text-matched as well because some themes
    strip the class.
    """
    n = 0
    for el in soup.select("p.akismet_comment_form_privacy_notice"):
        el.decompose()
        n += 1
    hosts = []
    for el in soup.find_all(string=re.compile(r"folose[șs]te Akismet")):
        host = _block_parent(el, ("p",))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n


# Sponsor / partner blocks. The scan found these only on mihai-vasilescu, where
# the theme uses Tailwind CDN hashed classes (x14z9mp xat24cr ...) that change
# on every deploy, so the text is the only stable handle.
_SPONSOR_TEXT = re.compile(
    r"EUROCHARGE|Parteneri:|Server Config|Eldrive|Schaeffler", re.IGNORECASE
)


@module("sponsor-block")
def _remove_sponsor(soup: BeautifulSoup) -> int:
    """Sponsor / partner blocks matched on their text.

    Class-based selectors cannot work here: the theme ships hashed Tailwind
    class names. The sponsor sentences are distinctive and stable, so match on
    those and remove the nearest block-level ancestor.
    """
    n = 0
    hosts = []
    for el in soup.find_all(string=_SPONSOR_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n


@module("emoji-images")
def _remove_emoji(soup: BeautifulSoup) -> int:
    """Facebook emoji served as images (static.xx.fbcdn.net).

    These are decorative glyphs the theme rasterises; the article text already
    carries the emoji characters themselves. Check both ``src`` and
    ``data-src`` -- lazy-loaded emoji use the latter.
    """
    n = 0
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "fbcdn.net" in src or "emoji" in src.lower():
            img.decompose()
            n += 1
    return n


# Romanian text often uses the cedilla forms (U+015F ş, U+0163 ţ) rather than
# the comma-below forms (U+0219 ș, U+021B ț) -- they look identical but are
# different code points, so both must be matched.
_RELATED_TEXT = re.compile(r"articole similare|pe aceea[șşs]i temă", re.IGNORECASE)


@module("related-posts")
def _remove_related(soup: BeautifulSoup) -> int:
    """Related-posts blocks ("Articole similare", div.crp_related)."""
    n = 0
    for sel in ("div.crp_related", "div.related-posts", ".jp-relatedposts"):
        for el in soup.select(sel):
            el.decompose()
            n += 1
    hosts = []
    for el in soup.find_all(string=_RELATED_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n


@module("social-share")
def _remove_share(soup: BeautifulSoup) -> int:
    """Share buttons (Facebook / Twitter / WhatsApp rows)."""
    n = 0
    for sel in ("div.post_share_new", "nav.partajare", "div.share-buttons",
                ".social-share", ".sharing", "div.sassy-social-share"):
        for el in soup.select(sel):
            el.decompose()
            n += 1
    return n


@module("head-meta")
def _remove_meta(soup: BeautifulSoup) -> int:
    """<meta>, <noscript>, <script> and <style> tags that end up inside the
    extracted body.

    <noscript> interstitials ("Enable JavaScript and cookies to continue"), stray
    <meta> tags, and <script>/<style> blocks are never article content; the
    scan found them across many sites.
    """
    n = 0
    for tag in soup.find_all(["meta", "noscript", "script", "style"]):
        tag.decompose()
        n += 1
    return n


@module("the-tags")
def _remove_tags(soup: BeautifulSoup) -> int:
    """The tags footer WordPress appends to posts ("Tags: Romania, ...")."""
    n = 0
    for el in soup.select("div.the-tags"):
        el.decompose()
        n += 1
    hosts = []
    for el in soup.find_all(string=re.compile(r"^Tags:")):
        host = _block_parent(el, ("div", "p"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n
