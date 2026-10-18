"""The gate, driven in a real browser.

``tests/test_pages_gate.py`` proves the *Python* half: the verifier is real
PBKDF2, the parameters are validated, the password never reaches the page. None
of that proves the page works. The gate's own JavaScript re-derives the password
with ``crypto.subtle``, and if that derivation ever stops matching
``pages_gate.derive_verifier`` the site locks out its owner -- silently, with the
correct password typed in. Nothing short of a browser can catch that: node's
``--check`` only parses the script.

The page is served over ``http://127.0.0.1``, which the browser treats as a
secure context exactly like Pages' ``https://``, so ``crypto.subtle`` exists.
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

from scripts import pages_gate

PASSWORD = "test-password-for-the-browser-contract"
# Fast on purpose: the real count is chosen against an attacker's budget, and
# this test runs on every push. The parameter is still exercised end to end.
TEST_ITERATIONS = 1000

PAGE = (
    "<!DOCTYPE html><html><head><title>Gated</title></head>"
    "<body><p id='secret'>reachable only with the password</p></body></html>"
)


def _browser_available() -> bool:
    """Chromium is an optional extra here: the suite must still run (skipped)
    where playwright's browser binary was never downloaded."""
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:  # pragma: no cover - depends on the machine, not the code
        return False
    return True


requires_chromium = pytest.mark.skipif(
    not _browser_available(), reason="playwright chromium is not installed"
)


@pytest.fixture
def gate_params() -> dict:
    return pages_gate.new_params(PASSWORD, TEST_ITERATIONS)


@pytest.fixture
def gated_url(tmp_path: Path, gate_params: dict):
    """Serve a gated page over http://127.0.0.1 and yield its URL."""
    (tmp_path / "index.html").write_text(
        pages_gate.inject(PAGE, gate_params), encoding="utf-8"
    )
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        server.server_close()


@requires_chromium
class TestTheGateInABrowser:
    def test_locked_then_unlocked_with_the_password(self, gated_url: str, gate_params: dict):
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            page.goto(gated_url, wait_until="load")

            secret = page.locator("#secret")
            # Hidden on arrival -- and hidden by the lock class, which is applied
            # while <head> parses, so the page never paints first.
            assert not secret.is_visible()
            assert page.evaluate("document.documentElement.classList.contains('rssgate-locked')")
            expect(page.locator("#rssgate input")).to_be_visible()

            page.fill("#rssgate input", "wrong")
            page.press("#rssgate input", "Enter")
            expect(page.locator("#rssgate p")).to_have_text("Wrong password.")
            assert not secret.is_visible()
            assert page.evaluate("sessionStorage.length") == 0, (
                "a failed attempt must not leave an unlock behind"
            )

            page.fill("#rssgate input", PASSWORD)
            page.press("#rssgate input", "Enter")
            expect(secret).to_be_visible(timeout=15000)
            expect(page.locator("#rssgate")).to_have_count(0)
            assert not page.evaluate("document.documentElement.classList.contains('rssgate-locked')")

            # The unlock is per tab session, and the password is never stored.
            page.reload(wait_until="load")
            expect(secret).to_be_visible()
            stored = page.evaluate("sessionStorage.getItem('rssgate_ok')")
            assert stored == gate_params["verifier"], (
                "the session flag is the public verifier, not the password"
            )
            assert stored != PASSWORD, "the password itself must never be stored"

            # A new session starts locked again: this is not a cookie or a
            # server-side flag, so nothing about it survives clearing storage.
            fresh = browser.new_context()
            fresh_page = fresh.new_page()
            fresh_page.goto(gated_url, wait_until="load")
            expect(fresh_page.locator("#rssgate input")).to_be_visible()
            assert not fresh_page.locator("#secret").is_visible()

            fresh.close()
            context.close()
            browser.close()

    def test_the_local_password_unlocks_these_very_parameters(self, tmp_path: Path):
        """Optional, and skipped where the password is not present: the strongest
        check available that a rotation did not desynchronise the two halves."""
        if not pages_gate.PASSWORD_FILE.exists():
            pytest.skip(".pages_pass.txt is local-only and not present here")

        from playwright.sync_api import expect, sync_playwright

        (tmp_path / "index.html").write_text(
            pages_gate.inject(PAGE, pages_gate.load_params()), encoding="utf-8"
        )
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(url, wait_until="load")
                page.fill("#rssgate input", pages_gate.read_password())
                page.press("#rssgate input", "Enter")
                expect(page.locator("#secret")).to_be_visible(timeout=15000)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
