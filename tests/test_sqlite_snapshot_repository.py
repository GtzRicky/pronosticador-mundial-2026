from __future__ import annotations

from pathlib import Path

from quiniela.adapters.outbound.sqlite.snapshot_repository import SQLiteSnapshotRepository
from quiniela.db import get_connection, get_latest_pre_match_snapshot, insert_pre_match_snapshot


def _snapshot(window: str, captured_at: str, data_hash: str) -> dict[str, str]:
    return {
        "match_id": "match-1",
        "fixture_id": "100",
        "window_label": window,
        "source_kind": "live",
        "kickoff_at": "2026-06-13T13:00:00-06:00",
        "captured_at": captured_at,
        "features_json": "{}",
        "prediction_json": "{}",
        "home_lineup_source": "confirmed_lineup",
        "away_lineup_source": "confirmed_lineup",
        "model_version": "poisson_v1",
        "outcome_model_version": "logit_v1",
        "data_hash": data_hash,
    }


def _player() -> dict[str, object]:
    return {
        "team_norm": "home",
        "api_player_id": 1,
        "player_name": "Player One",
        "player_norm": "player_one",
        "lineup_role": "starter",
        "role_bucket": "forward",
        "attack_impact": 0.3,
        "defense_impact": 0.1,
        "discipline_impact": -0.1,
        "availability_impact": 0.8,
        "net_impact": 0.4,
    }


def test_sqlite_snapshot_repository_preserves_immutability(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "snapshots.sqlite")
    repository = SQLiteSnapshotRepository(connection)

    first_id, first_created = repository.insert_pre_match_snapshot(
        _snapshot("t-60", "2026-06-13T11:00:00-06:00", "first"),
        [_player()],
    )
    duplicate_id, duplicate_created = repository.insert_pre_match_snapshot(
        _snapshot("t-60", "2026-06-13T11:30:00-06:00", "changed"),
        [{**_player(), "net_impact": 99}],
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate_id == first_id
    row = connection.execute("SELECT data_hash FROM pre_match_snapshots WHERE id = ?", (first_id,)).fetchone()
    assert row["data_hash"] == "first"


def test_snapshot_wrappers_delegate_to_repository(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "snapshots-wrapper.sqlite")
    first_id, _ = insert_pre_match_snapshot(
        connection,
        _snapshot("t-60", "2026-06-13T11:00:00-06:00", "first"),
        [_player()],
    )
    latest_id, _ = insert_pre_match_snapshot(
        connection,
        _snapshot("t-1", "2026-06-13T12:59:00-06:00", "second"),
        [_player()],
    )

    latest = get_latest_pre_match_snapshot(connection, "match-1", "2026-06-13T13:00:00-06:00")
    assert latest["id"] == latest_id
    assert latest["id"] != first_id
