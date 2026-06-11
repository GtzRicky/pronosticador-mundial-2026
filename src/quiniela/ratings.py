from __future__ import annotations

from typing import Any

import pandas as pd


def _clip(value: float, lower: float, upper: float) -> float:
    return max(min(value, upper), lower)


def calculate_team_strength(team_features: dict[str, Any]) -> float:
    gd_component = _clip(float(team_features.get("gd_avg", 0.0)) * 0.4, -0.8, 0.8)
    form_component = _clip(float(team_features.get("recent_form_score", 0.0)) * 0.8, -0.6, 0.6)
    lineup_component = _clip(float(team_features.get("lineup_strength", 0.0)) * 1.5, -0.3, 0.3)
    odds_component = _clip(float(team_features.get("odds_adjustment", 0.0)) * 1.2, -0.3, 0.3)
    host_component = _clip(float(team_features.get("host_adjustment", 0.0)), -0.2, 0.2)
    return gd_component + form_component + lineup_component + odds_component + host_component


def apply_ratings(features_df: pd.DataFrame) -> pd.DataFrame:
    rated = features_df.copy()
    for prefix in ("home", "away"):
        rated[f"{prefix}_team_strength"] = rated.apply(
            lambda row: calculate_team_strength(
                {
                    "gd_avg": row[f"{prefix}_gd_avg"],
                    "recent_form_score": row[f"{prefix}_recent_form_score"],
                    "lineup_strength": row[f"{prefix}_lineup_strength"],
                    "odds_adjustment": row[f"{prefix}_odds_adjustment"],
                    "host_adjustment": row[f"{prefix}_host_adjustment"],
                }
            ),
            axis=1,
        )
    return rated
