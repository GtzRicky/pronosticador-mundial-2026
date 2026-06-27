from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from quiniela.wake_scheduler import plan_wake_events


TZ = ZoneInfo("America/Mexico_City")


def _match(match_id: str, kickoff: str, status: str = "NS") -> dict[str, Any]:
    return {
        "match_id": match_id,
        "datetime_cdmx": kickoff,
        "status": status,
    }


def test_plan_wake_events_creates_pre_and_post_windows() -> None:
    now = datetime(2026, 6, 25, 10, 0, tzinfo=TZ)
    events = plan_wake_events(
        [_match("match-1", "2026-06-25T14:00:00-06:00")],
        now,
    )
    reasons = {reason for event in events for reason in event.reasons}

    assert {
        "pre:match-1:t-60",
        "pre:match-1:t-30",
        "pre:match-1:t-15",
        "pre:match-1:t-5",
        "pre:match-1:t-1",
        "post:match-1:t+105",
        "post:match-1:t+360",
    }.issubset(reasons)


def test_plan_wake_events_groups_simultaneous_matches() -> None:
    now = datetime(2026, 6, 25, 10, 0, tzinfo=TZ)
    events = plan_wake_events(
        [
            _match("match-1", "2026-06-25T14:00:00-06:00"),
            _match("match-2", "2026-06-25T14:00:00-06:00"),
        ],
        now,
    )
    t60 = next(
        event
        for event in events
        if event.scheduled_for == datetime(2026, 6, 25, 13, 0, tzinfo=TZ)
    )

    assert t60.reasons == ("pre:match-1:t-60", "pre:match-2:t-60")


def test_plan_wake_events_skips_inactive_and_past_events() -> None:
    now = datetime(2026, 6, 25, 15, 0, tzinfo=TZ)
    events = plan_wake_events(
        [
            _match("past", "2026-06-25T14:00:00-06:00", status="FT"),
            _match("cancelled", "2026-06-25T17:00:00-06:00", status="CANC"),
        ],
        now,
    )

    assert all(
        "cancelled" not in reason for event in events for reason in event.reasons
    )
    assert all(event.scheduled_for > now for event in events)


def test_plan_wake_events_skips_post_polling_after_result() -> None:
    now = datetime(2026, 6, 25, 14, 30, tzinfo=TZ)
    match = _match("finished", "2026-06-25T14:00:00-06:00", status="FT")
    match["has_actual_result"] = True

    events = plan_wake_events([match], now)

    assert events == []
