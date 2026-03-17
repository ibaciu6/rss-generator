import asyncio

import httpx

from scraper.fetcher import FetchResult, Fetcher


class _StrategyFetcher(Fetcher):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def close(self) -> None:
        return None

    def _build_strategy_chain(self, method: str):
        return [self._blocked, self._working]

    async def _blocked(self, url: str) -> FetchResult:
        self.calls.append("blocked")
        return FetchResult(url=url, content="cf-error-details", status_code=200)

    async def _working(self, url: str) -> FetchResult:
        self.calls.append("working")
        return FetchResult(url=url, content="<html>ok</html>", status_code=200)


def test_fetch_tries_next_strategy_when_validator_rejects_result() -> None:
    fetcher = _StrategyFetcher()

    def _validator(result: FetchResult) -> None:
        if "cf-error-details" in result.content:
            raise ValueError("blocked content")

    result = asyncio.run(fetcher.fetch("https://example.com", method="http", validator=_validator))

    assert result.content == "<html>ok</html>"
    assert fetcher.calls == ["blocked", "working"]


def test_fetcher_ignores_empty_proxy_env(monkeypatch) -> None:
    monkeypatch.setenv("RSS_GENERATOR_PROXY_URL", "   ")

    fetcher = Fetcher()
    try:
        assert fetcher._proxy_url is None
        assert isinstance(fetcher._client, httpx.AsyncClient)
    finally:
        asyncio.run(fetcher.close())
