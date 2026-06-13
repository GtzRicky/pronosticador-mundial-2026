from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection, insert_prediction_rows
from quiniela.features import build_features_for_matches
from quiniela.logging_utils import get_logger
from quiniela.name_maps import normalize_team_name
from quiniela.outcome_model import (
    hybridize_score_matrix,
    load_outcome_model_artifact,
    poisson_outcome_probabilities,
    predict_outcome_probabilities,
)
from quiniela.player_model import load_player_model_artifact, persist_prediction_impacts
from quiniela.poisson_model import PoissonScoreModel
from quiniela.ratings import apply_ratings


logger = get_logger(__name__)


class Predictor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.connection = get_connection(self.settings.db_path)
        trained_bundle = load_player_model_artifact()
        self.model = PoissonScoreModel(trained_bundle=trained_bundle)
        self.outcome_bundle = load_outcome_model_artifact()

    def _matches_for_date(self, date_str: str) -> pd.DataFrame:
        return fetch_dataframe(
            self.connection,
            """
            SELECT
                match_id, date_cdmx, datetime_cdmx, group_name AS "group", home_team, away_team,
                home_team_norm, away_team_norm, stage, status, api_fixture_id
            FROM matches
            WHERE date_cdmx = ?
            ORDER BY datetime_cdmx, home_team
            """,
            (date_str,),
        )

    def _match_for_teams(self, home_team: str, away_team: str) -> pd.DataFrame:
        return fetch_dataframe(
            self.connection,
            """
            SELECT
                match_id, date_cdmx, datetime_cdmx, group_name AS "group", home_team, away_team,
                home_team_norm, away_team_norm, stage, status, api_fixture_id
            FROM matches
            WHERE home_team_norm = ? AND away_team_norm = ?
            ORDER BY datetime_cdmx
            LIMIT 1
            """,
            (normalize_team_name(home_team), normalize_team_name(away_team)),
        )

    def _synthetic_match(self, home_team: str, away_team: str) -> pd.DataFrame:
        now = datetime.now().astimezone().isoformat()
        row = {
            "match_id": f"manual_{normalize_team_name(home_team)}_{normalize_team_name(away_team)}",
            "date_cdmx": now[:10],
            "datetime_cdmx": now,
            "group": "manual",
            "home_team": home_team,
            "away_team": away_team,
            "home_team_norm": normalize_team_name(home_team),
            "away_team_norm": normalize_team_name(away_team),
            "stage": "manual",
            "status": "manual",
            "api_fixture_id": None,
        }
        return pd.DataFrame([row])

    def _predict_from_matches(self, matches_df: pd.DataFrame) -> pd.DataFrame:
        features_df = build_features_for_matches(self.connection, matches_df)
        rated_df = apply_ratings(features_df)
        rows: list[dict[str, Any]] = []
        generated_at = datetime.utcnow().isoformat()

        for row in rated_df.to_dict(orient="records"):
            score = self.model.predict_score(row)
            logit_probabilities = predict_outcome_probabilities(self.outcome_bundle, row)
            outcome_probabilities = logit_probabilities or poisson_outcome_probabilities(score.matrix)
            hybrid_matrix = hybridize_score_matrix(score.matrix, outcome_probabilities)
            hybrid_home_goals, hybrid_away_goals = np.unravel_index(
                np.argmax(hybrid_matrix),
                hybrid_matrix.shape,
            )
            data_freshness_at = self._data_freshness_at(row.get("api_fixture_id"), generated_at)
            home_impacts = row.get("home_player_impacts") or []
            away_impacts = row.get("away_player_impacts") or []
            persist_prediction_impacts(self.connection, row["match_id"], home_impacts, away_impacts)
            rows.append(
                {
                    "match_id": row["match_id"],
                    "datetime_cdmx": row["datetime_cdmx"],
                    "group": row["group"],
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "predicted_score": f"{score.home_goals}-{score.away_goals}",
                    "probability": score.probability,
                    "model_version": self.model.model_version,
                    "generated_at": generated_at,
                    "home_goals": score.home_goals,
                    "away_goals": score.away_goals,
                    "lambda_home": score.lambda_home,
                    "lambda_away": score.lambda_away,
                    "hybrid_predicted_score": f"{int(hybrid_home_goals)}-{int(hybrid_away_goals)}",
                    "hybrid_probability": float(
                        hybrid_matrix[hybrid_home_goals, hybrid_away_goals]
                    ),
                    "home_win_probability": outcome_probabilities["home"],
                    "draw_probability": outcome_probabilities["draw"],
                    "away_win_probability": outcome_probabilities["away"],
                    "outcome_model_version": (
                        (
                            f"{self.outcome_bundle.get('model_version')} (preliminar)"
                            if self.outcome_bundle.get("preliminary")
                            else self.outcome_bundle.get("model_version")
                        )
                        if logit_probabilities and self.outcome_bundle
                        else "poisson_fallback"
                    ),
                    "data_freshness_at": data_freshness_at,
                    "home_lineup_source": row.get("home_lineup_source"),
                    "away_lineup_source": row.get("away_lineup_source"),
                    "home_top_impacts_json": json.dumps(
                        sorted(home_impacts, key=lambda impact: float(impact.get("net_impact", 0.0)), reverse=True)[:3],
                        ensure_ascii=False,
                    ),
                    "away_top_impacts_json": json.dumps(
                        sorted(away_impacts, key=lambda impact: float(impact.get("net_impact", 0.0)), reverse=True)[:3],
                        ensure_ascii=False,
                    ),
                }
            )
        predictions_df = pd.DataFrame(rows)
        if not predictions_df.empty:
            insert_prediction_rows(self.connection, predictions_df)
        return predictions_df

    def _data_freshness_at(self, fixture_id: Any, fallback: str) -> str:
        if fixture_id is None or str(fixture_id) == "nan":
            return fallback
        fixture_key = str(int(fixture_id))
        freshness_df = fetch_dataframe(
            self.connection,
            """
            SELECT MAX(fetched_at) AS freshness_at
            FROM (
                SELECT fetched_at FROM historical_lineups WHERE fixture_id = ?
                UNION ALL
                SELECT generated_at AS fetched_at FROM lineup_estimates WHERE fixture_id = ?
                UNION ALL
                SELECT fetched_at FROM odds_snapshots WHERE fixture_id = ?
                UNION ALL
                SELECT fetched_at FROM historical_team_stats WHERE fixture_id = ?
                UNION ALL
                SELECT fetched_at FROM fixture_player_stats WHERE fixture_id = ?
            )
            """,
            (fixture_key, fixture_key, fixture_key, fixture_key, fixture_key),
        )
        if freshness_df.empty or pd.isna(freshness_df.iloc[0]["freshness_at"]):
            return fallback
        return str(freshness_df.iloc[0]["freshness_at"])

    def save_predictions_csv(self, predictions_df: pd.DataFrame, file_name: str) -> Path:
        path = self.settings.predictions_dir / file_name
        predictions_df.to_csv(path, index=False, encoding="utf-8")
        json_path = path.with_suffix(".json")
        json_path.write_text(
            predictions_df.to_json(orient="records", force_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def predict_by_date(self, date_str: str) -> tuple[pd.DataFrame, Path]:
        matches_df = self._matches_for_date(date_str)
        if matches_df.empty:
            raise ValueError(f"No se encontraron partidos para {date_str}.")
        predictions_df = self._predict_from_matches(matches_df)
        output_path = self.save_predictions_csv(predictions_df, f"predicciones_{date_str}.csv")
        return predictions_df, output_path

    def predict_match(self, home_team: str, away_team: str) -> pd.DataFrame:
        matches_df = self._match_for_teams(home_team, away_team)
        if matches_df.empty:
            matches_df = self._synthetic_match(home_team, away_team)
        return self._predict_from_matches(matches_df)


def format_prediction_lines(predictions_df: pd.DataFrame) -> list[str]:
    lines = []
    for _, row in predictions_df.iterrows():
        lines.append(f"{row['home_team']} {int(row['home_goals'])}-{int(row['away_goals'])} {row['away_team']}")
    return lines
