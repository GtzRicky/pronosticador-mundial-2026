from __future__ import annotations

from typing import Any

import requests

from quiniela.config import Settings, get_settings
from quiniela.db import (
    count_api_requests_today,
    delete_cached_response,
    get_cached_response,
    get_connection,
    record_api_usage,
    set_cached_response,
)
from quiniela.logging_utils import get_logger


class APILimitReachedError(RuntimeError):
    """Raised when the configured daily API limit is exhausted."""


class APIFootballClient:
    def __init__(
        self,
        settings: Settings | None = None,
        dry_run: bool = False,
        force_refresh: bool = False,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.connection = get_connection(self.settings.db_path)
        self.dry_run = dry_run
        self.force_refresh = force_refresh
        self.session = session or requests.Session()
        self.logger = get_logger(self.__class__.__name__)
        self.base_url = f"https://{self.settings.api_football_host}"

    def requests_remaining(self) -> int:
        used = count_api_requests_today(self.connection)
        return max(self.settings.api_daily_limit - used, 0)

    def _empty_response(self, reason: str) -> dict[str, Any]:
        return {"response": [], "results": 0, "errors": [reason]}

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.force_refresh:
            cached = get_cached_response(self.connection, endpoint, params)
            if cached is not None:
                record_api_usage(self.connection, endpoint, params, cache_hit=True, status_code=200)
                return cached
        else:
            delete_cached_response(self.connection, endpoint, params)

        if self.dry_run:
            self.logger.warning("Dry-run activo; no se consulta API para %s", endpoint)
            return self._empty_response("dry_run_no_request")

        if not self.settings.api_football_key:
            self.logger.warning("No hay API_FOOTBALL_KEY; devolviendo respuesta vacía para %s", endpoint)
            return self._empty_response("missing_api_key")

        if count_api_requests_today(self.connection) >= self.settings.api_daily_limit:
            raise APILimitReachedError(
                f"Se alcanzó el límite diario de {self.settings.api_daily_limit} requests."
            )

        headers = {
            "x-apisports-key": self.settings.api_football_key,
            "x-apisports-host": self.settings.api_football_host,
            "x-rapidapi-host": self.settings.api_football_host,
        }

        url = f"{self.base_url}{endpoint}"
        response = self.session.get(url, params=params or {}, headers=headers, timeout=30)
        record_api_usage(
            self.connection,
            endpoint,
            params,
            cache_hit=False,
            status_code=response.status_code,
        )
        if response.status_code == 429:
            raise APILimitReachedError("API-Football devolvió 429 Too Many Requests.")
        response.raise_for_status()
        payload = response.json()
        set_cached_response(self.connection, endpoint, params, payload, status_code=response.status_code)
        return payload

    def search_teams(self, team_name: str) -> dict[str, Any]:
        return self._request("/teams", {"search": team_name})

    def get_fixtures(self, **params: Any) -> dict[str, Any]:
        return self._request("/fixtures", params)

    def get_fixture_by_id(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/fixtures", {"id": fixture_id})

    def get_fixture_lineups(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/fixtures/lineups", {"fixture": fixture_id})

    def get_fixture_statistics(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/fixtures/statistics", {"fixture": fixture_id})

    def get_fixture_events(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/fixtures/events", {"fixture": fixture_id})

    def get_fixture_players(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/fixtures/players", {"fixture": fixture_id})

    def get_odds(self, fixture_id: int | str) -> dict[str, Any]:
        return self._request("/odds", {"fixture": fixture_id})

    def get_team_fixtures(self, team_id: int | str, from_date: str, to_date: str) -> dict[str, Any]:
        return self.get_fixtures(team=team_id, **{"from": from_date, "to": to_date})

    def get_players(self, **params: Any) -> dict[str, Any]:
        return self._request("/players", params)

    def get_player_seasons(self, player_id: int | str) -> dict[str, Any]:
        return self._request("/players/seasons", {"player": player_id})
