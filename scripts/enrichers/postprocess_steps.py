"""Named post-processing steps for the ``fix_feeds`` stage.

``scripts/fix_feeds.py`` rewrites an item's title and description with a handful
of independent repairs: year formatting, poster sizes, duplicate links, the
content-module layout, and so on. Most feeds need some of them and not others --
an article feed has no magnet link and no poster to lay out, and a feed whose
enricher already wrote the canonical layout has nothing for the placement pass
to do.

Which steps a feed runs is configured per site in ``config/sites.yaml``::

    postprocess: [images, links, layout]

Omitting the key runs every step (the behaviour before the switch existed), and
an explicit empty list runs none. The names are coarse on purpose: they group the
passes a reader would describe as one repair, so a feed's config says "this feed
needs its links deduplicated" rather than naming six regex helpers.

The registry is also what ``scripts/analyze_postprocess.py`` measures, so every
step here has a matching gate in ``fix_feeds``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PostprocessStep:
    """One independently switchable repair in the fix stage."""

    name: str
    summary: str


# Run order matters: `images` normalizes the markup that `layout` then places,
# and `separators` cleans up what the removals in `chrome` leave behind. The
# registry lists them in the order ``fix_feeds`` applies them so the analyzer and
# the docs cannot drift from the code.
POSTPROCESS_STEPS: tuple[PostprocessStep, ...] = (
    PostprocessStep(
        "titles",
        "year format, the per-site title cleanups, and a year recovered from the URL",
    ),
    PostprocessStep(
        "images",
        "broken TMDB/next-image URLs, poster sizing and the src downscale",
    ),
    PostprocessStep(
        "labels",
        "the per-feed label fields a legacy selector emitted (uindex)",
    ),
    PostprocessStep(
        "links",
        "duplicate or fabricated Trailer, IMDb and EpGuides anchors, and a missing year in a search query",
    ),
    PostprocessStep(
        "layout",
        "the content-module placement pass (poster, air date, generated links)",
    ),
    PostprocessStep(
        "chrome",
        "re-apply the site's removal modules to a description already in the feed",
    ),
    PostprocessStep(
        "magnet",
        "the Download button on an item whose own link is a magnet URI",
    ),
    PostprocessStep(
        "separators",
        "the dangling separator an article extractor leaves at the end",
    ),
)

STEP_NAMES: tuple[str, ...] = tuple(step.name for step in POSTPROCESS_STEPS)
STEP_BY_NAME: dict[str, PostprocessStep] = {step.name: step for step in POSTPROCESS_STEPS}


def known_steps() -> list[str]:
    """Names accepted by ``postprocess:`` (for config validation)."""
    return list(STEP_NAMES)


def resolve_steps(
    configured: tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    """Resolve a feed's configured steps, or every step when unset.

    ``None`` means "not configured", which keeps the historical behaviour of
    running every pass. ``()`` means "configured as empty, so run nothing".
    """
    if configured is None:
        return STEP_NAMES
    unknown = [name for name in configured if name not in STEP_BY_NAME]
    if unknown:
        raise ValueError(
            f"unknown postprocess step(s) {sorted(unknown)}; known: {known_steps()}"
        )
    # The steps run in registry order regardless of how the config lists them,
    # so normalize rather than preserve the caller's order.
    wanted = {str(name).strip() for name in configured}
    return tuple(name for name in STEP_NAMES if name in wanted)


def step_enabled(steps: tuple[str, ...] | None, name: str) -> bool:
    """Whether `name` runs for this feed.

    ``None`` means "no step list supplied" (a direct caller that predates the
    switch); treat that as every step enabled, which is the old behaviour.
    """
    return steps is None or name in steps
