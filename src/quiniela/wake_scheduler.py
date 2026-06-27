from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.matchday import (
    INACTIVE_STATUSES,
    POST_MATCH_INTERVAL_MINUTES,
    POST_MATCH_MAX_MINUTES,
    POST_MATCH_START_MINUTES,
    PRE_MATCH_WINDOWS,
    TERMINAL_STATUSES,
)


WAKE_TASK_PREFIX = "Quiniela Mundial 2026 Wake "


@dataclass(frozen=True)
class WakeEvent:
    scheduled_for: datetime
    reasons: tuple[str, ...]

    @property
    def task_name(self) -> str:
        stamp = self.scheduled_for.strftime("%Y%m%dT%H%M%S")
        return f"{WAKE_TASK_PREFIX}{stamp}"


def _local_datetime(value: str, timezone_name: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone_name)
    return timestamp.to_pydatetime().astimezone(ZoneInfo(timezone_name))


def plan_wake_events(
    matches: Iterable[dict[str, Any]],
    now: datetime,
    *,
    timezone_name: str = "America/Mexico_City",
    horizon_days: int = 3,
    minimum_lead_seconds: int = 30,
) -> list[WakeEvent]:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    horizon = local_now + timedelta(days=horizon_days)
    earliest = local_now + timedelta(seconds=minimum_lead_seconds)
    reasons_by_time: dict[datetime, set[str]] = {}

    for match in matches:
        status = str(match.get("status") or "").upper()
        if status in INACTIVE_STATUSES:
            continue
        kickoff = _local_datetime(str(match["datetime_cdmx"]), timezone_name)
        match_id = str(match["match_id"])
        if kickoff > horizon:
            continue

        if status not in TERMINAL_STATUSES:
            for minutes_before in PRE_MATCH_WINDOWS:
                scheduled = kickoff - timedelta(minutes=minutes_before)
                if earliest <= scheduled <= horizon:
                    reasons_by_time.setdefault(scheduled, set()).add(
                        f"pre:{match_id}:t-{minutes_before}"
                    )

        if (
            not bool(match.get("has_actual_result"))
            and status not in TERMINAL_STATUSES
        ):
            post_minute = POST_MATCH_START_MINUTES
            while post_minute <= POST_MATCH_MAX_MINUTES:
                scheduled = kickoff + timedelta(minutes=post_minute)
                if earliest <= scheduled <= horizon:
                    reasons_by_time.setdefault(scheduled, set()).add(
                        f"post:{match_id}:t+{post_minute}"
                    )
                post_minute += POST_MATCH_INTERVAL_MINUTES

    return [
        WakeEvent(scheduled_for=scheduled, reasons=tuple(sorted(reasons)))
        for scheduled, reasons in sorted(reasons_by_time.items())
    ]


def serialize_wake_events(events: Iterable[WakeEvent]) -> list[dict[str, Any]]:
    return [
        {
            "task_name": event.task_name,
            "scheduled_for": event.scheduled_for.isoformat(),
            "reasons": list(event.reasons),
        }
        for event in events
    ]
