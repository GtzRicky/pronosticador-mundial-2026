from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from quiniela.config import ROOT_DIR, Settings


DEFAULT_COMPETITION_CONFIG = "world_cup_2026"
DEFAULT_COMPETITION_CONFIG_DIR = ROOT_DIR / "configs" / "competitions"
DEFAULT_COMPETITION_ID = "fifa_world_cup"
DEFAULT_SEASON_ID = "world_cup_2026"


class CompetitionConfigError(ValueError):
    """Raised when a competition config is missing or invalid."""


@dataclass(frozen=True)
class Competition:
    slug: str
    name: str
    sport: str
    organizer: str
    competition_type: str
    default_timezone: str


@dataclass(frozen=True)
class Season:
    slug: str
    name: str
    start_date: date
    end_date: date
    timezone: str
    status: str


@dataclass(frozen=True)
class CompetitionRules:
    profile: str
    points_win: int
    points_draw: int
    points_loss: int
    neutral_venue_policy: str
    extra_time_policy: str
    penalties_policy: str


@dataclass(frozen=True)
class DataSourceConfig:
    parser: str
    input_path: Path


@dataclass(frozen=True)
class OutputConfig:
    namespace: str
    stable_aliases: bool


@dataclass(frozen=True)
class ApiFootballCompetitionConfig:
    league_id: int | None
    season: int | None
    enabled: bool


@dataclass(frozen=True)
class CompetitionConfig:
    competition: Competition
    season: Season
    rules: CompetitionRules
    data_sources: Mapping[str, DataSourceConfig]
    outputs: OutputConfig
    api_football: ApiFootballCompetitionConfig
    raw: Mapping[str, Any]

    @property
    def competition_id(self) -> str:
        return self.competition.slug

    @property
    def season_id(self) -> str:
        return self.season.slug


@dataclass(frozen=True)
class CompetitionContext:
    config: CompetitionConfig
    selector: str

    @property
    def competition_id(self) -> str:
        return self.config.competition_id

    @property
    def season_id(self) -> str:
        return self.config.season_id

    @property
    def namespace(self) -> str:
        return self.config.outputs.namespace

    @property
    def is_default(self) -> bool:
        return (
            self.competition_id == DEFAULT_COMPETITION_ID
            and self.season_id == DEFAULT_SEASON_ID
        )

    @property
    def stable_aliases(self) -> bool:
        return self.is_default and self.config.outputs.stable_aliases

    def processed_path(self, settings: Settings, filename: str) -> Path:
        if self.is_default:
            return settings.processed_dir / filename
        return settings.processed_dir / self.namespace / filename

    def prediction_path(self, settings: Settings, filename: str) -> Path:
        return settings.predictions_dir / self.namespace / filename


def load_default_competition_config() -> CompetitionConfig:
    return load_competition_config(DEFAULT_COMPETITION_CONFIG)


def resolve_competition_context(
    name_or_path: str | Path | None = None,
    *,
    season: str | None = None,
    config_dir: Path | None = None,
    root_dir: Path = ROOT_DIR,
) -> CompetitionContext:
    selector = str(name_or_path or DEFAULT_COMPETITION_CONFIG)
    config = load_competition_config(
        name_or_path or DEFAULT_COMPETITION_CONFIG,
        config_dir=config_dir,
        root_dir=root_dir,
    )
    if season is not None and season != config.season_id:
        raise CompetitionConfigError(
            f"season {season!r} does not match selected competition config "
            f"{config.season_id!r}"
        )
    return CompetitionContext(config=config, selector=selector)


def load_competition_config(
    name_or_path: str | Path = DEFAULT_COMPETITION_CONFIG,
    *,
    config_dir: Path | None = None,
    root_dir: Path = ROOT_DIR,
) -> CompetitionConfig:
    path = _resolve_config_path(name_or_path, config_dir or DEFAULT_COMPETITION_CONFIG_DIR)
    if not path.exists():
        raise CompetitionConfigError(f"competition config not found: {path}")
    raw = _parse_minimal_yaml(path.read_text(encoding="utf-8"))
    return _build_competition_config(raw, root_dir=root_dir)


def _resolve_config_path(name_or_path: str | Path, config_dir: Path) -> Path:
    value = Path(name_or_path)
    if value.suffix in {".yaml", ".yml"} or value.parent != Path("."):
        return value
    return config_dir / f"{value.name}.yaml"


def _build_competition_config(raw: Mapping[str, Any], *, root_dir: Path) -> CompetitionConfig:
    competition = _section(raw, "competition")
    season = _section(raw, "season")
    rules = _section(raw, "rules")
    data_sources = _section(raw, "data_sources")
    outputs = _section(raw, "outputs")
    api_football = _optional_section(raw, "api_football")

    return CompetitionConfig(
        competition=Competition(
            slug=_required_str(competition, "slug"),
            name=_required_str(competition, "name"),
            sport=_required_str(competition, "sport"),
            organizer=_required_str(competition, "organizer"),
            competition_type=_required_str(competition, "competition_type"),
            default_timezone=_required_str(competition, "default_timezone"),
        ),
        season=Season(
            slug=_required_str(season, "slug"),
            name=_required_str(season, "name"),
            start_date=_required_date(season, "start_date"),
            end_date=_required_date(season, "end_date"),
            timezone=_optional_str(season, "timezone", _required_str(competition, "default_timezone")),
            status=_optional_str(season, "status", "planned"),
        ),
        rules=CompetitionRules(
            profile=_required_str(rules, "profile"),
            points_win=_required_int(rules, "points_win"),
            points_draw=_required_int(rules, "points_draw"),
            points_loss=_required_int(rules, "points_loss"),
            neutral_venue_policy=_required_str(rules, "neutral_venue_policy"),
            extra_time_policy=_optional_str(rules, "extra_time_policy", "none"),
            penalties_policy=_optional_str(rules, "penalties_policy", "none"),
        ),
        data_sources={
            name: DataSourceConfig(
                parser=_required_str(_ensure_mapping(source, f"data_sources.{name}"), "parser"),
                input_path=_resolve_input_path(
                    _required_str(_ensure_mapping(source, f"data_sources.{name}"), "input"),
                    root_dir,
                ),
            )
            for name, source in data_sources.items()
        },
        outputs=OutputConfig(
            namespace=_required_str(outputs, "namespace"),
            stable_aliases=_required_bool(outputs, "stable_aliases"),
        ),
        api_football=ApiFootballCompetitionConfig(
            league_id=_optional_int(api_football, "league_id"),
            season=_optional_int(api_football, "season"),
            enabled=_optional_bool(api_football, "enabled", True),
        ),
        raw=raw,
    )


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        content = raw_line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2 != 0:
            raise CompetitionConfigError(f"invalid indentation at line {line_number}")

        line = content.strip()
        if line.startswith("- "):
            raise CompetitionConfigError("list syntax is not supported in competition configs")
        if ":" not in line:
            raise CompetitionConfigError(f"expected key/value pair at line {line_number}")

        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise CompetitionConfigError(f"empty key at line {line_number}")

        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if key in parent:
            raise CompetitionConfigError(f"duplicate key {key!r} at line {line_number}")

        raw_value = raw_value.strip()
        if not raw_value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value)

    return root


def _parse_scalar(value: str) -> Any:
    if value in {"''", '""'}:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    normalized = value.lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    if normalized in {"null", "none", "~"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in raw:
        raise CompetitionConfigError(f"missing required section: {key}")
    return _ensure_mapping(raw[key], key)


def _optional_section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in raw:
        return {}
    return _ensure_mapping(raw[key], key)


def _ensure_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompetitionConfigError(f"{label} must be a mapping")
    return value


def _required_str(section: Mapping[str, Any], key: str) -> str:
    value = section.get(key)
    if value is None or not str(value).strip():
        raise CompetitionConfigError(f"missing required string field: {key}")
    return str(value).strip()


def _optional_str(section: Mapping[str, Any], key: str, default: str) -> str:
    value = section.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def _required_int(section: Mapping[str, Any], key: str) -> int:
    value = section.get(key)
    if not isinstance(value, int):
        raise CompetitionConfigError(f"missing required integer field: {key}")
    return value


def _optional_int(section: Mapping[str, Any], key: str) -> int | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise CompetitionConfigError(f"{key} must be an integer")
    return value


def _required_bool(section: Mapping[str, Any], key: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise CompetitionConfigError(f"missing required boolean field: {key}")
    return value


def _optional_bool(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CompetitionConfigError(f"{key} must be a boolean")
    return value


def _required_date(section: Mapping[str, Any], key: str) -> date:
    value = _required_str(section, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CompetitionConfigError(f"{key} must be an ISO date") from exc


def _resolve_input_path(value: str, root_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root_dir / path


__all__ = [
    "ApiFootballCompetitionConfig",
    "Competition",
    "CompetitionConfig",
    "CompetitionContext",
    "CompetitionConfigError",
    "CompetitionRules",
    "DataSourceConfig",
    "DEFAULT_COMPETITION_CONFIG",
    "DEFAULT_COMPETITION_CONFIG_DIR",
    "DEFAULT_COMPETITION_ID",
    "DEFAULT_SEASON_ID",
    "OutputConfig",
    "Season",
    "load_competition_config",
    "load_default_competition_config",
    "resolve_competition_context",
]
