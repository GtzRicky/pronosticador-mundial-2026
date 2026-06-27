from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

import pandas as pd


class SQLitePredictionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert_prediction_rows(self, predictions_df: pd.DataFrame) -> list[int]:
        inserted_ids: list[int] = []
        with self.connection:
            for row in predictions_df.to_dict(orient="records"):
                row = _with_audit_defaults(row)
                safe_row = _json_safe(row)
                cursor = self.connection.execute(
                    """
                    INSERT INTO predictions (
                        competition_id, season_id,
                        match_id, datetime_cdmx, group_name, home_team, away_team,
                        predicted_score, probability, model_version,
                        hybrid_predicted_score, hybrid_probability,
                        home_win_probability, draw_probability, away_win_probability,
                        outcome_model_version, data_freshness_at,
                        audit_snapshot_id, audit_lineup_sources_json,
                        audit_odds_source_json, audit_degradation_reasons_json,
                        not_evaluable_reason, source_json,
                        prediction_context, window_label, generated_at_utc,
                        is_pre_kickoff
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("competition_id", "fifa_world_cup"),
                        row.get("season_id", "world_cup_2026"),
                        row.get("match_id"),
                        row.get("datetime_cdmx"),
                        row.get("group"),
                        row.get("home_team"),
                        row.get("away_team"),
                        row.get("predicted_score"),
                        float(row.get("probability", 0.0)),
                        row.get("model_version"),
                        row.get("hybrid_predicted_score"),
                        float(row.get("hybrid_probability", 0.0))
                        if row.get("hybrid_probability") is not None
                        else None,
                        float(row.get("home_win_probability", 0.0))
                        if row.get("home_win_probability") is not None
                        else None,
                        float(row.get("draw_probability", 0.0))
                        if row.get("draw_probability") is not None
                        else None,
                        float(row.get("away_win_probability", 0.0))
                        if row.get("away_win_probability") is not None
                        else None,
                        row.get("outcome_model_version"),
                        row.get("data_freshness_at"),
                        row.get("audit_snapshot_id"),
                        row.get("audit_lineup_sources_json"),
                        row.get("audit_odds_source_json"),
                        row.get("audit_degradation_reasons_json"),
                        row.get("not_evaluable_reason"),
                        json.dumps(safe_row, ensure_ascii=False, allow_nan=False),
                        row.get("prediction_context", "manual"),
                        row.get("window_label"),
                        row.get("generated_at_utc") or row.get("generated_at"),
                        int(row.get("is_pre_kickoff", 0)),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return a prediction row id")
                inserted_ids.append(int(cursor.lastrowid))
        return inserted_ids


def _with_audit_defaults(row: dict[str, Any]) -> dict[str, Any]:
    audit_row = dict(row)
    is_pre_kickoff = int(audit_row.get("is_pre_kickoff", 0) or 0)
    if _is_missing(audit_row.get("audit_lineup_sources_json")):
        audit_row["audit_lineup_sources_json"] = _json_text(
            {
                "home": audit_row.get("home_lineup_source"),
                "away": audit_row.get("away_lineup_source"),
            }
        )
    else:
        audit_row["audit_lineup_sources_json"] = _json_text(
            audit_row["audit_lineup_sources_json"]
        )
    if _is_missing(audit_row.get("audit_odds_source_json")):
        audit_row["audit_odds_source_json"] = _json_text(
            {
                "fixture_id": audit_row.get("api_fixture_id"),
                "model_version": audit_row.get("odds_model_version"),
                "consensus_available": audit_row.get("odds_consensus") is not None,
            }
        )
    else:
        audit_row["audit_odds_source_json"] = _json_text(
            audit_row["audit_odds_source_json"]
        )
    if _is_missing(audit_row.get("audit_degradation_reasons_json")):
        reasons = ["post_kickoff_prediction"] if is_pre_kickoff == 0 else []
        audit_row["audit_degradation_reasons_json"] = _json_array_text(reasons)
    else:
        audit_row["audit_degradation_reasons_json"] = _json_array_text(
            audit_row["audit_degradation_reasons_json"]
        )
    if _is_missing(audit_row.get("not_evaluable_reason")) and is_pre_kickoff == 0:
        audit_row["not_evaluable_reason"] = "post_kickoff_prediction"
    return audit_row


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False)
        return value
    return json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False)


def _json_array_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    else:
        parsed = value
    if _is_missing(parsed):
        parsed = []
    if not isinstance(parsed, list):
        parsed = [parsed]
    return json.dumps(_json_safe(parsed), ensure_ascii=False, allow_nan=False)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(result)
    except TypeError:
        return False


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
