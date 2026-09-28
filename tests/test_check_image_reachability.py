"""Tests for the image-host reachability probe.

The tool exists to answer one question -- is a missing image a blocked host or
a broken feed? -- so the TLS floor is part of that answer rather than a detail.
"""
from __future__ import annotations

import contextlib
import ssl

from scripts import check_image_reachability as probe_mod


class TestTlsFloor:
    """`ssl.create_default_context()` still permits TLS 1.0 and 1.1 on some
    builds, and a host offering only those would then be reported as
    "tls-error" -- which reads as a defect in the feed when it is the host
    refusing a modern handshake. The floor is pinned rather than inherited."""

    def test_the_context_floor_is_tls_1_2(self, monkeypatch):
        seen: dict[str, ssl.SSLContext] = {}

        def fake_create_default_context(*a, **kw):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            seen["ctx"] = ctx
            return ctx

        monkeypatch.setattr(probe_mod.ssl, "create_default_context", fake_create_default_context)
        # The probe cannot complete a real handshake here; only the context
        # configuration is under test.
        with contextlib.suppress(Exception):
            probe_mod.probe("example.invalid", timeout=0.01)
        assert seen["ctx"].minimum_version is ssl.TLSVersion.TLSv1_2
