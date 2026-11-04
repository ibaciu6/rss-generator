# -*- coding: utf-8 -*-
"""Utility for loading a repository ``.env`` file into the process environment.

The repository uses a git‑ignored ``.env`` file for secrets such as
``TVMAZE_TOKEN``, ``TMDB_API_KEY`` and ``OMDB_API_KEY``.  Several scripts need
to read these variables, and the original implementation duplicated the parsing
logic across ``scripts/generate_feeds.py``, ``scripts/compare_pipeline.py`` and
``scripts/e2e_player_servers.py``.

This module provides a single, testable source of truth:

* ``load_dotenv(repo_root: Path) -> dict[str, str]`` — parses ``.env`` into a
  dictionary. When ``python-dotenv`` is installed the result is identical to
  ``dotenv_values``; otherwise a small hand‑crafted parser is used as fallback.
  Callers that need the variables in the *current* process must merge the
  returned dict into ``os.environ`` themselves (or use the convenience helpers
  below).
* ``apply_dotenv(repo_root: Path)`` — convenience wrapper that merges the parsed
  variables into ``os.environ`` using ``setdefault`` (existing values are not
  overwritten, matching ``load_dotenv`` behaviour).

Exported names: ``load_dotenv``, ``apply_dotenv``.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(repo_root: Path) -> dict[str, str]:
    """Read ``repo_root/.env`` and return the variables as a dict.

    If ``python-dotenv`` is installed its ``dotenv_values`` is used verbatim so
    existing behaviour (and error semantics) are preserved.  Otherwise a minimal
    built‑in parser is used that handles ``export VAR=...``, surrounding quotes
    and comment lines.

    The returned dict is a *copy* — it does not mutate ``os.environ``.
    """
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return {}
    try:
        # ``dotenv_values`` merges the file contents (returns a plain dict) and
        # is the standard public API in python-dotenv.
        from dotenv import dotenv_values  # type: ignore
        result = dotenv_values(str(env_path))
        return {k: v for k, v in result.items() if v is not None}
    except Exception:
        pass

    # Fallback parser — mirrors the behaviour above without external deps.
    result: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if name:
            result[name] = value
    return result


def apply_dotenv(repo_root: Path) -> dict[str, str]:
    """Merge ``.env`` variables into ``os.environ`` and return them.

    Uses ``os.environ.setdefault`` so already‑present values are not clobbered.
    Returns the parsed dict for callers that want to inspect what was loaded.
    """
    values = load_dotenv(repo_root)
    for key, val in values.items():
        os.environ.setdefault(key, val)
    return values
