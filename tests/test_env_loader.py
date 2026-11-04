from __future__ import annotations

import os
from pathlib import Path
from scripts.env_loader import load_dotenv, apply_dotenv


def test_load_dotenv_missing(tmp_path: Path) -> None:
    # When .env does not exist, returns empty dict
    assert load_dotenv(tmp_path) == {}


def test_load_dotenv_parsing(tmp_path: Path) -> None:
    env_content = """
    # Comment line
    FOO=bar
    export BAR="baz"
    QUX='quux value'
    EMPTY=
    """
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")

    vars_loaded = load_dotenv(tmp_path)
    assert vars_loaded == {
        "FOO": "bar",
        "BAR": "baz",
        "QUX": "quux value",
        "EMPTY": "",
    }


def test_apply_dotenv(tmp_path: Path) -> None:
    env_content = "TEST_ENV_KEY_XYZ=test_val_123"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")

    # Ensure it's not already set
    if "TEST_ENV_KEY_XYZ" in os.environ:
        del os.environ["TEST_ENV_KEY_XYZ"]

    loaded = apply_dotenv(tmp_path)
    assert loaded["TEST_ENV_KEY_XYZ"] == "test_val_123"
    assert os.environ.get("TEST_ENV_KEY_XYZ") == "test_val_123"

    # Cleanup
    del os.environ["TEST_ENV_KEY_XYZ"]
