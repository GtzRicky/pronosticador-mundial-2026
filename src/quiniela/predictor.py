from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection, insert_prediction_rows
from quiniela.features import build_features_for_matches
from quiniela.logging_utils import get_logger
from quiniela.name_maps import normalize_team_name
from quiniela.poisson_model import PoissonScoreModel
from quiniela.ratings import apply_ratings


logger = get_logger(__name__)


class Predictor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.connection = get_connection(self.settings.db_path)
        self.model = PoissonScoreModel()

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
                }
            )
        predictions_df = pd.DataFrame(rows)
        if not predictions_df.empty:
            insert_prediction_rows(self.connection, predictions_df)
        return predictions_df

    def save_predictions_csv(self, predictions_df: pd.DataFrame, file_name: str) -> Path:
        path = self.settings.predictions_dir / file_name
        predictions_df.to_csv(path, index=False, encoding="utf-8")
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
