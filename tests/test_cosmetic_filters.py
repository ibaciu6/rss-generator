"""Tests for the declarative cosmetic filter engine.

The behaviour worth pinning here is not the selector syntax -- soupsieve
already tests that -- but the three guards, because each one was written in
response to something that actually went wrong on real feeds:

  * the content budget, after ``sponsor-block`` was measured wanting 98% of a
    securelist article;
  * ``noimg``, after a tag-cloud rule took a real theregister.com photo;
  * exact-match exceptions, after ``@@div.entry-content`` shielded every node
    in every WordPress article and silently disabled the whole rule set.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.enrichers.cosmetic_filters import (
    DEFAULT_MAX_IMAGE_RATIO,
    MIN_KEEP_RATIO,
    apply_cosmetic_filters,
    load_rules,
    parse_rule,
)


def html(text: str) -> str:
    return textwrap.dedent(text).strip()


def rules(*lines: str):
    return [r for r in (parse_rule(x) for x in lines) if r is not None]


def run(markup: str, *lines: str, **kw):
    parsed = rules(*lines)
    ex = kw.pop("exempts", ())
    ex = [r for r in (parse_rule(x) for x in ex) if r is not None]
    return apply_cosmetic_filters(markup, parsed, exempts=ex, **kw)


class TestRuleParsing:
    def test_a_plain_selector(self):
        r = parse_rule("div.share")
        assert (r.selector, r.up, r.noimg, r.exempt) == ("div.share", 0, False, False)
        assert r.domains == frozenset()

    def test_comments_blanks_and_bang_metadata_are_ignored(self):
        assert parse_rule("# a comment") is None
        assert parse_rule("") is None
        assert parse_rule("   ") is None
        assert parse_rule("! title: something") is None

    def test_domain_anchoring_uses_the_explicit_token(self):
        r = parse_rule("||example.com|test.ro^ div.tagcloud")
        assert r.selector == "div.tagcloud"
        assert r.domains == frozenset({"example.com", "test.ro"})

    @pytest.mark.parametrize(
        "selector",
        [
            "div.crp_related, div.related-posts",
            "div.bar a",             # a dotted class followed by a combinator
            "div.share",
            "a[href*='/tag/'] span",
        ],
    )
    def test_a_dotted_selector_is_never_read_as_a_domain(self, selector):
        """The bug this syntax exists to prevent: `div.bar a` parsed as the
        domain "div.bar" applied to the selector "a", so the rule silently
        matched nothing."""
        r = parse_rule(selector)
        assert r.domains == frozenset(), f"{selector} was read as anchored"
        assert r.selector == selector

    def test_options(self):
        r = parse_rule("div.related {up=1 noimg}")
        assert (r.selector, r.up, r.noimg) == ("div.related", 1, True)

    def test_exceptions(self):
        assert parse_rule("@@div.keep").exempt is True
        assert parse_rule("@@div.keep").selector == "div.keep"

    def test_domain_matching_covers_subdomains_only(self):
        r = parse_rule("||example.com^ div.x")
        assert r.applies_to("example.com")
        assert r.applies_to("www.example.com")
        assert not r.applies_to("notexample.com")
        assert not r.applies_to(None)

    def test_load_rules_splits_active_from_exempt(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("div.a\n@@div.b\n# c\ndiv.d {up=2}\n", encoding="utf-8")
        active, exempts = load_rules(f)
        assert [r.selector for r in active] == ["div.a", "div.d"]
        assert [r.selector for r in exempts] == ["div.b"]


class TestContentBudget:
    """A browser tab can lose a div and shrug. A published feed item cannot."""

    # A long body wrapped in a div, so a rule has a container to escalate to.
    LONG = "<div><p>" + ("Real article prose. " * 400) + "</p></div>"

    def test_a_rule_that_would_gut_the_article_is_refused(self):
        out, rep = run(
            self.LONG,
            "div:has(> p) {up=1}",
        )
        assert "Real article prose." in out, "the article was emptied"
        assert rep.refused, "the refusal must be recorded, not silent"

    def test_the_refusal_names_the_rule_and_says_why(self):
        """Two guards can stop this and which one fires depends on the markup:
        the budget, or the structural rule that escalation never reaches the
        content container. Either is a pass; silence is not."""
        _, rep = run(self.LONG, "div:has(> p) {up=1}")
        sel, why = rep.refused[0]
        assert sel == "div:has(> p)"
        assert why, "a refusal must explain itself"

    def test_the_catastrophic_shape_is_refused_at_a_low_ratio(self):
        """The measured securelist case: 19750 of 20079 chars, 98%."""
        body = "<div>" + "<p>" + ("Article prose. " * 1500) + "</p></div><div>" + ("Ad. " * 6000) + "</div>"
        out, rep = run(body, "div:has(> p) {up=1}", max_text_ratio=0.25)
        assert "Article prose." in out
        assert rep.refused

    def test_a_normal_tag_cloud_still_goes(self):
        body = "<p>" + ("Real article prose. " * 400) + "</p>" + '<div class="tags">' + (
            '<a href="/tag/a">a</a>' * 20
        ) + "</div>"
        out, rep = run(body, "div:has(> a[href*='/tag/']) {noimg}")
        assert "Real article prose." in out
        assert "tags" not in out
        assert not rep.refused

    def test_a_body_where_the_ratio_guard_never_bites_still_gets_cleaned(self):
        """The absolute floor exists so a short body is not judged on a ratio
        alone: a 12-character related-posts block in a 300-character item is 4%
        and should go, not be refused for looking disproportionate."""
        small = (
            '<div><p>' + ("Kept prose. " * 20) + '</p>'
            '<div class="rel"><h2>Related Posts:</h2><ul><li>x</li></ul></div></div>'
        )
        out, rep = run(small, "div.rel")
        assert "Kept prose." in out
        assert "Related Posts" not in out
        assert not rep.refused, rep.refused

    def test_budgets_do_not_shrink_as_rules_remove_thing(self):
        """Recomputing against the shrinking document would let three rules
        each take 25% and collectively eat the article."""
        body = "<p>" + ("Prose that matters. " * 400) + "</p>" + "".join(
            f'<div class="c{i}">' + ("chrome " * 200) + "</div>" for i in range(4)
        )
        out, _rep = run(
            body,
            *[f"div.c{i} {{noimg}}" for i in range(4)],
            max_text_ratio=0.25,
        )
        assert "Prose that matters." in out
        assert len(out) > len(body) * 0.5

    def test_the_image_budget_has_a_floor(self):
        """rapid7 carries 6 images of which 4 are the logos a rule should take;
        a flat 50% capped that at 3 and refused a correct removal."""
        body = (
            '<div class="strip">'
            + '<img src="/linkedin-logo.svg">'
            + '<img src="/facebook-logo.svg">'
            + '<img src="/x-logo.svg">'
            + '<img src="/bluesky-dark-logo.svg">'
            + "</div>"
            + "<p>" + ("Real article prose. " * 400) + "</p>"
            + '<img src="/the-article-photo.jpg">'
        )
        out, rep = run(body, "div.strip", max_image_ratio=DEFAULT_MAX_IMAGE_RATIO)
        assert "linkedin-logo" not in out and "the-article-photo" in out
        assert not rep.refused, rep.refused

    def test_the_image_budget_still_blocks_a_rule_taking_almost_all(self):
        body = '<div class="all">' + '<img src="/a.jpg">' * 9 + "</div>" + "<p>short</p>"
        out, rep = run(body, "div.all", max_image_ratio=0.8)
        assert "a.jpg" in out, "a rule took 9 of 9 images"
        assert rep.refused


class TestNoImageRules:
    def test_a_text_only_rule_refuses_a_match_carrying_an_image(self):
        body = '<p>' + ("Prose. " * 400) + '</p><div class="tags"><a href="/tag/a">a</a><img src="/real.jpg"></div>'
        out, rep = run(body, "div.tags {noimg}")
        assert "/real.jpg" in out, "a tag-cloud rule deleted an article photo"
        assert "3 match(es) carry images" in rep.refused[0][1] or "carry images" in rep.refused[0][1]

    def test_without_noimg_the_same_rule_would_have_taken_the_image(self):
        """Pins that the guard is what saves the photo, not luck."""
        body = '<p>' + ("Prose. " * 400) + '</p><div class="tags"><a href="/tag/a">a</a><img src="/real.jpg"></div>'
        out, _ = run(body, "div.tags")
        assert "/real.jpg" not in out


class TestExceptions:
    def test_an_exception_protects_exactly_that_element(self):
        markup = '<div class="keep">mine</div><div class="drop">chrome</div>'
        out, _ = run(markup, "div.drop", exempts=["@@div.keep"])
        assert "mine" in out and "chrome" not in out

    def test_an_exception_does_not_shield_a_subtree(self):
        """The bug: treating an exception as covering descendants made
        `@@div.entry-content` protect every node inside every WordPress
        article, so the whole rule set silently stopped working."""
        markup = '<div class="wrapper"><div class="drop">chrome</div><p>article</p></div>'
        out, rep = run(markup, "div.drop", exempts=["@@div.wrapper"])
        assert "chrome" not in out, "an exception shielded its whole subtree"
        assert rep.matched

    def test_an_exception_can_protect_a_matched_element_from_removal(self):
        markup = '<div class="widget">x</div>'
        out, rep = run(markup, "div.widget", exempts=["@@div.widget"])
        assert "widget" in out
        assert not rep.matched


class TestUpward:
    def test_up_escalates_unused_placeholder_to_the_nearest_block_ancestor(self):
        markup = '<ul class="bar"><li><a href="#">fb</a></li></ul><p>keep</p>'
        out, _ = run(markup, "ul.bar a {up=1}")
        assert "fb" not in out
        assert "keep" in out

    def test_up_two_reaches_the_list_itself(self):
        markup = '<ul class="bar"><li><a href="#">fb</a></li></ul><p>keep</p>'
        out, _ = run(markup, "ul.bar a {up=2}")
        assert "bar" not in out
        assert "keep" in out

    def test_an_empty_wrapper_left_behind_is_swept(self):
        """Removing the <a> out of a share list leaves a bare <ul>, and an empty
        block with margins is a band of blank space -- the original complaint."""
        markup = '<ul class="bar"><li><a href="#">fb</a></li></ul><p>keep</p>'
        out, rep = run(markup, "ul.bar a {up=1}")
        assert "<ul" not in out, out
        assert rep.swept == 1

    def test_the_sweep_never_touches_a_wrapper_with_content(self):
        markup = '<div><p>Prose that must stay.</p><figure><img src="/a.jpg"></figure></div>'
        out, rep = run(markup, "div.nothing-here")
        assert "Prose that must stay." in out
        assert "/a.jpg" in out
        assert rep.swept == 0

    def test_up_never_climbs_out_of_the_fragment(self):
        markup = '<a href="#">x</a>'
        out, rep = run(markup, "a {up=9}")
        assert "x" in out
        assert not rep.matched, "escalated past the fragment root"

    def test_up_zero_removes_only_the_match(self):
        markup = '<div class="bar">before <a href="#">fb</a> after</div>'
        out, _ = run(markup, "div.bar a")
        assert "fb" not in out
        assert "before" in out and "after" in out


class TestReporting:
    def test_every_removed_image_source_is_recorded(self):
        """Counting is not enough: rapid7 lost 60 images and nothing could name
        the culprit, because a before/after diff misattributes when two images
        share a src."""
        body = '<ul class="s"><li><img src="/linkedin-logo.svg"></li><li><img src="/x-logo.svg"></li></ul><p>' + (
            "Prose. " * 400
        ) + "</p>"
        _, rep = run(body, "ul.s", "ul.s:has(img)")
        srcs = rep.image_srcs.get("ul.s", []) + rep.image_srcs.get("ul.s:has(img)", [])
        assert "/linkedin-logo.svg" in srcs
        assert "/x-logo.svg" in srcs

    def test_counts_are_per_rule_not_cumulative(self):
        """Measuring once against the original made a later rule report images
        an earlier rule had already taken."""
        body = (
            '<div class="a"><img src="/a1.png"></div>'
            '<div class="b"><img src="/b1.png"></div>'
            + "<p>" + ("Prose. " * 400) + "</p>"
        )
        _, rep = run(body, "div.a", "div.b")
        assert rep.images.get("div.a") == 1
        assert rep.images.get("div.b") == 1, rep.images

    def test_an_invalid_selector_is_recorded_and_does_not_stop_the_run(self):
        out, rep = run('<div class="drop">x</div><p>keep</p>', "div[[[bad", "div.drop")
        assert "keep" in out
        assert "div.drop" in rep.matched, "the run stopped at the bad selector"
        assert any("invalid selector" in why for _, why in rep.refused)

    def test_refused_rules_remove_nothing(self):
        body = "<p>" + ("Prose. " * 400) + "</p><div>" + ("chrome " * 900) + "</div>"
        out, rep = run(body, "div:has(p) {up=1}")
        if rep.refused:
            assert "chrome" in out, "a refused rule still mutated the document"


class TestTheShippedFilterFile:
    """The real file, not a fixture: a syntax error in it would otherwise
    surface only as "no chrome removed" on a live feed."""

    @staticmethod
    def _load():
        p = Path(__file__).resolve().parent.parent / "config" / "cosmetic-filters.txt"
        return load_rules(p)

    def test_it_parses_without_error(self):
        active, _exempts = self._load()
        assert len(active) > 20, "the filter file looks empty or truncated"

    def test_every_selector_is_valid_for_soupsieve(self):
        from bs4 import BeautifulSoup

        active, _ = self._load()
        soup = BeautifulSoup("<div><p>x</p></div>", "html.parser")
        for r in active:
            soup.select(r.selector)  # raises on a bad selector

    def test_no_bare_tag_or_id_selectors(self):
        """A rule like `div` or `#content` matches something on every site and
        will eventually match something an article needs."""
        active, _exempts2 = self._load()
        for r in active:
            for sel in r.selector.split(","):
                sel = sel.strip()
                if not sel:
                    continue
                assert not sel.startswith("#"), f"id selector: {sel}"
                assert sel.split()[0] not in ("div", "p", "span", "a", "section", "ul", "li"), (
                    f"bare tag selector: {sel}"
                )

    def test_it_removes_a_share_row_and_keeps_the_article(self):
        active, exempts = self._load()
        body = (
            '<article><button aria-label="Share on Facebook">'
            '<img src="/assets/images/icons/facebook-icon.svg"></button>'
            "<p>The actual article sentence, which must survive.</p>"
            '<div class="crp_related"><h2>Related Posts:</h2><ul><li><a href="/a">b</a></li></ul></div>'
            "</article>"
        )
        out, rep = apply_cosmetic_filters(body, active, exempts=exempts)
        assert "must survive" in out
        assert "facebook-icon.svg" not in out
        assert "Related Posts" not in out
        assert rep.elements >= 2


class TestRemainingGuard:
    def test_the_keep_floor_is_a_fraction(self):
        assert 0 < MIN_KEEP_RATIO < 1

    def test_a_small_document_cannot_be_emptied(self):
        """The guard used to be skipped below 800 characters, and a 60-character
        item was emptied outright by one rule while every check passed."""
        body = '<article><button aria-label="Share on Facebook">fb</button><p>Sentence.</p></article>'
        out, rep = apply_cosmetic_filters(body, rules('[aria-label^="Share on"] {up=1}'))
        assert "Sentence." in out, out
        assert rep.refused


class TestIdempotency:
    """fix_feeds re-applies every site's removals on every run, so a pass that
    shifts a byte changes the published feeds on every pass."""

    @staticmethod
    def _load():
        p = Path(__file__).resolve().parent.parent / "config" / "cosmetic-filters.txt"
        return load_rules(p)

    def test_a_second_pass_changes_nothing(self):
        active, exempts = self._load()
        body = (
            '<article><button aria-label="Share on Facebook">'
            '<img src="/assets/images/icons/facebook-icon.svg"></button>'
            "<p>Sentence that must survive the first pass intact.</p>"
            '<div class="crp_related"><h2>Related Posts:</h2><ul><li><a href="/a">b</a></li></ul></div>'
            "</article>"
        )
        first, _ = apply_cosmetic_filters(body, active, exempts=exempts)
        second, _ = apply_cosmetic_filters(first, active, exempts=exempts)
        assert first == second

    def test_a_document_with_nothing_to_remove_is_returned_byte_for_byte(self):
        """The round-trip through BeautifulSoup shifts a byte here and there;
        217 of 1065 published items drifted by one byte per pass until a
        no-op returned its input unchanged."""
        active, exempts = self._load()
        body = '<p>Just an article, with <a href="https://example.com/x">a link</a>.</p>\n'
        out, rep = apply_cosmetic_filters(body, active, exempts=exempts)
        assert out == body
        assert rep.elements == 0
