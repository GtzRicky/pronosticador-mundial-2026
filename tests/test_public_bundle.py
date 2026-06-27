from pathlib import Path
import sqlite3

from quiniela.public_bundle import export_public_bundle, import_public_bundle


def _seed_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("CREATE TABLE teams (team_name TEXT, team_norm TEXT, api_team_id INTEGER)")
        conn.execute("CREATE TABLE api_cache (endpoint TEXT, params_hash TEXT, params_json TEXT, response_json TEXT, status_code INTEGER, cached_at TEXT)")
        conn.execute("CREATE TABLE api_usage (request_date TEXT, endpoint TEXT, params_hash TEXT, cache_hit INTEGER, status_code INTEGER, created_at TEXT)")
        conn.execute("INSERT INTO teams VALUES ('Mexico', 'mexico', 16)")
        conn.execute("INSERT INTO api_cache VALUES ('/teams', 'abc', '{}', '{}', 200, '2026-01-01')")
        conn.execute("INSERT INTO api_usage VALUES ('2026-01-01', '/teams', 'abc', 0, 200, '2026-01-01')")
    conn.close()


def test_export_public_bundle_removes_api_tables_data(tmp_path: Path) -> None:
    source_db = tmp_path / "source.sqlite"
    _seed_test_db(source_db)
    bundle_dir = tmp_path / "bundle"
    summary = export_public_bundle(output_dir=bundle_dir, source_db_path=source_db, include_archive=False)
    assert summary.db_path.exists()
    conn = sqlite3.connect(summary.db_path)
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 0
    conn.close()


def test_export_public_bundle_migrates_legacy_matches_scope_before_scoping(tmp_path: Path) -> None:
    source_db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(source_db)
    with conn:
        conn.execute(
            """
            CREATE TABLE matches (
                match_id TEXT PRIMARY KEY,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE TABLE api_cache (endpoint TEXT, params_hash TEXT, params_json TEXT, response_json TEXT, status_code INTEGER, cached_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE api_usage (request_date TEXT, endpoint TEXT, params_hash TEXT, cache_hit INTEGER, status_code INTEGER, created_at TEXT)"
        )
        conn.execute("INSERT INTO matches VALUES ('match-1', 'Mexico', 'Canada')")
        conn.execute("INSERT INTO api_cache VALUES ('/fixtures', 'abc', '{}', '{}', 200, '2026-01-01')")
        conn.execute("INSERT INTO api_usage VALUES ('2026-01-01', '/fixtures', 'abc', 0, 200, '2026-01-01')")
    conn.close()

    bundle_dir = tmp_path / "bundle"
    summary = export_public_bundle(output_dir=bundle_dir, source_db_path=source_db, include_archive=False)

    assert summary.db_path.exists()
    conn = sqlite3.connect(summary.db_path)
    match_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
    assert {"competition_id", "season_id"}.issubset(match_columns)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 0
    conn.close()


def test_import_public_bundle_restores_db(tmp_path: Path) -> None:
    source_db = tmp_path / "source.sqlite"
    _seed_test_db(source_db)
    bundle_dir = tmp_path / "bundle"
    export_public_bundle(output_dir=bundle_dir, source_db_path=source_db, include_archive=False)
    destination_db = tmp_path / "imported.sqlite"
    summary = import_public_bundle(bundle_dir, destination_db_path=destination_db)
    assert summary.db_path == destination_db
    conn = sqlite3.connect(destination_db)
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1
    conn.close()
