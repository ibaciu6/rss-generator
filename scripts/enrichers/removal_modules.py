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
  theme-icons      any <img> under /wp-content/themes/         5 sites, 57 items
  blank-embeds     <iframe>/<embed>/<object> naming no document 4 sites, 17 embeds
  dedupe-images    the same photo twice inside the body        12 sites, 78 items
  page-shell       doctype + <html>/<head> from the fallback    3 sites, 19 items
  svg-sprites      inline <svg> referencing theme sprites       1 site
  gnews-banner     "add us as a Google News source" CTA         1 site, 30 items
  author-box       author bio + "Articole: N" footer            1 site
  post-navigation  prev/next article navigation                1 site
  promo-footer     daily-offer / partner banner                 1 site
  subscribe-forms  newsletter signups, search boxes, any <form> 4 sites, 39 items
  cosmetic-filters config/cosmetic-filters.txt, the declarative set     many
  site-footer      the page <footer> extraction dragged in with the body
  donation-cta     "Ți-a fost util acest articol?" support/donation cards
  subscribe-embeds <iframe> pointing at a Substack/Mailchimp signup widget
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Doctype
from bs4.element import Comment

from .cosmetic_filters import apply_cosmetic_filters, load_rules

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COSMETIC_FILTERS = REPO_ROOT / "config" / "cosmetic-filters.txt"

# A module takes a parsed body and returns how many elements it removed.
ModuleFn = Callable[[BeautifulSoup], int]

_REGISTRY: dict[str, ModuleFn] = {}


# Elements whose text is a sentence rather than a block's label. A phrase found
# inside one of these is prose, and climbing out of it reaches whatever div holds
# the prose -- which on a WordPress site is `div.entry-content`, the article
# itself. Every text-matching module calls `_block_parent`, so this is where the
# climb has to stop.
_PROSE_PARENTS = frozenset({"p", "li", "blockquote", "td", "figcaption", "dd"})


def _block_parent(node, tags: tuple[str, ...]):
    """First block-level ancestor of a text node, refusing to climb out of prose.

    ``find_all(string=...)`` yields NavigableStrings, which have ``parents``
    but no ``parent`` -- so ``find_parent`` blows up on them. Walk ``parents``
    instead and return the first tag whose name is in ``tags``.

    A match inside a paragraph is not a block label *unless the caller asked for
    the paragraph* -- ``akismet-notice`` targets ``("p",)`` because its notice is
    a paragraph, and that must keep working. So the owner is checked first, and
    the guard applies only to climbing past it.

    Without that guard APADOR-CH lost whole articles: its posts write "Articole
    similare" and "Citește și" in prose, and the nearest div above such a
    sentence is `div.entry-content`. One item fell to 2% of its text, and no
    guard noticed -- the module is trusted by name.
    """
    owner = getattr(node, "parent", None)
    owner_name = getattr(owner, "name", None)
    if owner_name in tags:
        return owner
    if owner_name in _PROSE_PARENTS:
        return None
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

    A module normally mutates the soup in place and returns a count. A module
    that rebuilds the whole document may instead return a string. That second
    path exists because grafting one soup's children into another corrupts the
    text: moving nodes across trees re-escapes their contents, so a description
    grew 128 characters per run and its already-doubled `&amp;` sequences
    doubled again, every time fix_feeds re-applied the site's removals.

    A returned string is *adopted as the working document*, not held back as
    the final answer. Holding it back was the bug this paragraph replaces: the
    loop went on calling later modules against the original soup, so their
    in-place edits were made to a tree that the return then threw away. Since
    ``cosmetic-filters`` is the one module that returns a string, and 23 of the
    38 sites that use it list it *first*, every module those sites name after it
    had been silently discarded on every run -- revoblog's newsletter form, the
    ``page-shell`` that keeps naked-security from publishing its nav menu,
    hackread's image dedupe. ``cosmetic-filters`` before ``subscribe-forms``
    left the form in place; the reverse order removed both.
    """
    if not names:
        return html
    soup = BeautifulSoup(html, "html.parser")
    replacement: str | None = None
    changed = False
    for name in names:
        fn = _REGISTRY.get(name)
        if fn is None:
            continue
        out = fn(soup)
        if isinstance(out, str):
            # Re-parse rather than grafting: the soup the next module receives
            # must be the document this one produced. The round trip is
            # lossless -- apply_cosmetic_filters relies on the same thing for
            # its rollback snapshots -- so no extra escaping is introduced.
            replacement = out
            soup = BeautifulSoup(out, "html.parser")
            changed = True
        elif out:
            changed = True
            # A later in-place edit invalidates an earlier rebuild.
            replacement = None
    if replacement is not None:
        return replacement
    if not changed:
        # Nothing was removed, so hand back the input untouched. Serialising the
        # soup would re-escape it: on a description already carrying a dozen
        # `&amp;` layers, html.parser plus decode_contents() adds another one,
        # and because fix_feeds re-applies every site's removals on every run
        # that compounded without limit. Only 296 of 1065 published items have
        # anything for these modules to remove, so this path is the common one.
        return html
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
        # Host compared as a host, and "emoji" looked for in the path where an
        # asset name belongs. As substrings this deleted any <img> whose src
        # merely mentioned fbcdn.net, and every image hosted on a CDN literally
        # named emoji-cdn.example.ro.
        try:
            parts = urlsplit(src)
        except ValueError:
            continue
        host = parts.netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
        if host == "fbcdn.net" or host.endswith(".fbcdn.net") or "emoji" in parts.path.lower():
            img.decompose()
            n += 1
    return n


# Romanian text often uses the cedilla forms (U+015F ş, U+0163 ţ) rather than
# the comma-below forms (U+0219 ș, U+021B ț) -- they look identical but are
# different code points, so both must be matched.
_RELATED_TEXT = re.compile(r"articole similare|pe aceea[șşs]i temă", re.IGNORECASE)

# Deliberately NOT matched on "Citește și", though APADOR-CH's block is titled
# exactly that: its own articles use the phrase in prose ("Citește motivarea
# instanței în procesul intentat"). The named container below catches the block
# without asking the text to be told apart from the article's own sentences.

# Altruista -- APADOR-CH's theme -- names its block with an underscore
# (`div.related_posts`, not `div.related-posts`), which is why it shipped in the
# feed for so long, and spells the heading "Citeste si:" unaccented, so matching
# the accented form finds nothing.
_RELATED_CONTAINERS = (
    "div.crp_related",
    "div.related-posts",
    ".jp-relatedposts",
    "div.related_posts",
    ".related_entries_container",
)


@module("related-posts")
def _remove_related(soup: BeautifulSoup) -> int:
    """Related-posts blocks ("Articole similare", div.crp_related).

    This lives here rather than in ``cosmetic-filters.txt`` because the block
    is mostly images: APADOR-CH's holds six 180x180 thumbnails of other posts
    beside the article's single photograph, and the cosmetic image budget --
    correctly, for a rule that cannot tell a photograph from a thumbnail --
    refuses any rule taking 6 of 7. ``div.related_posts`` names the block as
    precisely as the selectors above do, and the module has no budget because
    it is only ever pointed at blocks that are chrome by name.
    """
    n = 0
    for sel in _RELATED_CONTAINERS:
        for el in _outermost(soup.select(sel)):
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
    # A bare "Share this" label above the buttons. Matched whole, and the climb
    # refuses to leave prose, so a paragraph that happens to contain the phrase
    # keeps it -- only the label paragraph itself goes, and the empty wrapper
    # above it is pruned after.
    hosts = []
    for el in soup.find_all(string=re.compile(r"^\s*Share this\s*[.!]?\s*$", re.IGNORECASE)):
        host = _block_parent(el, ("div", "section", "aside", "p"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n + (_prune_empty_wrappers(soup) if n else 0)


@module("head-meta")
def _remove_meta(soup: BeautifulSoup) -> int:
    """Strip tags that only make sense outside the rendered article body.

    ``<meta>``, ``<script>`` and ``<style>`` are dropped outright -- nothing
    inside them is ever article content.

    ``<noscript>`` needs care because it plays two roles. A text-only one is a
    bot interstitial ("Enable JavaScript and cookies to continue") and is
    dropped, but a lazy-loading theme wraps the real photo in one
    (``<noscript><img src="..."></noscript>``), and deleting that would take
    the article's only image with it. So a ``<noscript>`` holding elements is
    unwrapped -- the tag goes, the photo stays.
    """
    n = 0
    for tag in soup.find_all(["meta", "script", "style"]):
        tag.decompose()
        n += 1
    for tag in soup.find_all("noscript"):
        if tag.find(["img", "picture", "video", "iframe", "figure", "source"]):
            tag.unwrap()
        else:
            tag.decompose()
        n += 1
    return n


@module("post-navigation")
def _remove_post_nav(soup: BeautifulSoup) -> int:
    """Previous/next article navigation (WordPress `nav.post-navigation`).

    Appears at the end of every post and links to the neighbouring articles --
    it is not part of the article being read.
    """
    n = 0
    for el in soup.select("nav.post-navigation"):
        el.decompose()
        n += 1
    return n


# Daily-offer / partner-promo blocks that repeat at the end of every post.
# revoblog's eMAG offer and Google News banner use this class family.
_OFFER_TEXT = re.compile(r"Oferta zilei|sursă preferată|sursa preferata", re.IGNORECASE)


# Daily-offer / partner-promo blocks that repeat at the end of every post.
# revoblog's eMAG offer and Google News banner use this class family.
#
# Matched on the class *attribute*, not on a tag: the same block ships as
# `<aside class="rb-emag-offer">` on the live site and `div.rb-emag-offer__content`
# in some cached renders, so a `div.`-prefixed selector silently missed it.
_OFFER_TEXT = re.compile(r"Oferta zilei|sursă preferată|sursa preferata", re.IGNORECASE)
_OFFER_CLASS_SUBSTRINGS = (
    "rb-emag-offer",
    "promo-footer",
    "google-news-wrap",
    "gnews-cta",
)


def _outermost(elements):
    """Drop matches already contained in another match.

    A BEM block matches both `<aside class="rb-emag-offer">` and its children
    `rb-emag-offer__inner`; decomposing the child first would detach the parent.
    """
    out = []
    for el in elements:
        if any(parent in out for parent in el.parents):
            continue
        out.append(el)
    return out


@module("promo-footer")
def _remove_promo_footer(soup: BeautifulSoup) -> int:
    """Daily-offer / partner-promo footer blocks.

    Matched on the class *attribute*, not on a tag: the same block ships as
    `<aside class="rb-emag-offer">` live and as `div.rb-emag-offer__content` in
    some cached renders, so a `div.`-prefixed selector silently missed it. The
    distinctive Romanian text catches a future rename.
    """
    n = 0
    for frag in _OFFER_CLASS_SUBSTRINGS:
        for el in _outermost(soup.select(f'[class*="{frag}"]')):
            el.decompose()
            n += 1
    hosts = []
    for el in soup.find_all(string=_OFFER_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n


# Author bio / post-count blocks that themes append to the end of every post.
# The pattern (author name, photo, short bio, "Articole: N") repeats verbatim
# across a feed, so it reads as boilerplate rather than content.
_AUTHOR_BOX_SEL = (
    "div.author-box",
    "div.author-box-bio",
    "div.author-bio",
    "section.author-box",
    "aside.author-box",
    "div.entry-author-bio",
)


@module("author-box")
def _remove_author_box(soup: BeautifulSoup) -> int:
    """Author bio / post-count footer block."""
    n = 0
    for sel in _AUTHOR_BOX_SEL:
        for el in soup.select(sel):
            el.decompose()
            n += 1
    return n


# Theme assets (icons, avatars, sprites, banners) live under
# /wp-content/themes/ and get picked up whenever the extracted container
# includes the theme's own chrome. Article photos are always under
# /wp-content/uploads/, so the path alone is a reliable discriminator -- a
# name-based heuristic was tried first and missed user.svg, calendar.svg,
# rss.png and avatar_default_*.png.
_THEME_ASSET_RE = re.compile(r"/wp-content/themes/", re.IGNORECASE)


@module("theme-icons")
def _remove_theme_icons(soup: BeautifulSoup) -> int:
    """Theme UI assets that leak into the article body."""
    n = 0
    for img in list(soup.find_all("img")):
        src = img.get("src") or img.get("data-src") or ""
        if _THEME_ASSET_RE.search(src):
            img.decompose()
            n += 1
    return n


# An embed that names no document. In a browser these are invisible -- a lazy
# loader or a consent plugin swaps them out before anything is painted -- but a
# feed carries no script, so the reader draws the box at the size the page
# declared and nothing ever fills it: a band of white the height of a video
# player, sitting between two paragraphs.
#
# Every shape this was written against is a placeholder sitting beside the real
# frame rather than instead of it: WordPress lazy-load (`src="about:blank"`,
# 13 of them on computerblog, each one element above the real frame), and
# digital-citizen's `data-src` frame duplicated one line below by the real one.
# Recorder's and snoop's **Complianz** consent placeholders are the exception --
# no real frame follows them, but they paint their poster from a stylesheet a
# reader never loads, so they draw nothing either. A placeholder that wraps
# fallback markup (a real `<iframe>`/`<embed>` or an `<img>` poster) is **not**
# removed: `blank-embeds` only deletes a leaf that names no document and has no
# content children.
#
# `about:blank` is not the only spelling: an attribute-less frame names no
# document either, and `javascript:` / `data:text/html` / `#` are the other ways
# a page asks for something that paints nothing.
_BLANK_EMBED_TAGS = ("iframe", "embed", "object")
_BLANK_EMBED_URLS = ("about:blank", "about:srcdoc", "#", "javascript:void(0)")
_BLANK_EMBED_PREFIXES = ("javascript:", "data:text/html")


def _renders_no_document(tag) -> bool:
    """True when the element names no document for the reader to fetch."""
    # <object> takes its URL in `data`; `src` is not part of its content model.
    url = tag.get("data" if tag.name == "object" else "src") or ""
    url = url.strip().lower()
    if not url:
        return True
    return url in _BLANK_EMBED_URLS or url.startswith(_BLANK_EMBED_PREFIXES)


def _is_blank_embed(tag) -> bool:
    """A placeholder: nothing to fetch, and nothing to fall back on."""
    if tag.name == "iframe" and (tag.get("srcdoc") or "").strip():
        return False  # the document is written into the markup -- that is content
    # Fallback markup. <object> with no `data` is the standard way to wrap a
    # frame for a browser that cannot play it, so deleting the wrapper would
    # delete the frame with it.
    if tag.find(_BLANK_EMBED_TAGS) or tag.find("img"):
        return False
    return _renders_no_document(tag)


@module("blank-embeds")
def _remove_blank_embeds(soup: BeautifulSoup) -> int:
    """Embeds that draw an empty box: no `src`, or one naming a blank document."""
    n = 0
    # Outermost only: `decompose()` clears the attributes of every descendant, so
    # a nested candidate judged afterwards would raise on a detached tag. That
    # raise escapes the module, and enrich_article_feed writes its tree only at
    # the end -- one malformed element would leave every item on the site on its
    # excerpt, reported as a one-line [ERR].
    for tag in _outermost([t for t in soup.find_all(_BLANK_EMBED_TAGS) if _is_blank_embed(t)]):
        tag.decompose()
        n += 1
    return n


# Same-photo duplicates *within* the body. `body_contains_image` only compares
# the featured image against the body, so two size variants of the same photo
# both sitting in the content still render twice.
_IMG_KEY_RE = re.compile(r"-\d+x\d+(?=\.[a-z0-9]+$)", re.IGNORECASE)


def _image_identity(url: str) -> str:
    path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    return _IMG_KEY_RE.sub("", path)


@module("dedupe-images")
def _dedupe_images(soup: BeautifulSoup) -> int:
    """Drop repeated images within the body, keeping the first occurrence.

    Two renditions of one photo (`photo.jpg` and `photo-845x321.jpg`) or the same
    file served through a CDN mirror render as the same picture twice.
    """
    n = 0
    seen: set[str] = set()
    for img in list(soup.find_all("img")):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        key = _image_identity(src)
        if key in seen:
            img.decompose()
            n += 1
        else:
            seen.add(key)
    return n


# When no selector matches, extract_main_content falls back to cleaning the
# whole page, so the feed ends up carrying a whole HTML document: the doctype,
# <html>/<head>/<body> wrappers and the <link>/<title> tags inside <head>.
# None of that is article content.
# Tags that are pure scaffolding: drop them and their subtree.
_SHELL_DROP = ("head", "link", "title", "base", "noscript")
# Tags that *wrap* the content: unwrap them, keeping their children. Deleting
# <body> would throw the whole article away.
_SHELL_UNWRAP = ("html", "body")


@module("page-shell")
def _remove_page_shell(soup: BeautifulSoup) -> int:
    """Document scaffolding left behind by the whole-page extraction fallback."""
    n = 0
    # A doctype is a `Doctype` object at the root of the soup, not a
    # NavigableString, so find_all(string=...) cannot see it.
    for node in list(soup.contents):
        if isinstance(node, Doctype):
            node.extract()
            n += 1
    for tag in soup.find_all(_SHELL_UNWRAP):
        tag.unwrap()
        n += 1
    for tag in soup.find_all(_SHELL_DROP):
        tag.decompose()
        n += 1
    return n


# Inline <svg><use xlink:href=".../themes/.../sprite/icons.svg#icon-x"/></svg>
# is a theme sprite reference, not an illustration. The <img>-based
# theme-icons module cannot see it because the asset lives in an attribute.
_THEME_SVG_USE_RE = re.compile(r"/wp-content/themes/", re.IGNORECASE)


@module("svg-sprites")
def _remove_svg_sprites(soup: BeautifulSoup) -> int:
    """Inline <svg> sprites that reference theme asset paths.

    The asset path lives on a descendant ``<use xlink:href=...>``, not on the
    ``<svg>`` itself, so the whole subtree is checked.
    """
    n = 0
    for svg in list(soup.find_all("svg")):
        if _THEME_SVG_USE_RE.search(str(svg)):
            svg.decompose()
            n += 1
    return n


# "Add us as a preferred source in Google News" banners. Sites use their own
# class prefixes, so this is matched on the distinctive Romanian button text
# plus the common class names.
_GNEWS_TEXT = re.compile(
    r"surs[ăa] preferat[ăa].{0,40}Google News|Adaug[ăa]-ne ca surs", re.IGNORECASE | re.DOTALL
)
_GNEWS_SEL = (
    "div.edupedu-google-wrap",
    "div.google-news-wrap",
    "div.gnews-cta",
    "a.edupedu-google-button",
    # gHacks' English spelling of the same button: a div wrapping one link to
    # google.com/preferences/source and a "preferred-source-badge" image. It is
    # an image, so a cosmetic rule for it is unreliable -- the image budget
    # counts it against the article's own -- but the module has no budget and
    # the container is named for the badge.
    "div.google-preferred-source-badge",
    # pressone's React build inlines the CTA into a prose paragraph as an <a>
    # holding an SVG logo and a "gn-cta-text" span ("Adaugă-ne la favorite pe
    # Google ca să nu dispărem din feed-ul tău"). It is an element, so taking
    # the anchor out never takes the paragraph's sentence with it.
    "a:has(.gn-cta-text)",
    "a:has(.gn-cta-btn)",
)


@module("gnews-banner")
def _remove_gnews_banner(soup: BeautifulSoup) -> int:
    """"Add us as a source in Google News" call-to-action banner."""
    n = 0
    for sel in _GNEWS_SEL:
        for el in soup.select(sel):
            el.decompose()
            n += 1
    hosts = []
    for el in soup.find_all(string=_GNEWS_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n


# Newsletter / subscribe blocks, and the empty wrappers they leave behind.
#
# A <form> in article prose is never the article: it is a MailChimp/MailerLite/
# Mautic signup, a site search box, or a login widget. All four were found
# shipping in feeds (revoblog, digital-citizen, hackingpassion, pressone), and
# they render as a visible input box in the reader.
#
# The vendor containers are listed separately because some themes emit the
# block as a bare <div> with no <form> at all.
_FORM_CONTAINERS = (
    "[id^=mc_embed_signup]",           # MailChimp
    ".mc_embed_signup",
    ".ml-form-embedContainer",         # MailerLite
    ".ml-subscribe-form",
    ".mauticform_wrapper",             # Mautic
    "[id^=mauticform_wrapper]",
    ".hp-news-form",                   # hackingpassion
    ".newsletter-form",
    ".subscribe-form",
    ".newsletter-box",                 # digital-citizen tabbed variant
    ".nsl-art-container",
    ".footer-nsl",
)

# Fallback for a newsletter block whose classes we do not recognise. These are
# full boilerplate sentences, not words that could occur in prose about
# newsletters, so matching them is safe.
_NEWSLETTER_TEXT = re.compile(
    r"Înscrie-te\s+la\s+newsletter"
    r"|Prime[ȘsŞş]te\s+zilnic\s+articolele\s+noastre"
    r"|Te\s+po[Țț]i\s+dezabona\s+oric[âa]nd"
    r"|Abonează-te\s+[ȘsŞş]i\s+vei\s+primi\s+un\s+mail",
    re.IGNORECASE,
)

# HTML comments a signup widget leaves behind once the form is gone. Matched on
# the widget's own name, so an article that *writes about* the marker in prose
# (a `<code>` sample of HTML) is not touched -- those are elements, not comments.
_FORM_COMMENT_RE = re.compile(
    r"\b(?:begin|end)?\s*"
    r"(?:mailchimp|mailerlite|mailmunch|mautic|convertkit|klaviyo|"
    r"newsletter|subscribe|signup|opt-?in)\b[^\n]{0,40}?\bform\b",
    re.IGNORECASE,
)

# Tags that can be pruned once they hold nothing a reader would see.
_PRUNE_TAGS = ("div", "section", "aside", "span")
_PRUNE_KEEP = ("img", "picture", "video", "iframe", "a", "embed", "object")


def _prune_empty_wrappers(soup: BeautifulSoup) -> int:
    """Drop containers left hollow by a removal, innermost first.

    Removing a newsletter leaves behind whatever wrappers held it -- a bare
    <form> leaves its parent div, a vendor container can leave a sibling
    (`nsl-art-2`) that only existed to position the widget. A stack of empty
    divs is invisible in HTML but shows up as blank space in the reader, so
    sweep bottom-up and drop any plain container with no text and no media.

    Stops at elements that still carry an image, video, embed or link, and at
    the soup root, so a real article wrapper is never removed.
    """
    n = 0
    for tag in list(soup.find_all(_PRUNE_TAGS)):
        if tag.parent is None or tag.decomposed:
            continue
        if tag.get_text(strip=True) or tag.find(_PRUNE_KEEP):
            continue
        tag.decompose()
        n += 1
    return n


@module("subscribe-forms")
def _remove_subscribe_forms(soup: BeautifulSoup) -> int:
    """Newsletter signups, search boxes and other forms in the article body."""
    n = 0
    for sel in _FORM_CONTAINERS:
        for el in _outermost(soup.select(sel)):
            el.decompose()
            n += 1
    for form in _outermost(soup.find_all("form")):
        if form.find_parent("form"):
            continue
        form.decompose()
        n += 1
    # A vendor that brackets its widget in HTML comments leaves those comments
    # behind when the form itself is removed, and they are the only trace of it
    # in the output: revoblog shipped `<!-- Begin MailChimp Signup Form -->` /
    # `<!-- End MailChimp Signup Form -->` at the end of all 7 of its items,
    # escaped as visible text in every reader that renders the description as
    # text rather than markup. `find_all(string=...)` cannot see them -- a
    # comment is a Comment, not a NavigableString -- so they are matched as
    # nodes instead.
    for node in list(soup.find_all(string=lambda t: isinstance(t, Comment))):
        if not _FORM_COMMENT_RE.search(str(node)):
            continue
        node.extract()
        n += 1
    hosts = []
    for el in soup.find_all(string=_NEWSLETTER_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        if host.find(_PRUNE_KEEP):
            continue
        # Keep the wrapper when it holds prose beyond the newsletter boilerplate:
        # an article *about* newsletters quotes the same sentences. Only
        # word/digit characters count -- stripping the phrases leaves the
        # sentence-ending punctuation behind.
        residual = _NEWSLETTER_TEXT.sub(" ", host.get_text(" ", strip=True))
        if re.search(r"\w", residual, re.UNICODE):
            continue
        host.decompose()
        n += 1
    return n + _prune_empty_wrappers(soup)


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


# --------------------------------------------------------------------------- #
# cosmetic-filters: the declarative set
# --------------------------------------------------------------------------- #

# Parsed once. The file changes with the code, and a run touches ~1000 items,
# so re-reading and re-parsing it per item would be pure waste.
_COSMETIC_CACHE: tuple | None = None


def _cosmetic_rules() -> tuple:
    global _COSMETIC_CACHE
    if _COSMETIC_CACHE is None:
        _COSMETIC_CACHE = load_rules(COSMETIC_FILTERS) if COSMETIC_FILTERS.is_file() else ([], [])
    return _COSMETIC_CACHE


@module("cosmetic-filters")
def _apply_cosmetic(soup: BeautifulSoup) -> str | int:
    """Apply config/cosmetic-filters.txt -- the generic, site-agnostic rules.

    The one module whose selectors live in data rather than here, on the
    reasoning that hand-written class names go stale the moment a site
    redesigns. See scripts/enrichers/cosmetic_filters.py for the format and for
    the content budget that stops a rule emptying an article.

    Returns the rebuilt document rather than mutating in place: the engine
    works on strings, and moving the result's nodes back into `soup` re-escapes
    their text, which grew a description by 128 characters every run.

    The engine's report -- which rules fired, which were refused, and exactly
    which image sources a rule took -- is discarded here;
    scripts/eval_cosmetic_filters.py exists to surface it.
    """
    rules, exempts = _cosmetic_rules()
    if not rules:
        return 0
    out, rep = apply_cosmetic_filters(soup.decode_contents(), rules, exempts=exempts)
    if not rep.elements and not rep.swept:
        return 0  # nothing to do: leave the document byte for byte alone
    return out


# --------------------------------------------------------------------------- #
# shortcodes: WordPress shortcodes the theme never rendered
# --------------------------------------------------------------------------- #

# A shortcode that a page forgot to render appears in the body as literal text:
# [su_note note_color="#b3eff6" text_color="#000000" radius="10"] ... [/su_note]
# The feed then shows the reader the plugin's source, including its
# smart-quoted attributes.
#
# The discriminator is the closing tag, not the shape. Every real shortcode
# comes in a pair, so requiring one is what makes this safe: an earlier pattern
# that matched any `[name key="value"]` also caught `[role="img"]` and
# `[active=true]` in snoop, and `[data-rmiz-content="found"]` 84 times in
# rapid7 -- CSS and template fragments leaking as text, with no closing form
# and nothing to do with shortcodes.
_SHORTCODE_RE = re.compile(r"\[(/?)([A-Za-z][A-Za-z0-9_-]{2,30})[^\]]{0,200}\]")


@module("shortcodes")
def _remove_shortcodes(soup: BeautifulSoup) -> int:
    """Strip unrendered shortcode pairs from the text of an article body.

    Only a tag whose closing form is also present is removed, so an unmatched
    `[...]` is left alone rather than guessed at.
    """
    closing: set[str] = set()
    # Every text node, then search inside it: a shortcode normally wraps real
    # prose -- "[su_note ...]Notele mele[/su_note]" -- so matching the whole
    # node against the pattern (find_all(string=...)) finds nothing.
    for node in soup.find_all(string=True):
        text = str(node)
        if "[" not in text:
            continue
        for m in _SHORTCODE_RE.finditer(text):
            if m.group(1):
                closing.add(m.group(2))
    if not closing:
        return 0

    n = 0
    for node in soup.find_all(string=True):
        text = str(node)
        if "[" not in text:
            continue
        # Both halves of each pair are dropped -- leaving [/su_note] behind
        # would show the reader a closing tag on its own. Spans are collected
        # first and the string rebuilt once: splicing into a shrinking buffer
        # with offsets taken from the original misplaces every match after the
        # first, which is how the opening tag went and the closing tag stayed.
        spans = [m.span() for m in _SHORTCODE_RE.finditer(text) if m.group(2) in closing]
        if not spans:
            continue
        parts: list[str] = []
        last = 0
        for start, end in spans:
            parts.append(text[last:start])
            last = end
            n += 1
        parts.append(text[last:])
        # Keep the node's outer whitespace. Stripping the rebuilt text instead
        # glues it to an adjacent inline tag: "a <b>bold</b> [su_note]x[/su_note]
        # b" came out as "<b>bold</b>x b", with "bold" and "x" run together.
        inner = "".join(parts).strip()
        lead = " " if text[:1].isspace() else ""
        trail = " " if text[-1:].isspace() else ""
        node.replace_with(lead + inner + trail)
    return n


@module("start-up-footer")
def remove_start_up_footer(soup: BeautifulSoup) -> int:
    """Remove start-up.ro footer/recommendations sections."""
    removed = 0
    import re

    # Remove elements containing these markers
    markers = [
        "Recomandarile noastre",
        "Citeste mai departe",
        "mai multe despre",
        "Abonează-te pe",
        "Cine suntem",
    ]
    for marker in markers:
        for elem in soup.find_all(string=re.compile(re.escape(marker))):
            cur = elem.parent
            for _ in range(40):
                if not cur or getattr(cur, "name", None) == "body":
                    break
                try:
                    cur.decompose()
                    removed += 1
                    break
                except Exception:
                    cur = getattr(cur, "parent", None)
                    continue

    # Remove article boxes (related posts)
    for abox in soup.find_all("div", class_="article-box"):
        abox.decompose()
        removed += 1

    # Remove other footer elements
    for cls in [
        "article-author-detailed",
        "google-news-subscribe",
        "tags-list",
        "article-footer-share",
        "ideal-width-2",
    ]:
        for div in soup.find_all("div", class_=cls):
            div.decompose()
            removed += 1

    for h6 in soup.find_all("h6", class_="small-bold-title"):
        h6.decompose()
        removed += 1

    return removed


# The page <footer> -- its link lists, copyright line and social icons -- when
# extraction climbs past the article and drags the site chrome in with it.
# pressone's body selector ended at `</article></main>` and kept going, so every
# item shipped the same "RSS / Newsletter / Despre noi / © ..." block.
@module("site-footer")
def _remove_site_footer(soup: BeautifulSoup) -> int:
    """The page footer and its class-named clones."""
    n = 0
    for el in _outermost(soup.find_all("footer")):
        el.decompose()
        n += 1
    for sel in ("div.site-footer", "div.footer", "div.copyright"):
        for el in _outermost(soup.select(sel)):
            el.decompose()
            n += 1
    return n


# "Support us" cards appended to the end of every article: a heading asking
# whether the piece was useful, the fundraising pitch, and the donation
# buttons. The class names are theme-specific, so the text does the matching
# and the container class does the removing.
_DONATION_TEXT = re.compile(
    r"Ți-a fost util acest articol"
    r"|Susține jurnalismul independent"
    r"|Devino abonat"
    r"|Redirecționează\s*3[.,]?5\s*%",
    re.IGNORECASE,
)


@module("donation-cta")
def _remove_donation_cta(soup: BeautifulSoup) -> int:
    """Fundraising cards at the end of the article.

    The pitch paragraph sits in prose, so the text pass deliberately refuses to
    climb out of a ``<p>`` -- an article *about* tax donations writes the same
    sentences, and taking the paragraph would take the article with it. The
    card's own class names are the primary target; the text pass is the
    fallback for the heading and buttons when a rename moves the card.
    """
    n = 0
    for sel in ("div.card.custom-card", "div.custom-card", ".support-card",
                ".donation-card", ".support-banner"):
        for el in _outermost(soup.select(sel)):
            el.decompose()
            n += 1
    hosts = []
    for el in soup.find_all(string=_DONATION_TEXT):
        host = _block_parent(el, ("div", "section", "aside"))
        if host is not None and not any(host is h for h in hosts):
            hosts.append(host)
    for host in hosts:
        host.decompose()
        n += 1
    return n + _prune_empty_wrappers(soup)


# Signup widgets some feeds embed at the end of every post. They draw a real
# document, so `blank-embeds` keeps them -- what makes them chrome is the host:
# a Substack or Mailchimp frame is always a subscribe form, never the article.
_SUBSCRIBE_EMBED_RE = re.compile(
    r"substack\.com|mailchimp\.com|buttondown\.email|convertkit\.com|kit\.com|revue\.fm",
    re.IGNORECASE,
)


@module("subscribe-embeds")
def _remove_subscribe_embeds(soup: BeautifulSoup) -> int:
    """Third-party signup widgets embedded as <iframe>/<embed>/<object>."""
    n = 0

    def _is_subscribe_embed(tag) -> bool:
        url = tag.get("data" if tag.name == "object" else "src") or ""
        return bool(_SUBSCRIBE_EMBED_RE.search(url))

    for tag in _outermost([t for t in soup.find_all(("iframe", "embed", "object")) if _is_subscribe_embed(t)]):
        tag.decompose()
        n += 1
    return n
