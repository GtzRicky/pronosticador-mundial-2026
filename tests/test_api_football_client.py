from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import requests

from quiniela.api_football_client import APIFootballClient
from quiniela.config import get_settings


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_api_client_retries_transient_connection_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = replace(
        get_settings(),
        db_path=tmp_path / "api-client.sqlite",
        api_football_key="test-key",
    )
    session = FakeSession(
        requests.ConnectionError("connection reset"),
        FakeResponse(200, {"response": [{"fixture": {"id": 123}}], "results": 1}),
    )
    client = APIFootballClient(settings=settings, force_refresh=True, session=session)
    monkeypatch.setattr("quiniela.api_football_client.time.sleep", lambda _: None)

    payload = client.get_fixtures(date="2026-06-18")

    assert session.calls == 2
    assert payload["results"] == 1
