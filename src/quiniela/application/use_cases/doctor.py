from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import platform
import sys
from typing import Any

from quiniela.config import Settings, get_settings
from quiniela.infrastructure.competition_config import (
    CompetitionContext,
    resolve_competition_context,
)
from quiniela.ports.doctor_health import DoctorHealthRepository


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any]


@dataclass(frozen=True)
class DoctorResult:
    status: str
    checks: tuple[DoctorCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
        }

    def to_markdown(self) -> str:
        lines = ["# Quiniela Doctor", "", f"- Overall: {self.status}", ""]
        for check in self.checks:
            lines.append(f"## {check.name}")
            lines.append("")
            lines.append(f"- Status: {check.status}")
            lines.append(f"- Message: {check.message}")
            for key, value in check.details.items():
                lines.append(f"- {key}: {value}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def to_human_text(self) -> str:
        lines = [f"Doctor status: {self.status}"]
        for check in self.checks:
            lines.append(f"[{check.status}] {check.name}: {check.message}")
        return "\n".join(lines)


class DoctorUseCase:
    REQUIRED_TABLES = (
        "matches",
        "predictions",
        "notification_deliveries",
        "model_releases",
    )
    STABLE_OUTPUTS = (
        "predictions_latest.csv",
        "predictions_latest.json",
        "predictions_history.csv",
        "predictions_history.json",
    )
    STABLE_LOGS = (
        "data_quality.md",
        "model_performance.md",
        "automation_status.md",
    )

    def __init__(
        self,
        settings: Settings | None = None,
        competition_context: CompetitionContext | None = None,
        health_repository: DoctorHealthRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.competition_context = (
            competition_context or resolve_competition_context()
        )
        if health_repository is None:
            raise ValueError("DoctorUseCase requires a health_repository")
        self.health_repository = health_repository

    def execute(self) -> DoctorResult:
        checks = (
            self._check_python(),
            self._check_configuration(),
            self._check_database(),
            self._check_outputs(),
            self._check_outbox(),
            self._check_model_release(),
        )
        return DoctorResult(status=_overall_status(checks), checks=checks)

    def _check_python(self) -> DoctorCheck:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if sys.version_info >= (3, 11):
            return DoctorCheck("python", "ok", f"Python {version}", {"implementation": platform.python_implementation()})
        return DoctorCheck(
            "python",
            "warn",
            f"Python {version}; proyecto declara >=3.11",
            {"implementation": platform.python_implementation()},
        )

    def _check_configuration(self) -> DoctorCheck:
        return DoctorCheck(
            "configuration",
            "ok",
            "Configuracion cargada sin exponer secretos.",
            {
                "db_path": str(self.settings.db_path),
                "api_key_configured": bool(self.settings.api_football_key),
                "notifications_enabled": self.settings.notifications_enabled,
                "ntfy_enabled": self.settings.ntfy_enabled,
                "discord_enabled": self.settings.discord_enabled,
                "competition_id": self.competition_context.competition_id,
                "season_id": self.competition_context.season_id,
                "namespace": self.competition_context.namespace,
            },
        )

    def _check_database(self) -> DoctorCheck:
        inspection = self.health_repository.inspect_database(
            competition_id=self.competition_context.competition_id,
            season_id=self.competition_context.season_id,
        )
        if not inspection.exists:
            return DoctorCheck(
                "database",
                "warn",
                "DB no existe; inicializa o importa datos antes de operar.",
                {"db_path": str(self.settings.db_path)},
            )
        if inspection.error:
            return DoctorCheck(
                "database",
                "error",
                "No se pudo inspeccionar la DB en modo read-only.",
                {"error": inspection.error},
            )
        missing = sorted(set(self.REQUIRED_TABLES) - inspection.tables)
        if missing:
            return DoctorCheck("database", "error", "DB accesible pero faltan tablas requeridas.", {"missing": missing})
        if not inspection.scope_initialized:
            return DoctorCheck(
                "database",
                "warn",
                "DB accesible, pero la competencia seleccionada no esta inicializada.",
                {
                    "competition_id": self.competition_context.competition_id,
                    "season_id": self.competition_context.season_id,
                },
            )
        return DoctorCheck("database", "ok", "DB accesible y migraciones base presentes.", {"tables_checked": list(self.REQUIRED_TABLES)})

    def _check_outputs(self) -> DoctorCheck:
        predictions_dir = (
            self.settings.predictions_dir
            if self.competition_context.is_default
            else self.settings.predictions_dir / self.competition_context.namespace
        )
        logs_dir = (
            self.settings.logs_dir
            if self.competition_context.is_default
            else self.settings.logs_dir / self.competition_context.namespace
        )
        missing_outputs = _missing_files(predictions_dir, self.STABLE_OUTPUTS)
        missing_logs = _missing_files(logs_dir, self.STABLE_LOGS)
        missing = missing_outputs + missing_logs
        if missing:
            return DoctorCheck(
                "outputs",
                "warn",
                "Faltan outputs estables; ejecuta rebuild cuando haya datos.",
                {"missing": missing},
            )
        return DoctorCheck("outputs", "ok", "Outputs estables presentes.", {"files_checked": len(self.STABLE_OUTPUTS) + len(self.STABLE_LOGS)})

    def _check_outbox(self) -> DoctorCheck:
        inspection = self.health_repository.inspect_outbox(
            competition_id=self.competition_context.competition_id,
            season_id=self.competition_context.season_id,
        )
        if not inspection.available:
            return DoctorCheck("outbox", "warn", "No se puede revisar outbox sin DB.", {})
        if inspection.error:
            return DoctorCheck("outbox", "warn", "No se pudo revisar outbox.", {"error": inspection.error})
        counts = inspection.counts
        total_open = sum(counts.values())
        status = "warn" if total_open else "ok"
        message = "Hay notificaciones abiertas." if total_open else "Sin notificaciones abiertas."
        return DoctorCheck("outbox", status, message, {"open": counts, "total_open": total_open})

    def _check_model_release(self) -> DoctorCheck:
        inspection = self.health_repository.inspect_active_model_release(
            competition_id=self.competition_context.competition_id,
            season_id=self.competition_context.season_id,
        )
        if not inspection.available:
            return DoctorCheck("model_release", "warn", "No se puede revisar modelo activo sin DB.", {})
        if inspection.error:
            return DoctorCheck("model_release", "warn", "No se pudo revisar modelo activo.", {"error": inspection.error})
        if inspection.release_id is None:
            return DoctorCheck("model_release", "warn", "No hay modelo activo registrado.", {})
        return DoctorCheck(
            "model_release",
            "ok",
            f"Modelo activo: {inspection.release_id}",
            {
                "release_id": inspection.release_id,
                "model_version": inspection.model_version,
                "preliminary": inspection.preliminary,
            },
        )


def _missing_files(directory: Path, names: tuple[str, ...]) -> list[str]:
    return [str(directory / name) for name in names if not (directory / name).is_file()]


def _overall_status(checks: tuple[DoctorCheck, ...]) -> str:
    statuses = {check.status for check in checks}
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    return "ok"
