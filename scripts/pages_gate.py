#!/usr/bin/env python3
"""Password gate for the two published HTML pages.

GitHub Pages serves static bytes and cannot ask for a password, so the gate is
client-side. Being explicit about what that means is the whole point of this
module:

- **What it does.** A visitor arriving at ``index.html`` or ``reader.html`` --
  from a link, a search result, or a guess -- gets a password prompt instead of
  the page. Nothing on the page paints until the password verifies.
- **What it cannot do.** The page bytes are public: anyone can read the
  response, and anyone can defeat the gate from a console. It is a speed bump
  for the two UI pages, not access control. Real access control needs a host
  that authenticates the *request* (Cloudflare Access in front of Pages, a
  password-protected host, or the site not being public at all).

``feeds/*.xml`` and ``feeds.opml`` stay public on purpose and are *not* gated:
they are the point of the project, and an RSS reader fetches them with no way
to answer a password prompt. The gate covers exactly the HTML pages.

The password never reaches this repository or CI:

- the password itself lives in the gitignored ``.pages_pass.txt`` (local only,
  so it cannot be committed by accident);
- what is committed is ``config/pages-gate.json`` -- a random salt, the
  iteration count, and the PBKDF2-HMAC-SHA256 verifier derived from the
  password. A salted, deliberately slow verifier is *safe to publish*: it ships
  inside the gated page anyway, so anyone can read it. What it must not do is
  reveal the password or let a wrong answer through. A bare
  ``sha256(password)`` fails the first property for a weak password, which is
  why this is PBKDF2 and not a hash compare.

Rotate the password with::

    python scripts/pages_gate.py --set-password   # reads .pages_pass.txt
    python scripts/pages_gate.py --check          # password vs. verifier

Both read the password from a file, never from argv, so it cannot land in the
shell history.

Injection belongs to the generators (``generate_index.py`` and
``generate_reader.py``'s ``build_page``), never to a hand edit: ``index.html``
and ``reader.html`` are rebuilt by CI every hour, so a hand-edited gate would be
gone by the next run.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARAMS_FILE = REPO_ROOT / "config" / "pages-gate.json"
PASSWORD_FILE = REPO_ROOT / ".pages_pass.txt"

KDF = "PBKDF2-HMAC-SHA256"
# OWASP's floor for PBKDF2-HMAC-SHA256. The cost is paid once per unlock, in the
# visitor's browser; against an offline attacker it is the only thing standing
# between the published verifier and the password.
ITERATIONS = 600_000
SALT_BYTES = 16
VERIFIER_BYTES = 32

# Names the page's JavaScript and the tests share. The class is what hides the
# page; the key is the per-tab-session flag an unlock writes.
LOCK_CLASS = "rssgate-locked"
SESSION_KEY = "rssgate_ok"

_BEGIN = "<!-- rssgate:begin -- injected by scripts/pages_gate.py -->"
_END = "<!-- rssgate:end -->"

_MAX_ITERATIONS = 5_000_000
_HEX_RE = re.compile(r"\A[0-9a-f]+\Z")

_NOTE = (
    "Public password verifier for index.html/reader.html: a random salt plus the "
    "PBKDF2 value the browser recomputes from what the visitor types. Safe to "
    "publish -- it is served inside the gated page anyway. The password itself is "
    "not in this repository; it stays in the gitignored .pages_pass.txt. Rotate "
    "with: python scripts/pages_gate.py --set-password"
)


class PagesGateError(RuntimeError):
    """The gate cannot be built. Publishing an ungated page is not the fallback."""


def derive_verifier(password: str, salt_hex: str, iterations: int = ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 of `password`, lowercase hex.

    ``crypto.subtle`` on the page derives the same 32 bytes, so a change here
    without the matching change there locks everyone out -- including the owner.
    tests/test_pages_gate_browser.py is the contract between the two.
    """
    if not _HEX_RE.match(salt_hex) or len(salt_hex) % 2:
        raise PagesGateError(f"salt must be lowercase hex: {salt_hex!r}")
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
        dklen=VERIFIER_BYTES,
    ).hex()


def _check_iterations(iterations: object) -> int:
    if not isinstance(iterations, int) or isinstance(iterations, bool):
        raise PagesGateError(f"iterations must be an integer: {iterations!r}")
    if not 1 <= iterations <= _MAX_ITERATIONS:
        raise PagesGateError(f"iterations out of range: {iterations}")
    return iterations


def new_params(password: str, iterations: int = ITERATIONS) -> dict:
    """A fresh salt and the verifier for `password`."""
    _check_iterations(iterations)
    salt = secrets.token_hex(SALT_BYTES)
    return {
        "version": 1,
        "kdf": KDF,
        "iterations": iterations,
        "salt": salt,
        "verifier": derive_verifier(password, salt, iterations),
        "note": _NOTE,
    }


def _validate(data: object, source: str) -> dict:
    """Reject anything the page's JavaScript could not use verbatim.

    Strictness is also what makes the parameters safe to interpolate into a
    ``<script>`` block: every value is hex or an int this function has bounded,
    so no byte of a malformed -- or edited -- file can reach the page as code.
    """
    if not isinstance(data, dict):
        raise PagesGateError(f"{source} must hold a JSON object")
    for key in ("kdf", "iterations", "salt", "verifier"):
        if key not in data:
            raise PagesGateError(f"{source} is missing {key!r}")
    if data["kdf"] != KDF:
        raise PagesGateError(
            f"{source} names kdf {data['kdf']!r}, but the page derives {KDF!r}"
        )
    iterations = _check_iterations(data["iterations"])
    salt = data["salt"]
    if not isinstance(salt, str) or not _HEX_RE.match(salt) or len(salt) < 2 * SALT_BYTES:
        raise PagesGateError(
            f"{source} needs a salt of at least {2 * SALT_BYTES} lowercase hex chars"
        )
    verifier = data["verifier"]
    if not isinstance(verifier, str) or not _HEX_RE.match(verifier):
        raise PagesGateError(f"{source} needs a hex verifier")
    if len(verifier) != 2 * VERIFIER_BYTES:
        raise PagesGateError(
            f"{source} needs a {2 * VERIFIER_BYTES}-char verifier, got {len(verifier)}"
        )
    return {
        "version": data.get("version", 1),
        "kdf": KDF,
        "iterations": iterations,
        "salt": salt,
        "verifier": verifier,
    }


def load_params(path: Path = PARAMS_FILE) -> dict:
    """Read and validate the committed gate parameters."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PagesGateError(
            f"{path} is missing. It carries the published verifier, and the "
            f"generators refuse to publish an ungated page. Create it with: "
            f"python scripts/pages_gate.py --set-password"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PagesGateError(f"{path} is not valid JSON: {exc}") from exc
    return _validate(data, str(path))


def verify(password: str, params: dict | None = None) -> bool:
    """True when `password` is the one `params` was derived from."""
    p = load_params() if params is None else params
    derived = derive_verifier(password, p["salt"], p["iterations"])
    return hmac.compare_digest(derived, p["verifier"])


def set_password(
    password: str, path: Path = PARAMS_FILE, iterations: int = ITERATIONS
) -> dict:
    """Write a fresh salt + verifier for `password` -- the rotation step."""
    params = new_params(password, iterations)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    return params


def read_password(path: Path = PASSWORD_FILE) -> str:
    """The password, as the file's content minus any line ending.

    A trailing newline is what an editor adds, not part of the secret, and the
    browser's input field would never carry it.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PagesGateError(
            f"{path} is missing. It holds the gate password and is gitignored on "
            f"purpose -- local only, so it cannot be committed."
        ) from exc
    password = raw.rstrip("\r\n")
    if not password:
        raise PagesGateError(f"{path} is empty")
    return password


_HEAD_TEMPLATE = """\
<!-- rssgate:begin -- injected by scripts/pages_gate.py -->
<meta name="robots" content="noindex, nofollow">
<style>html.@@LOCK@@ body > *:not(#rssgate) { visibility: hidden !important; }
#rssgate { position: fixed; inset: 0; z-index: 2147483647; display: flex;
  align-items: center; justify-content: center; background: #f5f1e8; color: #1f2933;
  font: 16px/1.4 system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
#rssgate form { display: flex; flex-direction: column; gap: 12px; min-width: 300px;
  padding: 24px; background: #fffaf0; border: 1px solid #d9cbb2; border-radius: 14px;
  box-shadow: 0 18px 60px rgba(102, 79, 46, 0.18); }
#rssgate h1 { margin: 0; font-size: 1.05rem; }
#rssgate label { font-size: 0.85rem; color: #52606d; }
#rssgate input { font: inherit; padding: 9px 11px; color: inherit; background: #fff;
  border: 1px solid #d9cbb2; border-radius: 8px; }
#rssgate button { font: inherit; font-weight: 700; padding: 9px 11px; color: #fff;
  background: #9f3a16; border: 0; border-radius: 8px; cursor: pointer; }
#rssgate p { margin: 0; min-height: 1.2em; font-size: 0.85rem; color: #52606d; }
#rssgate p.bad { color: #a61b1b; }</style>
<script>
(function () {
  "use strict";
  /* The gate. Client-side by necessity -- GitHub Pages serves static bytes and
     cannot ask for a password, so this hides the two published UI pages from a
     casual visit and nothing more. The feeds and feeds.opml stay public on
     purpose. See scripts/pages_gate.py. */
  var PARAMS = @@PARAMS@@;
  var LOCK = "@@LOCK@@";
  var KEY = "@@KEY@@";
  var ID = "rssgate";
  var root = document.documentElement;
  var overlay = null;
  var unlocked = false;

  /* Runs while <head> is parsed, before any body content exists, so the page is
     never painted for a moment before it is hidden. */
  try {
    unlocked = window.sessionStorage.getItem(KEY) === PARAMS.verifier;
  } catch (err) {
    unlocked = false; /* storage blocked (private mode): stay locked */
  }
  if (!unlocked) root.classList.add(LOCK);

  /* Salt hex -> bytes. */
  var salt = (function () {
    var out = new Uint8Array(PARAMS.salt.length / 2);
    for (var i = 0; i < out.length; i++) {
      out[i] = parseInt(PARAMS.salt.slice(i * 2, i * 2 + 2), 16);
    }
    return out;
  })();

  function hex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = "";
    for (var i = 0; i < bytes.length; i++) {
      out += (bytes[i] + 0x100).toString(16).slice(1);
    }
    return out;
  }

  /* Same derivation as pages_gate.derive_verifier(): PBKDF2-HMAC-SHA256, 32
     bytes, lowercase hex. node --check parses this, but only a browser can
     prove the two agree -- tests/test_pages_gate_browser.py does. */
  function derive(password) {
    if (!(window.crypto && window.crypto.subtle)) {
      return Promise.reject(new Error("crypto.subtle unavailable"));
    }
    var encoder = new TextEncoder();
    return window.crypto.subtle
      .importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveBits"])
      .then(function (key) {
        return window.crypto.subtle.deriveBits(
          { name: "PBKDF2", salt: salt, iterations: PARAMS.iterations, hash: "SHA-256" },
          key,
          256
        );
      })
      .then(hex);
  }

  function unlock() {
    unlocked = true;
    root.classList.remove(LOCK);
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    overlay = null;
  }

  function say(text, bad) {
    var note = overlay.querySelector("p");
    note.textContent = text;
    note.className = bad ? "bad" : "";
  }

  function build() {
    overlay = document.createElement("div");
    overlay.id = ID;
    var form = document.createElement("form");
    var title = document.createElement("h1");
    title.textContent = "Private page";
    var label = document.createElement("label");
    label.setAttribute("for", ID + "-input");
    label.textContent = "Password";
    var input = document.createElement("input");
    input.type = "password";
    input.id = ID + "-input";
    input.name = "password";
    input.autocomplete = "current-password";
    var button = document.createElement("button");
    button.type = "submit";
    button.textContent = "Unlock";
    var note = document.createElement("p");
    note.setAttribute("role", "status");
    note.setAttribute("aria-live", "polite");
    note.textContent = "Enter the password to continue.";
    form.appendChild(title);
    form.appendChild(label);
    form.appendChild(input);
    form.appendChild(button);
    form.appendChild(note);
    overlay.appendChild(form);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var password = input.value;
      input.value = "";
      button.disabled = true;
      say("Checking\\u2026", false);
      derive(password).then(
        function (derived) {
          button.disabled = false;
          if (derived !== PARAMS.verifier) {
            say("Wrong password.", true);
            input.focus();
            return;
          }
          try {
            window.sessionStorage.setItem(KEY, PARAMS.verifier);
          } catch (err) {
            /* Unlock this view only; the next visit asks again. */
          }
          unlock();
        },
        function () {
          button.disabled = false;
          say(
            "Open this page over https:// or http://localhost -- the browser " +
              "refuses to check a password anywhere else.",
            true
          );
        }
      );
    });
    document.body.appendChild(overlay);
    input.focus();
  }

  function start() {
    if (unlocked) return;
    build();
  }

  /* The overlay belongs to the page's body, but this script runs from the head. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
</script>
<!-- rssgate:end -->"""


def head_block(params: dict | None = None) -> str:
    """The block the generators inject at the end of ``<head>``.

    ``noindex`` rides along: a page behind a password has no business in a
    search index, and the pages it points to are public anyway.
    """
    p = load_params() if params is None else _validate(params, "gate parameters")
    block = (
        _HEAD_TEMPLATE.replace("@@LOCK@@", LOCK_CLASS)
        .replace("@@KEY@@", SESSION_KEY)
        .replace(
            "@@PARAMS@@",
            json.dumps(
                {
                    "iterations": p["iterations"],
                    "salt": p["salt"],
                    "verifier": p["verifier"],
                },
                separators=(",", ":"),
            ),
        )
    )
    if "@@" in block:
        raise PagesGateError("a placeholder in the gate template was never filled")
    return block


def inject(page: str, params: dict | None = None) -> str:
    """Return `page` with the gate at the end of its ``<head>``.

    Re-injecting a page that already carries a gate replaces the block, so the
    function is idempotent. A page with no ``</head>`` raises: a page that
    cannot be gated must fail the build, never ship ungated.
    """
    block = head_block(params)
    start = page.find(_BEGIN)
    if start >= 0:
        end = page.find(_END, start)
        if end < 0:
            raise PagesGateError("the page carries an unterminated gate block")
        return page[:start] + block + page[end + len(_END) :]
    close = page.find("</head>")
    if close < 0:
        raise PagesGateError("the page has no </head> to inject the gate before")
    return page[:close] + block + "\n" + page[close:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Password gate for the published HTML pages (index.html, reader.html)."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--set-password",
        action="store_true",
        help="derive a fresh salt + verifier from the password file and write the params",
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="verify the password file against the committed verifier",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=PASSWORD_FILE,
        help="file holding the password (default: %(default)s)",
    )
    parser.add_argument(
        "--params-file",
        type=Path,
        default=PARAMS_FILE,
        help="JSON holding salt + verifier (default: %(default)s)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=ITERATIONS,
        help="PBKDF2 iterations for --set-password (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        password = read_password(args.password_file)
        if args.set_password:
            params = set_password(password, args.params_file, args.iterations)
            print(
                f"Wrote {args.params_file}: {params['kdf']}, "
                f"{params['iterations']} iterations, fresh salt."
            )
            print(
                "Only the public verifier was written; the password stays in "
                f"{args.password_file}. Rebuild the pages: "
                "PYTHONPATH=. python scripts/generate_index.py && "
                "PYTHONPATH=. python scripts/generate_reader.py"
            )
            return 0
        params = load_params(args.params_file)
        if verify(password, params):
            print(f"{args.password_file} matches the verifier in {args.params_file}.")
            return 0
        print(
            f"error: {args.password_file} does NOT match the verifier in "
            f"{args.params_file}",
            file=sys.stderr,
        )
        print(
            "Rotate both: python scripts/pages_gate.py --set-password",
            file=sys.stderr,
        )
        return 1
    except PagesGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
