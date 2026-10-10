from core.logging_utils import _redact_api_keys


def test_an_api_key_in_any_string_field_is_masked() -> None:
    """The generation log is uploaded as a public CI artifact, so a URL secret
    the engine only resolves at request time must never reach it.
    """
    event = _redact_api_keys(
        None,
        "info",
        {
            "event": "fetch.start",
            "url": "https://api.themoviedb.org/3/discover/movie?x=1&api_key=abc123def456",
            "error": "Failed to fetch 'https://api.example/?api_key=abc123def456': HTTP 401",
            "site": "atlantic-movies",
        },
    )

    rendered = str(event)
    assert "abc123def456" not in rendered
    assert "api_key=[REDACTED]" in rendered
    assert event["site"] == "atlantic-movies"


def test_a_url_without_a_key_passes_through_unchanged() -> None:
    event = _redact_api_keys(None, "info", {"event": "fetch.start", "url": "https://example.com/feed/"})
    assert event["url"] == "https://example.com/feed/"