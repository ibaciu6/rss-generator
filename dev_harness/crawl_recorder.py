#!/usr/bin/env python3
"""
Crawl recorder for recording and replaying HTTP responses.
Records at the fetcher level to capture all HTTP interactions.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

# Global recorder instance
_recorder: CrawlRecorder | None = None


@dataclass
class RecordedResponse:
    """A recorded HTTP response."""
    url: str
    method: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    elapsed_seconds: float
    timestamp: str
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: bytes | None = None


@dataclass
class CrawlRecorder:
    """Records and replays HTTP interactions."""

    run_dir: Path
    mode: str  # "record" or "replay"
    recordings: dict[str, RecordedResponse] = field(default_factory=dict)
    missing_recordings: list[str] = field(default_factory=list)
    _index_path: Path = field(init=False)

    def __post_init__(self):
        self.recordings_dir = self.run_dir / "01_crawl" / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.recordings_dir / "index.json"
        if self.mode == "replay":
            self._load_index()

    def _load_index(self):
        """Load recording index from disk."""
        if self._index_path.exists():
            with self._index_path.open() as f:
                index = json.load(f)
            for key, _meta in index.items():
                # Load full response from separate file
                resp_file = self.recordings_dir / f"{key}.pkl"
                if resp_file.exists():
                    with resp_file.open("rb") as f:
                        self.recordings[key] = pickle.load(f)

    def _save_index(self):
        """Save recording index to disk."""
        index = {}
        for key, resp in self.recordings.items():
            index[key] = {
                "url": resp.url,
                "method": resp.method,
                "status_code": resp.status_code,
                "timestamp": resp.timestamp,
            }
            # Save full response to separate file
            resp_file = self.recordings_dir / f"{key}.pkl"
            with resp_file.open("wb") as f:
                pickle.dump(resp, f)
        with self._index_path.open("w") as f:
            json.dump(index, f, indent=2)

    def _make_key(self, method: str, url: str, params: dict | None = None) -> str:
        """Create a deterministic key for a request."""
        # Normalize URL
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        # Include method and params
        key_data = f"{method}:{normalized}"
        if params:
            key_data += f":{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def record(self, method: str, url: str, response: httpx.Response,
               request_headers: dict | None = None, request_body: bytes | None = None,
               params: dict | None = None) -> None:
        """Record a response."""
        if self.mode != "record":
            return
        key = self._make_key(method, url, params)
        recorded = RecordedResponse(
            url=url,
            method=method,
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            elapsed_seconds=response.elapsed.total_seconds(),
            timestamp=datetime.now(UTC).isoformat(),
            request_headers=request_headers or {},
            request_body=request_body,
        )
        self.recordings[key] = recorded

    def get_recorded(self, method: str, url: str, params: dict | None = None) -> RecordedResponse | None:
        """Get a recorded response for replay."""
        if self.mode != "replay":
            return None
        key = self._make_key(method, url, params)
        if key in self.recordings:
            return self.recordings[key]
        self.missing_recordings.append(f"{method} {url}")
        return None

    def finalize(self):
        """Finalize recording - save index."""
        if self.mode == "record":
            self._save_index()
        # Report missing recordings in replay mode
        if self.mode == "replay" and self.missing_recordings:
            missing_file = self.run_dir / "logs" / "missing_recordings.txt"
            missing_file.parent.mkdir(parents=True, exist_ok=True)
            with missing_file.open("w") as f:
                f.write("\n".join(self.missing_recordings))


def get_recorder() -> CrawlRecorder | None:
    """Get the global recorder instance."""
    return _recorder


def set_recorder(recorder: CrawlRecorder | None):
    """Set the global recorder instance."""
    global _recorder
    _recorder = recorder


def install_recording_hooks():
    """Install hooks into httpx to record/replay requests."""
    # This would be called in live mode to wrap httpx.AsyncClient
    # For now, we'll use a simpler approach: monkey-patch fetcher.py
    pass


class RecordingClient(httpx.AsyncClient):
    """httpx.AsyncClient subclass that records all requests."""

    def __init__(self, *args, recorder: CrawlRecorder | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._recorder = recorder

    async def request(self, method: str, url: str, *args, **kwargs) -> httpx.Response:
        response = await super().request(method, url, *args, **kwargs)

        if self._recorder:
            self._recorder.record(
                method=method.upper(),
                url=str(response.url),
                response=response,
                request_headers=dict(response.request.headers),
                request_body=response.request.content,
            )
        return response


class ReplayTransport(httpx.AsyncHTTPTransport):
    """HTTP transport that serves recorded responses."""

    def __init__(self, recorder: CrawlRecorder, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._recorder = recorder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        recorded = self._recorder.get_recorded(
            request.method, str(request.url)
        )
        if recorded is None:
            raise httpx.RequestError(
                f"No recording found for {request.method} {request.url}",
                request=request,
            )
        return httpx.Response(
            status_code=recorded.status_code,
            headers=recorded.headers,
            content=recorded.content,
            request=request,
        )