from __future__ import annotations

import pytest

from quiniela.domain import DomainValidationError
from quiniela.domain.odds import (
    DecimalOdds,
    MarketOdds,
    MarketProbabilities,
    MarketSelectionOdds,
    MarketSelectionProbability,
    MultiplicativeMarketProbabilityConverter,
)


def _market() -> MarketOdds:
    return MarketOdds(
        market_key="match_winner",
        selections=(
            MarketSelectionOdds("home", DecimalOdds(1.50), bookmaker="Bet365"),
            MarketSelectionOdds("draw", DecimalOdds(4.00), bookmaker="Bet365"),
            MarketSelectionOdds("away", DecimalOdds(7.00), bookmaker="Bet365"),
        ),
    )


@pytest.mark.parametrize("value", [1.0, 0.99, float("nan"), float("inf")])
def test_decimal_odds_reject_invalid_values(value: float) -> None:
    with pytest.raises(DomainValidationError):
        DecimalOdds(value)


def test_market_odds_exposes_basic_overround() -> None:
    market = _market()

    assert market.implied_total == pytest.approx((1 / 1.50) + (1 / 4.00) + (1 / 7.00))
    assert market.overround == pytest.approx(market.implied_total - 1.0)
    assert market.overround > 0.0


def test_multiplicative_converter_matches_existing_devig_formula() -> None:
    market = _market()
    converter = MultiplicativeMarketProbabilityConverter()
    probabilities = converter.to_probabilities(market)

    implied_total = market.implied_total
    assert probabilities.total_probability == pytest.approx(1.0)
    assert probabilities.as_dict() == pytest.approx(
        {
            "home": (1 / 1.50) / implied_total,
            "draw": (1 / 4.00) / implied_total,
            "away": (1 / 7.00) / implied_total,
        }
    )
    assert probabilities.overround_method == "multiplicative"


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan")])
def test_market_probability_rejects_invalid_values(probability: float) -> None:
    with pytest.raises(DomainValidationError):
        MarketSelectionProbability("home", probability)


def test_market_probabilities_reject_impossible_total() -> None:
    with pytest.raises(DomainValidationError):
        MarketProbabilities(
            market_key="match_winner",
            selections=(
                MarketSelectionProbability("home", 0.8),
                MarketSelectionProbability("away", 0.8),
            ),
        )
