from __future__ import annotations

from pathlib import Path

import pytest

from quiniela.config import ROOT_DIR
from quiniela.infrastructure.competition_config import (
    CompetitionConfigError,
    load_competition_config,
    load_default_competition_config,
    resolve_competition_context,
)


def test_loads_default_world_cup_config_without_network() -> None:
    config = load_default_competition_config()

    assert config.competition.slug == "fifa_world_cup"
    assert config.season.slug == "world_cup_2026"
    assert config.season_id == "world_cup_2026"
    assert config.rules.points_win == 3
    assert config.outputs.namespace == "world-cup-2026"
    assert config.outputs.stable_aliases is True
    assert config.api_football.league_id == 1
    assert config.api_football.season == 2026
    assert config.data_sources["calendar"].parser == "world_cup_markdown"
    assert config.data_sources["calendar"].input_path == ROOT_DIR / "data/raw/calendario_mundial.md"


@pytest.mark.parametrize(
    ("name", "expected_namespace"),
    [
        ("champions_league_2026_2027", "champions-league-2026-2027"),
        ("liga_mx_apertura_2026", "liga-mx-apertura-2026"),
    ],
)
def test_documented_example_configs_load_read_only(name: str, expected_namespace: str) -> None:
    config = load_competition_config(name)

    assert config.season.status == "example"
    assert config.outputs.namespace == expected_namespace
    assert config.outputs.stable_aliases is False
    assert config.api_football.enabled is False


def test_context_defaults_to_world_cup_and_validates_season() -> None:
    default = resolve_competition_context()
    example = resolve_competition_context(
        "champions_league_2026_2027",
        season="champions_league_2026_2027",
    )

    assert default.is_default is True
    assert default.stable_aliases is True
    assert example.competition_id == "uefa_champions_league"
    assert example.namespace == "champions-league-2026-2027"
    assert example.stable_aliases is False

    with pytest.raises(CompetitionConfigError, match="does not match"):
        resolve_competition_context(
            "champions_league_2026_2027",
            season="world_cup_2026",
        )


def test_loader_accepts_explicit_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "minimal.yaml"
    config_path.write_text(
        """
competition:
  slug: custom_cup
  name: Custom Cup
  sport: football
  organizer: Local
  competition_type: clubs
  default_timezone: UTC
season:
  slug: custom_cup_2026
  name: Custom Cup 2026
  start_date: 2026-01-01
  end_date: 2026-02-01
rules:
  profile: simple_league
  points_win: 3
  points_draw: 1
  points_loss: 0
  neutral_venue_policy: optional
data_sources:
  calendar:
    parser: custom_calendar
    input: calendar.md
  rosters:
    parser: custom_rosters
    input: rosters.md
outputs:
  namespace: custom-cup-2026
  stable_aliases: false
""",
        encoding="utf-8",
    )

    config = load_competition_config(config_path, root_dir=tmp_path)

    assert config.season.timezone == "UTC"
    assert config.rules.extra_time_policy == "none"
    assert config.data_sources["rosters"].input_path == tmp_path / "rosters.md"
    assert config.api_football.enabled is True
    assert config.api_football.league_id is None


def test_loader_rejects_missing_required_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.yaml"
    config_path.write_text(
        """
competition:
  slug: broken
season:
  slug: broken_2026
rules:
  profile: simple
data_sources:
  calendar:
    parser: custom
    input: calendar.md
outputs:
  namespace: broken
  stable_aliases: true
""",
        encoding="utf-8",
    )

    with pytest.raises(CompetitionConfigError, match="missing required string field"):
        load_competition_config(config_path, root_dir=tmp_path)


def test_loader_rejects_unsupported_yaml_lists(tmp_path: Path) -> None:
    config_path = tmp_path / "list.yaml"
    config_path.write_text(
        """
competition:
  slug: list_case
  aliases:
    - one
""",
        encoding="utf-8",
    )

    with pytest.raises(CompetitionConfigError, match="list syntax"):
        load_competition_config(config_path, root_dir=tmp_path)
