"""Application use cases."""

from quiniela.application.use_cases.build_odds_consensus import BuildOddsConsensusCommand
from quiniela.application.use_cases.build_odds_consensus import BuildOddsConsensusResult
from quiniela.application.use_cases.build_odds_consensus import BuildOddsConsensusUseCase
from quiniela.application.use_cases.doctor import DoctorCheck, DoctorResult, DoctorUseCase
from quiniela.application.use_cases.generate_prediction import GeneratePredictionCommand, GeneratePredictionResult
from quiniela.application.use_cases.generate_prediction import GeneratePredictionUseCase
from quiniela.application.use_cases.refresh_matchday import RefreshMatchdayCommand, RefreshMatchdayResult
from quiniela.application.use_cases.refresh_matchday import RefreshMatchdayUseCase

__all__ = [
    "BuildOddsConsensusCommand",
    "BuildOddsConsensusResult",
    "BuildOddsConsensusUseCase",
    "DoctorCheck",
    "DoctorResult",
    "DoctorUseCase",
    "GeneratePredictionCommand",
    "GeneratePredictionResult",
    "GeneratePredictionUseCase",
    "RefreshMatchdayCommand",
    "RefreshMatchdayResult",
    "RefreshMatchdayUseCase",
]
