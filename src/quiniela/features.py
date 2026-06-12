from __future__ import annotations

import json
from typing import Any

import pandas as pd

from quiniela.db import fetch_dataframe
from quiniela.name_maps import normalize_team_name
from quiniela.player_model import aggregate_team_player_features


DEFAULT_TEAM_FEATURES = {
    "gf_avg": 1.0,
    "ga_avg": 1.0,
    "gd_avg": 0.0,
    "win_rate": 0.33,
    "clean_sheet_rate": 0.0,
    "recent_form_score": 0.0,
    "host_adjustment": 0.0,
    "lineup_strength": 0.0,
    "odds_adjustment": 0.0,
    "starter_attack_strength": 0.0,
    "starter_defense_strength": 0.0,
    "starter_midfield_control": 0.0,
    "goalkeeper_strength": 0.0,
    "bench_impact": 0.0,
    "discipline_risk_penalty": 0.0,
    "lineup_source": "fallback",
    "player_impacts": [],
    "matches_used": 0,
    "used_fallback": 1,
}


HOST_TEAMS = {"mexico", "usa", "canada"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_recent_matches(connection, team_norm: str, as_of_datetime: str | None, window: int) -> pd.DataFrame:
    params: list[Any] = [team_norm, team_norm]
    query = """
        SELECT *
        FROM historical_matches
        WHERE (home_team_norm = ? OR away_team_norm = ?)
    """
    if as_of_datetime:
        query += " AND (match_date IS NULL OR match_date < ?)"
        params.append(as_of_datetime)
    query += " ORDER BY match_date DESC LIMIT ?"
    params.append(window)
    return fetch_dataframe(connection, query, params)


def _lineup_strength_from_db(connection, fixture_id: Any, team_norm: str) -> float:
    if fixture_id is None or pd.isna(fixture_id):
        return 0.0
    lineup_df = fetch_dataframe(
        connection,
        """
        SELECT source_json
        FROM historical_lineups
        WHERE fixture_id = ? AND team_norm = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(int(fixture_id)), team_norm),
    )
    if lineup_df.empty:
        return 0.0
    payload = json.loads(lineup_df.iloc[0]["source_json"])
    starters = payload.get("startXI") or []
    if starters:
        return min(len(starters) / 11.0, 1.0) * 0.2
    players = payload.get("players") or []
    if players:
        return min(len(players) / 15.0, 1.0) * 0.1
    return 0.0


def _extract_implied_probability(source_json: str, team_name: str, is_home: bool) -> float | None:
    try:
        payload = json.loads(source_json)
    except json.JSONDecodeError:
        return None

    bookmakers = payload.get("bookmakers", [])
    if not bookmakers:
        return None

    for bookmaker in bookmakers:
        for bet in bookmaker.get("bets", []):
            if (bet.get("name") or "").lower() not in {"match winner", "winner"}:
                continue
            values = bet.get("values", [])
            for value in values:
                odd_name = (value.get("value") or "").strip().lower()
                odd = _safe_float(value.get("odd"), default=0.0)
                if odd <= 0:
                    continue
                if odd_name == team_name.lower():
                    return 1.0 / odd
                if is_home and odd_name in {"home", "1"}:
                    return 1.0 / odd
                if (not is_home) and odd_name in {"away", "2"}:
                    return 1.0 / odd
    return None


def _odds_adjustment_from_db(connection, fixture_id: Any, team_name: str, is_home: bool) -> float:
    if fixture_id is None or pd.isna(fixture_id):
        return 0.0
    odds_df = fetch_dataframe(
        connection,
        """
        SELECT source_json
        FROM odds_snapshots
        WHERE fixture_id = ?
        ORDER BY id DESC
        LIMIT 5
        """,
        (str(int(fixture_id)),),
    )
    for _, row in odds_df.iterrows():
        implied = _extract_implied_probability(row["source_json"], team_name, is_home)
        if implied is not None:
            return max(min((implied - 0.33) * 0.6, 0.25), -0.25)
    return 0.0


def compute_team_features(
    connection,
    team_norm: str,
    team_name: str,
    as_of_datetime: str | None = None,
    window: int = 5,
    fixture_id: Any = None,
    is_home: bool = False,
) -> dict[str, Any]:
    matches_df = _load_recent_matches(connection, team_norm, as_of_datetime, window)
    player_features, player_impacts = aggregate_team_player_features(
        connection,
        team_norm=team_norm,
        team_name=team_name,
        as_of_datetime=as_of_datetime,
        fixture_id=fixture_id,
    )
    if matches_df.empty:
        fallback = DEFAULT_TEAM_FEATURES.copy()
        fallback.update(player_features)
        fallback["lineup_strength"] = max(
            _lineup_strength_from_db(connection, fixture_id, team_norm),
            _safe_float(player_features.get("lineup_strength"), 0.0),
        )
        fallback["odds_adjustment"] = _odds_adjustment_from_db(connection, fixture_id, team_name, is_home)
        fallback["host_adjustment"] = 0.08 if is_home else 0.0
        fallback["player_impacts"] = player_impacts
        return fallback

    gf: list[float] = []
    ga: list[float] = []
    results: list[float] = []
    clean_sheets = 0
    for _, row in matches_df.iterrows():
        is_home_team = row["home_team_norm"] == team_norm
        goals_for = _safe_float(row["home_goals"] if is_home_team else row["away_goals"], default=0.0)
        goals_against = _safe_float(row["away_goals"] if is_home_team else row["home_goals"], default=0.0)
        gf.append(goals_for)
        ga.append(goals_against)
        if goals_for > goals_against:
            results.append(1.0)
        elif goals_for == goals_against:
            results.append(0.5)
        else:
            results.append(0.0)
        if goals_against == 0:
            clean_sheets += 1

    matches_used = len(matches_df)
    gf_avg = sum(gf) / matches_used
    ga_avg = sum(ga) / matches_used
    win_rate = sum(1 for value in results if value == 1.0) / matches_used
    recent_form_score = (sum(results) / matches_used) - 0.5
    host_adjustment = 0.08 if is_home else 0.0
    if team_norm in HOST_TEAMS and is_home:
        host_adjustment += 0.04

    return {
        "gf_avg": gf_avg,
        "ga_avg": ga_avg,
        "gd_avg": gf_avg - ga_avg,
        "win_rate": win_rate,
        "clean_sheet_rate": clean_sheets / matches_used,
        "recent_form_score": recent_form_score,
        "host_adjustment": host_adjustment,
        "lineup_strength": max(
            _lineup_strength_from_db(connection, fixture_id, team_norm),
            _safe_float(player_features.get("lineup_strength"), 0.0),
        ),
        "odds_adjustment": _odds_adjustment_from_db(connection, fixture_id, team_name, is_home),
        "starter_attack_strength": _safe_float(player_features.get("starter_attack_strength"), 0.0),
        "starter_defense_strength": _safe_float(player_features.get("starter_defense_strength"), 0.0),
        "starter_midfield_control": _safe_float(player_features.get("starter_midfield_control"), 0.0),
        "goalkeeper_strength": _safe_float(player_features.get("goalkeeper_strength"), 0.0),
        "bench_impact": _safe_float(player_features.get("bench_impact"), 0.0),
        "discipline_risk_penalty": _safe_float(player_features.get("discipline_risk_penalty"), 0.0),
        "lineup_source": player_features.get("lineup_source", "fallback"),
        "player_impacts": player_impacts,
        "matches_used": matches_used,
        "used_fallback": 0,
    }


def build_match_feature_row(connection, match_row: dict[str, Any], window: int = 5) -> dict[str, Any]:
    home_features = compute_team_features(
        connection,
        team_norm=match_row["home_team_norm"],
        team_name=match_row["home_team"],
        as_of_datetime=match_row.get("datetime_cdmx"),
        window=window,
        fixture_id=match_row.get("api_fixture_id"),
        is_home=True,
    )
    away_features = compute_team_features(
        connection,
        team_norm=match_row["away_team_norm"],
        team_name=match_row["away_team"],
        as_of_datetime=match_row.get("datetime_cdmx"),
        window=window,
        fixture_id=match_row.get("api_fixture_id"),
        is_home=False,
    )

    row = {
        "match_id": match_row.get("match_id"),
        "date_cdmx": match_row.get("date_cdmx"),
        "datetime_cdmx": match_row.get("datetime_cdmx"),
        "group": match_row.get("group"),
        "home_team": match_row.get("home_team"),
        "away_team": match_row.get("away_team"),
        "home_team_norm": match_row.get("home_team_norm"),
        "away_team_norm": match_row.get("away_team_norm"),
        "api_fixture_id": match_row.get("api_fixture_id"),
        "stage": match_row.get("stage", "group"),
    }

    for prefix, features in (("home", home_features), ("away", away_features)):
        for key, value in features.items():
            row[f"{prefix}_{key}"] = value
    row["delta_attack_strength"] = _safe_float(home_features.get("starter_attack_strength")) - _safe_float(
        away_features.get("starter_attack_strength")
    )
    row["delta_defense_strength"] = _safe_float(home_features.get("starter_defense_strength")) - _safe_float(
        away_features.get("starter_defense_strength")
    )
    row["delta_midfield_control"] = _safe_float(home_features.get("starter_midfield_control")) - _safe_float(
        away_features.get("starter_midfield_control")
    )
    row["delta_goalkeeper_strength"] = _safe_float(home_features.get("goalkeeper_strength")) - _safe_float(
        away_features.get("goalkeeper_strength")
    )
    row["delta_bench_impact"] = _safe_float(home_features.get("bench_impact")) - _safe_float(
        away_features.get("bench_impact")
    )
    row["delta_discipline_risk_penalty"] = _safe_float(home_features.get("discipline_risk_penalty")) - _safe_float(
        away_features.get("discipline_risk_penalty")
    )
    return row


def build_features_for_matches(connection, matches_df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    rows = [build_match_feature_row(connection, row, window=window) for row in matches_df.to_dict(orient="records")]
    return pd.DataFrame(rows)
