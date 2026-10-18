"""Tests for the password gate on the two published HTML pages.

Two properties carry the whole design and neither is visible by reading the
page it produces:

1. **The password file is local-only.** A committed ``.pages_pass.txt`` would
   publish the password, and the point of committing a PBKDF2 verifier instead
   is that the secret has exactly one home. That is a git check, not a code
   check, so it is asserted here.
2. **The verifier the browser computes equals the one Python computed.** The
   page's JavaScript derives the password itself; if the two implementations
   ever disagree, the password simply stops working -- for the owner first.
   ``derive_verifier`` is pinned to published PBKDF2 vectors here, and
   ``tests/test_pages_gate_browser.py`` closes the loop in a real browser.

The functional end-to-end behaviour (locked on arrival, wrong password
rejected, unlock, per-session) lives in that browser module too.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import pages_gate

REPO_ROOT = Path(__file__).resolve().parent.parent


def _params(password: str = "correct horse battery staple", iterations: int = 1000) -> dict:
    """Fast parameters: the real iteration count is chosen for an attacker's
    budget, not a test suite's."""
    return pages_gate.new_params(password, iterations=iterations)


class TestTheDerivation:
    def test_matches_published_pbkdf2_hmac_sha256_vectors(self):
        """The KDF must be PBKDF2-HMAC-SHA256 and nothing else.

        Vectors cross-checked against node's ``crypto.pbkdf2Sync``, so this is
        two independent implementations agreeing, not Python agreeing with
        itself. A silent change to the digest or the key length would break
        every unlock and would be invisible in the page.
        """
        vectors = [
            ("password", "salt", 1, "120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b"),
            ("password", "salt", 2, "ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43"),
            ("password", "salt", 4096, "c5e478d59288c841aa530db6845c4c8d962893a001ce4e11a4963873aa98134a"),
            ("passwordPASSWORDpassword", "saltSALTsaltSALTsaltSALTsaltSALTsalt", 4096,
             "348c89dbcbd32b2f32d814b8116e84cf2b17347ebc1800181c4e2a1fb8dd53e1"),
        ]
        for password, salt, iterations, expected in vectors:
            derived = pages_gate.derive_verifier(
                password, salt.encode().hex(), iterations
            )
            assert derived == expected, f"{password=} {iterations=}"

    def test_a_verifier_is_32_bytes_of_lowercase_hex(self):
        verifier = pages_gate.derive_verifier("pw", "00" * 16, 10)

        assert len(verifier) == 64
        assert verifier == verifier.lower()
        assert all(c in "0123456789abcdef" for c in verifier)

    def test_the_salt_is_required_to_be_hex(self):
        with pytest.raises(pages_gate.PagesGateError):
            pages_gate.derive_verifier("pw", "not-hex", 10)

    def test_the_iteration_count_is_bounded(self):
        for bad in (0, -1, 5_000_001, "600000", 1.5, True):
            with pytest.raises(pages_gate.PagesGateError):
                pages_gate.new_params("pw", iterations=bad)


class TestVerify:
    def test_accepts_the_password_and_rejects_everything_else(self):
        params = _params("hunter2")

        assert pages_gate.verify("hunter2", params) is True
        # Negative cases, including the near-misses a sloppy compare would pass.
        for wrong in ("", "hunter", "hunter22", "Hunter2", "hunter2 ", " hunter2"):
            assert pages_gate.verify(wrong, params) is False, wrong

    def test_two_passwords_never_share_a_verifier(self):
        """A fresh salt per rotation is what stops a precomputed table from
        covering the whole key space."""
        first = pages_gate.new_params("same-password")
        second = pages_gate.new_params("same-password")

        assert first["salt"] != second["salt"]
        assert first["verifier"] != second["verifier"]


class TestParamsFile:
    def test_round_trips_through_the_file(self, tmp_path: Path):
        path = tmp_path / "pages-gate.json"
        written = pages_gate.set_password("local-only", path, iterations=1000)

        loaded = pages_gate.load_params(path)
        assert loaded["kdf"] == pages_gate.KDF
        assert loaded["iterations"] == 1000
        assert loaded["salt"] == written["salt"]
        assert loaded["verifier"] == written["verifier"]
        assert pages_gate.verify("local-only", loaded) is True

    def test_the_written_file_carries_no_password(self, tmp_path: Path):
        path = tmp_path / "pages-gate.json"
        pages_gate.set_password("a-secret-nobody-may-commit", path, iterations=1000)

        raw = path.read_text(encoding="utf-8")
        assert "a-secret-nobody-may-commit" not in raw
        # The note is there so whoever finds the file knows it is public on
        # purpose and where the password actually lives.
        assert ".pages_pass.txt" in json.loads(raw)["note"]

    def test_a_missing_file_raises_rather_than_publishing_ungated(self, tmp_path: Path):
        with pytest.raises(pages_gate.PagesGateError) as excinfo:
            pages_gate.load_params(tmp_path / "absent.json")

        assert "--set-password" in str(excinfo.value)

    def test_malformed_parameters_are_refused(self, tmp_path: Path):
        path = tmp_path / "pages-gate.json"
        good = _params("pw")
        cases = {
            "not-json": "{",
            "not-an-object": "[]",
            "missing-kdf": json.dumps({k: v for k, v in good.items() if k != "kdf"}),
            "wrong-kdf": json.dumps({**good, "kdf": "MD5"}),
            "zero-iterations": json.dumps({**good, "iterations": 0}),
            "iterations-as-string": json.dumps({**good, "iterations": "1000"}),
            "short-salt": json.dumps({**good, "salt": "abcd"}),
            "uppercase-salt": json.dumps({**good, "salt": good["salt"].upper()}),
            "verifier-not-hex": json.dumps({**good, "verifier": "z" * 64}),
            "truncated-verifier": json.dumps({**good, "verifier": good["verifier"][:32]}),
        }
        for label, raw in cases.items():
            path.write_text(raw, encoding="utf-8")
            try:
                pages_gate.load_params(path)
            except pages_gate.PagesGateError:
                continue
            pytest.fail(f"{label}: load_params accepted {raw}")

    def test_the_committed_parameters_are_valid(self):
        """The file the generators read on every run must parse. If this fails,
        the deploy fails on purpose instead of shipping a public page."""
        params = pages_gate.load_params()

        assert params["kdf"] == pages_gate.KDF
        assert params["iterations"] >= 600_000, "below the OWASP floor for PBKDF2-HMAC-SHA256"
        assert len(params["salt"]) >= 32
        assert len(params["verifier"]) == 64

    def test_the_local_password_matches_the_committed_verifier(self):
        """Skipped wherever the password is absent, which is everywhere except
        the owner's machine -- this is the only test that can catch a rotation
        that updated one side and not the other."""
        if not pages_gate.PASSWORD_FILE.exists():
            pytest.skip(".pages_pass.txt is local-only and not present here")

        password = pages_gate.read_password()
        assert pages_gate.verify(password, pages_gate.load_params()) is True

    def test_the_password_file_is_gitignored(self):
        """The one thing that must never be committed. `git check-ignore` is the
        only check that answers the question the repository actually asks."""
        result = subprocess.run(
            ["git", "check-ignore", "-v", ".pages_pass.txt"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert ".pages_pass.txt" in result.stdout

    def test_the_params_file_is_tracked(self):
        """The complement of the test above: the verifier *must* be in the
        repository, because CI rebuilds the pages and has no password."""
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config/pages-gate.json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr


class TestInjection:
    PAGE = "<!DOCTYPE html><html><head><title>t</title></head><body><p>x</p></body></html>"

    def test_the_gate_lands_at_the_end_of_the_head(self):
        """In <head>, not at the end of <body>: the lock class has to be on
        <html> before any body content is parsed, or the page paints for a
        moment before it is hidden."""
        gated = pages_gate.inject(self.PAGE, _params())

        assert gated.index("<title>t</title>") < gated.index(pages_gate._BEGIN)
        assert gated.index(pages_gate._END) < gated.index("</head>")
        assert gated.index("</head>") < gated.index("<p>x</p>")
        assert "<p>x</p>" in gated, "the page itself must survive untouched"

    def test_injecting_twice_changes_nothing(self):
        params = _params()
        once = pages_gate.inject(self.PAGE, params)
        twice = pages_gate.inject(once, params)

        assert twice == once
        assert twice.count(pages_gate._BEGIN) == 1

    def test_a_rotation_replaces_the_previous_parameters(self):
        """Re-injecting is how a rotated password reaches a page that already
        carries one; the stale verifier must not survive anywhere."""
        first = _params("first-password")
        second = _params("second-password")
        rotated = pages_gate.inject(pages_gate.inject(self.PAGE, first), second)

        assert second["verifier"] in rotated
        assert first["verifier"] not in rotated
        assert first["salt"] not in rotated

    def test_a_page_without_a_head_raises(self):
        with pytest.raises(pages_gate.PagesGateError):
            pages_gate.inject("<p>fragment</p>", _params())

    def test_an_unterminated_block_raises(self):
        broken = self.PAGE.replace(
            "</head>", pages_gate._BEGIN + "\n</head>"
        )
        with pytest.raises(pages_gate.PagesGateError):
            pages_gate.inject(broken, _params())

    def test_no_placeholder_survives_into_the_page(self):
        assert "@@" not in pages_gate.head_block(_params())


class TestTheInjectedBlock:
    """What the block must and must not contain. The must-nots are the
    security-relevant half."""

    def test_carries_the_public_verifier_and_the_derivation_parameters(self):
        params = _params()
        block = pages_gate.head_block(params)

        assert params["verifier"] in block
        assert params["salt"] in block
        assert f'"iterations":{params["iterations"]}' in block
        assert "PBKDF2" in block
        assert "SHA-256" in block

    def test_never_carries_the_password(self):
        block = pages_gate.head_block(_params("a-password-that-must-not-ship"))

        assert "a-password-that-must-not-ship" not in block

    def test_hides_the_page_before_it_paints_and_tells_crawlers_to_stay_out(self):
        block = pages_gate.head_block(_params())

        assert pages_gate.LOCK_CLASS in block
        assert "visibility: hidden" in block
        assert pages_gate.SESSION_KEY in block
        assert 'name="robots"' in block and "noindex" in block

    def test_is_one_plain_script_block_the_node_check_can_find(self):
        """tests/test_generate_reader.py regexes ``<script>(.*?)</script>`` and
        runs each block through ``node --check``; attributes on the tag would
        hide the block from that check."""
        block = pages_gate.head_block(_params())

        assert block.count("<script>") == 1
        assert "<script " not in block
        assert block.count("</script>") == 1

    def test_the_lock_is_synchronous_and_the_prompt_is_not(self):
        """The lock has to be applied while <head> parses; the overlay needs a
        <body> to live in, so it waits for DOMContentLoaded."""
        block = pages_gate.head_block(_params())

        lock_at = block.index("root.classList.add(LOCK)")
        assert lock_at < block.index("DOMContentLoaded")
        assert block.index("document.createElement") > lock_at
