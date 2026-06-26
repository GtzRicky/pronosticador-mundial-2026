from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quiniela.domain.errors import InvalidIdentifierError


@dataclass(frozen=True)
class _StringId:
    value: str

    def __post_init__(self) -> None:
        if self.value is None:
            raise InvalidIdentifierError(f"{self.__class__.__name__} cannot be None")
        normalized = str(self.value).strip()
        if not normalized:
            raise InvalidIdentifierError(f"{self.__class__.__name__} cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.value!r})"

    def as_key(self) -> str:
        return self.value

    def __json__(self) -> str:
        return self.value


class MatchId(_StringId):
    """Stable local identifier for a match."""


class TeamId(_StringId):
    """Stable normalized identifier for a team."""


class PlayerId(_StringId):
    """Stable identifier for a player within current legacy constraints."""


class PredictionId(_StringId):
    """Stable identifier for a prediction."""


def coerce_id(value: Any, id_type: type[_StringId]) -> _StringId:
    if isinstance(value, id_type):
        return value
    return id_type(str(value))
