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
    attack_component = _clip(float(team_features.get("starter_attack_strength", 0.0)) * 0.6, -0.4, 0.8)
    defense_component = _clip(float(team_features.get("starter_defense_strength", 0.0)) * 0.5, -0.4, 0.7)
    midfield_component = _clip(float(team_features.get("starter_midfield_control", 0.0)) * 0.4, -0.3, 0.5)
    goalkeeper_component = _clip(float(team_features.get("goalkeeper_strength", 0.0)) * 0.6, -0.3, 0.5)
    bench_component = _clip(float(team_features.get("bench_impact", 0.0)) * 0.4, -0.2, 0.3)
    discipline_component = _clip(float(team_features.get("discipline_risk_penalty", 0.0)) * -0.5, -0.4, 0.2)
    return (
        gd_component
        + form_component
        + lineup_component
        + odds_component
        + host_component
        + attack_component
        + defense_component
        + midfield_component
        + goalkeeper_component
        + bench_component
        + discipline_component
    )


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
                    "starter_attack_strength": row[f"{prefix}_starter_attack_strength"],
                    "starter_defense_strength": row[f"{prefix}_starter_defense_strength"],
                    "starter_midfield_control": row[f"{prefix}_starter_midfield_control"],
                    "goalkeeper_strength": row[f"{prefix}_goalkeeper_strength"],
                    "bench_impact": row[f"{prefix}_bench_impact"],
                    "discipline_risk_penalty": row[f"{prefix}_discipline_risk_penalty"],
                }
            ),
            axis=1,
        )
    return rated
