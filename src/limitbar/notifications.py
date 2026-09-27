from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from limitbar.config import Settings
from limitbar.models import Health, LimitWindow, UsageSnapshot


@dataclass(frozen=True, slots=True)
class Notice:
    title: str
    message: str
    key: str


class NotificationEngine:
    """Turns snapshot transitions into a small number of actionable notices."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sent: set[str] = set()
        self._previous: dict[tuple[str, str], tuple[float, datetime, datetime | None]] = {}
        self._failures: dict[str, int] = {}

    def evaluate(self, snapshot: UsageSnapshot, now: datetime | None = None) -> list[Notice]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self.settings.notifications_enabled:
            self._remember(snapshot)
            return []

        notices: list[Notice] = []
        if snapshot.health != Health.OK:
            self._failures[snapshot.provider_id] = self._failures.get(snapshot.provider_id, 0) + 1
            if self.settings.notify_auth_errors and self._failures[snapshot.provider_id] >= 2:
                key = f"stale:{snapshot.provider_id}:{snapshot.message}"
                notices.append(Notice(f"LimitBar · {snapshot.provider_name}", snapshot.message or "Данные давно не обновлялись", key))
        else:
            self._failures[snapshot.provider_id] = 0
            for window in snapshot.windows:
                notices.extend(self._window_notices(snapshot, window, now))

        self._remember(snapshot)
        if self.settings.quiet_hours and _in_quiet_hours(now.astimezone()):
            return []
        result = [notice for notice in notices if notice.key not in self._sent]
        self._sent.update(notice.key for notice in result)
        return result

    def _window_notices(self, snapshot: UsageSnapshot, window: LimitWindow, now: datetime) -> list[Notice]:
        if window.used_percent is None:
            return []
        remaining = round(100 - window.used_percent)
        reset = window.resets_at.astimezone(timezone.utc) if window.resets_at else None
        reset_key = reset.isoformat(timespec="minutes") if reset else "unknown"
        label = "5-часовой лимит" if window.duration_minutes == 300 else "недельный лимит"
        notices: list[Notice] = []

        if reset:
            minutes = (reset - now).total_seconds() / 60
            if (
                self.settings.notify_reset_soon
                and 0 < minutes <= self.settings.reset_notice_minutes
                and remaining >= self.settings.reset_notice_remaining
            ):
                notices.append(
                    Notice(
                        f"LimitBar · {snapshot.provider_name}",
                        f"Reset через {_duration(minutes * 60)} · осталось {remaining}% {label}",
                        f"soon:{snapshot.provider_id}:{window.id}:{reset_key}",
                    )
                )

            if self.settings.notify_fast_usage and window.duration_minutes and window.duration_minutes >= 10_080:
                elapsed = 1 - max(0.0, (reset - now).total_seconds()) / (window.duration_minutes * 60)
                expected = max(0.0, min(100.0, elapsed * 100))
                if window.used_percent >= 40 and window.used_percent > expected + 15:
                    notices.append(
                        Notice(
                            f"LimitBar · {snapshot.provider_name}",
                            f"Недельный расход выше темпа: использовано {round(window.used_percent)}%, до reset {_duration((reset-now).total_seconds())}",
                            f"pace:{snapshot.provider_id}:{window.id}:{reset_key}",
                        )
                    )

        previous = self._previous.get((snapshot.provider_id, window.id))
        if previous:
            old_used, old_time, old_reset = previous
            if self.settings.notify_reset_complete and old_used - window.used_percent >= 20 and window.used_percent <= 10:
                notices.append(
                    Notice(
                        f"LimitBar · {snapshot.provider_name}",
                        f"{label.capitalize()} восстановлен · доступно {remaining}%",
                        f"complete:{snapshot.provider_id}:{window.id}:{snapshot.fetched_at.isoformat(timespec='minutes')}",
                    )
                )
            elapsed_seconds = (snapshot.fetched_at.astimezone(timezone.utc) - old_time).total_seconds()
            consumed = window.used_percent - old_used
            if self.settings.notify_fast_usage and reset and elapsed_seconds >= 120 and consumed >= 2:
                burn_per_hour = consumed * 3600 / elapsed_seconds
                projected_hours = remaining / burn_per_hour if burn_per_hour > 0 else 999
                reset_hours = max(0.0, (reset - now).total_seconds() / 3600)
                if projected_hours < 6 and projected_hours + 0.5 < reset_hours:
                    notices.append(
                        Notice(
                            f"LimitBar · {snapshot.provider_name}",
                            f"При текущем темпе {label} закончится примерно через {_duration(projected_hours*3600)}",
                            f"burn:{snapshot.provider_id}:{window.id}:{reset_key}",
                        )
                    )
        return notices

    def _remember(self, snapshot: UsageSnapshot) -> None:
        if snapshot.health != Health.OK:
            return
        for window in snapshot.windows:
            if window.used_percent is not None:
                self._previous[(snapshot.provider_id, window.id)] = (
                    window.used_percent,
                    snapshot.fetched_at.astimezone(timezone.utc),
                    window.resets_at.astimezone(timezone.utc) if window.resets_at else None,
                )


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч {(seconds % 3600) // 60} мин"
    return f"{seconds // 86400} д {(seconds % 86400) // 3600} ч"


def _in_quiet_hours(local_now: datetime) -> bool:
    return local_now.hour >= 23 or local_now.hour < 8
