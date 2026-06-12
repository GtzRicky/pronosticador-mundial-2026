from pathlib import Path

from quiniela.db import get_connection, upsert_player_api_profile
from quiniela.player_model import resolve_player_identity, train_player_model


def test_resolve_player_identity_exact_and_fuzzy(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "players.sqlite")
    upsert_player_api_profile(
        connection,
        team_norm="mexico",
        player_name="Roberto Alvarado",
        player_norm="roberto_alvarado",
        position_group="forward",
        team_name="México",
    )

    exact = resolve_player_identity(connection, "mexico", "Roberto Alvarado")
    fuzzy = resolve_player_identity(connection, "mexico", "Roberto Alvarad")

    assert exact.player_norm == "roberto_alvarado"
    assert exact.resolution_method == "exact_name"
    assert fuzzy.player_norm == "roberto_alvarado"
    assert fuzzy.resolution_method == "fuzzy_name"


def test_train_player_model_returns_empty_summary_without_data(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "model.sqlite")

    summary = train_player_model(connection, artifact_path=tmp_path / "poisson_player_v1.pkl", min_matches=5)

    assert summary["trained"] is False
    assert summary["reason"] == "empty_training_set"
