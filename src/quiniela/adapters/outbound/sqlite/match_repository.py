from __future__ import annotations

from datetime import datetime
import sqlite3

from quiniela.domain import MatchId, TeamId
from quiniela.ports.repositories import MatchRecord


class SQLiteMatchRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_by_id(self, match_id: MatchId) -> MatchRecord | None:
        row = self.connection.execute(
            """
            SELECT
                match_id, date_cdmx, datetime_cdmx, group_name, home_team, away_team,
                home_team_norm, away_team_norm, stage, status, api_fixture_id
            FROM matches
            WHERE match_id = ?
            """,
            (str(match_id),),
        ).fetchone()
        if row is None:
            return None
        return _match_record_from_row(row)

    def list_by_date(self, date_cdmx: str) -> list[MatchRecord]:
        rows = self.connection.execute(
            """
            SELECT
                match_id, date_cdmx, datetime_cdmx, group_name, home_team, away_team,
                home_team_norm, away_team_norm, stage, status, api_fixture_id
            FROM matches
            WHERE date_cdmx = ?
            ORDER BY datetime_cdmx, home_team
            """,
            (date_cdmx,),
        ).fetchall()
        return [_match_record_from_row(row) for row in rows]


def _match_record_from_row(row: sqlite3.Row) -> MatchRecord:
    api_fixture_id = row["api_fixture_id"]
    return MatchRecord(
        match_id=MatchId(str(row["match_id"])),
        home_team_id=TeamId(str(row["home_team_norm"])),
        away_team_id=TeamId(str(row["away_team_norm"])),
        kickoff_at=datetime.fromisoformat(str(row["datetime_cdmx"])),
        status=str(row["status"]),
        date_cdmx=str(row["date_cdmx"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        group_name=str(row["group_name"]),
        stage=str(row["stage"]),
        api_fixture_id=int(api_fixture_id) if api_fixture_id is not None else None,
    )
