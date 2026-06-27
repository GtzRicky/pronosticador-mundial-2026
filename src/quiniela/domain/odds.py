from __future__ import annotations

from dataclasses import dataclass
import math

from quiniela.domain.errors import DomainValidationError


@dataclass(frozen=True)
class DecimalOdds:
    value: float

    def __post_init__(self) -> None:
        value = float(self.value)
        if not math.isfinite(value) or value <= 1.0:
            raise DomainValidationError("decimal odds must be finite and greater than 1")
        object.__setattr__(self, "value", value)

    @property
    def implied_probability(self) -> float:
        return 1.0 / self.value


@dataclass(frozen=True)
class MarketSelectionOdds:
    selection_key: str
    decimal_odds: DecimalOdds
    bookmaker: str | None = None

    def __post_init__(self) -> None:
        selection_key = str(self.selection_key).strip()
        if not selection_key:
            raise DomainValidationError("selection key cannot be empty")
        object.__setattr__(self, "selection_key", selection_key)


@dataclass(frozen=True)
class MarketOdds:
    market_key: str
    selections: tuple[MarketSelectionOdds, ...]
    line_key: str = ""

    def __post_init__(self) -> None:
        market_key = str(self.market_key).strip()
        if not market_key:
            raise DomainValidationError("market key cannot be empty")
        if len(self.selections) < 2:
            raise DomainValidationError("market odds require at least two selections")
        object.__setattr__(self, "market_key", market_key)
        object.__setattr__(self, "line_key", str(self.line_key or ""))
        object.__setattr__(self, "selections", tuple(self.selections))

    @property
    def implied_total(self) -> float:
        return sum(selection.decimal_odds.implied_probability for selection in self.selections)

    @property
    def overround(self) -> float:
        return self.implied_total - 1.0


@dataclass(frozen=True)
class MarketSelectionProbability:
    selection_key: str
    probability: float

    def __post_init__(self) -> None:
        selection_key = str(self.selection_key).strip()
        probability = float(self.probability)
        if not selection_key:
            raise DomainValidationError("selection key cannot be empty")
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise DomainValidationError("probability must be finite and between 0 and 1")
        object.__setattr__(self, "selection_key", selection_key)
        object.__setattr__(self, "probability", probability)


@dataclass(frozen=True)
class MarketProbabilities:
    market_key: str
    selections: tuple[MarketSelectionProbability, ...]
    line_key: str = ""
    overround_method: str = "multiplicative"

    def __post_init__(self) -> None:
        market_key = str(self.market_key).strip()
        if not market_key:
            raise DomainValidationError("market key cannot be empty")
        if not self.selections:
            raise DomainValidationError("market probabilities cannot be empty")
        total = sum(selection.probability for selection in self.selections)
        if not math.isfinite(total) or total <= 0.0:
            raise DomainValidationError("market probability total must be positive")
        if total > 1.000001:
            raise DomainValidationError("market probabilities cannot exceed 1")
        object.__setattr__(self, "market_key", market_key)
        object.__setattr__(self, "line_key", str(self.line_key or ""))
        object.__setattr__(self, "selections", tuple(self.selections))

    @property
    def total_probability(self) -> float:
        return sum(selection.probability for selection in self.selections)

    def as_dict(self) -> dict[str, float]:
        return {
            selection.selection_key: selection.probability
            for selection in self.selections
        }


class MultiplicativeMarketProbabilityConverter:
    overround_method = "multiplicative"

    def to_probabilities(self, market: MarketOdds) -> MarketProbabilities:
        total = market.implied_total
        if total <= 0.0:
            raise DomainValidationError("market odds imply no probability")
        return MarketProbabilities(
            market_key=market.market_key,
            line_key=market.line_key,
            overround_method=self.overround_method,
            selections=tuple(
                MarketSelectionProbability(
                    selection_key=selection.selection_key,
                    probability=selection.decimal_odds.implied_probability / total,
                )
                for selection in market.selections
            ),
        )
