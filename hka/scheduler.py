from datetime import datetime, timedelta
from typing import Any


class DailySchedule:
    """One daily start, with bounded retries after a failed daily run."""
    def __init__(self) -> None:
        self.day = ""
        self.attempts = 0
        self.requested_at: datetime | None = None

    def due(self, settings: dict[str, Any], now: datetime, *, running: bool,
            result: dict[str, Any] | None) -> bool:
        day = now.date().isoformat()
        if self.day != day:
            self.day, self.attempts, self.requested_at = day, 0, None
        if not settings.get("scheduler_enabled") or running or now.strftime("%H:%M") < settings.get("daily_at", "07:30"):
            return False
        if self.attempts == 0:
            return True
        if self.attempts > settings.get("retry_attempts", 0) or not result:
            return False
        if result.get("mode") != "daily" or result.get("status") != "failed":
            return False
        try:
            finished = datetime.fromisoformat(result["finished_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return bool(self.requested_at and finished >= self.requested_at and
                    now >= finished + timedelta(minutes=settings.get("retry_interval_minutes", 15)))

    def started(self, now: datetime) -> None:
        self.attempts += 1
        self.requested_at = now.replace(microsecond=0)
