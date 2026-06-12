from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import poisson

from quiniela.player_model import PLAYER_MODEL_FEATURE_COLUMNS


SCORE_PRIOR = {
    (0, 0): 1.10,
    (1, 0): 1.08,
    (1, 1): 1.12,
    (2, 0): 1.06,
    (2, 1): 1.05,
}


@dataclass
class ScorePrediction:
    home_goals: int
    away_goals: int
    probability: float
    lambda_home: float
    lambda_away: float
    matrix: np.ndarray


class PoissonScoreModel:
    def __init__(
        self,
        max_goals: int = 5,
        model_version: str = "poisson_v1",
        trained_bundle: dict[str, Any] | None = None,
    ) -> None:
        self.max_goals = max_goals
        self.model_version = trained_bundle.get("model_version", model_version) if trained_bundle else model_version
        self.trained_bundle = trained_bundle

    def estimate_lambdas(self, row: dict[str, Any]) -> tuple[float, float]:
        if self.trained_bundle is not None:
            feature_columns = self.trained_bundle.get("feature_columns", PLAYER_MODEL_FEATURE_COLUMNS)
            feature_frame = pd.DataFrame(
                [
                    {
                        column: float(row.get(column, 0.0) if row.get(column) is not None else 0.0)
                        for column in feature_columns
                    }
                ]
            )
            home_regressor = self.trained_bundle.get("home_regressor")
            away_regressor = self.trained_bundle.get("away_regressor")
            if home_regressor is not None and away_regressor is not None:
                lambda_home = float(home_regressor.predict(feature_frame)[0])
                lambda_away = float(away_regressor.predict(feature_frame)[0])
                return max(min(lambda_home, 2.8), 0.2), max(min(lambda_away, 2.8), 0.2)

        home_strength = float(row.get("home_team_strength", 0.0))
        away_strength = float(row.get("away_team_strength", 0.0))
        home_gf = float(row.get("home_gf_avg", 1.0))
        away_gf = float(row.get("away_gf_avg", 1.0))
        home_ga = float(row.get("home_ga_avg", 1.0))
        away_ga = float(row.get("away_ga_avg", 1.0))

        lambda_home = 1.10 + 0.35 * (home_gf - away_ga) + 0.30 * (home_strength - away_strength)
        lambda_away = 1.00 + 0.35 * (away_gf - home_ga) + 0.30 * (away_strength - home_strength)

        lambda_home = max(min(lambda_home, 2.8), 0.2)
        lambda_away = max(min(lambda_away, 2.8), 0.2)
        return lambda_home, lambda_away

    def score_matrix(self, lambda_home: float, lambda_away: float) -> np.ndarray:
        home_probs = poisson.pmf(np.arange(0, self.max_goals + 1), lambda_home)
        away_probs = poisson.pmf(np.arange(0, self.max_goals + 1), lambda_away)
        matrix = np.outer(home_probs, away_probs)
        for (home_goals, away_goals), weight in SCORE_PRIOR.items():
            if home_goals <= self.max_goals and away_goals <= self.max_goals:
                matrix[home_goals, away_goals] *= weight
        matrix /= matrix.sum()
        return matrix

    def predict_score(self, row: dict[str, Any]) -> ScorePrediction:
        lambda_home, lambda_away = self.estimate_lambdas(row)
        matrix = self.score_matrix(lambda_home, lambda_away)
        home_goals, away_goals = np.unravel_index(np.argmax(matrix), matrix.shape)
        return ScorePrediction(
            home_goals=int(home_goals),
            away_goals=int(away_goals),
            probability=float(matrix[home_goals, away_goals]),
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            matrix=matrix,
        )
