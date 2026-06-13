from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.config import get_settings
from quiniela.db import (
    claim_automation_run,
    fetch_dataframe,
    finish_automation_run,
    get_connection,
)
from quiniela.historical_loader import fetch_today_data, sync_finished_results_for_date
from quiniela.html_report import build_predictions_html_report
from quiniela.predictor import Predictor


PRE_MATCH_WINDOWS = (60, 30, 15, 5, 1)
POST_MATCH_START_MINUTES = 105
POST_MATCH_INTERVAL_MINUTES = 15
POST_MATCH_MAX_MINUTES = 360
TERMINAL_STATUSES = {"FT", "AET", "PEN"}
INACTIVE_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO"}


@dataclass(frozen=True)
class MatchdayAction:
    run_key: str
    action: str
    scheduled_for: str
    date_cdmx: str
    match_id: str | None = None
    fetch_mode: str | None = None


def _as_local_datetime(value: str, timezone_name: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone_name)
    return timestamp.to_pydatetime().astimezone(ZoneInfo(timezone_name))


def plan_matchday_actions(
    matches: list[dict[str, Any]],
    now: datetime,
    timezone_name: str = "America/Mexico_City",
) -> list[MatchdayAction]:
    local_tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(local_tz)
    actions = [
        MatchdayAction(
            run_key=f"daily:{local_now.date().isoformat()}",
            action="daily",
            scheduled_for=local_now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            date_cdmx=local_now.date().isoformat(),
            fetch_mode="full",
        ),
        MatchdayAction(
            run_key=f"hourly:{local_now.strftime('%Y-%m-%dT%H')}",
            action="hourly",
            scheduled_for=local_now.replace(minute=0, second=0, microsecond=0).isoformat(),
            date_cdmx=local_now.date().isoformat(),
            fetch_mode="hourly",
        ),
    ]

    for match in matches:
        status = str(match.get("status") or "").upper()
        if status in INACTIVE_STATUSES:
            continue
        kickoff = _as_local_datetime(str(match["datetime_cdmx"]), timezone_name)
        match_id = str(match["match_id"])
        date_cdmx = str(match["date_cdmx"])
        if status not in TERMINAL_STATUSES and local_now < kickoff:
            for minutes_before in PRE_MATCH_WINDOWS:
                scheduled = kickoff - timedelta(minutes=minutes_before)
                if scheduled <= local_now < kickoff:
                    actions.append(
                        MatchdayAction(
                            run_key=f"pre:{match_id}:t-{minutes_before}",
                            action="pre_match",
                            scheduled_for=scheduled.isoformat(),
                            date_cdmx=date_cdmx,
                            match_id=match_id,
                            fetch_mode="pre_match",
                        )
                    )

        post_start = kickoff + timedelta(minutes=POST_MATCH_START_MINUTES)
        post_end = kickoff + timedelta(minutes=POST_MATCH_MAX_MINUTES)
        has_result = bool(match.get("has_actual_result"))
        if (
            not has_result
            and status not in TERMINAL_STATUSES
            and post_start <= local_now <= post_end
        ):
            elapsed = int((local_now - post_start).total_seconds() // 60)
            slot = elapsed // POST_MATCH_INTERVAL_MINUTES
            scheduled = post_start + timedelta(
                minutes=slot * POST_MATCH_INTERVAL_MINUTES
            )
            actions.append(
                MatchdayAction(
                    run_key=f"post:{match_id}:{slot}",
                    action="post_match",
                    scheduled_for=scheduled.isoformat(),
                    date_cdmx=date_cdmx,
                    match_id=match_id,
                    fetch_mode="post_status",
                )
            )
    return actions


class MatchdayRunner:
    def __init__(
        self,
        connection=None,
        refresh: Callable[..., dict[str, Any]] = fetch_today_data,
        predict: Callable[[str], Any] | None = None,
        render: Callable[[list[str]], Any] = build_predictions_html_report,
        sync_results: Callable[..., dict[str, Any]] = sync_finished_results_for_date,
        capture_snapshot: Callable[..., dict[str, Any]] | None = None,
        finalize_evidence: Callable[..., dict[str, Any]] | None = None,
        lineup_fallback: Callable[..., dict[str, Any]] | None = None,
        timezone_name: str | None = None,
    ) -> None:
        from quiniela.player_evidence import (
            capture_pre_match_snapshot,
            finalize_player_evidence,
        )
        from quiniela.web_lineup_fallback import refresh_web_lineup_fallback

        settings = get_settings()
        self.connection = connection or get_connection(settings.db_path)
        self.refresh = refresh
        self.predict = predict or self._predict_date
        self.render = render
        self.sync_results = sync_results
        self.capture_snapshot = capture_snapshot or capture_pre_match_snapshot
        self.finalize_evidence = finalize_evidence or finalize_player_evidence
        self.lineup_fallback = lineup_fallback or refresh_web_lineup_fallback
        self.timezone_name = timezone_name or settings.local_timezone
        self._web_fallback_run_keys: set[str] = set()

    def _predict_date(self, date_str: str) -> Any:
        return Predictor().predict_by_date(date_str)

    def _load_relevant_matches(self, now: datetime) -> list[dict[str, Any]]:
        local_now = now.astimezone(ZoneInfo(self.timezone_name))
        dates = [
            (local_now.date() - timedelta(days=1)).isoformat(),
            local_now.date().isoformat(),
        ]
        rows = fetch_dataframe(
            self.connection,
            """
            SELECT m.*,
                   CASE WHEN ar.match_id IS NULL THEN 0 ELSE 1 END AS has_actual_result
            FROM matches m
            LEFT JOIN actual_results ar ON ar.match_id = m.match_id
            WHERE m.date_cdmx IN (?, ?)
            ORDER BY m.datetime_cdmx
            """,
            dates,
        )
        return rows.to_dict(orient="records")

    def _execute(self, action: MatchdayAction) -> dict[str, Any]:
        force_refresh = action.action in {"hourly", "pre_match", "post_match"}
        refresh_result = self.refresh(
            action.date_cdmx,
            force_refresh=force_refresh,
            fetch_mode=action.fetch_mode or "hourly",
        )
        result: dict[str, Any] = {"refresh": refresh_result}
        if action.action == "pre_match" and action.match_id:
            window_label = action.run_key.rsplit(":", 1)[-1]
            minutes_before = int(window_label.removeprefix("t-"))
            if minutes_before <= 30 and action.run_key in self._web_fallback_run_keys:
                result["lineup_fallback"] = self.lineup_fallback(
                    action.match_id,
                    window_label=window_label,
                    connection=self.connection,
                )
        if action.action in {"hourly", "post_match"}:
            result["results"] = self.sync_results(
                action.date_cdmx,
                connection=self.connection,
            )
            newly_finished = set(
                result["results"].get(
                    "new_match_ids",
                    result["results"].get("match_ids", []),
                )
            )
            should_fetch_final_data = (
                action.match_id in newly_finished
                if action.action == "post_match"
                else bool(newly_finished)
            )
            if should_fetch_final_data:
                result["final_refresh"] = self.refresh(
                    action.date_cdmx,
                    force_refresh=True,
                    fetch_mode="full",
                )
                result["evidence"] = self.finalize_evidence(
                    sorted(newly_finished),
                    connection=self.connection,
                )
        result["prediction"] = self.predict(action.date_cdmx)
        if action.action == "pre_match" and action.match_id:
            result["snapshot"] = self.capture_snapshot(
                action.match_id,
                window_label=action.run_key.rsplit(":", 1)[-1],
                connection=self.connection,
                source_kind="live",
            )
        result["html"] = str(self.render([action.date_cdmx]))
        return result

    def run(self, now: datetime | None = None) -> dict[str, Any]:
        local_tz = ZoneInfo(self.timezone_name)
        local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
        actions = plan_matchday_actions(
            self._load_relevant_matches(local_now),
            local_now,
            self.timezone_name,
        )
        fallback_actions: dict[str, tuple[int, str]] = {}
        for action in actions:
            if action.action != "pre_match" or not action.match_id:
                continue
            minutes_before = int(action.run_key.rsplit(":", 1)[-1].removeprefix("t-"))
            if minutes_before > 30:
                continue
            current = fallback_actions.get(action.match_id)
            if current is None or minutes_before < current[0]:
                fallback_actions[action.match_id] = (minutes_before, action.run_key)
        self._web_fallback_run_keys = {
            run_key for _, run_key in fallback_actions.values()
        }
        completed: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []
        for action in actions:
            claimed = claim_automation_run(
                self.connection,
                run_key=action.run_key,
                action=action.action,
                scheduled_for=action.scheduled_for,
                match_id=action.match_id,
                details=asdict(action),
            )
            if not claimed:
                skipped.append(action.run_key)
                continue
            try:
                details = self._execute(action)
            except Exception as exc:
                finish_automation_run(
                    self.connection,
                    action.run_key,
                    "failed",
                    {"error": str(exc), "action": asdict(action)},
                )
                failed.append({"run_key": action.run_key, "error": str(exc)})
                continue
            finish_automation_run(
                self.connection,
                action.run_key,
                "completed",
                details,
            )
            completed.append(action.run_key)
        return {
            "now": local_now.isoformat(),
            "planned": len(actions),
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
        }
