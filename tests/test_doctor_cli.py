from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from typer.testing import CliRunner

from quiniela.adapters.outbound.sqlite import SQLiteDoctorHealthRepository
from quiniela.application.use_cases.doctor import DoctorUseCase
from quiniela.cli import app
from quiniela.config import get_settings
from quiniela.db import activate_model_release, get_connection, register_model_release


runner = CliRunner()


def _settings(tmp_path: Path):
    settings = replace(
        get_settings(),
        root_dir=tmp_path,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        db_dir=tmp_path / "data" / "db",
        outputs_dir=tmp_path / "outputs",
        bundles_dir=tmp_path / "outputs" / "bundles",
        predictions_dir=tmp_path / "outputs" / "predictions",
        logs_dir=tmp_path / "outputs" / "logs",
        public_dir=tmp_path / "data" / "public",
        model_artifacts_dir=tmp_path / "data" / "processed" / "model_artifacts",
        db_path=tmp_path / "data" / "db" / "doctor.sqlite",
        api_football_key="super-secret-key",
    )
    for directory in (
        settings.db_dir,
        settings.predictions_dir,
        settings.logs_dir,
        settings.model_artifacts_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return settings


def _write_outputs(settings) -> None:
    for name in DoctorUseCase.STABLE_OUTPUTS:
        (settings.predictions_dir / name).write_text("[]", encoding="utf-8")
    for name in DoctorUseCase.STABLE_LOGS:
        (settings.logs_dir / name).write_text("# ok\n", encoding="utf-8")


def _doctor(settings) -> DoctorUseCase:
    return DoctorUseCase(
        settings=settings,
        health_repository=SQLiteDoctorHealthRepository(settings.db_path),
    )


def test_doctor_use_case_reports_read_only_health_without_secrets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    connection = get_connection(settings.db_path)
    register_model_release(
        connection,
        {
            "release_id": "release-1",
            "release_path": str(tmp_path / "release-1"),
            "status": "candidate",
            "model_version": "player_evidence_v2",
            "dataset_hash": "hash",
            "cutoff_at": "2026-06-13",
            "training_matches": 30,
            "live_matches": 0,
            "metrics": {"candidate": {}},
        },
    )
    assert activate_model_release(connection, "release-1")
    _write_outputs(settings)

    result = _doctor(settings).execute()
    payload = result.to_dict()

    assert payload["status"] in {"ok", "warn"}
    assert "super-secret-key" not in json.dumps(payload)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["database"]["status"] == "ok"
    assert checks["outputs"]["status"] == "ok"
    assert checks["model_release"]["status"] == "ok"
    assert result.to_human_text().startswith("Doctor status:")
    assert result.to_markdown().startswith("# Quiniela Doctor")


def test_doctor_use_case_warns_when_db_is_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    result = _doctor(settings).execute()
    checks = {check.name: check for check in result.checks}

    assert result.status in {"warn", "error"}
    assert checks["database"].status == "warn"


def test_doctor_cli_json_output_is_offline_and_sanitized(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    get_connection(settings.db_path)
    _write_outputs(settings)
    monkeypatch.setattr("quiniela.cli.get_settings", lambda: settings)

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "super-secret-key" not in result.stdout
    assert {check["name"] for check in payload["checks"]} >= {"python", "database", "outbox"}


def test_doctor_cli_accepts_offline_competition_selector(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    get_connection(settings.db_path)
    monkeypatch.setattr("quiniela.cli.get_settings", lambda: settings)

    result = runner.invoke(
        app,
        [
            "doctor",
            "--format",
            "json",
            "--competition",
            "champions_league_2026_2027",
            "--season",
            "champions_league_2026_2027",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    config_check = next(
        check for check in payload["checks"] if check["name"] == "configuration"
    )
    assert config_check["details"]["competition_id"] == "uefa_champions_league"
    assert config_check["details"]["season_id"] == "champions_league_2026_2027"
