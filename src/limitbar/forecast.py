from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from limitbar.config import app_data_dir
from limitbar.models import LimitWindow, UsageSnapshot


@dataclass(frozen=True, slots=True)
class Sample:
    at: datetime
    used: float
    reset: datetime | None


class UsageForecaster:
    """Persisted, conservative burn-rate estimates for each quota window."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "forecast.json"
        self.history = self._load()

    def enrich(self, snapshot: UsageSnapshot) -> UsageSnapshot:
        stamp = snapshot.fetched_at.astimezone(timezone.utc)
        windows: list[LimitWindow] = []
        for window in snapshot.windows:
            if window.used_percent is None:
                windows.append(window)
                continue
            key = f"{snapshot.provider_id}:{_base_window_id(window.id)}"
            samples = self.history.setdefault(key, [])
            reset = window.resets_at.astimezone(timezone.utc) if window.resets_at else None
            if samples and _started_new_window(samples[-1], float(window.used_percent), reset):
                samples.clear()
            current = Sample(stamp, float(window.used_percent), reset)
            if samples and samples[-1].at == stamp:
                samples[-1] = current
            elif not samples or samples[-1].at < stamp:
                samples.append(current)
            horizon_hours = 6 if window.duration_minutes and window.duration_minutes <= 300 else 72
            cutoff = stamp - timedelta(hours=horizon_hours)
            self.history[key] = samples = [sample for sample in samples if sample.at >= cutoff][-96:]
            exhaustion = _project_exhaustion(samples, stamp, window)
            windows.append(replace(window, projected_exhaustion_at=exhaustion))
        self._save()
        return replace(snapshot, windows=tuple(windows))

    def retain(self, provider_ids: set[str]) -> None:
        self.history = {
            key: samples
            for key, samples in self.history.items()
            if key.split(":", 1)[0] in provider_ids
        }
        self._save()

    def _load(self) -> dict[str, list[Sample]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            result: dict[str, list[Sample]] = {}
            for key, values in raw.items():
                samples = []
                for value in values:
                    at = _parse_time(value.get("at"))
                    used = value.get("used")
                    if at is not None and isinstance(used, (int, float)):
                        samples.append(Sample(at, float(used), _parse_time(value.get("reset"))))
                if samples:
                    result[str(key)] = samples[-96:]
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            key: [
                {
                    "at": sample.at.isoformat(),
                    "used": sample.used,
                    "reset": sample.reset.isoformat() if sample.reset else None,
                }
                for sample in samples
            ]
            for key, samples in self.history.items()
            if samples
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)


def _base_window_id(window_id: str) -> str:
    return window_id.removesuffix("_estimated")


def _started_new_window(previous: Sample, used: float, reset: datetime | None) -> bool:
    if previous.used - used >= 2:
        return True
    if previous.reset and reset and abs((reset - previous.reset).total_seconds()) >= 1800 and used <= previous.used + 0.5:
        return True
    return False


def _project_exhaustion(samples: list[Sample], now: datetime, window: LimitWindow) -> datetime | None:
    used = float(window.used_percent or 0)
    if used >= 100:
        return now
    rates: list[float] = []
    if len(samples) >= 2:
        latest = samples[-1]
        minimum_span = 3600 if window.duration_minutes and window.duration_minutes >= 10_080 else 300
        for sample in samples[:-1]:
            elapsed = (latest.at - sample.at).total_seconds()
            delta = latest.used - sample.used
            if elapsed >= minimum_span and 0.25 <= delta <= 50:
                rates.append(delta * 3600 / elapsed)
    rate = float(median(rates)) if rates else _window_average_rate(window, now)
    if rate is None:
        return None
    if rate < 0.05:
        return None
    hours = max(0.0, 100.0 - used) / rate
    if hours > 24 * 60:
        return None
    return now + timedelta(hours=hours)


def _window_average_rate(window: LimitWindow, now: datetime) -> float | None:
    if window.used_percent is None or window.used_percent < 1 or not window.resets_at or not window.duration_minutes:
        return None
    reset = window.resets_at.astimezone(timezone.utc)
    started = reset - timedelta(minutes=window.duration_minutes)
    elapsed_hours = (now - started).total_seconds() / 3600
    if elapsed_hours < 0.25 or elapsed_hours >= window.duration_minutes / 60:
        return None
    return float(window.used_percent) / elapsed_hours


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
