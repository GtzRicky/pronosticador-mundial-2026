from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quiniela.db import get_connection
from quiniela.matchday import MatchdayRunner, plan_matchday_actions


TZ = ZoneInfo("America/Mexico_City")


def _match(kickoff: str, status: str = "NS", result: bool = False) -> dict:
    return {
        "match_id": "match-1",
        "date_cdmx": kickoff[:10],
        "datetime_cdmx": kickoff,
        "status": status,
        "has_actual_result": int(result),
    }


def test_plan_includes_t_minus_one_and_late_restart_windows() -> None:
    kickoff = "2026-06-13T13:00:00-06:00"
    at_t_minus_one = datetime(2026, 6, 13, 12, 59, tzinfo=TZ)
    actions = plan_matchday_actions([_match(kickoff)], at_t_minus_one)
    keys = {action.run_key for action in actions}
    assert "pre:match-1:t-1" in keys

    restarted_late = datetime(2026, 6, 13, 12, 50, tzinfo=TZ)
    late_actions = plan_matchday_actions([_match(kickoff)], restarted_late)
    late_keys = {action.run_key for action in late_actions}
    assert "pre:match-1:t-60" in late_keys
    assert "pre:match-1:t-30" in late_keys
    assert "pre:match-1:t-15" in late_keys
    assert "pre:match-1:t-5" not in late_keys


def test_plan_post_match_uses_current_fifteen_minute_slot() -> None:
    kickoff = "2026-06-13T13:00:00-06:00"
    now = datetime(2026, 6, 13, 15, 0, tzinfo=TZ)
    actions = plan_matchday_actions([_match(kickoff)], now)
    post = [action for action in actions if action.action == "post_match"]
    assert len(post) == 1
    assert post[0].run_key == "post:match-1:1"


def test_runner_is_idempotent_and_records_failures(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "runner.sqlite")
    calls = []

    def refresh(*args, **kwargs):
        calls.append(("refresh", args, kwargs))
        return {"fixtures": 1}

    runner = MatchdayRunner(
        connection=connection,
        refresh=refresh,
        predict=lambda date: calls.append(("predict", date)),
        render=lambda dates: tmp_path / "index.html",
        sync_results=lambda *args, **kwargs: {"updated": 0},
        finalize_evidence=lambda *args, **kwargs: {},
    )
    runner._load_relevant_matches = lambda now: []
    now = datetime(2026, 6, 13, 10, 1, tzinfo=TZ)

    first = runner.run(now)
    second = runner.run(now)

    assert len(first["completed"]) == 2
    assert len(second["skipped"]) == 2
    assert len([call for call in calls if call[0] == "refresh"]) == 2

    failing = MatchdayRunner(
        connection=connection,
        refresh=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
        predict=lambda date: None,
        render=lambda dates: tmp_path / "index.html",
        finalize_evidence=lambda *args, **kwargs: {},
    )
    failing._load_relevant_matches = lambda now: []
    result = failing.run(datetime(2026, 6, 13, 11, 1, tzinfo=TZ))
    assert result["failed"]


def test_hourly_run_syncs_new_final_result_and_fetches_final_data(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "hourly-final.sqlite")
    calls = []

    def refresh(*args, **kwargs):
        calls.append(("refresh", kwargs["fetch_mode"]))
        return {"fixtures": 1}

    def sync_results(*args, **kwargs):
        calls.append(("sync", args[0]))
        return {
            "updated": 1,
            "match_ids": ["match-final"],
            "new_match_ids": ["match-final"],
        }

    runner = MatchdayRunner(
        connection=connection,
        refresh=refresh,
        predict=lambda date: calls.append(("predict", date)),
        render=lambda dates: tmp_path / "index.html",
        sync_results=sync_results,
        finalize_evidence=lambda match_ids, **kwargs: calls.append(
            ("evidence", tuple(match_ids))
        )
        or {"targets": 1},
    )
    runner._load_relevant_matches = lambda now: []

    result = runner.run(datetime(2026, 6, 13, 10, 1, tzinfo=TZ))

    assert not result["failed"]
    assert ("sync", "2026-06-13") in calls
    assert ("evidence", ("match-final",)) in calls
    assert ("refresh", "hourly") in calls
    assert calls.count(("refresh", "full")) == 2


def test_hourly_run_does_not_refetch_known_final_result(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "hourly-known-final.sqlite")
    fetch_modes = []

    runner = MatchdayRunner(
        connection=connection,
        refresh=lambda *args, **kwargs: (
            fetch_modes.append(kwargs["fetch_mode"]) or {"fixtures": 1}
        ),
        predict=lambda date: None,
        render=lambda dates: tmp_path / "index.html",
        sync_results=lambda *args, **kwargs: {
            "updated": 1,
            "match_ids": ["match-final"],
            "new_match_ids": [],
        },
        finalize_evidence=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("known results must not rebuild evidence")
        ),
    )
    runner._load_relevant_matches = lambda now: []

    runner.run(datetime(2026, 6, 13, 10, 1, tzinfo=TZ))

    assert fetch_modes.count("hourly") == 1
    assert fetch_modes.count("full") == 1


def test_pre_match_run_captures_window_after_prediction(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "pre-match-snapshot.sqlite")
    calls = []
    runner = MatchdayRunner(
        connection=connection,
        refresh=lambda *args, **kwargs: calls.append(("refresh", kwargs["fetch_mode"])) or {},
        predict=lambda date: calls.append(("predict", date)),
        render=lambda dates: calls.append(("render", tuple(dates))) or tmp_path / "index.html",
        capture_snapshot=lambda match_id, **kwargs: calls.append(
            ("snapshot", match_id, kwargs["window_label"])
        )
        or {"created": True},
        lineup_fallback=lambda match_id, **kwargs: calls.append(
            ("fallback", match_id, kwargs["window_label"])
        )
        or {"status": "completed"},
        finalize_evidence=lambda *args, **kwargs: {},
    )
    runner._load_relevant_matches = lambda now: [
        _match("2026-06-13T13:00:00-06:00")
    ]

    result = runner.run(datetime(2026, 6, 13, 12, 59, tzinfo=TZ))

    assert not result["failed"]
    fallback_index = calls.index(("fallback", "match-1", "t-1"))
    predict_index = calls.index(("predict", "2026-06-13"), fallback_index)
    snapshot_index = calls.index(("snapshot", "match-1", "t-1"))
    assert fallback_index < predict_index
    assert calls[snapshot_index - 1] == ("predict", "2026-06-13")
    assert calls[snapshot_index + 1] == ("render", ("2026-06-13",))


def test_pre_match_t_minus_sixty_does_not_use_web_fallback(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "pre-match-t60.sqlite")
    fallback_calls = []
    runner = MatchdayRunner(
        connection=connection,
        refresh=lambda *args, **kwargs: {},
        predict=lambda date: None,
        render=lambda dates: tmp_path / "index.html",
        capture_snapshot=lambda *args, **kwargs: {},
        lineup_fallback=lambda *args, **kwargs: fallback_calls.append(args) or {},
        finalize_evidence=lambda *args, **kwargs: {},
    )
    runner._load_relevant_matches = lambda now: [
        _match("2026-06-13T13:00:00-06:00")
    ]

    runner.run(datetime(2026, 6, 13, 12, 0, tzinfo=TZ))

    assert not fallback_calls
