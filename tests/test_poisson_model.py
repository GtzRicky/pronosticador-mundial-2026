from quiniela.poisson_model import PoissonScoreModel


def test_poisson_predict_score_returns_non_negative_scoreline() -> None:
    model = PoissonScoreModel()
    prediction = model.predict_score(
        {
            "home_team_strength": 0.25,
            "away_team_strength": 0.15,
            "home_gf_avg": 1.4,
            "away_gf_avg": 1.1,
            "home_ga_avg": 0.9,
            "away_ga_avg": 1.2,
        }
    )
    assert prediction.home_goals >= 0
    assert prediction.away_goals >= 0
    assert 0.0 < prediction.probability < 1.0
    assert abs(prediction.matrix.sum() - 1.0) < 1e-6
