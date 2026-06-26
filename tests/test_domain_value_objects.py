from __future__ import annotations

import pytest

from quiniela.domain import InvalidIdentifierError, MatchId, PlayerId, PredictionId, TeamId
from quiniela.domain.value_objects import coerce_id


def test_identifier_value_is_stable_and_string_like() -> None:
    match_id = MatchId(" match_001 ")

    assert str(match_id) == "match_001"
    assert match_id.as_key() == "match_001"
    assert repr(match_id) == "MatchId('match_001')"


@pytest.mark.parametrize("identifier_type", [MatchId, TeamId, PlayerId, PredictionId])
def test_identifier_rejects_empty_values(identifier_type: type[MatchId]) -> None:
    with pytest.raises(InvalidIdentifierError):
        identifier_type("   ")


def test_identifier_types_do_not_compare_equal_by_value_only() -> None:
    assert MatchId("mexico") != TeamId("mexico")


def test_coerce_id_preserves_existing_identifier() -> None:
    team_id = TeamId("mexico")

    assert coerce_id(team_id, TeamId) is team_id
    assert coerce_id("canada", TeamId) == TeamId("canada")
