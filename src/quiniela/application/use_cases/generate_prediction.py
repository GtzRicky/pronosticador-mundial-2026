from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pandas as pd


class PredictionFacade(Protocol):
    def predict_by_date(
        self,
        date_str: str,
        prediction_context: str = "manual",
        window_label: str | None = None,
    ) -> tuple[pd.DataFrame, Path]:
        """Generate predictions for a CDMX date."""

    def predict_match(self, home_team: str, away_team: str) -> pd.DataFrame:
        """Generate a prediction for an explicit home/away pair."""


@dataclass(frozen=True)
class GeneratePredictionCommand:
    date: str | None = None
    home: str | None = None
    away: str | None = None
    prediction_context: str = "manual"
    window_label: str | None = None


@dataclass(frozen=True)
class GeneratePredictionResult:
    predictions: pd.DataFrame
    output_path: Path | None = None


class GeneratePredictionUseCase:
    def __init__(self, predictor: PredictionFacade | None = None) -> None:
        self._predictor = predictor

    @property
    def predictor(self) -> PredictionFacade:
        if self._predictor is None:
            from quiniela.predictor import Predictor

            self._predictor = cast(PredictionFacade, Predictor())
        return self._predictor

    def execute(self, command: GeneratePredictionCommand) -> GeneratePredictionResult:
        if command.date:
            predictions, output_path = self.predictor.predict_by_date(
                command.date,
                prediction_context=command.prediction_context,
                window_label=command.window_label,
            )
            return GeneratePredictionResult(predictions=predictions, output_path=output_path)

        if command.home and command.away:
            predictions = self.predictor.predict_match(command.home, command.away)
            return GeneratePredictionResult(predictions=predictions)

        raise ValueError("Debes enviar date o bien home y away.")
