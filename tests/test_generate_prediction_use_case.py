from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quiniela.application.use_cases.generate_prediction import GeneratePredictionCommand, GeneratePredictionUseCase


class FakePredictor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.date_predictions = pd.DataFrame([{"home_team": "Mexico", "away_team": "Canada"}])
        self.match_predictions = pd.DataFrame([{"home_team": "Canada", "away_team": "Mexico"}])

    def predict_by_date(
        self,
        date_str: str,
        prediction_context: str = "manual",
        window_label: str | None = None,
    ) -> tuple[pd.DataFrame, Path]:
        self.calls.append(
            (
                "predict_by_date",
                (date_str,),
                {"prediction_context": prediction_context, "window_label": window_label},
            )
        )
        return self.date_predictions, Path("outputs/predictions/predictions_latest.csv")

    def predict_match(self, home_team: str, away_team: str) -> pd.DataFrame:
        self.calls.append(("predict_match", (home_team, away_team), {}))
        return self.match_predictions


def test_generate_prediction_by_date_delegates_to_predictor() -> None:
    predictor = FakePredictor()
    use_case = GeneratePredictionUseCase(predictor=predictor)

    result = use_case.execute(
        GeneratePredictionCommand(date="2026-06-11", prediction_context="manual", window_label="t-60")
    )

    assert result.predictions is predictor.date_predictions
    assert result.output_path == Path("outputs/predictions/predictions_latest.csv")
    assert predictor.calls == [
        (
            "predict_by_date",
            ("2026-06-11",),
            {"prediction_context": "manual", "window_label": "t-60"},
        )
    ]


def test_generate_prediction_by_match_delegates_to_predictor() -> None:
    predictor = FakePredictor()
    use_case = GeneratePredictionUseCase(predictor=predictor)

    result = use_case.execute(GeneratePredictionCommand(home="Mexico", away="Canada"))

    assert result.predictions is predictor.match_predictions
    assert result.output_path is None
    assert predictor.calls == [("predict_match", ("Mexico", "Canada"), {})]


def test_generate_prediction_requires_date_or_teams() -> None:
    use_case = GeneratePredictionUseCase(predictor=FakePredictor())

    with pytest.raises(ValueError, match="date"):
        use_case.execute(GeneratePredictionCommand())
