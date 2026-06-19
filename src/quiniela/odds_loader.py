from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
from statistics import median
from typing import Any

import numpy as np

from quiniela.api_football_client import APIFootballClient
from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection
from quiniela.name_maps import normalize_team_name, normalize_text


SUPPORTED_MARKET_HINTS = {
    "match_winner",
    "winner",
    "double_chance",
    "exact_score",
    "correct_score",
    "goals_over_under",
    "both_teams_score",
    "asian_handicap",
    "handicap_result",
    "cards_over_under",
    "total_shotongoal",
    "home_total_shotongoal",
    "corners_over_under",
    "team_to_score_first",
}
ODDS_MODEL_VERSION = "odds_prematch_consensus_v1"


@dataclass(frozen=True)
class MatchMetadata:
    match_id: str | None
    home_team_norm: str | None
    away_team_norm: str | None
    home_team: str | None
    away_team: str | None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fixture_id(odds_row: dict[str, Any]) -> str | None:
    value = odds_row.get("fixture", {}).get("id")
    if value is None:
        return None
    return str(value)


def _match_metadata(connection: sqlite3.Connection, fixture_id: str) -> MatchMetadata:
    row = connection.execute(
        """
        SELECT match_id, home_team_norm, away_team_norm, home_team, away_team
        FROM matches
        WHERE CAST(api_fixture_id AS TEXT) = ?
        ORDER BY datetime_cdmx
        LIMIT 1
        """,
        (fixture_id,),
    ).fetchone()
    if row is None:
        return MatchMetadata(None, None, None, None, None)
    return MatchMetadata(
        match_id=str(row["match_id"]),
        home_team_norm=str(row["home_team_norm"]),
        away_team_norm=str(row["away_team_norm"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
    )


def _market_key(name: str) -> str:
    key = normalize_text(name)
    if key in {"match_winner", "winner", "fulltime_result"}:
        return "match_winner"
    if key in {"goals_over_under", "goals_over_under_first_half", "goals_over_under_second_half"}:
        return key
    if key in {"both_teams_score", "both_teams_to_score"}:
        return "both_teams_score"
    if key in {"exact_score", "correct_score"}:
        return "exact_score"
    return key


def _parse_line(value_name: str, handicap: Any) -> tuple[str, str | None]:
    if handicap not in (None, ""):
        text = str(handicap).strip()
        return text, text
    match = re.search(r"([-+]?\d+(?:\.\d+)?)", value_name)
    if match:
        return match.group(1), match.group(1)
    return "", None


def _selection_key(
    market_key: str,
    value_name: str,
    metadata: MatchMetadata,
) -> tuple[str, str | None]:
    clean = str(value_name or "").strip()
    normalized = normalize_text(clean)
    lower = clean.lower()
    if market_key == "match_winner":
        if normalized in {"home", "1"}:
            return "home", metadata.home_team_norm
        if normalized in {"away", "2"}:
            return "away", metadata.away_team_norm
        if normalized in {"draw", "x"}:
            return "draw", None
        try:
            team_norm = normalize_team_name(clean, strict=True)
            if team_norm == metadata.home_team_norm:
                return "home", team_norm
            if team_norm == metadata.away_team_norm:
                return "away", team_norm
            return team_norm, team_norm
        except Exception:
            return normalized, None
    if market_key == "both_teams_score":
        if normalized in {"yes", "si"}:
            return "yes", None
        if normalized == "no":
            return "no", None
    if "over_under" in market_key:
        if lower.startswith("over"):
            return "over", None
        if lower.startswith("under"):
            return "under", None
    if market_key == "exact_score":
        score = re.search(r"(\d+)\s*[-:]\s*(\d+)", clean)
        if score:
            return f"{score.group(1)}-{score.group(2)}", None
    if normalized in {"home", "1"}:
        return "home", metadata.home_team_norm
    if normalized in {"away", "2"}:
        return "away", metadata.away_team_norm
    if normalized in {"draw", "x"}:
        return "draw", None
    return normalized, None


def store_odds_payload(
    connection: sqlite3.Connection,
    odds_payload: dict[str, Any],
) -> dict[str, int]:
    inserted = 0
    skipped = 0
    for odds_row in odds_payload.get("response", []):
        fixture_id = _fixture_id(odds_row)
        if not fixture_id:
            skipped += 1
            continue
        metadata = _match_metadata(connection, fixture_id)
        source_update = str(odds_row.get("update") or "")
        for bookmaker in odds_row.get("bookmakers") or []:
            bookmaker_id = bookmaker.get("id")
            bookmaker_name = str(bookmaker.get("name") or "")
            for bet in bookmaker.get("bets") or []:
                market_name = str(bet.get("name") or "")
                market_key = _market_key(market_name)
                bet_id = bet.get("id")
                for value in bet.get("values") or []:
                    value_name = str(value.get("value") or "")
                    decimal_odd = _safe_float(value.get("odd"))
                    if decimal_odd is None or decimal_odd <= 1.0:
                        skipped += 1
                        continue
                    line_key, handicap = _parse_line(
                        value_name,
                        value.get("handicap"),
                    )
                    selection_key, selection_team_norm = _selection_key(
                        market_key,
                        value_name,
                        metadata,
                    )
                    source_json = {
                        "bookmaker": bookmaker,
                        "bet": bet,
                        "value": value,
                        "fixture": odds_row.get("fixture"),
                        "league": odds_row.get("league"),
                        "update": source_update,
                    }
                    with connection:
                        cursor = connection.execute(
                            """
                            INSERT OR IGNORE INTO odds_market_snapshots (
                                fixture_id, match_id, bookmaker_id, bookmaker,
                                bet_id, market_key, market_name, selection_key,
                                selection_name, selection_team_norm, line_key,
                                handicap, decimal_odd, implied_probability,
                                suspended, source_update, source_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                fixture_id,
                                metadata.match_id,
                                bookmaker_id,
                                bookmaker_name,
                                bet_id,
                                market_key,
                                market_name,
                                selection_key,
                                value_name,
                                selection_team_norm,
                                line_key,
                                handicap,
                                decimal_odd,
                                1.0 / decimal_odd,
                                int(bool(value.get("suspended"))),
                                source_update,
                                json.dumps(source_json, ensure_ascii=False),
                            ),
                        )
                    inserted += int(cursor.rowcount == 1)
    return {"inserted": inserted, "skipped": skipped}


def _devigged_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["bookmaker_id"], row["market_key"], row["line_key"])].append(row)

    devigged: list[dict[str, Any]] = []
    for _, group_rows in grouped.items():
        total = sum(float(row["implied_probability"]) for row in group_rows)
        if total <= 0:
            continue
        for row in group_rows:
            devigged.append(
                {
                    "fixture_id": str(row["fixture_id"]),
                    "match_id": row["match_id"],
                    "market_key": str(row["market_key"]),
                    "market_name": str(row["market_name"]),
                    "selection_key": str(row["selection_key"]),
                    "selection_name": str(row["selection_name"]),
                    "line_key": str(row["line_key"] or ""),
                    "decimal_odd": float(row["decimal_odd"]),
                    "bookmaker": str(row["bookmaker"] or ""),
                    "probability": float(row["implied_probability"]) / total,
                }
            )
    return devigged


def build_odds_consensus(
    connection: sqlite3.Connection | None = None,
    *,
    date_str: str | None = None,
    fixture_id: str | None = None,
) -> dict[str, int]:
    settings = get_settings()
    connection = connection or get_connection(settings.db_path)
    where = ["suspended = 0"]
    params: list[Any] = []
    if fixture_id:
        where.append("fixture_id = ?")
        params.append(str(fixture_id))
    if date_str:
        where.append(
            """
            match_id IN (
                SELECT match_id FROM matches WHERE date_cdmx = ?
            )
            """
        )
        params.append(date_str)
    rows = connection.execute(
        f"""
        SELECT *
        FROM odds_market_snapshots
        WHERE {' AND '.join(where)}
        ORDER BY fixture_id, market_key, line_key, bookmaker_id, id DESC
        """,
        params,
    ).fetchall()
    devigged = _devigged_rows(rows)
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in devigged:
        grouped[
            (
                row["fixture_id"],
                row["market_key"],
                row["line_key"],
                row["selection_key"],
            )
        ].append(row)

    consensus_rows: list[dict[str, Any]] = []
    for (fixture, market_key, line_key, selection_key), values in grouped.items():
        probabilities = [float(value["probability"]) for value in values]
        odds = [float(value["decimal_odd"]) for value in values]
        first = values[0]
        consensus_rows.append(
            {
                "fixture_id": fixture,
                "match_id": first["match_id"],
                "market_key": market_key,
                "market_name": first["market_name"],
                "selection_key": selection_key,
                "selection_name": first["selection_name"],
                "line_key": line_key,
                "consensus_probability": float(median(probabilities)),
                "median_decimal_odd": float(median(odds)),
                "bookmaker_count": len(values),
                "source_json": {
                    "method": "bookmaker_devig_then_median",
                    "bookmakers": sorted({value["bookmaker"] for value in values}),
                    "probabilities": probabilities,
                },
            }
        )

    renormalized: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in consensus_rows:
        renormalized[(row["fixture_id"], row["market_key"], row["line_key"])] += float(
            row["consensus_probability"]
        )
    calculated = datetime.now(timezone.utc).isoformat()
    upserted = 0
    with connection:
        for row in consensus_rows:
            total = renormalized[(row["fixture_id"], row["market_key"], row["line_key"])]
            if total <= 0:
                continue
            probability = float(row["consensus_probability"]) / total
            cursor = connection.execute(
                """
                INSERT INTO odds_market_consensus (
                    fixture_id, match_id, market_key, market_name,
                    selection_key, selection_name, line_key,
                    consensus_probability, median_decimal_odd,
                    bookmaker_count, overround_method, source_json, calculated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fixture_id, market_key, selection_key, line_key)
                DO UPDATE SET
                    match_id = excluded.match_id,
                    market_name = excluded.market_name,
                    selection_name = excluded.selection_name,
                    consensus_probability = excluded.consensus_probability,
                    median_decimal_odd = excluded.median_decimal_odd,
                    bookmaker_count = excluded.bookmaker_count,
                    overround_method = excluded.overround_method,
                    source_json = excluded.source_json,
                    calculated_at = excluded.calculated_at
                """,
                (
                    row["fixture_id"],
                    row["match_id"],
                    row["market_key"],
                    row["market_name"],
                    row["selection_key"],
                    row["selection_name"],
                    row["line_key"],
                    probability,
                    row["median_decimal_odd"],
                    row["bookmaker_count"],
                    "bookmaker_devig_then_market_renormalized",
                    json.dumps(row["source_json"], ensure_ascii=False),
                    calculated,
                ),
            )
            upserted += int(cursor.rowcount > 0)
    return {"snapshots": len(rows), "consensus_rows": upserted}


def fetch_odds_by_date(
    date_str: str,
    dry_run: bool = False,
    force_refresh: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    client = APIFootballClient(
        settings=settings,
        dry_run=dry_run,
        force_refresh=force_refresh,
    )
    payload = client.get_odds_by_date(
        date_str,
        league=1,
        season=int(date_str[:4]),
    )
    stored = store_odds_payload(connection, payload)
    consensus = build_odds_consensus(connection, date_str=date_str)
    return {
        "fixtures": int(payload.get("results") or 0),
        "inserted": stored["inserted"],
        "skipped": stored["skipped"],
        "consensus_rows": consensus["consensus_rows"],
    }


def _load_consensus(
    connection: sqlite3.Connection,
    fixture_id: str,
) -> dict[tuple[str, str], dict[str, float]]:
    rows = connection.execute(
        """
        SELECT market_key, selection_key, line_key, consensus_probability,
               median_decimal_odd, bookmaker_count
        FROM odds_market_consensus
        WHERE fixture_id = ?
        """,
        (fixture_id,),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        key = (str(row["market_key"]), str(row["line_key"] or ""))
        grouped[key][str(row["selection_key"])] = float(row["consensus_probability"])
    return grouped


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    total = float(matrix.sum())
    if total <= 0 or not math.isfinite(total):
        return matrix
    return matrix / total


def _scale_binary_constraint(
    matrix: np.ndarray,
    mask: np.ndarray,
    target_true: float,
) -> np.ndarray:
    target_true = min(max(float(target_true), 0.0), 1.0)
    current_true = float(matrix[mask].sum())
    current_false = float(matrix[~mask].sum())
    adjusted = matrix.copy()
    if current_true > 0:
        adjusted[mask] *= target_true / current_true
    if current_false > 0:
        adjusted[~mask] *= (1.0 - target_true) / current_false
    return _normalize_matrix(adjusted)


def _apply_outcome_constraint(
    matrix: np.ndarray,
    probs: dict[str, float],
) -> np.ndarray:
    adjusted = matrix.copy()
    home_mask = np.fromfunction(lambda i, j: i > j, matrix.shape)
    draw_mask = np.fromfunction(lambda i, j: i == j, matrix.shape)
    away_mask = np.fromfunction(lambda i, j: i < j, matrix.shape)
    for selection, mask in (
        ("home", home_mask),
        ("draw", draw_mask),
        ("away", away_mask),
    ):
        target = probs.get(selection)
        current = float(adjusted[mask].sum())
        if target is not None and current > 0:
            adjusted[mask] *= float(target) / current
    return _normalize_matrix(adjusted)


def _exact_score_matrix(shape: tuple[int, int], probs: dict[str, float]) -> np.ndarray | None:
    matrix = np.zeros(shape)
    found = False
    for score_key, probability in probs.items():
        match = re.fullmatch(r"(\d+)-(\d+)", str(score_key))
        if not match:
            continue
        home = int(match.group(1))
        away = int(match.group(2))
        if home < shape[0] and away < shape[1]:
            matrix[home, away] = float(probability)
            found = True
    if not found:
        return None
    return _normalize_matrix(matrix)


def odds_adjusted_score_prediction(
    connection: sqlite3.Connection,
    *,
    fixture_id: Any,
    poisson_matrix: np.ndarray,
) -> dict[str, Any] | None:
    if fixture_id is None or str(fixture_id) == "nan":
        return None
    fixture_key = str(int(fixture_id))
    consensus = _load_consensus(connection, fixture_key)
    if not consensus:
        return None

    adjusted = _normalize_matrix(poisson_matrix.astype(float).copy())
    match_winner = consensus.get(("match_winner", ""))
    if match_winner:
        adjusted = _apply_outcome_constraint(adjusted, match_winner)

    btts = consensus.get(("both_teams_score", ""))
    if btts and "yes" in btts:
        btts_mask = np.fromfunction(lambda i, j: (i > 0) & (j > 0), adjusted.shape)
        adjusted = _scale_binary_constraint(adjusted, btts_mask, btts["yes"])

    over_under_lines = [
        (line, values)
        for (market_key, line), values in consensus.items()
        if market_key.startswith("goals_over_under") and "over" in values
    ]
    if over_under_lines:
        line, values = sorted(
            over_under_lines,
            key=lambda item: abs((_safe_float(item[0]) or 999.0) - 2.5),
        )[0]
        threshold = _safe_float(line)
        if threshold is not None:
            over_mask = np.fromfunction(lambda i, j: (i + j) > threshold, adjusted.shape)
            adjusted = _scale_binary_constraint(adjusted, over_mask, values["over"])

    exact = consensus.get(("exact_score", ""))
    exact_matrix = _exact_score_matrix(adjusted.shape, exact) if exact else None
    if exact_matrix is not None:
        adjusted = _normalize_matrix((0.65 * adjusted) + (0.35 * exact_matrix))

    home_goals, away_goals = np.unravel_index(np.argmax(adjusted), adjusted.shape)
    summary = odds_consensus_summary(connection, fixture_key)
    result = {
        "model_version": ODDS_MODEL_VERSION,
        "score": f"{int(home_goals)}-{int(away_goals)}",
        "probability": float(adjusted[home_goals, away_goals]),
        "fixture_id": fixture_key,
        "constraints": summary,
    }
    with connection:
        connection.execute(
            """
            INSERT INTO odds_model_predictions (
                fixture_id, match_id, model_version, market_key,
                prediction_key, probability, source_json, calculated_at
            ) VALUES (
                ?, (SELECT match_id FROM matches WHERE CAST(api_fixture_id AS TEXT) = ? LIMIT 1),
                ?, 'exact_score', ?, ?, ?, ?
            )
            ON CONFLICT(fixture_id, model_version, market_key, prediction_key)
            DO UPDATE SET
                probability = excluded.probability,
                source_json = excluded.source_json,
                calculated_at = excluded.calculated_at
            """,
            (
                fixture_key,
                fixture_key,
                ODDS_MODEL_VERSION,
                result["score"],
                result["probability"],
                json.dumps(result, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return result


def odds_consensus_summary(
    connection: sqlite3.Connection,
    fixture_id: Any,
) -> dict[str, Any]:
    if fixture_id is None or str(fixture_id) == "nan":
        return {}
    fixture_key = str(int(fixture_id))
    frame = fetch_dataframe(
        connection,
        """
        SELECT market_key, selection_key, line_key, consensus_probability,
               median_decimal_odd, bookmaker_count
        FROM odds_market_consensus
        WHERE fixture_id = ?
          AND market_key IN (
              'match_winner', 'both_teams_score', 'exact_score',
              'goals_over_under', 'cards_over_under',
              'corners_over_under', 'total_shotongoal',
              'home_total_shotongoal'
          )
        ORDER BY market_key, line_key, selection_key
        """,
        (fixture_key,),
    )
    if frame.empty:
        return {}
    summary: dict[str, Any] = {"fixture_id": fixture_key, "markets": {}}
    for row in frame.to_dict(orient="records"):
        market_key = str(row["market_key"])
        line_key = str(row["line_key"] or "")
        bucket_key = market_key if not line_key else f"{market_key}:{line_key}"
        summary["markets"].setdefault(bucket_key, {})[str(row["selection_key"])] = {
            "probability": float(row["consensus_probability"]),
            "median_decimal_odd": (
                float(row["median_decimal_odd"])
                if row["median_decimal_odd"] is not None
                else None
            ),
            "bookmakers": int(row["bookmaker_count"]),
        }
    return summary
