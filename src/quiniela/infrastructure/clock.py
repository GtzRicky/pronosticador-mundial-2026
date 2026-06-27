from __future__ import annotations

from datetime import datetime, timezone, tzinfo


class SystemClock:
    def __init__(self, timezone_info: tzinfo = timezone.utc) -> None:
        self.timezone_info = timezone_info

    def now(self) -> datetime:
        return datetime.now(self.timezone_info)
