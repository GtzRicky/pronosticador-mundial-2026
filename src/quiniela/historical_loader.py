from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.api_football_client import APIFootballClient, APILimitReachedError
from quiniela.config import get_settings
from quiniela.db import (
    fetch_dataframe,
    get_connection,
    get_team_api_id,
    store_historical_match,
    store_json_row,
    upsert_team_api_id,
)
from quiniela.logging_utils import get_logger
from quiniela.name_maps import (
    TEAM_NAME_MAP,
    UnknownTeamNameError,
    fix_common_mojibake,
    normalize_team_name,
    normalize_text,
    preferred_team_search_name,
    require_known_team_name,
)
from quiniela.player_model import (
    store_fixture_players_payload,
    store_player_season_payload,
    write_player_resolution_report,
)


logger = get_logger(__name__)
FREE_PLAN_FALLBACK_SEASONS = (2024, 2023, 2022)


class RetrievalValidationError(RuntimeError):
    def __init__(self, message: str, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


def _display_team_name(team_name: str) -> str:
    repaired = fix_common_mojibake(team_name).strip()
    if repaired and "?" not in repaired:
        return repaired
    return preferred_team_search_name(team_name)


def _team_aliases(team_name: str) -> list[str]:
    repaired = fix_common_mojibake(team_name)
    team_norm = normalize_team_name(repaired)
    aliases = [repaired]
    preferred = preferred_team_search_name(repaired)
    if preferred and preferred != repaired:
        aliases.append(preferred)
    aliases.extend([key for key, value in TEAM_NAME_MAP.items() if value == team_norm and key != repaired])
    unique_aliases: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias not in seen:
            unique_aliases.append(alias)
            seen.add(alias)
    return unique_aliases


def _strict_team_norm(team_name: str, *, context: str) -> str:
    return require_known_team_name(team_name, context=context)


def _retrieval_issue(
    *,
    reason: str,
    fixture_id: str | None = None,
    raw_home_team: str | None = None,
    raw_away_team: str | None = None,
    expected_match_id: str | None = None,
    expected_pair: tuple[str, str] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"reason": reason}
    if fixture_id:
        issue["fixture_id"] = fixture_id
    if raw_home_team:
        issue["raw_home_team"] = raw_home_team
    if raw_away_team:
        issue["raw_away_team"] = raw_away_team
    if expected_match_id:
        issue["expected_match_id"] = expected_match_id
    if expected_pair:
        issue["expected_pair"] = list(expected_pair)
    if detail:
        issue["detail"] = detail
    return issue


def _format_retrieval_issues(issues: list[dict[str, Any]]) -> str:
    lines = ["Retrieval validation failed:"]
    for issue in issues:
        parts = [issue.get("reason", "unknown_reason")]
        if issue.get("fixture_id"):
            parts.append(f"fixture={issue['fixture_id']}")
        if issue.get("raw_home_team") or issue.get("raw_away_team"):
            parts.append(
                f"raw={issue.get('raw_home_team', '?')} vs {issue.get('raw_away_team', '?')}"
            )
        if issue.get("expected_match_id"):
            parts.append(f"match_id={issue['expected_match_id']}")
        if issue.get("expected_pair"):
            home_expected, away_expected = issue["expected_pair"]
            parts.append(f"expected_norm={home_expected} vs {away_expected}")
        if issue.get("detail"):
            parts.append(str(issue["detail"]))
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def _normalize_live_team_name(
    team_name: str,
    *,
    context: str,
) -> tuple[str | None, str | None]:
    try:
        return _strict_team_norm(team_name, context=context), None
    except UnknownTeamNameError as exc:
        return None, str(exc)


def _pick_team_candidate(team_name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    target_norm = _strict_team_norm(team_name, context="team id resolution target")
    exact = []
    fallback = []
    for candidate in candidates:
        team_info = candidate.get("team", {})
        name = team_info.get("name") or ""
        try:
            candidate_norm = _strict_team_norm(
                str(name),
                context=f"team id resolution candidate for {team_name}",
            )
        except UnknownTeamNameError:
            fallback.append(candidate)
            continue
        if candidate_norm == target_norm:
            exact.append(candidate)
        else:
            fallback.append(candidate)

    for candidate in exact:
        team_info = candidate.get("team", {})
        if team_info.get("national") is True:
            return candidate
    return None


def resolve_team_id(client: APIFootballClient, team_name: str) -> int | None:
    connection = client.connection
    cached = get_team_api_id(connection, team_name)
    if cached is not None:
        return cached

    for alias in _team_aliases(team_name):
        payload = client.search_teams(alias)
        candidates = payload.get("response", [])
        candidate = _pick_team_candidate(team_name, candidates)
        if candidate:
            api_team_id = candidate.get("team", {}).get("id")
            api_team_name = candidate.get("team", {}).get("name") or alias
            if api_team_id is not None:
                upsert_team_api_id(connection, api_team_name, int(api_team_id))
                return int(api_team_id)
    return None


def _error_text(payload: dict[str, Any]) -> str:
    errors = payload.get("errors", {})
    if isinstance(errors, dict):
        return " | ".join(f"{key}:{value}" for key, value in errors.items())
    if isinstance(errors, list):
        return " | ".join(str(value) for value in errors)
    return str(errors or "")


def _is_free_plan_limit(payload: dict[str, Any]) -> bool:
    return "free plans do not have access" in _error_text(payload).lower()


def _is_empty_success(payload: dict[str, Any]) -> bool:
    return (payload.get("results") or 0) == 0 and not _error_text(payload)


def _requires_season(payload: dict[str, Any]) -> bool:
    return "season field is required" in _error_text(payload).lower()


def _fixture_date(payload: dict[str, Any]) -> str:
    return payload.get("fixture", {}).get("date") or ""


def _fixture_id(payload: dict[str, Any]) -> str:
    return str(payload.get("fixture", {}).get("id"))


def _select_recent_fixtures(fixtures: list[dict[str, Any]], max_matches: int) -> list[dict[str, Any]]:
    ordered = sorted(fixtures, key=_fixture_date, reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fixture in ordered:
        fixture_id = _fixture_id(fixture)
        if not fixture_id or fixture_id == "None" or fixture_id in seen:
            continue
        seen.add(fixture_id)
        selected.append(fixture)
        if len(selected) >= max_matches:
            break
    return selected


def _filter_finished_fixtures(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        fixture
        for fixture in fixtures
        if fixture.get("fixture", {}).get("status", {}).get("short") in {"FT", "AET", "PEN"}
    ]


def _filter_fixtures_by_date_range(
    fixtures: list[dict[str, Any]],
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]]:
    start = pd.Timestamp(from_date).tz_localize("UTC")
    end = pd.Timestamp(to_date).tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    filtered: list[dict[str, Any]] = []
    for fixture in fixtures:
        date_value = fixture.get("fixture", {}).get("date")
        if not date_value:
            continue
        kickoff = pd.Timestamp(date_value)
        if kickoff.tzinfo is None:
            kickoff = kickoff.tz_localize("UTC")
        if start <= kickoff <= end:
            filtered.append(fixture)
    return filtered


def _season_candidates(from_date: str, to_date: str) -> list[int]:
    start_year = pd.Timestamp(from_date).year
    end_year = pd.Timestamp(to_date).year
    return list(range(start_year, end_year + 1))


def _fetch_team_history_candidates(
    client: APIFootballClient,
    team_name: str,
    api_team_id: int,
    from_date: str,
    to_date: str,
    max_matches_per_team: int,
) -> tuple[list[dict[str, Any]], str]:
    try:
        direct_payload = client.get_team_fixtures(api_team_id, from_date, to_date)
    except APILimitReachedError:
        return [], "api_limit_reached:date_range"
    direct_rows = _filter_finished_fixtures(direct_payload.get("response", []))
    if direct_rows:
        return _select_recent_fixtures(direct_rows, max_matches=max_matches_per_team), "date_range"

    fallback_reason = _error_text(direct_payload) or "empty_date_range"
    if _requires_season(direct_payload) or _is_empty_success(direct_payload):
        season_rows: list[dict[str, Any]] = []
        seasons_tried = _season_candidates(from_date, to_date)
        for season in seasons_tried:
            try:
                season_payload = client.get_fixtures(team=api_team_id, season=season, status="FT")
            except APILimitReachedError:
                break
            season_filtered = _filter_fixtures_by_date_range(
                _filter_finished_fixtures(season_payload.get("response", [])),
                from_date=from_date,
                to_date=to_date,
            )
            season_rows.extend(season_filtered)
            unique_fixture_ids = {_fixture_id(row) for row in season_rows if _fixture_id(row) != "None"}
            if len(unique_fixture_ids) >= max_matches_per_team:
                break
        selected = _select_recent_fixtures(season_rows, max_matches=max_matches_per_team)
        if selected:
            return selected, f"season_range_fallback:{','.join(str(s) for s in seasons_tried)}:{fallback_reason}"

    if _is_free_plan_limit(direct_payload) or _is_empty_success(direct_payload) or _requires_season(direct_payload):
        season_rows: list[dict[str, Any]] = []
        for season in FREE_PLAN_FALLBACK_SEASONS:
            try:
                season_payload = client.get_fixtures(team=api_team_id, season=season, status="FT")
            except APILimitReachedError:
                break
            season_rows.extend(_filter_finished_fixtures(season_payload.get("response", [])))
            unique_fixture_ids = {_fixture_id(row) for row in season_rows if _fixture_id(row) != "None"}
            if len(unique_fixture_ids) >= max_matches_per_team:
                break
        selected = _select_recent_fixtures(season_rows, max_matches=max_matches_per_team)
        return selected, f"free_plan_season_fallback:{fallback_reason}"

    logger.warning("No se pudieron obtener fixtures para %s: %s", team_name, fallback_reason)
    return [], f"empty:{fallback_reason}"


def _store_fixture_children(
    client: APIFootballClient,
    fixture_id: str,
    collect_odds: bool,
    resolution_entries: list[dict[str, Any]] | None = None,
    include_stats_events: bool = True,
    match_id: str | None = None,
    kickoff_at: str | None = None,
) -> dict[str, int]:
    counts = {
        "lineups": 0,
        "fixture_player_stats": 0,
        "statistics": 0,
        "events": 0,
        "odds": 0,
        "odds_market_snapshots": 0,
        "odds_market_consensus": 0,
        "official_lineup_notifications": 0,
        "official_lineup_superseded": 0,
    }

    try:
        lineups_payload = client.get_fixture_lineups(fixture_id)
        for lineup in lineups_payload.get("response", []):
            team_name = lineup.get("team", {}).get("name") or ""
            store_json_row(
                client.connection,
                "historical_lineups",
                fixture_id,
                _strict_team_norm(
                    team_name,
                    context=f"fixture {fixture_id} lineup team",
                ),
                lineup,
            )
            counts["lineups"] += 1
        if match_id and kickoff_at:
            from quiniela.notifications import queue_official_lineup_notifications

            notification_result = queue_official_lineup_notifications(
                client.connection,
                match_id=match_id,
                kickoff_at=kickoff_at,
                lineups_payload=lineups_payload,
            )
            counts["official_lineup_notifications"] += int(
                notification_result.get("scheduled", 0)
            )
            counts["official_lineup_superseded"] += int(
                notification_result.get("superseded", 0)
            )
    except APILimitReachedError:
        logger.warning("Límite/rate limit alcanzado al pedir lineups para fixture %s", fixture_id)
        return counts

    if include_stats_events:
        try:
            players_payload = client.get_fixture_players(fixture_id)
            for team_payload in players_payload.get("response", []):
                team_name = team_payload.get("team", {}).get("name") or ""
                counts["fixture_player_stats"] += store_fixture_players_payload(
                    client.connection,
                    fixture_id=fixture_id,
                    team_norm=_strict_team_norm(
                        team_name,
                        context=f"fixture {fixture_id} player stats team",
                    ),
                    team_name=team_name,
                    team_payload=team_payload,
                    resolution_entries=resolution_entries,
                )
        except APILimitReachedError:
            logger.warning("LÃ­mite/rate limit alcanzado al pedir stats de jugadores para fixture %s", fixture_id)
            return counts

        try:
            stats_payload = client.get_fixture_statistics(fixture_id)
            for stat_row in stats_payload.get("response", []):
                team_name = stat_row.get("team", {}).get("name") or ""
                store_json_row(
                    client.connection,
                    "historical_team_stats",
                    fixture_id,
                    _strict_team_norm(
                        team_name,
                        context=f"fixture {fixture_id} statistics team",
                    ),
                    stat_row,
                )
                counts["statistics"] += 1
        except APILimitReachedError:
            logger.warning("Límite/rate limit alcanzado al pedir estadísticas para fixture %s", fixture_id)
            return counts

        try:
            events_payload = client.get_fixture_events(fixture_id)
            for event_row in events_payload.get("response", []):
                player_name = event_row.get("player", {}).get("name") or ""
                team_name = event_row.get("team", {}).get("name") or ""
                store_json_row(
                    client.connection,
                    "historical_player_stats",
                    fixture_id,
                    _strict_team_norm(
                        team_name,
                        context=f"fixture {fixture_id} event team",
                    ),
                    event_row,
                    player_norm=normalize_text(player_name),
                )
                counts["events"] += 1
        except APILimitReachedError:
            logger.warning("Límite/rate limit alcanzado al pedir eventos para fixture %s", fixture_id)
            return counts

    if collect_odds:
        try:
            odds_payload = client.get_odds(fixture_id)
            from quiniela.odds_loader import build_odds_consensus, store_odds_payload

            normalized_odds = store_odds_payload(client.connection, odds_payload)
            for odd_row in odds_payload.get("response", []):
                bookmakers = odd_row.get("bookmakers", [])
                if bookmakers:
                    bookmaker_name = bookmakers[0].get("name")
                else:
                    bookmaker_name = None
                store_json_row(
                    client.connection,
                    "odds_snapshots",
                    fixture_id,
                    None,
                    odd_row,
                    bookmaker=bookmaker_name,
                    market="match_winner",
                )
                counts["odds"] += 1
            consensus = build_odds_consensus(
                client.connection,
                fixture_id=fixture_id,
            )
            counts["odds_market_snapshots"] = normalized_odds.get("inserted", 0)
            counts["odds_market_consensus"] = consensus.get("consensus_rows", 0)
        except APILimitReachedError:
            logger.warning("Límite/rate limit alcanzado al pedir odds para fixture %s", fixture_id)
            return counts

    return counts


def _fetch_team_player_season_stats(
    client: APIFootballClient,
    api_team_id: int,
    team_norm: str,
    team_name: str,
    seasons: list[int],
    resolution_entries: list[dict[str, Any]] | None = None,
) -> int:
    stored_rows = 0
    for season in seasons:
        page = 1
        while True:
            payload = client.get_players(team=api_team_id, season=season, page=page)
            response_rows = payload.get("response", [])
            if not response_rows:
                break
            stored_rows += store_player_season_payload(
                client.connection,
                team_norm=team_norm,
                team_name=team_name,
                payload=payload,
                resolution_entries=resolution_entries,
            )
            if len(response_rows) < 20:
                break
            page += 1
            if page > 25:
                break
    return stored_rows


def write_backfill_report(report: dict[str, Any], output_path: Path | None = None) -> Path:
    settings = get_settings()
    path = output_path or settings.logs_dir / "data_quality_report.md"
    lines = [
        "# Data Quality Report",
        "",
        f"- Teams requested: {', '.join(report['teams_requested'])}",
        f"- Teams resolved: {', '.join(report['teams_resolved']) if report['teams_resolved'] else 'none'}",
        f"- Teams missing: {', '.join(report['teams_missing']) if report['teams_missing'] else 'none'}",
        f"- Fixtures stored: {report['fixtures_stored']}",
        f"- Lineups stored: {report['lineups_stored']}",
        f"- Fixture player stats stored: {report.get('fixture_player_stats_stored', 0)}",
        f"- Player season stats stored: {report.get('season_player_stats_stored', 0)}",
        f"- Statistics rows stored: {report['statistics_stored']}",
        f"- Event rows stored: {report['events_stored']}",
        f"- Odds rows stored: {report['odds_stored']}",
        "",
        "## Strategy by team",
        "",
    ]
    if report.get("team_history_strategy"):
        for team, strategy in report["team_history_strategy"].items():
            lines.append(f"- {team}: {strategy}")
    else:
        lines.append("- none")
    lines.extend(
        [
        "",
        "## Missing fixtures by team",
        "",
        ]
    )
    if report["missing_fixture_counts"]:
        for team, count in report["missing_fixture_counts"].items():
            lines.append(f"- {team}: {count}")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def backfill_team_history(
    team_names: list[str],
    months: int = 6,
    max_matches_per_team: int = 8,
    dry_run: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    client = APIFootballClient(settings=settings, dry_run=dry_run, force_refresh=force_refresh)
    today = datetime.now().date()
    date_from = (today - timedelta(days=months * 30)).isoformat()
    date_to = today.isoformat()

    report: dict[str, Any] = {
        "teams_requested": [_display_team_name(team_name) for team_name in team_names],
        "teams_resolved": [],
        "teams_missing": [],
        "fixtures_stored": 0,
        "lineups_stored": 0,
        "fixture_player_stats_stored": 0,
        "season_player_stats_stored": 0,
        "statistics_stored": 0,
        "events_stored": 0,
        "odds_stored": 0,
        "missing_fixture_counts": {},
        "team_history_strategy": {},
    }
    resolution_entries: list[dict[str, Any]] = []

    selected_fixture_ids: set[str] = set()
    fixture_payloads: dict[str, dict[str, Any]] = {}

    for team_name in team_names:
        display_name = _display_team_name(team_name)
        api_team_id = resolve_team_id(client, team_name)
        if api_team_id is None:
            report["teams_missing"].append(display_name)
            report["missing_fixture_counts"][display_name] = max_matches_per_team
            report["team_history_strategy"][display_name] = "unresolved_team_id"
            continue

        report["teams_resolved"].append(display_name)
        report["season_player_stats_stored"] += _fetch_team_player_season_stats(
            client=client,
            api_team_id=api_team_id,
            team_norm=normalize_team_name(team_name),
            team_name=display_name,
            seasons=_season_candidates(date_from, date_to),
            resolution_entries=resolution_entries,
        )
        selected, strategy = _fetch_team_history_candidates(
            client=client,
            team_name=team_name,
            api_team_id=api_team_id,
            from_date=date_from,
            to_date=date_to,
            max_matches_per_team=max_matches_per_team,
        )
        report["team_history_strategy"][display_name] = strategy
        report["missing_fixture_counts"][display_name] = max(max_matches_per_team - len(selected), 0)

        for fixture in selected:
            fixture_id = _fixture_id(fixture)
            if not fixture_id or fixture_id == "None":
                continue
            selected_fixture_ids.add(fixture_id)
            fixture_payloads[fixture_id] = fixture

    for fixture_id, payload in sorted(fixture_payloads.items(), key=lambda item: _fixture_date(item[1]), reverse=True):
        store_historical_match(client.connection, payload)
        report["fixtures_stored"] += 1
        collect_odds = client.requests_remaining() >= 15
        counts = _store_fixture_children(
            client,
            fixture_id,
            collect_odds=collect_odds,
            resolution_entries=resolution_entries,
            include_stats_events=True,
        )
        report["lineups_stored"] += counts["lineups"]
        report["fixture_player_stats_stored"] += counts["fixture_player_stats"]
        report["statistics_stored"] += counts["statistics"]
        report["events_stored"] += counts["events"]
        report["odds_stored"] += counts["odds"]

    from quiniela.output_manager import rebuild_outputs

    rebuild_outputs(connection=client.connection)
    return report


def fetch_today_data(
    date_str: str,
    lineups_only: bool = False,
    dry_run: bool = False,
    force_refresh: bool = False,
    fetch_mode: str = "full",
) -> dict[str, Any]:
    valid_modes = {"full", "hourly", "pre_match", "post_status", "lineups"}
    if lineups_only:
        fetch_mode = "lineups"
    if fetch_mode not in valid_modes:
        raise ValueError(f"fetch_mode debe ser uno de: {', '.join(sorted(valid_modes))}")

    settings = get_settings()
    client = APIFootballClient(settings=settings, dry_run=dry_run, force_refresh=force_refresh)
    target_date = pd.Timestamp(date_str).date()
    api_dates = [
        (target_date + timedelta(days=offset)).isoformat()
        for offset in range(3)
    ]
    fixtures: list[dict[str, Any]] = []
    seen_fixture_ids: set[str] = set()
    cdmx_tz = ZoneInfo(settings.local_timezone)
    for api_date in api_dates:
        payload = client.get_fixtures(date=api_date)
        for fixture in payload.get("response", []):
            fixture_id = str(fixture.get("fixture", {}).get("id"))
            if not fixture_id or fixture_id == "None" or fixture_id in seen_fixture_ids:
                continue
            kickoff = pd.Timestamp(fixture.get("fixture", {}).get("date"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.tz_localize("UTC")
            kickoff_cdmx = kickoff.tz_convert(cdmx_tz)
            seen_fixture_ids.add(fixture_id)
            fixtures.append(fixture)
    scheduled_df = fetch_dataframe(
        client.connection,
        """
        SELECT match_id, home_team, away_team
        FROM matches
        WHERE date_cdmx = ?
        """,
        (date_str,),
    )
    scheduled_pairs: dict[tuple[str, str], str] = {}
    scheduled_team_expectations: dict[str, list[tuple[tuple[str, str], str]]] = defaultdict(list)
    retrieval_issues: list[dict[str, Any]] = []
    for row in scheduled_df.to_dict(orient="records"):
        pair = (
            _strict_team_norm(
                str(row.get("home_team") or ""),
                context=f"scheduled match {row['match_id']} home team",
            ),
            _strict_team_norm(
                str(row.get("away_team") or ""),
                context=f"scheduled match {row['match_id']} away team",
            ),
        )
        if pair in scheduled_pairs:
            retrieval_issues.append(
                _retrieval_issue(
                    reason="duplicate_scheduled_pair",
                    expected_match_id=str(row["match_id"]),
                    expected_pair=pair,
                )
            )
            continue
        scheduled_pairs[pair] = str(row["match_id"])
        scheduled_team_expectations[pair[0]].append((pair, str(row["match_id"])))
        scheduled_team_expectations[pair[1]].append((pair, str(row["match_id"])))
    scheduled_team_norms = set(scheduled_team_expectations)

    validated_fixtures: list[dict[str, Any]] = []
    matched_pairs: dict[tuple[str, str], str] = {}
    ignored_non_world_cup_fixtures = 0
    for fixture in fixtures:
        fixture_id = str(fixture.get("fixture", {}).get("id"))
        kickoff = pd.Timestamp(fixture.get("fixture", {}).get("date"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.tz_localize("UTC")
        kickoff_cdmx = kickoff.tz_convert(cdmx_tz)
        if kickoff_cdmx.date() != target_date:
            continue
        home_team = str(fixture.get("teams", {}).get("home", {}).get("name") or "")
        away_team = str(fixture.get("teams", {}).get("away", {}).get("name") or "")
        home_team_norm, home_error = _normalize_live_team_name(
            home_team,
            context=f"live fixture {fixture_id} home team",
        )
        away_team_norm, away_error = _normalize_live_team_name(
            away_team,
            context=f"live fixture {fixture_id} away team",
        )
        candidate_norms = {
            team_norm
            for team_norm in (home_team_norm, away_team_norm)
            if team_norm in scheduled_team_norms
        }
        if not candidate_norms:
            ignored_non_world_cup_fixtures += 1
            continue
        expected_context = {
            expectation
            for team_norm in candidate_norms
            for expectation in scheduled_team_expectations[team_norm]
        }
        expected_pair = None
        expected_match_id = None
        if len(expected_context) == 1:
            expected_pair, expected_match_id = next(iter(expected_context))
        if home_team_norm is None or away_team_norm is None:
            details = [detail for detail in (home_error, away_error) if detail]
            retrieval_issues.append(
                _retrieval_issue(
                    reason="unknown_candidate_team_name",
                    fixture_id=fixture_id,
                    raw_home_team=home_team,
                    raw_away_team=away_team,
                    expected_match_id=expected_match_id,
                    expected_pair=expected_pair,
                    detail=" | ".join(details),
                )
            )
            continue
        pair = (home_team_norm, away_team_norm)
        match_id = scheduled_pairs.get(pair)
        if match_id is None:
            retrieval_issues.append(
                _retrieval_issue(
                    reason="scheduled_team_mismatch",
                    fixture_id=fixture_id,
                    raw_home_team=home_team,
                    raw_away_team=away_team,
                    expected_match_id=expected_match_id,
                    expected_pair=expected_pair,
                    detail=f"normalized_pair={pair[0]} vs {pair[1]}",
                )
            )
            continue
        previous_fixture = matched_pairs.get(pair)
        if previous_fixture and previous_fixture != fixture_id:
            retrieval_issues.append(
                _retrieval_issue(
                    reason="duplicate_candidate_fixture",
                    fixture_id=fixture_id,
                    raw_home_team=home_team,
                    raw_away_team=away_team,
                    expected_match_id=match_id,
                    expected_pair=pair,
                    detail=f"previous_fixture={previous_fixture}",
                )
            )
            continue
        matched_pairs[pair] = fixture_id
        validated_fixtures.append(
            {
                "fixture": fixture,
                "fixture_id": fixture_id,
                "kickoff_cdmx": kickoff_cdmx,
                "home_team": home_team,
                "away_team": away_team,
                "home_team_norm": pair[0],
                "away_team_norm": pair[1],
                "match_id": match_id,
            }
        )

    if retrieval_issues:
        raise RetrievalValidationError(
            _format_retrieval_issues(retrieval_issues),
            issues=retrieval_issues,
        )
    updated = defaultdict(int)
    updated["ignored_non_world_cup_fixtures"] = ignored_non_world_cup_fixtures
    updated["candidate_fixtures_validated"] = len(validated_fixtures)
    resolution_entries: list[dict[str, Any]] = []

    with client.connection:
        for validated in validated_fixtures:
            fixture = validated["fixture"]
            store_historical_match(client.connection, fixture)
            updated["fixtures"] += 1
            fixture_id = str(validated["fixture_id"])
            home_team = str(validated["home_team"])
            away_team = str(validated["away_team"])
            kickoff_cdmx = validated["kickoff_cdmx"]
            match_id = str(validated["match_id"])
            client.connection.execute(
                """
                UPDATE matches
                SET api_fixture_id = ?,
                    status = ?,
                    date_cdmx = ?,
                    time_cdmx = ?,
                    datetime_cdmx = ?
                WHERE match_id = ?
                """,
                (
                    fixture.get("fixture", {}).get("id"),
                    fixture.get("fixture", {}).get("status", {}).get("short") or "scheduled",
                    kickoff_cdmx.date().isoformat(),
                    kickoff_cdmx.strftime("%H:%M"),
                    kickoff_cdmx.isoformat(),
                    match_id,
                ),
            )
            if fetch_mode == "post_status":
                continue
            include_stats_events = (
                fetch_mode == "full"
                and client.requests_remaining() > settings.api_critical_reserve
            )
            collect_odds = fetch_mode in {"full", "hourly", "pre_match"}
            counts = _store_fixture_children(
                client,
                fixture_id,
                collect_odds=collect_odds,
                resolution_entries=resolution_entries,
                include_stats_events=include_stats_events,
                match_id=match_id,
                kickoff_at=kickoff_cdmx.isoformat(),
            )
            updated["lineups"] += counts["lineups"]
            updated["fixture_player_stats"] += counts["fixture_player_stats"]
            updated["odds"] += counts["odds"]
            updated["odds_market_snapshots"] += counts["odds_market_snapshots"]
            updated["odds_market_consensus"] += counts["odds_market_consensus"]
            updated["official_lineup_notifications"] += counts[
                "official_lineup_notifications"
            ]
            updated["official_lineup_superseded"] += counts[
                "official_lineup_superseded"
            ]
            if include_stats_events:
                updated["statistics"] += counts["statistics"]
                updated["events"] += counts["events"]
                for team_name, team_norm in (
                    (home_team, str(validated["home_team_norm"])),
                    (away_team, str(validated["away_team_norm"])),
                ):
                    api_team_id = get_team_api_id(client.connection, team_norm)
                    if api_team_id is not None:
                        updated["season_player_stats"] += _fetch_team_player_season_stats(
                            client=client,
                            api_team_id=api_team_id,
                            team_norm=team_norm,
                            team_name=team_name,
                            seasons=[pd.Timestamp(date_str).year],
                            resolution_entries=resolution_entries,
                        )

    from quiniela.output_manager import rebuild_outputs

    rebuild_outputs(connection=client.connection)
    return dict(updated)


def sync_finished_results_for_date(
    date_str: str,
    connection=None,
) -> dict[str, Any]:
    if connection is None:
        settings = get_settings()
        connection = get_connection(settings.db_path)
    finished_df = fetch_dataframe(
        connection,
        """
        SELECT
            m.match_id,
            hm.home_goals,
            hm.away_goals,
            hm.source_json,
            CASE WHEN ar.match_id IS NULL THEN 1 ELSE 0 END AS is_new_result
        FROM matches m
        INNER JOIN historical_matches hm
            ON hm.fixture_id = CAST(m.api_fixture_id AS TEXT)
        LEFT JOIN actual_results ar
            ON ar.match_id = m.match_id
        WHERE m.date_cdmx = ?
          AND hm.status IN ('FT', 'AET', 'PEN')
          AND hm.home_goals IS NOT NULL
          AND hm.away_goals IS NOT NULL
        """,
        (date_str,),
    )
    if finished_df.empty:
        return {"updated": 0, "match_ids": [], "new_match_ids": []}

    finished_records = finished_df.to_dict(orient="records")
    rows = [
        (
            row["match_id"],
            int(row["home_goals"]),
            int(row["away_goals"]),
            row["source_json"],
        )
        for row in finished_records
    ]
    new_match_ids = [
        str(row["match_id"])
        for row in finished_records
        if int(row["is_new_result"]) == 1
    ]
    with connection:
        connection.executemany(
            """
            INSERT INTO actual_results (match_id, home_goals, away_goals, result_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                result_json = excluded.result_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
    return {
        "updated": len(rows),
        "match_ids": [row[0] for row in rows],
        "new_match_ids": new_match_ids,
    }


def update_after_match(
    home_team: str,
    away_team: str,
    dry_run: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    client = APIFootballClient(settings=settings, dry_run=dry_run, force_refresh=force_refresh)
    connection = client.connection
    match_df = fetch_dataframe(
        connection,
        """
        SELECT match_id, api_fixture_id, home_team, away_team, home_team_norm, away_team_norm
        FROM matches
        WHERE (home_team_norm = ? AND away_team_norm = ?)
           OR (home_team = ? AND away_team = ?)
        ORDER BY datetime_cdmx DESC
        LIMIT 1
        """,
        (
            normalize_team_name(home_team),
            normalize_team_name(away_team),
            home_team,
            away_team,
        ),
    )
    if match_df.empty:
        return {"updated": False, "reason": "match_not_found"}

    row = match_df.iloc[0]
    fixture_id = row.get("api_fixture_id")
    if pd.isna(fixture_id):
        historical = fetch_dataframe(
            connection,
            """
            SELECT fixture_id, home_goals, away_goals
            FROM historical_matches
            WHERE home_team_norm = ? AND away_team_norm = ?
            ORDER BY match_date DESC
            LIMIT 1
            """,
            (normalize_team_name(home_team), normalize_team_name(away_team)),
        )
        if historical.empty:
            return {"updated": False, "reason": "no_result_data"}
        fixture_id = historical.iloc[0]["fixture_id"]
        home_goals = historical.iloc[0]["home_goals"]
        away_goals = historical.iloc[0]["away_goals"]
        payload = {"fixture_id": fixture_id, "home_goals": home_goals, "away_goals": away_goals}
    else:
        payload = client.get_fixture_by_id(int(fixture_id))
        response_rows = payload.get("response", [])
        if not response_rows:
            return {"updated": False, "reason": "empty_fixture_response"}
        fixture = response_rows[0]
        store_historical_match(connection, fixture)
        home_goals = fixture.get("goals", {}).get("home")
        away_goals = fixture.get("goals", {}).get("away")
        payload = fixture

    with connection:
        connection.execute(
            """
            INSERT INTO actual_results (match_id, home_goals, away_goals, result_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                result_json = excluded.result_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (row["match_id"], home_goals, away_goals, json.dumps(payload, ensure_ascii=False)),
        )
    return {"updated": True, "match_id": row["match_id"], "home_goals": home_goals, "away_goals": away_goals}
