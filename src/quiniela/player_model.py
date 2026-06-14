from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import pickle
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd
from rapidfuzz import fuzz, process
from sklearn.linear_model import PoissonRegressor

from quiniela.config import get_settings
from quiniela.db import (
    fetch_dataframe,
    insert_prediction_player_impacts,
    upsert_fixture_player_stats,
    upsert_player_api_profile,
    upsert_player_season_stats,
)
from quiniela.name_maps import normalize_text


PLAYER_MATCH_ALIASES: dict[tuple[str, str], str] = {}
MODEL_ARTIFACT_NAME = "poisson_player_v1.pkl"
PLAYER_MODEL_FEATURE_COLUMNS = [
    "home_gf_avg",
    "home_ga_avg",
    "home_gd_avg",
    "home_recent_form_score",
    "home_host_adjustment",
    "home_odds_adjustment",
    "home_starter_attack_strength",
    "home_starter_defense_strength",
    "home_starter_midfield_control",
    "home_goalkeeper_strength",
    "home_bench_impact",
    "home_discipline_risk_penalty",
    "away_gf_avg",
    "away_ga_avg",
    "away_gd_avg",
    "away_recent_form_score",
    "away_host_adjustment",
    "away_odds_adjustment",
    "away_starter_attack_strength",
    "away_starter_defense_strength",
    "away_starter_midfield_control",
    "away_goalkeeper_strength",
    "away_bench_impact",
    "away_discipline_risk_penalty",
    "delta_attack_strength",
    "delta_defense_strength",
    "delta_midfield_control",
    "delta_goalkeeper_strength",
    "delta_bench_impact",
    "delta_discipline_risk_penalty",
]


@dataclass(frozen=True)
class ResolvedPlayer:
    player_norm: str
    player_name: str
    api_player_id: int | None
    api_position: str | None
    height_cm: float | None
    nationality: str | None
    position_group: str
    resolution_method: str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value) or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, lower: float, upper: float) -> float:
    return max(min(value, upper), lower)


def _per90(value: Any, minutes: Any) -> float:
    minutes_value = _safe_float(minutes)
    if minutes_value <= 0:
        return 0.0
    return _safe_float(value) * 90.0 / minutes_value


def _ratio(numerator: Any, denominator: Any) -> float:
    denominator_value = _safe_float(denominator)
    if denominator_value <= 0:
        return 0.0
    return _safe_float(numerator) / denominator_value


def _position_group(position: str | None) -> str:
    text = (position or "").strip().lower()
    if text in {"g", "goalkeeper"}:
        return "goalkeeper"
    if text in {"d", "defender"}:
        return "defender"
    if text in {"m", "midfielder"}:
        return "midfielder"
    if text in {"f", "attacker", "forward"}:
        return "forward"
    return "unknown"


def _parse_height_cm(value: Any) -> float | None:
    text = str(value or "").lower().replace("cm", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _latest_season_year(as_of_datetime: str | None) -> int | None:
    if not as_of_datetime:
        return None
    return pd.Timestamp(as_of_datetime).year


def resolve_player_identity(
    connection: sqlite3.Connection,
    team_norm: str,
    player_name: str,
    api_player_id: int | None = None,
    api_position: str | None = None,
    height_cm: float | None = None,
    nationality: str | None = None,
    team_name: str | None = None,
) -> ResolvedPlayer:
    normalized_name = normalize_text(player_name)
    alias_key = (team_norm, normalized_name)
    if alias_key in PLAYER_MATCH_ALIASES:
        normalized_name = PLAYER_MATCH_ALIASES[alias_key]

    if api_player_id is not None:
        row = connection.execute(
            """
            SELECT player_norm, player, position_group
            FROM players
            WHERE team_norm = ? AND api_player_id = ?
            LIMIT 1
            """,
            (team_norm, int(api_player_id)),
        ).fetchone()
        if row is not None:
            upsert_player_api_profile(
                connection,
                team_norm=team_norm,
                player_name=str(row["player"]),
                player_norm=str(row["player_norm"]),
                position_group=str(row["position_group"]),
                api_player_id=int(api_player_id),
                api_player_name=player_name,
                api_position=api_position,
                height_cm=height_cm,
                nationality=nationality,
                team_name=team_name,
            )
            return ResolvedPlayer(
                player_norm=str(row["player_norm"]),
                player_name=str(row["player"]),
                api_player_id=int(api_player_id),
                api_position=api_position,
                height_cm=height_cm,
                nationality=nationality,
                position_group=str(row["position_group"]),
                resolution_method="api_player_id",
            )

    row = connection.execute(
        """
        SELECT player_norm, player, position_group
        FROM players
        WHERE team_norm = ? AND player_norm = ?
        LIMIT 1
        """,
        (team_norm, normalized_name),
    ).fetchone()
    if row is not None:
        upsert_player_api_profile(
            connection,
            team_norm=team_norm,
            player_name=str(row["player"]),
            player_norm=str(row["player_norm"]),
            position_group=str(row["position_group"]),
            api_player_id=api_player_id,
            api_player_name=player_name,
            api_position=api_position,
            height_cm=height_cm,
            nationality=nationality,
            team_name=team_name,
        )
        return ResolvedPlayer(
            player_norm=str(row["player_norm"]),
            player_name=str(row["player"]),
            api_player_id=api_player_id,
            api_position=api_position,
            height_cm=height_cm,
            nationality=nationality,
            position_group=str(row["position_group"]),
            resolution_method="exact_name",
        )

    team_players = fetch_dataframe(
        connection,
        "SELECT player_norm, player, position_group FROM players WHERE team_norm = ?",
        (team_norm,),
    )
    if not team_players.empty:
        choices = {row["player_norm"]: row for row in team_players.to_dict(orient="records")}
        match = process.extractOne(normalized_name, list(choices.keys()), scorer=fuzz.ratio, score_cutoff=90)
        if match:
            matched = choices[match[0]]
            upsert_player_api_profile(
                connection,
                team_norm=team_norm,
                player_name=str(matched["player"]),
                player_norm=str(matched["player_norm"]),
                position_group=str(matched["position_group"]),
                api_player_id=api_player_id,
                api_player_name=player_name,
                api_position=api_position,
                height_cm=height_cm,
                nationality=nationality,
                team_name=team_name,
            )
            return ResolvedPlayer(
                player_norm=str(matched["player_norm"]),
                player_name=str(matched["player"]),
                api_player_id=api_player_id,
                api_position=api_position,
                height_cm=height_cm,
                nationality=nationality,
                position_group=str(matched["position_group"]),
                resolution_method="fuzzy_name",
            )

    position_group = _position_group(api_position)
    upsert_player_api_profile(
        connection,
        team_norm=team_norm,
        player_name=player_name,
        player_norm=normalized_name,
        position_group=position_group,
        api_player_id=api_player_id,
        api_player_name=player_name,
        api_position=api_position,
        height_cm=height_cm,
        nationality=nationality,
        team_name=team_name,
    )
    return ResolvedPlayer(
        player_norm=normalized_name,
        player_name=player_name,
        api_player_id=api_player_id,
        api_position=api_position,
        height_cm=height_cm,
        nationality=nationality,
        position_group=position_group,
        resolution_method="api_only",
    )


def write_player_resolution_report(entries: list[dict[str, Any]], output_path: Path | None = None) -> Path:
    settings = get_settings()
    path = output_path or settings.logs_dir / "player_resolution_report.md"
    unresolved = [entry for entry in entries if entry.get("resolution_method") == "api_only"]
    lines = [
        "# Player Resolution Report",
        "",
        f"- Total rows processed: {len(entries)}",
        f"- Unresolved rows: {len(unresolved)}",
        "",
        "## Unresolved players",
        "",
    ]
    if not unresolved:
        lines.append("- none")
    else:
        for entry in unresolved:
            lines.append(
                f"- {entry.get('team_norm')}: {entry.get('api_player_name')} -> {entry.get('player_norm')}"
            )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def store_fixture_players_payload(
    connection: sqlite3.Connection,
    fixture_id: str,
    team_norm: str,
    team_name: str,
    team_payload: dict[str, Any],
    resolution_entries: list[dict[str, Any]] | None = None,
) -> int:
    count = 0
    for player_payload in team_payload.get("players") or []:
        player_info = player_payload.get("player", {})
        stats = (player_payload.get("statistics") or [{}])[0]
        resolved = resolve_player_identity(
            connection,
            team_norm=team_norm,
            player_name=str(player_info.get("name") or "Unknown"),
            api_player_id=player_info.get("id"),
            api_position=stats.get("games", {}).get("position"),
            team_name=team_name,
        )
        upsert_fixture_player_stats(connection, fixture_id, team_norm, player_payload)
        if resolution_entries is not None:
            resolution_entries.append(
                {
                    "fixture_id": fixture_id,
                    "team_norm": team_norm,
                    "api_player_name": player_info.get("name"),
                    "player_norm": resolved.player_norm,
                    "resolution_method": resolved.resolution_method,
                }
            )
        count += 1
    return count


def store_player_season_payload(
    connection: sqlite3.Connection,
    team_norm: str,
    team_name: str,
    payload: dict[str, Any],
    resolution_entries: list[dict[str, Any]] | None = None,
) -> int:
    count = 0
    for player_response in payload.get("response") or []:
        player_info = player_response.get("player", {})
        statistics = player_response.get("statistics") or []
        api_position = statistics[0].get("games", {}).get("position") if statistics else None
        resolved = resolve_player_identity(
            connection,
            team_norm=team_norm,
            player_name=str(player_info.get("name") or "Unknown"),
            api_player_id=player_info.get("id"),
            api_position=api_position,
            height_cm=_parse_height_cm(player_info.get("height")),
            nationality=player_info.get("nationality"),
            team_name=team_name,
        )
        upsert_player_season_stats(connection, team_norm, player_response)
        if resolution_entries is not None:
            resolution_entries.append(
                {
                    "team_norm": team_norm,
                    "api_player_name": player_info.get("name"),
                    "player_norm": resolved.player_norm,
                    "resolution_method": resolved.resolution_method,
                }
            )
        count += 1
    return count


def _latest_prior_lineup(
    connection: sqlite3.Connection,
    team_norm: str,
    as_of_datetime: str | None,
) -> dict[str, Any] | None:
    params: list[Any] = [team_norm]
    query = """
        SELECT hl.source_json
        FROM historical_lineups hl
        LEFT JOIN historical_matches hm ON hm.fixture_id = hl.fixture_id
        WHERE hl.team_norm = ?
    """
    if as_of_datetime:
        query += " AND (hm.match_date IS NULL OR hm.match_date < ?)"
        params.append(as_of_datetime)
    query += " ORDER BY hm.match_date DESC, hl.id DESC LIMIT 1"
    df = fetch_dataframe(connection, query, params)
    if df.empty:
        return None
    return json.loads(df.iloc[0]["source_json"])


def _player_ref_from_lineup_entry(entry: dict[str, Any]) -> dict[str, Any]:
    player = entry.get("player", {})
    return {
        "api_player_id": player.get("id"),
        "player_name": str(player.get("name") or "Unknown"),
        "player_norm": normalize_text(str(player.get("name") or "")),
        "position": player.get("pos"),
        "number": player.get("number"),
    }


def infer_team_sheet(
    connection: sqlite3.Connection,
    team_norm: str,
    as_of_datetime: str | None,
    fixture_id: Any = None,
    allow_season_stats: bool = True,
) -> dict[str, Any]:
    lineup_payload: dict[str, Any] | None = None
    source = "season_stats"
    if fixture_id is not None and str(fixture_id) != "nan":
        df = fetch_dataframe(
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
        if not df.empty:
            official_payload = json.loads(df.iloc[0]["source_json"])
            if len(official_payload.get("startXI") or []) >= 11:
                lineup_payload = official_payload
                source = "confirmed_lineup"

        if lineup_payload is None:
            estimate_df = fetch_dataframe(
                connection,
                """
                SELECT source_json
                FROM lineup_estimates
                WHERE fixture_id = ? AND team_norm = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(int(fixture_id)), team_norm),
            )
            if not estimate_df.empty:
                estimated_payload = json.loads(estimate_df.iloc[0]["source_json"])
                if len(estimated_payload.get("startXI") or []) >= 11:
                    lineup_payload = estimated_payload
                    source = "web_estimated_lineup"

    if lineup_payload is None:
        lineup_payload = _latest_prior_lineup(connection, team_norm, as_of_datetime)
        if lineup_payload is not None:
            source = "recent_lineup"

    if lineup_payload is not None:
        return {
            "source": source,
            "starters": [_player_ref_from_lineup_entry(row) for row in lineup_payload.get("startXI") or []],
            "bench": [_player_ref_from_lineup_entry(row) for row in lineup_payload.get("substitutes") or []],
        }

    if not allow_season_stats:
        return {
            "source": "historical_no_future_fallback",
            "starters": [],
            "bench": [],
        }

    season_year = _latest_season_year(as_of_datetime)
    params: list[Any] = [team_norm]
    query = """
        SELECT api_player_id, player_name, player_norm, position, lineups, minutes
        FROM player_season_stats
        WHERE team_norm = ?
    """
    if season_year is not None:
        query += " AND season <= ?"
        params.append(season_year)
    query += " ORDER BY season DESC, lineups DESC, minutes DESC LIMIT 18"
    df = fetch_dataframe(connection, query, params)
    players = df.to_dict(orient="records")
    return {
        "source": source,
        "starters": players[:11],
        "bench": players[11:18],
    }


def _load_player_recent_stats(
    connection: sqlite3.Connection,
    team_norm: str,
    api_player_id: int | None,
    player_norm: str,
    as_of_datetime: str | None,
    window: int = 5,
) -> pd.DataFrame:
    params: list[Any]
    if api_player_id is not None:
        params = [team_norm, int(api_player_id)]
        where = "fps.team_norm = ? AND fps.api_player_id = ?"
    else:
        params = [team_norm, player_norm]
        where = "fps.team_norm = ? AND fps.player_norm = ?"
    query = f"""
        SELECT fps.*, hm.match_date
        FROM fixture_player_stats fps
        LEFT JOIN historical_matches hm ON hm.fixture_id = fps.fixture_id
        WHERE {where}
    """
    if as_of_datetime:
        query += " AND (hm.match_date IS NULL OR hm.match_date < ?)"
        params.append(as_of_datetime)
    query += " ORDER BY hm.match_date DESC LIMIT ?"
    params.append(window)
    return fetch_dataframe(connection, query, params)


def _load_player_season_snapshot(
    connection: sqlite3.Connection,
    team_norm: str,
    api_player_id: int | None,
    player_norm: str,
    as_of_datetime: str | None,
) -> pd.DataFrame:
    params: list[Any]
    if api_player_id is not None:
        params = [team_norm, int(api_player_id)]
        where = "team_norm = ? AND api_player_id = ?"
    else:
        params = [team_norm, player_norm]
        where = "team_norm = ? AND player_norm = ?"
    query = f"SELECT * FROM player_season_stats WHERE {where}"
    season_year = _latest_season_year(as_of_datetime)
    if season_year is not None:
        query += " AND season <= ?"
        params.append(season_year)
    query += " ORDER BY season DESC, minutes DESC"
    return fetch_dataframe(connection, query, params)


def _weighted_recent_mean(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    values = [_safe_float(value) for value in df[column]]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _weighted_season_mean(df: pd.DataFrame, column: str, weight_column: str = "minutes") -> float:
    if df.empty or column not in df.columns:
        return 0.0
    numer = 0.0
    denom = 0.0
    for _, row in df.iterrows():
        weight = max(_safe_float(row.get(weight_column)), 1.0)
        numer += _safe_float(row.get(column)) * weight
        denom += weight
    return numer / denom if denom else 0.0


def _compute_player_snapshot(
    connection: sqlite3.Connection,
    team_norm: str,
    player_ref: dict[str, Any],
    as_of_datetime: str | None,
    role_weight: float,
    allow_season_stats: bool = True,
) -> dict[str, Any]:
    api_player_id = player_ref.get("api_player_id")
    player_norm = player_ref.get("player_norm") or normalize_text(str(player_ref.get("player_name") or ""))
    recent_df = _load_player_recent_stats(connection, team_norm, api_player_id, player_norm, as_of_datetime)
    season_df = (
        _load_player_season_snapshot(
            connection,
            team_norm,
            api_player_id,
            player_norm,
            as_of_datetime,
        )
        if allow_season_stats
        else pd.DataFrame()
    )
    position_group = _position_group(player_ref.get("position"))
    if position_group == "unknown" and not season_df.empty:
        position_group = _position_group(str(season_df.iloc[0].get("position")))

    recent_minutes = _weighted_recent_mean(recent_df, "minutes")
    season_minutes = _weighted_season_mean(season_df, "minutes")
    recent_lineups = float(len(recent_df[recent_df["minutes"] > 0])) if not recent_df.empty else 0.0
    season_appearances = _weighted_season_mean(season_df, "appearences")
    season_lineups = _weighted_season_mean(season_df, "lineups")

    def blended_per90(recent_column: str, season_column: str) -> float:
        recent_value = _per90(_weighted_recent_mean(recent_df, recent_column), recent_minutes)
        season_value = _per90(_weighted_season_mean(season_df, season_column), season_minutes)
        if recent_df.empty and season_df.empty:
            return 0.0
        if recent_df.empty:
            return season_value
        if season_df.empty:
            return recent_value
        return recent_value * 0.6 + season_value * 0.4

    def blended_rate(recent_column: str, recent_total: str, season_column: str, season_total: str) -> float:
        recent_value = _ratio(_weighted_recent_mean(recent_df, recent_column), _weighted_recent_mean(recent_df, recent_total))
        season_value = _ratio(_weighted_season_mean(season_df, season_column), _weighted_season_mean(season_df, season_total))
        if recent_df.empty and season_df.empty:
            return 0.0
        if recent_df.empty:
            return season_value
        if season_df.empty:
            return recent_value
        return recent_value * 0.6 + season_value * 0.4

    blended_rating = (
        _weighted_recent_mean(recent_df, "rating") * 0.6 + _weighted_season_mean(season_df, "rating") * 0.4
        if (not recent_df.empty or not season_df.empty)
        else 0.0
    )
    blended_height_cm = _weighted_season_mean(season_df, "height_cm")
    aerial_proxy = _clip(((blended_height_cm - 170.0) / 30.0), -0.1, 0.35) + blended_rate(
        "duels_won", "duels_total", "duels_won", "duels_total"
    ) * 0.25

    attack_score = (
        blended_per90("goals_total", "goals_total") * 1.2
        + blended_per90("shots_on", "shots_on") * 0.45
        + blended_per90("goals_assists", "goals_assists") * 0.7
        + blended_per90("passes_key", "passes_key") * 0.18
        + blended_per90("dribbles_success", "dribbles_success") * 0.14
    )
    defense_score = (
        blended_per90("tackles_total", "tackles_total") * 0.22
        + blended_per90("tackles_interceptions", "tackles_interceptions") * 0.35
        + blended_rate("duels_won", "duels_total", "duels_won", "duels_total") * 0.85
        + blended_per90("duels_won", "duels_won") * 0.05
        + aerial_proxy * 0.25
    )
    discipline_risk = (
        blended_per90("fouls_committed", "fouls_committed") * 0.18
        + blended_per90("cards_yellow", "cards_yellow") * 0.8
        + blended_per90("cards_red", "cards_red") * 2.0
        + blended_per90("penalty_commited", "penalty_commited") * 1.8
    )
    availability_score = _clip(
        min(recent_minutes / 450.0, 1.0) * 0.55
        + min(recent_lineups / 5.0, 1.0) * 0.2
        + min(season_appearances / 10.0, 1.0) * 0.15
        + min(season_lineups / 10.0, 1.0) * 0.1,
        0.0,
        1.0,
    )
    goalkeeper_score = 0.0
    if position_group == "goalkeeper":
        goalkeeper_score = (
            blended_per90("goals_saves", "penalty_saved") * 0.2
            - blended_per90("goals_conceded", "goals_conceded") * 0.25
            + _clip((blended_rating - 6.0) / 2.0, -0.25, 0.6) * 0.5
            + availability_score * 0.25
        )

    net_impact = (
        attack_score * 0.45
        + defense_score * 0.35
        + availability_score * 0.2
        + goalkeeper_score * 0.25
        - discipline_risk * 0.3
    ) * role_weight
    return {
        "team_norm": team_norm,
        "api_player_id": api_player_id,
        "player_name": player_ref.get("player_name") or player_norm,
        "player_norm": player_norm,
        "role_bucket": position_group,
        "attack_impact": attack_score * role_weight,
        "defense_impact": (defense_score + (goalkeeper_score if position_group == "goalkeeper" else 0.0)) * role_weight,
        "discipline_impact": -discipline_risk * role_weight,
        "availability_impact": availability_score * role_weight,
        "net_impact": net_impact,
        "goalkeeper_score": goalkeeper_score * role_weight,
        "source_coverage": "season_and_recent" if (not recent_df.empty and not season_df.empty) else "partial",
    }


def aggregate_team_player_features(
    connection: sqlite3.Connection,
    team_norm: str,
    team_name: str,
    as_of_datetime: str | None = None,
    fixture_id: Any = None,
    allow_season_stats: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sheet = infer_team_sheet(
        connection,
        team_norm,
        as_of_datetime,
        fixture_id=fixture_id,
        allow_season_stats=allow_season_stats,
    )
    impacts: list[dict[str, Any]] = []
    starter_rows = [
        {
            **_compute_player_snapshot(
                connection,
                team_norm,
                player_ref,
                as_of_datetime,
                role_weight=1.0,
                allow_season_stats=allow_season_stats,
            ),
            "lineup_role": "starter",
        }
        for player_ref in sheet.get("starters", [])
    ]
    bench_rows = [
        {
            **_compute_player_snapshot(
                connection,
                team_norm,
                player_ref,
                as_of_datetime,
                role_weight=0.25,
                allow_season_stats=allow_season_stats,
            ),
            "lineup_role": "bench",
        }
        for player_ref in sheet.get("bench", [])
    ]
    impacts.extend(starter_rows)
    impacts.extend(bench_rows)

    def _avg(rows: list[dict[str, Any]], key: str, role: str | None = None) -> float:
        filtered = rows if role is None else [row for row in rows if row.get("role_bucket") == role]
        if not filtered:
            return 0.0
        return sum(_safe_float(row.get(key)) for row in filtered) / len(filtered)

    starters_only = [row for row in starter_rows if row.get("role_bucket") != "goalkeeper"]
    midfield_rows = [row for row in starter_rows if row.get("role_bucket") == "midfielder"]
    goalkeeper_rows = [row for row in starter_rows if row.get("role_bucket") == "goalkeeper"]
    aggregate = {
        "starter_attack_strength": _avg(starters_only, "attack_impact"),
        "starter_defense_strength": _avg(starters_only, "defense_impact"),
        "starter_midfield_control": _avg(midfield_rows, "net_impact"),
        "goalkeeper_strength": _avg(goalkeeper_rows, "goalkeeper_score"),
        "bench_impact": _avg(bench_rows, "net_impact"),
        "discipline_risk_penalty": abs(_avg(starter_rows + bench_rows, "discipline_impact")),
        "lineup_strength": _avg(starter_rows, "net_impact") + _avg(bench_rows, "net_impact") * 0.4,
        "lineup_source": sheet.get("source"),
    }
    return aggregate, impacts


def build_training_dataset(connection: sqlite3.Connection, window: int = 5) -> pd.DataFrame:
    from quiniela.features import build_match_feature_row

    historical_df = fetch_dataframe(
        connection,
        """
        SELECT fixture_id, match_date, home_team, away_team, home_team_norm, away_team_norm, home_goals, away_goals
        FROM historical_matches
        WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
        ORDER BY match_date
        """,
    )
    rows: list[dict[str, Any]] = []
    for match in historical_df.to_dict(orient="records"):
        feature_row = build_match_feature_row(
            connection,
            {
                "match_id": f"historical_{match['fixture_id']}",
                "date_cdmx": str(match.get("match_date") or "")[:10],
                "datetime_cdmx": match.get("match_date"),
                "group": "historical",
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "home_team_norm": match.get("home_team_norm"),
                "away_team_norm": match.get("away_team_norm"),
                "api_fixture_id": match.get("fixture_id"),
                "stage": "historical",
            },
            window=window,
        )
        coverage = 0
        if feature_row.get("home_lineup_source") in {"confirmed_lineup", "recent_lineup", "season_stats"}:
            coverage += 1
        if feature_row.get("away_lineup_source") in {"confirmed_lineup", "recent_lineup", "season_stats"}:
            coverage += 1
        feature_row["player_feature_coverage"] = coverage
        feature_row["home_goals_target"] = _safe_float(match.get("home_goals"))
        feature_row["away_goals_target"] = _safe_float(match.get("away_goals"))
        rows.append(feature_row)
    return pd.DataFrame(rows)


def train_player_model(
    connection: sqlite3.Connection,
    artifact_path: Path | None = None,
    min_matches: int = 30,
) -> dict[str, Any]:
    settings = get_settings()
    artifact_path = artifact_path or (settings.model_artifacts_dir / MODEL_ARTIFACT_NAME)
    training_df = build_training_dataset(connection)
    if training_df.empty:
        return {"trained": False, "reason": "empty_training_set", "matches": 0}
    qualified_df = training_df[training_df["player_feature_coverage"] >= 2].copy()
    if len(qualified_df) < min_matches:
        return {
            "trained": False,
            "reason": "insufficient_player_coverage",
            "matches": int(len(qualified_df)),
            "required_min_matches": min_matches,
        }

    x_train = qualified_df[PLAYER_MODEL_FEATURE_COLUMNS].fillna(0.0)
    home_regressor = PoissonRegressor(alpha=0.15, max_iter=500)
    away_regressor = PoissonRegressor(alpha=0.15, max_iter=500)
    home_regressor.fit(x_train, qualified_df["home_goals_target"])
    away_regressor.fit(x_train, qualified_df["away_goals_target"])
    payload = {
        "model_version": "poisson_player_v1",
        "trained_at": datetime.utcnow().isoformat(),
        "feature_columns": PLAYER_MODEL_FEATURE_COLUMNS,
        "home_regressor": home_regressor,
        "away_regressor": away_regressor,
        "training_matches": int(len(qualified_df)),
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return {
        "trained": True,
        "artifact_path": str(artifact_path),
        "training_matches": int(len(qualified_df)),
        "model_version": payload["model_version"],
    }


def load_player_model_artifact(artifact_path: Path | None = None) -> dict[str, Any] | None:
    if artifact_path is None:
        from quiniela.player_evidence import load_active_release_bundle

        active_bundle = load_active_release_bundle()
        if active_bundle is not None:
            return active_bundle
    settings = get_settings()
    artifact_path = artifact_path or (settings.model_artifacts_dir / MODEL_ARTIFACT_NAME)
    if not artifact_path.exists():
        return None
    with artifact_path.open("rb") as handle:
        return pickle.load(handle)


def persist_prediction_impacts(
    connection: sqlite3.Connection,
    prediction_id: int,
    match_id: str,
    home_impacts: list[dict[str, Any]],
    away_impacts: list[dict[str, Any]],
) -> None:
    insert_prediction_player_impacts(
        connection,
        prediction_id,
        match_id,
        home_impacts + away_impacts,
    )
