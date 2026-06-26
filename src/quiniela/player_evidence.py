from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import sqlite3
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, PoissonRegressor, Ridge
from sklearn.dummy import DummyRegressor
from sklearn.metrics import log_loss, mean_absolute_error, mean_poisson_deviance
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quiniela.config import get_settings
from quiniela.db import (
    activate_model_release as activate_db_release,
    claim_automation_run,
    fetch_dataframe,
    finish_automation_run,
    get_active_model_release,
    get_latest_pre_match_snapshot,
    insert_player_targets,
    insert_pre_match_snapshot,
    register_model_release,
)
from quiniela.features import build_match_feature_row
from quiniela.name_maps import normalize_text
from quiniela.infrastructure.competition_config import (
    CompetitionContext,
    resolve_competition_context,
)
from quiniela.outcome_model import OUTCOME_CLASSES
from quiniela.player_model import PLAYER_MODEL_FEATURE_COLUMNS


MODEL_VERSION = "player_evidence_v2"
COUNT_TARGETS = [
    "shots_total", "shots_on", "goals_total", "goals_assists",
    "dribbles_attempts", "dribbles_success", "passes_total", "passes_key",
    "tackles_total", "tackles_blocks", "tackles_interceptions",
    "duels_total", "duels_won", "goals_saves", "goals_conceded",
    "fouls_committed", "cards_yellow", "cards_red", "penalty_won",
    "penalty_commited", "penalty_scored", "penalty_missed", "penalty_saved",
]
BINARY_EVENT_TARGETS = [
    "goals_total", "goals_assists", "cards_yellow", "cards_red",
    "penalty_won", "penalty_commited", "penalty_scored",
    "penalty_missed", "penalty_saved",
]
CONTINUOUS_TARGETS = ["minutes", "rating", "passes_accuracy"]
INDIVIDUAL_FEATURE_COLUMNS = [
    "attack_impact", "defense_impact", "discipline_impact",
    "availability_impact", "net_impact", "is_starter", "is_goalkeeper",
    "is_defender", "is_midfielder", "is_forward",
]
BASELINE_FEATURE_COLUMNS = [
    column
    for column in PLAYER_MODEL_FEATURE_COLUMNS
    if not any(
        token in column
        for token in (
            "starter_", "goalkeeper_", "bench_", "discipline_",
            "delta_attack", "delta_defense", "delta_midfield",
            "delta_goalkeeper", "delta_bench", "delta_discipline",
        )
    )
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _prediction_for_match(connection: sqlite3.Connection, match_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM predictions WHERE match_id = ? ORDER BY id DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return dict(row) if row else {}


def _snapshot_payload(
    match: dict[str, Any],
    feature_row: dict[str, Any],
    prediction: dict[str, Any],
    window_label: str,
    source_kind: str,
    captured_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    home_players = list(feature_row.get("home_player_impacts") or [])
    away_players = list(feature_row.get("away_player_impacts") or [])
    features = {
        key: value
        for key, value in feature_row.items()
        if key not in {"home_player_impacts", "away_player_impacts"}
    }
    player_rows = home_players + away_players
    fingerprint = {
        "match_id": match["match_id"],
        "window_label": window_label,
        "source_kind": source_kind,
        "features": features,
        "prediction": prediction,
        "players": player_rows,
    }
    snapshot = {
        "match_id": str(match["match_id"]),
        "fixture_id": (
            str(int(match["api_fixture_id"]))
            if match.get("api_fixture_id") not in (None, "") and str(match.get("api_fixture_id")) != "nan"
            else None
        ),
        "window_label": window_label,
        "source_kind": source_kind,
        "kickoff_at": str(match["datetime_cdmx"]),
        "captured_at": captured_at,
        "features_json": _canonical_json(features),
        "prediction_json": _canonical_json(prediction),
        "home_lineup_source": feature_row.get("home_lineup_source"),
        "away_lineup_source": feature_row.get("away_lineup_source"),
        "model_version": prediction.get("model_version"),
        "outcome_model_version": prediction.get("outcome_model_version"),
        "data_hash": _hash_payload(fingerprint),
    }
    return snapshot, player_rows


def capture_pre_match_snapshot(
    match_id: str,
    window_label: str,
    connection: sqlite3.Connection,
    source_kind: str = "live",
    captured_at: str | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    context = competition_context or resolve_competition_context()
    match_df = fetch_dataframe(
        connection,
        """
        SELECT match_id, date_cdmx, datetime_cdmx, group_name AS "group",
               home_team, away_team, home_team_norm, away_team_norm,
               stage, api_fixture_id
        FROM matches
        WHERE match_id = ?
          AND competition_id = ?
          AND season_id = ?
        """,
        (match_id, context.competition_id, context.season_id),
    )
    if match_df.empty:
        return {"created": False, "reason": "match_not_found", "match_id": match_id}
    match = match_df.iloc[0].to_dict()
    feature_row = build_match_feature_row(
        connection,
        match,
        allow_season_stats=source_kind == "live",
    )
    timestamp = captured_at or datetime.now(timezone.utc).isoformat()
    snapshot, players = _snapshot_payload(
        match,
        feature_row,
        _prediction_for_match(connection, match_id),
        window_label,
        source_kind,
        timestamp,
    )
    snapshot["competition_id"] = context.competition_id
    snapshot["season_id"] = context.season_id
    snapshot_id, created = insert_pre_match_snapshot(connection, snapshot, players)
    return {
        "snapshot_id": snapshot_id,
        "created": created,
        "match_id": match_id,
        "window_label": window_label,
        "players": len(players),
        "data_hash": snapshot["data_hash"],
    }


def reconstruct_historical_snapshots(
    connection: sqlite3.Connection,
    limit: int | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, int]:
    context = competition_context or resolve_competition_context()
    query = """
        SELECT fixture_id, match_date, home_team, away_team,
               home_team_norm, away_team_norm
        FROM historical_matches
        WHERE match_date IS NOT NULL
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
        ORDER BY match_date, fixture_id
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    matches = fetch_dataframe(connection, query)
    created = 0
    skipped = 0
    for row in matches.to_dict(orient="records"):
        kickoff = pd.Timestamp(row["match_date"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.tz_localize("UTC")
        match = {
            "match_id": f"historical_{row['fixture_id']}",
            "date_cdmx": kickoff.date().isoformat(),
            "datetime_cdmx": kickoff.isoformat(),
            "group": "historical",
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_team_norm": row["home_team_norm"],
            "away_team_norm": row["away_team_norm"],
            "stage": "historical",
            "api_fixture_id": row["fixture_id"],
        }
        feature_row = build_match_feature_row(
            connection,
            match,
            allow_season_stats=False,
        )
        captured_at = (kickoff - timedelta(minutes=1)).isoformat()
        snapshot, players = _snapshot_payload(
            match,
            feature_row,
            {},
            "historical-t-1",
            "reconstructed",
            captured_at,
        )
        snapshot["competition_id"] = context.competition_id
        snapshot["season_id"] = context.season_id
        _, was_created = insert_pre_match_snapshot(connection, snapshot, players)
        created += int(was_created)
        skipped += int(not was_created)
    return {"created": created, "skipped": skipped}


def _target_value(row: dict[str, Any] | None, column: str) -> float | None:
    if row is None:
        return 0.0
    value = row.get(column)
    if column in {"rating", "passes_accuracy"} and value is None:
        return None
    return _safe_float(value)


def build_player_targets(
    connection: sqlite3.Connection,
    match_ids: list[str] | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    context = competition_context or resolve_competition_context()
    params: list[Any] = [context.competition_id, context.season_id]
    where = "WHERE s.competition_id = ? AND s.season_id = ?"
    if match_ids:
        placeholders = ",".join("?" for _ in match_ids)
        where += f" AND s.match_id IN ({placeholders})"
        params.extend(match_ids)
    snapshots = fetch_dataframe(
        connection,
        f"""
        SELECT s.*
        FROM pre_match_snapshots s
        INNER JOIN (
            SELECT match_id, MAX(julianday(captured_at)) AS captured_at_jd
            FROM pre_match_snapshots
            WHERE julianday(captured_at) < julianday(kickoff_at)
            GROUP BY match_id
        ) latest
          ON latest.match_id = s.match_id
         AND latest.captured_at_jd = julianday(s.captured_at)
        {where}
        ORDER BY s.kickoff_at, s.id
        """,
        params,
    )
    rows: list[dict[str, Any]] = []
    complete_matches: list[str] = []
    for snapshot in snapshots.to_dict(orient="records"):
        fixture_id = str(snapshot.get("fixture_id") or "")
        if not fixture_id:
            continue
        players = fetch_dataframe(
            connection,
            "SELECT * FROM pre_match_player_snapshots WHERE snapshot_id = ?",
            (snapshot["id"],),
        )
        actual = fetch_dataframe(
            connection,
            "SELECT * FROM fixture_player_stats WHERE fixture_id = ?",
            (fixture_id,),
        )
        actual_records = actual.to_dict(orient="records")
        by_id = {
            int(row["api_player_id"]): row
            for row in actual_records
            if row.get("api_player_id") is not None
        }
        by_name = {
            (str(row["team_norm"]), normalize_text(str(row["player_name"]))): row
            for row in actual_records
        }
        participant_counts = (
            actual[actual["minutes"].fillna(0) > 0]
            .groupby("team_norm")
            .size()
            .to_dict()
            if not actual.empty
            else {}
        )
        expected_teams = set(players["team_norm"].tolist()) if not players.empty else set()
        official_lineups = (
            snapshot.get("home_lineup_source") == "confirmed_lineup"
            and snapshot.get("away_lineup_source") == "confirmed_lineup"
        )
        if (
            official_lineups
            and expected_teams
            and all(int(participant_counts.get(team, 0)) >= 11 for team in expected_teams)
        ):
            complete_matches.append(str(snapshot["match_id"]))
        for player in players.to_dict(orient="records"):
            actual_row = None
            if (
                player.get("api_player_id") is not None
                and not pd.isna(player.get("api_player_id"))
            ):
                actual_row = by_id.get(int(player["api_player_id"]))
            if actual_row is None:
                actual_row = by_name.get(
                    (
                        str(player["team_norm"]),
                        normalize_text(str(player["player_name"])),
                    )
                )
            minutes = _safe_float(actual_row.get("minutes")) if actual_row else 0.0
            target = {
                "match_id": snapshot["match_id"],
                "fixture_id": fixture_id,
                "snapshot_id": int(snapshot["id"]),
                "team_norm": player["team_norm"],
                "api_player_id": player.get("api_player_id"),
                "player_name": player["player_name"],
                "player_norm": player.get("player_norm"),
                "participated": int(minutes > 0),
                "minutes": minutes,
            }
            for column in COUNT_TARGETS + ["rating", "passes_accuracy"]:
                target[column] = _target_value(actual_row, column)
            target["target_json"] = target
            rows.append(target)
    inserted = insert_player_targets(connection, rows)
    return {
        "snapshots": int(len(snapshots)),
        "targets_considered": len(rows),
        "inserted": int(inserted),
        "complete_match_ids": sorted(set(complete_matches)),
    }


def _individual_dataset(connection: sqlite3.Connection) -> pd.DataFrame:
    frame = fetch_dataframe(
        connection,
        """
        SELECT s.match_id, s.kickoff_at, s.source_kind,
               ps.team_norm, ps.player_name, ps.lineup_role, ps.role_bucket,
               ps.attack_impact, ps.defense_impact, ps.discipline_impact,
               ps.availability_impact, ps.net_impact,
               t.*
        FROM player_match_targets t
        INNER JOIN pre_match_snapshots s ON s.id = t.snapshot_id
        INNER JOIN pre_match_player_snapshots ps
          ON ps.snapshot_id = t.snapshot_id
         AND ps.team_norm = t.team_norm
         AND ps.player_name = t.player_name
        ORDER BY s.kickoff_at, s.match_id, ps.team_norm, ps.player_name
        """,
    )
    if frame.empty:
        return frame
    frame["is_starter"] = (frame["lineup_role"] == "starter").astype(float)
    for role in ("goalkeeper", "defender", "midfielder", "forward"):
        frame[f"is_{role}"] = (frame["role_bucket"] == role).astype(float)
    return frame


def evaluate_player_predictions(
    connection: sqlite3.Connection,
    match_ids: list[str] | None = None,
) -> dict[str, int]:
    params: list[Any] = []
    match_filter = ""
    if match_ids:
        placeholders = ",".join("?" for _ in match_ids)
        match_filter = f"AND s.match_id IN ({placeholders})"
        params.extend(match_ids)
    rows = fetch_dataframe(
        connection,
        f"""
        SELECT
            s.id AS snapshot_id, s.match_id, s.source_kind,
            ps.team_norm, ps.player_name,
            ps.attack_impact, ps.defense_impact, ps.discipline_impact,
            ps.availability_impact,
            t.participated, t.minutes, t.shots_on, t.goals_total,
            t.goals_assists, t.dribbles_success, t.passes_key,
            t.passes_accuracy, t.tackles_total, t.tackles_blocks,
            t.tackles_interceptions, t.duels_won, t.goals_saves,
            t.goals_conceded, t.fouls_committed, t.cards_yellow,
            t.cards_red, t.penalty_commited
        FROM player_match_targets t
        INNER JOIN pre_match_snapshots s ON s.id = t.snapshot_id
        INNER JOIN pre_match_player_snapshots ps
          ON ps.snapshot_id = t.snapshot_id
         AND ps.team_norm = t.team_norm
         AND ps.player_name = t.player_name
        INNER JOIN (
            SELECT match_id, MAX(julianday(captured_at)) AS captured_at_jd
            FROM pre_match_snapshots
            WHERE julianday(captured_at) < julianday(kickoff_at)
            GROUP BY match_id
        ) latest
          ON latest.match_id = s.match_id
         AND latest.captured_at_jd = julianday(s.captured_at)
        WHERE 1 = 1 {match_filter}
        """,
        params,
    )
    evaluated_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with connection:
        for row in rows.to_dict(orient="records"):
            actual = {
                "attack": (
                    _safe_float(row.get("shots_on"))
                    + 2.0 * _safe_float(row.get("goals_total"))
                    + _safe_float(row.get("goals_assists"))
                    + _safe_float(row.get("dribbles_success"))
                ),
                "creation": (
                    _safe_float(row.get("passes_key"))
                    + _safe_float(row.get("goals_assists"))
                    + _safe_float(row.get("passes_accuracy")) / 100.0
                ),
                "defense": (
                    _safe_float(row.get("tackles_total"))
                    + _safe_float(row.get("tackles_blocks"))
                    + _safe_float(row.get("tackles_interceptions"))
                    + _safe_float(row.get("duels_won"))
                ),
                "goalkeeping": (
                    _safe_float(row.get("goals_saves"))
                    - _safe_float(row.get("goals_conceded"))
                ),
                "discipline": (
                    _safe_float(row.get("fouls_committed"))
                    + 2.0 * _safe_float(row.get("cards_yellow"))
                    + 5.0 * _safe_float(row.get("cards_red"))
                    + 2.0 * _safe_float(row.get("penalty_commited"))
                ),
            }
            metrics = {
                "predicted": {
                    "attack": _safe_float(row.get("attack_impact")),
                    "defense": _safe_float(row.get("defense_impact")),
                    "discipline": _safe_float(row.get("discipline_impact")),
                    "availability": _safe_float(row.get("availability_impact")),
                },
                "actual": actual,
                "participated": int(row.get("participated") or 0),
                "minutes": _safe_float(row.get("minutes")),
            }
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO player_prediction_evaluations (
                    snapshot_id, match_id, team_norm, player_name, source_kind,
                    participated, minutes, predicted_attack, predicted_defense,
                    predicted_discipline, predicted_availability,
                    actual_attack, actual_creation, actual_defense,
                    actual_goalkeeping, actual_discipline,
                    metrics_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["snapshot_id"]),
                    str(row["match_id"]),
                    str(row["team_norm"]),
                    str(row["player_name"]),
                    str(row["source_kind"]),
                    int(row.get("participated") or 0),
                    _safe_float(row.get("minutes")),
                    _safe_float(row.get("attack_impact")),
                    _safe_float(row.get("defense_impact")),
                    _safe_float(row.get("discipline_impact")),
                    _safe_float(row.get("availability_impact")),
                    actual["attack"],
                    actual["creation"],
                    actual["defense"],
                    actual["goalkeeping"],
                    actual["discipline"],
                    _canonical_json(metrics),
                    evaluated_at,
                ),
            )
            inserted += int(cursor.rowcount == 1)
    return {"considered": len(rows), "inserted": inserted}


def _match_dataset(connection: sqlite3.Connection) -> pd.DataFrame:
    snapshots = fetch_dataframe(
        connection,
        """
        SELECT s.*
        FROM pre_match_snapshots s
        INNER JOIN (
            SELECT match_id, MAX(julianday(captured_at)) AS captured_at_jd
            FROM pre_match_snapshots
            WHERE julianday(captured_at) < julianday(kickoff_at)
            GROUP BY match_id
        ) latest
          ON latest.match_id = s.match_id
         AND latest.captured_at_jd = julianday(s.captured_at)
        ORDER BY s.kickoff_at, s.match_id
        """,
    )
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots.to_dict(orient="records"):
        if (
            snapshot.get("home_lineup_source") != "confirmed_lineup"
            or snapshot.get("away_lineup_source") != "confirmed_lineup"
        ):
            continue
        complete = connection.execute(
            """
            SELECT team_norm, SUM(CASE WHEN participated = 1 THEN 1 ELSE 0 END) AS participants
            FROM player_match_targets WHERE snapshot_id = ?
            GROUP BY team_norm
            """,
            (snapshot["id"],),
        ).fetchall()
        if len(complete) < 2 or any(int(row["participants"]) < 11 for row in complete):
            continue
        actual = connection.execute(
            "SELECT home_goals, away_goals FROM actual_results WHERE match_id = ?",
            (snapshot["match_id"],),
        ).fetchone()
        if actual is None:
            actual = connection.execute(
                "SELECT home_goals, away_goals FROM historical_matches WHERE fixture_id = ?",
                (snapshot["fixture_id"],),
            ).fetchone()
        if actual is None:
            continue
        features = json.loads(snapshot["features_json"])
        home_goals = int(actual["home_goals"])
        away_goals = int(actual["away_goals"])
        outcome = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
        rows.append(
            {
                "snapshot_id": int(snapshot["id"]),
                "match_id": snapshot["match_id"],
                "kickoff_at": snapshot["kickoff_at"],
                "source_kind": snapshot["source_kind"],
                **{column: _safe_float(features.get(column)) for column in PLAYER_MODEL_FEATURE_COLUMNS},
                "home_goals": home_goals,
                "away_goals": away_goals,
                "outcome": outcome,
            }
        )
    return pd.DataFrame(rows)


def temporal_match_splits(
    match_count: int,
    minimum_train: int | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    minimum_train = minimum_train or max(20, match_count // 2)
    remaining = match_count - minimum_train
    if remaining < 3:
        return []
    test_size = max(3, remaining // 3)
    splits = []
    train_end = minimum_train
    while train_end < match_count:
        test_end = min(train_end + test_size, match_count)
        splits.append((np.arange(train_end), np.arange(train_end, test_end)))
        train_end = test_end
    return splits


def _goal_models(features: list[str]) -> tuple[PoissonRegressor, PoissonRegressor]:
    return (
        PoissonRegressor(alpha=0.2, max_iter=500),
        PoissonRegressor(alpha=0.2, max_iter=500),
    )


def _outcome_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("logit", LogisticRegression(C=0.1, penalty="l2", solver="lbfgs", class_weight="balanced", max_iter=1000)),
        ]
    )


def _calibration_error(labels: list[str], probabilities: np.ndarray) -> float:
    errors: list[float] = []
    labels_array = np.asarray(labels)
    for index, label in enumerate(OUTCOME_CLASSES):
        observed = (labels_array == label).astype(float)
        predicted = probabilities[:, index]
        for lower, upper in zip(np.linspace(0, 1, 6)[:-1], np.linspace(0, 1, 6)[1:]):
            mask = (predicted >= lower) & (predicted <= upper if upper == 1 else predicted < upper)
            if mask.any():
                errors.append(abs(float(predicted[mask].mean() - observed[mask].mean())))
    return float(np.mean(errors)) if errors else 0.0


def _brier(labels: list[str], probabilities: np.ndarray) -> float:
    encoded = np.zeros_like(probabilities)
    for row, label in enumerate(labels):
        encoded[row, OUTCOME_CLASSES.index(label)] = 1.0
    return float(np.mean(np.sum((probabilities - encoded) ** 2, axis=1)))


def _evaluate_feature_set(
    dataset: pd.DataFrame,
    features: list[str],
    minimum_train: int | None = None,
) -> dict[str, Any] | None:
    goal_actual: list[float] = []
    goal_predicted: list[float] = []
    outcome_labels: list[str] = []
    outcome_probabilities: list[np.ndarray] = []
    source_rows: list[str] = []
    goal_source_rows: list[str] = []
    for train_idx, test_idx in temporal_match_splits(len(dataset), minimum_train):
        train = dataset.iloc[train_idx]
        test = dataset.iloc[test_idx]
        if set(train["outcome"]) != set(OUTCOME_CLASSES):
            continue
        home_model, away_model = _goal_models(features)
        home_model.fit(train[features].fillna(0.0), train["home_goals"])
        away_model.fit(train[features].fillna(0.0), train["away_goals"])
        home_pred = np.clip(home_model.predict(test[features].fillna(0.0)), 1e-6, None)
        away_pred = np.clip(away_model.predict(test[features].fillna(0.0)), 1e-6, None)
        goal_actual.extend(test["home_goals"].tolist() + test["away_goals"].tolist())
        goal_predicted.extend(home_pred.tolist() + away_pred.tolist())
        goal_source_rows.extend(test["source_kind"].tolist() + test["source_kind"].tolist())
        outcome_model = _outcome_pipeline()
        outcome_model.fit(train[features].fillna(0.0), train["outcome"])
        raw = outcome_model.predict_proba(test[features].fillna(0.0))
        classes = list(outcome_model.named_steps["logit"].classes_)
        aligned = np.zeros((len(test), len(OUTCOME_CLASSES)))
        for class_index, label in enumerate(OUTCOME_CLASSES):
            aligned[:, class_index] = raw[:, classes.index(label)]
        outcome_labels.extend(test["outcome"].tolist())
        outcome_probabilities.extend(aligned)
        source_rows.extend(test["source_kind"].tolist())
    if not goal_actual or not outcome_labels:
        return None
    probabilities = np.vstack(outcome_probabilities)
    def metric_bundle(
        goals: list[float],
        predicted_goals: list[float],
        labels: list[str],
        outcome_probs: np.ndarray,
    ) -> dict[str, float]:
        return {
            "goal_mae": float(mean_absolute_error(goals, predicted_goals)),
            "goal_poisson_deviance": float(
                mean_poisson_deviance(goals, np.clip(predicted_goals, 1e-6, None))
            ),
            "log_loss": float(log_loss(labels, outcome_probs, labels=list(OUTCOME_CLASSES))),
            "brier_score": _brier(labels, outcome_probs),
            "calibration_error": _calibration_error(labels, outcome_probs),
            "validation_matches": len(labels),
        }

    metrics = metric_bundle(goal_actual, goal_predicted, outcome_labels, probabilities)
    metrics["source_counts"] = dict(pd.Series(source_rows).value_counts())
    metrics["by_source"] = {}
    for source in sorted(set(source_rows)):
        outcome_mask = np.asarray(source_rows) == source
        goal_mask = np.asarray(goal_source_rows) == source
        if not outcome_mask.any() or not goal_mask.any():
            continue
        source_labels = np.asarray(outcome_labels)[outcome_mask].tolist()
        source_probabilities = probabilities[outcome_mask]
        metrics["by_source"][source] = metric_bundle(
            np.asarray(goal_actual)[goal_mask].tolist(),
            np.asarray(goal_predicted)[goal_mask].tolist(),
            source_labels,
            source_probabilities,
        )
    return metrics


def should_promote(
    baseline: dict[str, float],
    candidate: dict[str, float],
    tolerance: float = 1e-9,
) -> tuple[bool, list[str]]:
    keys = ["goal_mae", "goal_poisson_deviance", "log_loss", "brier_score", "calibration_error"]
    reasons = [
        f"{key}_worse"
        for key in keys
        if float(candidate[key]) > float(baseline[key]) + tolerance
    ]
    deviance_gain = (
        float(baseline["goal_poisson_deviance"]) - float(candidate["goal_poisson_deviance"])
    ) / max(float(baseline["goal_poisson_deviance"]), 1e-9)
    log_loss_gain = (
        float(baseline["log_loss"]) - float(candidate["log_loss"])
    ) / max(float(baseline["log_loss"]), 1e-9)
    if max(deviance_gain, log_loss_gain) < 0.02:
        reasons.append("minimum_two_percent_gain_not_met")
    return not reasons, reasons


def _fit_individual_models(dataset: pd.DataFrame) -> dict[str, Any]:
    models: dict[str, Any] = {}
    if dataset.empty:
        return models
    x = dataset[INDIVIDUAL_FEATURE_COLUMNS].fillna(0.0)
    if dataset["participated"].nunique() > 1:
        participation = Pipeline(
            [("scale", StandardScaler()), ("model", LogisticRegression(C=0.1, class_weight="balanced", max_iter=1000))]
        )
        participation.fit(x, dataset["participated"])
        models["participated"] = participation
    participated = dataset[dataset["participated"] == 1]
    if participated.empty:
        return models
    px = participated[INDIVIDUAL_FEATURE_COLUMNS].fillna(0.0)
    for target in COUNT_TARGETS:
        target_values = participated[target].fillna(0.0).clip(lower=0.0)
        if float(target_values.sum()) <= 0.0 or target_values.nunique() == 1:
            model = DummyRegressor(strategy="constant", constant=float(target_values.mean()))
        else:
            model = PoissonRegressor(alpha=0.2, max_iter=500)
        model.fit(px, target_values)
        models[target] = model
        occurrence = (target_values > 0).astype(int)
        if occurrence.nunique() > 1 and target in BINARY_EVENT_TARGETS:
            event_model = Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=0.1, class_weight="balanced", max_iter=1000)),
                ]
            )
            event_model.fit(px, occurrence)
            models[f"{target}__event"] = event_model
    for target in CONTINUOUS_TARGETS:
        valid = participated[target].notna()
        if valid.any():
            model = Ridge(alpha=1.0)
            model.fit(px[valid], participated.loc[valid, target])
            models[target] = model
    return models


def _individual_metrics(
    dataset: pd.DataFrame,
    models: dict[str, Any],
) -> dict[str, Any]:
    if dataset.empty:
        return {}
    x = dataset[INDIVIDUAL_FEATURE_COLUMNS].fillna(0.0)
    metrics: dict[str, Any] = {}
    participation = models.get("participated")
    if participation is not None:
        probabilities = participation.predict_proba(x)[:, 1]
        labels = dataset["participated"].astype(int).to_numpy()
        metrics["participated"] = {
            "log_loss": float(log_loss(labels, np.column_stack([1 - probabilities, probabilities]), labels=[0, 1])),
            "brier_score": float(np.mean((probabilities - labels) ** 2)),
        }
    participated = dataset[dataset["participated"] == 1]
    if participated.empty:
        return metrics
    px = participated[INDIVIDUAL_FEATURE_COLUMNS].fillna(0.0)
    for target in COUNT_TARGETS:
        model = models.get(target)
        if model is None:
            continue
        actual = participated[target].fillna(0.0).clip(lower=0.0)
        predicted = np.clip(model.predict(px), 1e-6, None)
        metrics[target] = {
            "mae": float(mean_absolute_error(actual, predicted)),
            "poisson_deviance": float(mean_poisson_deviance(actual, predicted)),
        }
        event_model = models.get(f"{target}__event")
        if event_model is not None:
            event_labels = (actual > 0).astype(int).to_numpy()
            event_probabilities = event_model.predict_proba(px)[:, 1]
            metrics[target]["event_log_loss"] = float(
                log_loss(
                    event_labels,
                    np.column_stack([1 - event_probabilities, event_probabilities]),
                    labels=[0, 1],
                )
            )
            metrics[target]["event_brier_score"] = float(
                np.mean((event_probabilities - event_labels) ** 2)
            )
    for target in CONTINUOUS_TARGETS:
        model = models.get(target)
        valid = participated[target].notna()
        if model is not None and valid.any():
            predicted = model.predict(px[valid])
            metrics[target] = {
                "mae": float(mean_absolute_error(participated.loc[valid, target], predicted))
            }
    return metrics


def _store_individual_evaluations(
    connection: sqlite3.Connection,
    release_id: str,
    dataset: pd.DataFrame,
    models: dict[str, Any],
) -> int:
    if dataset.empty:
        return 0
    x = dataset[INDIVIDUAL_FEATURE_COLUMNS].fillna(0.0)
    participation = models.get("participated")
    participation_probabilities = (
        participation.predict_proba(x)[:, 1]
        if participation is not None
        else np.full(len(dataset), float(dataset["participated"].mean()))
    )
    rows = []
    for position, (_, row) in enumerate(dataset.iterrows()):
        features = x.iloc[[position]]
        diagnostics: dict[str, Any] = {
            "evaluation_kind": "release_fit_diagnostic",
            "participated_actual": int(row["participated"]),
            "participated_probability": float(participation_probabilities[position]),
        }
        if int(row["participated"]) == 1:
            for target in COUNT_TARGETS + CONTINUOUS_TARGETS:
                model = models.get(target)
                if model is None or pd.isna(row.get(target)):
                    continue
                predicted = float(model.predict(features)[0])
                if target in COUNT_TARGETS:
                    predicted = max(predicted, 0.0)
                diagnostics[target] = {
                    "actual": float(row[target]),
                    "predicted": predicted,
                    "absolute_error": abs(float(row[target]) - predicted),
                }
                event_model = models.get(f"{target}__event")
                if event_model is not None:
                    diagnostics[target]["event_probability"] = float(
                        event_model.predict_proba(features)[0, 1]
                    )
        rows.append(
            (
                release_id,
                int(row["snapshot_id"]),
                str(row["match_id"]),
                str(row["team_norm"]),
                str(row["player_name"]),
                _canonical_json(diagnostics),
            )
        )
    before = connection.total_changes
    with connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO player_evidence_evaluations (
                release_id, snapshot_id, match_id, team_norm, player_name, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return connection.total_changes - before


def _write_report(release_dir: Path, metadata: dict[str, Any]) -> Path:
    baseline = metadata["metrics"]["baseline"]
    candidate = metadata["metrics"]["candidate"]
    lines = [
        "# Player Evidence Model Report",
        "",
        f"- Release: {metadata['release_id']}",
        f"- Dataset hash: {metadata['dataset_hash']}",
        f"- Training matches: {metadata['training_matches']}",
        f"- Live matches: {metadata['live_matches']}",
        f"- Promoted: {metadata['promoted']}",
        f"- Reasons: {', '.join(metadata['promotion_reasons']) or 'gate passed'}",
        "",
        "## Baseline without player variables",
        *[f"- {key}: {value}" for key, value in baseline.items()],
        "",
        "## Candidate with player variables",
        *[f"- {key}: {value}" for key, value in candidate.items()],
    ]
    path = release_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def train_player_evidence(
    connection: sqlite3.Connection,
    releases_dir: Path | None = None,
    min_matches: int = 30,
    min_new_matches: int = 5,
    auto_promote: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    releases_dir = releases_dir or settings.model_artifacts_dir / "releases"
    dataset = _match_dataset(connection)
    if len(dataset) < min_matches:
        return {
            "trained": False,
            "reason": "insufficient_complete_matches",
            "matches": int(len(dataset)),
            "required": min_matches,
        }
    latest = connection.execute(
        "SELECT match_count FROM model_training_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_count = int(latest["match_count"]) if latest else 0
    new_matches = len(dataset) - previous_count
    if new_matches < min_new_matches:
        return {
            "trained": False,
            "reason": "insufficient_new_matches",
            "matches": int(len(dataset)),
            "new_matches": int(new_matches),
            "required_new": min_new_matches,
        }
    dataset_hash = _hash_payload(
        dataset[["match_id", "kickoff_at", "source_kind", "home_goals", "away_goals"]].to_dict(orient="records")
    )
    existing = connection.execute(
        "SELECT status, release_id FROM model_training_runs WHERE dataset_hash = ?",
        (dataset_hash,),
    ).fetchone()
    if existing:
        return {
            "trained": False,
            "reason": "dataset_already_processed",
            "status": existing["status"],
            "release_id": existing["release_id"],
        }
    cutoff_at = str(dataset["kickoff_at"].max())
    with connection:
        connection.execute(
            """
            INSERT INTO model_training_runs (
                dataset_hash, status, cutoff_at, match_count, new_match_count,
                source_counts_json
            ) VALUES (?, 'running', ?, ?, ?, ?)
            """,
            (
                dataset_hash,
                cutoff_at,
                len(dataset),
                new_matches,
                _canonical_json(dataset["source_kind"].value_counts().to_dict()),
            ),
        )
    baseline = _evaluate_feature_set(dataset, BASELINE_FEATURE_COLUMNS)
    candidate = _evaluate_feature_set(dataset, PLAYER_MODEL_FEATURE_COLUMNS)
    if baseline is None or candidate is None:
        with connection:
            connection.execute(
                """
                UPDATE model_training_runs
                SET status = 'rejected', reason = ?, finished_at = CURRENT_TIMESTAMP
                WHERE dataset_hash = ?
                """,
                ("insufficient_temporal_folds", dataset_hash),
            )
        return {"trained": False, "reason": "insufficient_temporal_folds"}
    promoted, reasons = should_promote(baseline, candidate)
    live_matches = int((dataset["source_kind"] == "live").sum())
    if live_matches >= 10:
        live_dataset = dataset[dataset["source_kind"] == "live"].reset_index(drop=True)
        live_baseline = _evaluate_feature_set(
            live_dataset,
            BASELINE_FEATURE_COLUMNS,
            minimum_train=6,
        )
        live_candidate = _evaluate_feature_set(
            live_dataset,
            PLAYER_MODEL_FEATURE_COLUMNS,
            minimum_train=6,
        )
        if live_baseline and live_candidate:
            live_promoted, live_reasons = should_promote(live_baseline, live_candidate)
            if not live_promoted:
                promoted = False
                reasons.extend(f"live_{reason}" for reason in live_reasons)
        else:
            promoted = False
            reasons.append("live_validation_unavailable")
    release_id = f"{MODEL_VERSION}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{dataset_hash[:8]}"
    release_dir = releases_dir / release_id
    if release_dir.exists():
        raise FileExistsError(f"Release directory already exists: {release_dir}")
    release_dir.mkdir(parents=True)
    home_model, away_model = _goal_models(PLAYER_MODEL_FEATURE_COLUMNS)
    x = dataset[PLAYER_MODEL_FEATURE_COLUMNS].fillna(0.0)
    home_model.fit(x, dataset["home_goals"])
    away_model.fit(x, dataset["away_goals"])
    outcome_model = _outcome_pipeline()
    outcome_model.fit(x, dataset["outcome"])
    individual_dataset = _individual_dataset(connection)
    individual_models = _fit_individual_models(individual_dataset)
    models = {
        "model_version": release_id,
        "feature_columns": PLAYER_MODEL_FEATURE_COLUMNS,
        "home_regressor": home_model,
        "away_regressor": away_model,
        "outcome_pipeline": outcome_model,
        "outcome_feature_columns": PLAYER_MODEL_FEATURE_COLUMNS,
        "individual_feature_columns": INDIVIDUAL_FEATURE_COLUMNS,
        "individual_models": individual_models,
    }
    with (release_dir / "models.pkl").open("wb") as handle:
        pickle.dump(models, handle)
    metadata = {
        "release_id": release_id,
        "model_version": MODEL_VERSION,
        "dataset_hash": dataset_hash,
        "cutoff_at": cutoff_at,
        "training_matches": len(dataset),
        "live_matches": live_matches,
        "source_counts": dataset["source_kind"].value_counts().to_dict(),
        "feature_columns": PLAYER_MODEL_FEATURE_COLUMNS,
        "metrics": {
            "baseline": baseline,
            "candidate": candidate,
            "individual": _individual_metrics(individual_dataset, individual_models),
        },
        "promoted": bool(promoted and auto_promote),
        "promotion_reasons": reasons,
        "preliminary": len(dataset) < 120 or live_matches < 10,
    }
    (release_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    report_path = _write_report(release_dir, metadata)
    register_model_release(
        connection,
        {
            **metadata,
            "release_path": str(release_dir),
            "status": "candidate" if promoted else "rejected",
            "metrics": metadata["metrics"],
        },
    )
    if promoted and auto_promote:
        activate_model_release(connection, release_id, releases_dir)
    evaluations_inserted = _store_individual_evaluations(
        connection,
        release_id,
        individual_dataset,
        individual_models,
    )
    with connection:
        connection.execute(
            """
            UPDATE model_training_runs
            SET status = 'completed', metrics_json = ?, release_id = ?,
                reason = ?, finished_at = CURRENT_TIMESTAMP
            WHERE dataset_hash = ?
            """,
            (_canonical_json(metadata["metrics"]), release_id, ",".join(reasons), dataset_hash),
        )
    return {
        "trained": True,
        "release_id": release_id,
        "release_path": str(release_dir),
        "report_path": str(report_path),
        "promoted": bool(promoted and auto_promote),
        "promotion_reasons": reasons,
        "metrics": metadata["metrics"],
        "evaluations_inserted": evaluations_inserted,
    }


def evaluate_model_release(
    connection: sqlite3.Connection,
    release_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM model_releases WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    if row is None:
        return {"found": False, "release_id": release_id}
    metadata_path = Path(row["release_path"]) / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {"found": True, **metadata, "status": row["status"]}


def activate_model_release(
    connection: sqlite3.Connection,
    release_id: str,
    releases_dir: Path | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    releases_dir = releases_dir or settings.model_artifacts_dir / "releases"
    row = connection.execute(
        "SELECT * FROM model_releases WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    if row is None:
        return {"activated": False, "reason": "release_not_found", "release_id": release_id}
    if row["status"] == "rejected":
        return {"activated": False, "reason": "release_rejected", "release_id": release_id}
    release_path = Path(row["release_path"])
    if not (release_path / "models.pkl").exists() or not (release_path / "metadata.json").exists():
        return {"activated": False, "reason": "release_artifacts_missing", "release_id": release_id}
    previous = get_active_model_release(connection)
    previous_release_id = str(previous["release_id"]) if previous else None
    if not activate_db_release(connection, release_id):
        return {"activated": False, "reason": "db_activation_failed", "release_id": release_id}
    manifest = {
        "release_id": release_id,
        "release_path": row["release_path"],
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    releases_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = releases_dir / "active.json"
    temporary = releases_dir / f".active-{os.getpid()}.tmp"
    try:
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temporary, manifest_path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if previous_release_id:
            activate_db_release(connection, previous_release_id)
        else:
            with connection:
                connection.execute(
                    """
                    UPDATE model_releases
                    SET status = 'candidate', activated_at = NULL
                    WHERE release_id = ?
                    """,
                    (release_id,),
                )
        raise
    return {"activated": True, **manifest, "manifest_path": str(manifest_path)}


def load_active_release_bundle() -> dict[str, Any] | None:
    settings = get_settings()
    manifest_path = settings.model_artifacts_dir / "releases" / "active.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_path = Path(manifest["release_path"]) / "models.pkl"
    if not model_path.exists():
        return None
    with model_path.open("rb") as handle:
        return pickle.load(handle)


def finalize_player_evidence(
    match_ids: list[str],
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    targets = build_player_targets(connection, match_ids)
    player_evaluations = evaluate_player_predictions(connection, match_ids)
    dataset = _match_dataset(connection)
    last_match = str(dataset["match_id"].iloc[-1]) if not dataset.empty else "none"
    run_key = f"player-evidence-train:{len(dataset)}:{last_match}"
    claimed = claim_automation_run(
        connection,
        run_key=run_key,
        action="train_player_evidence",
        scheduled_for=datetime.now(timezone.utc).isoformat(),
        details={"complete_matches": len(dataset), "match_ids": match_ids},
    )
    if not claimed:
        training = {"trained": False, "reason": "automation_attempt_already_recorded"}
    else:
        try:
            training = train_player_evidence(connection)
        except Exception as exc:
            finish_automation_run(
                connection,
                run_key,
                "failed",
                {"error": str(exc), "match_ids": match_ids},
            )
            raise
        finish_automation_run(connection, run_key, "completed", training)
    return {
        "targets": targets,
        "player_evaluations": player_evaluations,
        "training": training,
    }
