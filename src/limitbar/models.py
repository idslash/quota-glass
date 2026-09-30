from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Health(str, Enum):
    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LimitWindow:
    id: str
    label: str
    used_percent: float | None
    resets_at: datetime | None = None
    duration_minutes: int | None = None
    projected_exhaustion_at: datetime | None = None
    forecast_confidence: str | None = None
    forecast_accuracy_percent: float | None = None
    historical_windows: int = 0
    historical_rate_per_hour: float | None = None

    @property
    def remaining_percent(self) -> int | None:
        if self.used_percent is None:
            return None
        return round(max(0.0, min(100.0, 100.0 - self.used_percent)))


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    provider_id: str
    provider_name: str
    windows: tuple[LimitWindow, ...]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
    health: Health = Health.OK
    message: str | None = None
    banked_resets_available: int | None = None
    banked_resets_expire_at: datetime | None = None

    def window(self, window_id: str) -> LimitWindow | None:
        return next((item for item in self.windows if item.id == window_id), None)

    @property
    def primary(self) -> LimitWindow | None:
        candidates = [window for window in self.windows if window.remaining_percent is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda window: window.remaining_percent or 0)

    def as_cache_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source,
            "banked_resets_available": self.banked_resets_available,
            "banked_resets_expire_at": self.banked_resets_expire_at.isoformat() if self.banked_resets_expire_at else None,
            "windows": [
                {
                    "id": item.id,
                    "label": item.label,
                    "used_percent": item.used_percent,
                    "resets_at": item.resets_at.isoformat() if item.resets_at else None,
                    "duration_minutes": item.duration_minutes,
                    "projected_exhaustion_at": item.projected_exhaustion_at.isoformat() if item.projected_exhaustion_at else None,
                    "forecast_confidence": item.forecast_confidence,
                    "forecast_accuracy_percent": item.forecast_accuracy_percent,
                    "historical_windows": item.historical_windows,
                    "historical_rate_per_hour": item.historical_rate_per_hour,
                }
                for item in self.windows
            ],
        }

    @classmethod
    def from_cache_dict(cls, value: dict) -> "UsageSnapshot":
        return cls(
            provider_id=str(value["provider_id"]),
            provider_name=str(value["provider_name"]),
            fetched_at=_parse_time(value.get("fetched_at")) or datetime.now(timezone.utc),
            source=str(value.get("source", "cache")),
            health=Health.STALE,
            message="Showing the last successful reading",
            banked_resets_available=_integer(value.get("banked_resets_available")),
            banked_resets_expire_at=_parse_time(value.get("banked_resets_expire_at")),
            windows=tuple(
                LimitWindow(
                    id=str(item["id"]),
                    label=str(item["label"]),
                    used_percent=_number(item.get("used_percent")),
                    resets_at=_parse_time(item.get("resets_at")),
                    duration_minutes=_integer(item.get("duration_minutes")),
                    projected_exhaustion_at=_parse_time(item.get("projected_exhaustion_at")),
                    forecast_confidence=str(item["forecast_confidence"]) if item.get("forecast_confidence") else None,
                    forecast_accuracy_percent=_number(item.get("forecast_accuracy_percent")),
                    historical_windows=_integer(item.get("historical_windows")) or 0,
                    historical_rate_per_hour=_number(item.get("historical_rate_per_hour")),
                )
                for item in value.get("windows", [])
            ),
        )


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_reset(instant: datetime | None, now: datetime | None = None) -> str:
    if instant is None:
        return "reset —"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    remaining = instant.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    seconds = max(0, int(remaining.total_seconds()))
    if seconds < 60:
        return "reset <1m"
    if seconds < 3600:
        return f"reset {seconds // 60}m"
    if seconds < 86400:
        hours, rem = divmod(seconds, 3600)
        return f"reset {hours}h {rem // 60}m"
    days, rem = divmod(seconds, 86400)
    return f"reset {days}d {rem // 3600}h"
