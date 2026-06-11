from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

from quiniela.config import get_settings
from quiniela.name_maps import normalize_team_name, normalize_text


POSITION_MAP = {
    "porteros": "goalkeeper",
    "arqueros": "goalkeeper",
    "defensas": "defender",
    "defensores": "defender",
    "mediocampistas": "midfielder",
    "delanteros": "forward",
}


def _split_outside_parentheses(text: str) -> list[str]:
    parts: list[str] = []
    token: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1

        if char == "," and depth == 0:
            candidate = "".join(token).strip()
            if candidate:
                parts.append(candidate)
            token = []
            continue
        token.append(char)

    candidate = "".join(token).strip()
    if candidate:
        parts.append(candidate)
    return parts


def _normalize_roster_line(value: str) -> str:
    value = re.sub(r",\s+\(", " (", value)
    value = re.sub(r"\)\s+(?=[A-ZÁÉÍÓÚÑÜ])", "), ", value)
    value = re.sub(r"\by(?=[A-ZÁÉÍÓÚÑÜ])", "y ", value)
    value = re.sub(r"\s+y\s+", ", ", value)
    value = re.sub(r"\s+e\s+", ", ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().rstrip(".")


def _parse_player_entry(entry: str) -> tuple[str, str | None]:
    cleaned = entry.strip().strip(",").strip(".")
    match = re.match(r"^(?P<player>.+?)(?:\s*\((?P<club>[^)]*)\))?$", cleaned)
    if not match:
        return cleaned, None
    player = match.group("player").strip(" ,")
    club = match.group("club")
    return player, club.strip() if club else None


def parse_roster_text(text: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    current_team: str | None = None
    team_coaches: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue

        if ":" not in line:
            current_team = line
            continue

        if current_team is None:
            continue

        label, content = line.split(":", 1)
        label_norm = normalize_text(label).replace("_", "")
        content = content.strip()

        if label_norm == "entrenador":
            team_coaches[current_team] = content.strip().strip(".")
            continue

        if label_norm not in POSITION_MAP:
            continue

        normalized_content = _normalize_roster_line(content)
        for entry in _split_outside_parentheses(normalized_content):
            player, club = _parse_player_entry(entry)
            if not player:
                continue
            rows.append(
                {
                    "team": current_team,
                    "team_norm": normalize_team_name(current_team),
                    "player": player,
                    "player_norm": normalize_text(player),
                    "position_group": POSITION_MAP[label_norm],
                    "club": club,
                    "coach": None,
                    "is_active": 1,
                }
            )

    df = pd.DataFrame(rows)
    if not df.empty:
        df["coach"] = df["team"].map(team_coaches)
        df = df.drop_duplicates(subset=["team_norm", "player_norm"]).reset_index(drop=True)
    return df


def parse_roster_file(input_path: Path | None = None) -> pd.DataFrame:
    settings = get_settings()
    path = input_path or settings.raw_dir / "seleccionados_mundialistas.md"
    text = path.read_text(encoding="utf-8")
    return parse_roster_text(text)


def save_rosters_csv(df: pd.DataFrame, output_path: Path | None = None) -> Path:
    settings = get_settings()
    path = output_path or settings.processed_dir / "rosters.csv"
    settings.ensure_directories()
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def ingest_rosters(input_path: Path | None = None, output_path: Path | None = None) -> pd.DataFrame:
    df = parse_roster_file(input_path)
    save_rosters_csv(df, output_path)
    return df
