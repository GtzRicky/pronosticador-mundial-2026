from __future__ import annotations

from quiniela.historical_loader import fetch_today_data


def fetch_lineups_by_date(date_str: str, dry_run: bool = False) -> dict[str, int]:
    return fetch_today_data(date_str=date_str, lineups_only=True, dry_run=dry_run)
