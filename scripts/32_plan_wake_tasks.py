from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection
from quiniela.wake_scheduler import plan_wake_events, serialize_wake_events


def main() -> int:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    matches = fetch_dataframe(
        connection,
        """
        SELECT m.match_id, m.datetime_cdmx, m.status,
               CASE WHEN ar.match_id IS NULL THEN 0 ELSE 1 END AS has_actual_result
        FROM matches m
        LEFT JOIN actual_results ar ON ar.match_id = m.match_id
        WHERE m.competition_id = 'fifa_world_cup'
          AND m.season_id = 'world_cup_2026'
        ORDER BY datetime(m.datetime_cdmx)
        """,
    ).to_dict(orient="records")
    now = datetime.now(ZoneInfo(settings.local_timezone))
    events = plan_wake_events(
        matches,
        now,
        timezone_name=settings.local_timezone,
        horizon_days=3,
    )

    schedule_path = settings.logs_dir / "wake_schedule.json"
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "timezone": settings.local_timezone,
                "events": serialize_wake_events(events),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "sync_wake_tasks.ps1"),
        "-SchedulePath",
        str(schedule_path),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"No se pudieron sincronizar wake tasks: {detail}")
    print(
        json.dumps(
            {
                "schedule_path": str(schedule_path),
                "events": len(events),
                "first_event": events[0].scheduled_for.isoformat() if events else None,
                "last_event": events[-1].scheduled_for.isoformat() if events else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
