import json
from pathlib import Path

from quiniela.db import (
    activate_model_release,
    get_active_model_release,
    get_connection,
    get_latest_pre_match_snapshot,
    insert_player_targets,
    insert_pre_match_snapshot,
    register_model_release,
)
from quiniela.player_evidence import build_player_targets


def _snapshot(window: str, captured_at: str, data_hash: str) -> dict:
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


def _player() -> dict:
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


def test_snapshot_is_immutable_and_latest_before_kickoff_is_selected(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "evidence.sqlite")
    first_id, first_created = insert_pre_match_snapshot(
        connection,
        _snapshot("t-60", "2026-06-13T11:00:00-06:00", "first"),
        [_player()],
    )
    duplicate_id, duplicate_created = insert_pre_match_snapshot(
        connection,
        _snapshot("t-60", "2026-06-13T11:30:00-06:00", "changed"),
        [{**_player(), "net_impact": 99}],
    )
    second_id, _ = insert_pre_match_snapshot(
        connection,
        _snapshot("t-1", "2026-06-13T12:59:00-06:00", "second"),
        [_player()],
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate_id == first_id
    assert connection.execute(
        "SELECT data_hash FROM pre_match_snapshots WHERE id = ?", (first_id,)
    ).fetchone()["data_hash"] == "first"
    latest = get_latest_pre_match_snapshot(
        connection,
        "match-1",
        "2026-06-13T13:00:00-06:00",
    )
    assert latest["id"] == second_id


def test_targets_are_idempotent_and_release_activation_keeps_previous(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "targets.sqlite")
    snapshot_id, _ = insert_pre_match_snapshot(
        connection,
        _snapshot("t-1", "2026-06-13T12:59:00-06:00", "hash"),
        [_player()],
    )
    target = {
        "match_id": "match-1",
        "fixture_id": "100",
        "snapshot_id": snapshot_id,
        "team_norm": "home",
        "api_player_id": 1,
        "player_name": "Player One",
        "player_norm": "player_one",
        "participated": 1,
        "minutes": 90,
        "rating": 7.1,
        **{
            column: 0
            for column in (
                "shots_total", "shots_on", "goals_total", "goals_assists",
                "dribbles_attempts", "dribbles_success", "passes_total",
                "passes_key", "passes_accuracy", "tackles_total",
                "tackles_blocks", "tackles_interceptions", "duels_total",
                "duels_won", "goals_saves", "goals_conceded",
                "fouls_committed", "cards_yellow", "cards_red", "penalty_won",
                "penalty_commited", "penalty_scored", "penalty_missed",
                "penalty_saved",
            )
        },
        "target_json": {},
    }
    assert insert_player_targets(connection, [target]) == 1
    assert insert_player_targets(connection, [target]) == 0

    for release_id in ("release-1", "release-2"):
        register_model_release(
            connection,
            {
                "release_id": release_id,
                "release_path": str(tmp_path / release_id),
                "status": "candidate",
                "model_version": "player_evidence_v2",
                "dataset_hash": release_id,
                "cutoff_at": "2026-06-13",
                "training_matches": 30,
                "live_matches": 0,
                "metrics": {"candidate": {}},
            },
        )
    assert activate_model_release(connection, "release-1")
    assert activate_model_release(connection, "release-2")
    active = get_active_model_release(connection)
    assert active["release_id"] == "release-2"
    previous = connection.execute(
        "SELECT status FROM model_releases WHERE release_id = 'release-1'"
    ).fetchone()
    assert previous["status"] == "archived"
    assert json.loads(active["metrics_json"]) == {"candidate": {}}


def test_missing_id_and_unused_bench_player_receive_zero_participation(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "bench.sqlite")
    snapshot_id, _ = insert_pre_match_snapshot(
        connection,
        _snapshot("t-1", "2026-06-13T12:59:00-06:00", "bench"),
        [
            {**_player(), "api_player_id": None},
            {
                **_player(),
                "api_player_id": 2,
                "player_name": "Unused Bench",
                "player_norm": "unused_bench",
                "lineup_role": "bench",
            },
        ],
    )
    with connection:
        connection.execute(
            """
            INSERT INTO fixture_player_stats (
                fixture_id, api_player_id, team_norm, player_name, player_norm,
                minutes, source_json
            ) VALUES ('100', 99, 'home', 'Player One', 'player_one', 75, '{}')
            """
        )

    summary = build_player_targets(connection, ["match-1"])
    rows = connection.execute(
        """
        SELECT player_name, participated, minutes
        FROM player_match_targets
        WHERE snapshot_id = ?
        ORDER BY player_name
        """,
        (snapshot_id,),
    ).fetchall()

    assert summary["inserted"] == 2
    assert [(row["player_name"], row["participated"], row["minutes"]) for row in rows] == [
        ("Player One", 1, 75.0),
        ("Unused Bench", 0, 0.0),
    ]
