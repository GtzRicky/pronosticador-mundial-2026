from __future__ import annotations

import json
import sqlite3
from typing import Any


class SQLiteSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert_pre_match_snapshot(
        self,
        snapshot: dict[str, Any],
        player_rows: list[dict[str, Any]],
    ) -> tuple[int, bool]:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO pre_match_snapshots (
                    competition_id, season_id,
                    match_id, fixture_id, window_label, source_kind,
                    kickoff_at, captured_at, features_json, prediction_json,
                    home_lineup_source, away_lineup_source,
                    model_version, outcome_model_version, data_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("competition_id", "fifa_world_cup"),
                    snapshot.get("season_id", "world_cup_2026"),
                    snapshot["match_id"],
                    snapshot.get("fixture_id"),
                    snapshot["window_label"],
                    snapshot["source_kind"],
                    snapshot["kickoff_at"],
                    snapshot["captured_at"],
                    snapshot["features_json"],
                    snapshot["prediction_json"],
                    snapshot.get("home_lineup_source"),
                    snapshot.get("away_lineup_source"),
                    snapshot.get("model_version"),
                    snapshot.get("outcome_model_version"),
                    snapshot["data_hash"],
                ),
            )
            created = cursor.rowcount == 1
            row = self.connection.execute(
                """
                SELECT id FROM pre_match_snapshots
                WHERE match_id = ? AND window_label = ? AND source_kind = ?
                """,
                (
                    snapshot["match_id"],
                    snapshot["window_label"],
                    snapshot["source_kind"],
                ),
            ).fetchone()
            snapshot_id = int(row["id"])
            if created:
                self.connection.executemany(
                    """
                    INSERT OR IGNORE INTO pre_match_player_snapshots (
                        competition_id, season_id,
                        snapshot_id, team_norm, api_player_id, player_name, player_norm,
                        lineup_role, role_bucket, attack_impact, defense_impact,
                        discipline_impact, availability_impact, net_impact, features_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            snapshot.get("competition_id", "fifa_world_cup"),
                            snapshot.get("season_id", "world_cup_2026"),
                            snapshot_id,
                            player["team_norm"],
                            player.get("api_player_id"),
                            player["player_name"],
                            player.get("player_norm"),
                            player.get("lineup_role", "unknown"),
                            player.get("role_bucket", "unknown"),
                            float(player.get("attack_impact", 0.0)),
                            float(player.get("defense_impact", 0.0)),
                            float(player.get("discipline_impact", 0.0)),
                            float(player.get("availability_impact", 0.0)),
                            float(player.get("net_impact", 0.0)),
                            json.dumps(player, ensure_ascii=False, default=str),
                        )
                        for player in player_rows
                    ],
                )
        return snapshot_id, created

    def get_latest_pre_match_snapshot(
        self,
        match_id: str,
        before_kickoff: str | None = None,
    ) -> sqlite3.Row | None:
        query = "SELECT * FROM pre_match_snapshots WHERE match_id = ?"
        params: list[Any] = [match_id]
        if before_kickoff:
            query += " AND julianday(captured_at) < julianday(?)"
            params.append(before_kickoff)
        query += " ORDER BY julianday(captured_at) DESC, id DESC LIMIT 1"
        return self.connection.execute(query, params).fetchone()
