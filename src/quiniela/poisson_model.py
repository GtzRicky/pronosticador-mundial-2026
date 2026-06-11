from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import poisson


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
    def __init__(self, max_goals: int = 5, model_version: str = "poisson_v1") -> None:
        self.max_goals = max_goals
        self.model_version = model_version

    def estimate_lambdas(self, row: dict[str, Any]) -> tuple[float, float]:
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
