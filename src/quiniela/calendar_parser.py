from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.config import get_settings
from quiniela.name_maps import normalize_team_name


DATE_RE = re.compile(
    r"^(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo),\s+(\d{1,2}) de ([a-záéíóú]+) (\d{4})$",
    re.IGNORECASE,
)
MATCH_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}) - (?P<home>.+?) v (?P<away>.+?) [–-] Grupo (?P<group>[A-L]) - (?P<stadium>.+)$"
)

MONTH_MAP = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def parse_calendar_text(text: str) -> pd.DataFrame:
    rows = []
    current_date: datetime | None = None
    et_tz = ZoneInfo("America/New_York")
    cdmx_tz = ZoneInfo("America/Mexico_City")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        date_match = DATE_RE.match(line)
        if date_match:
            _, day, month_name, year = date_match.groups()
            current_date = datetime(
                int(year),
                MONTH_MAP[month_name.lower()],
                int(day),
            )
            continue

        match = MATCH_RE.match(line)
        if not match or current_date is None:
            continue

        time_et = match.group("time")
        hour, minute = map(int, time_et.split(":"))
        kickoff_et = current_date.replace(hour=hour, minute=minute, tzinfo=et_tz)
        kickoff_cdmx = kickoff_et.astimezone(cdmx_tz)

        home_team = match.group("home").strip()
        away_team = match.group("away").strip()
        home_team_norm = normalize_team_name(home_team)
        away_team_norm = normalize_team_name(away_team)

        rows.append(
            {
                "match_id": f"{kickoff_et.strftime('%Y%m%d')}_{home_team_norm}_{away_team_norm}",
                "date_et": kickoff_et.date().isoformat(),
                "time_et": kickoff_et.strftime("%H:%M"),
                "datetime_et": kickoff_et.isoformat(),
                "date_cdmx": kickoff_cdmx.date().isoformat(),
                "time_cdmx": kickoff_cdmx.strftime("%H:%M"),
                "datetime_cdmx": kickoff_cdmx.isoformat(),
                "home_team": home_team,
                "away_team": away_team,
                "home_team_norm": home_team_norm,
                "away_team_norm": away_team_norm,
                "group": match.group("group"),
                "stadium": match.group("stadium").strip(),
                "stage": "group",
                "status": "scheduled",
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["datetime_et", "group", "home_team"]).reset_index(drop=True)
    return df


def parse_calendar_file(input_path: Path | None = None) -> pd.DataFrame:
    settings = get_settings()
    path = input_path or settings.raw_dir / "calendario_mundial.md"
    text = path.read_text(encoding="utf-8")
    return parse_calendar_text(text)


def save_calendar_csv(df: pd.DataFrame, output_path: Path | None = None) -> Path:
    settings = get_settings()
    path = output_path or settings.processed_dir / "calendar.csv"
    settings.ensure_directories()
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def ingest_calendar(input_path: Path | None = None, output_path: Path | None = None) -> pd.DataFrame:
    df = parse_calendar_file(input_path)
    save_calendar_csv(df, output_path)
    return df
