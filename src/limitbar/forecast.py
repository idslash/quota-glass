from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

from limitbar.config import app_data_dir
from limitbar.models import LimitWindow, UsageSnapshot


@dataclass(frozen=True, slots=True)
class Sample:
    at: datetime
    used: float
    reset: datetime | None
    projected: datetime | None = None


@dataclass(frozen=True, slots=True)
class CompletedWindow:
    started_at: datetime
    ended_at: datetime
    reset: datetime | None
    max_used: float
    consumed: float
    rate_per_hour: float | None
    exhausted_at: datetime | None
    forecast_scores: tuple[float, ...] = ()
    calibration_ratios: tuple[float, ...] = ()


class UsageForecaster:
    """Long-lived per-window burn model with measured forecast calibration."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "forecast.json"
        self.history, self.completed = self._load()

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
                completed = _finish_window(samples, window.duration_minutes, stamp)
                if completed is not None:
                    self.completed.setdefault(key, []).append(completed)
                    self.completed[key] = self.completed[key][-52:]
                samples.clear()

            current = Sample(stamp, float(window.used_percent), reset)
            if samples and samples[-1].at == stamp:
                samples[-1] = current
            elif not samples or samples[-1].at < stamp:
                samples.append(current)

            periods = self.completed.get(key, [])
            exhaustion = _project_exhaustion(samples, stamp, window, periods)
            samples[-1] = replace(samples[-1], projected=exhaustion)
            horizon_hours = 12 if window.duration_minutes and window.duration_minutes <= 300 else 24 * 14
            cutoff = stamp - timedelta(hours=horizon_hours)
            self.history[key] = [sample for sample in samples if sample.at >= cutoff][-512:]

            rates = [period.rate_per_hour for period in periods if period.rate_per_hour and period.rate_per_hour > 0]
            scores = [score for period in periods for score in period.forecast_scores]
            windows.append(replace(
                window,
                projected_exhaustion_at=exhaustion,
                forecast_confidence=_confidence(len(periods), len(scores)),
                forecast_accuracy_percent=round(mean(scores), 1) if scores else None,
                historical_windows=len(periods),
                historical_rate_per_hour=round(float(median(rates)), 3) if rates else None,
            ))
        self._save()
        return replace(snapshot, windows=tuple(windows))

    def retain(self, provider_ids: set[str]) -> None:
        self.history = {key: value for key, value in self.history.items() if key.split(":", 1)[0] in provider_ids}
        self.completed = {key: value for key, value in self.completed.items() if key.split(":", 1)[0] in provider_ids}
        self._save()

    def _load(self) -> tuple[dict[str, list[Sample]], dict[str, list[CompletedWindow]]]:
        if not self.path.exists():
            return {}, {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            active_raw = raw.get("active", {}) if isinstance(raw, dict) and raw.get("version") == 2 else raw
            completed_raw = raw.get("completed", {}) if isinstance(raw, dict) and raw.get("version") == 2 else {}
            active: dict[str, list[Sample]] = {}
            for key, values in active_raw.items():
                samples = [_sample_from_json(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []
                valid = [sample for sample in samples if sample is not None]
                if valid:
                    active[str(key)] = valid[-512:]
            completed: dict[str, list[CompletedWindow]] = {}
            for key, values in completed_raw.items():
                periods = [_completed_from_json(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []
                valid = [period for period in periods if period is not None]
                if valid:
                    completed[str(key)] = valid[-52:]
            return active, completed
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}, {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "version": 2,
            "active": {key: [_sample_to_json(sample) for sample in samples] for key, samples in self.history.items() if samples},
            "completed": {key: [_completed_to_json(period) for period in periods] for key, periods in self.completed.items() if periods},
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)


def _base_window_id(window_id: str) -> str:
    return window_id.removesuffix("_estimated")


def _started_new_window(previous: Sample, used: float, reset: datetime | None) -> bool:
    if previous.used - used >= 2:
        return True
    return bool(previous.reset and reset and abs((reset - previous.reset).total_seconds()) >= 1800 and used <= previous.used + 0.5)


def _finish_window(samples: list[Sample], duration_minutes: int | None, observed_at: datetime) -> CompletedWindow | None:
    if not samples:
        return None
    reset = samples[-1].reset
    started = reset - timedelta(minutes=duration_minutes) if reset and duration_minutes else samples[0].at
    ended = min(observed_at, reset) if reset else observed_at
    elapsed_hours = max(0.0, (samples[-1].at - started).total_seconds() / 3600)
    max_used = max(sample.used for sample in samples)
    rate = max_used / elapsed_hours if elapsed_hours >= 0.25 and max_used > 0 else None
    exhausted = next((sample.at for sample in samples if sample.used >= 99.5), None)
    outcome = exhausted or reset
    scores: list[float] = []
    ratios: list[float] = []
    if outcome:
        for sample in samples:
            if not sample.projected or sample.at >= outcome:
                continue
            predicted_seconds = (sample.projected - sample.at).total_seconds()
            actual_seconds = (outcome - sample.at).total_seconds()
            if predicted_seconds <= 0 or actual_seconds <= 0:
                continue
            if exhausted:
                relative_error = abs(predicted_seconds - actual_seconds) / actual_seconds
                scores.append(max(0.0, 100.0 * (1.0 - relative_error)))
                ratios.append(max(0.4, min(2.5, actual_seconds / predicted_seconds)))
            elif sample.projected >= outcome - timedelta(minutes=5):
                scores.append(100.0)
            else:
                scores.append(max(0.0, min(100.0, 100.0 * predicted_seconds / actual_seconds)))
    return CompletedWindow(started, ended, reset, max_used, max(0.0, max_used - samples[0].used), rate, exhausted, tuple(scores), tuple(ratios))


def _project_exhaustion(samples: list[Sample], now: datetime, window: LimitWindow, periods: list[CompletedWindow]) -> datetime | None:
    used = float(window.used_percent or 0)
    if used >= 100:
        return now
    current_rate = _observed_rate(samples, window) or _window_average_rate(window, now)
    historic_rates = [period.rate_per_hour for period in periods if period.rate_per_hour and period.rate_per_hour > 0]
    historic_rate = float(median(historic_rates)) if historic_rates else None
    if current_rate is not None and historic_rate is not None:
        history_weight = min(0.45, 0.10 * len(historic_rates))
        rate = current_rate * (1.0 - history_weight) + historic_rate * history_weight
    else:
        rate = current_rate or historic_rate
    if rate is None or rate < 0.05:
        return None
    hours = max(0.0, 100.0 - used) / rate
    ratios = [ratio for period in periods for ratio in period.calibration_ratios]
    if ratios:
        hours *= max(0.55, min(1.6, float(median(ratios[-24:]))))
    if hours > 24 * 60:
        return None
    return now + timedelta(hours=hours)


def _observed_rate(samples: list[Sample], window: LimitWindow) -> float | None:
    if len(samples) < 2:
        return None
    latest = samples[-1]
    minimum_span = 3600 if window.duration_minutes and window.duration_minutes >= 10_080 else 300
    rates = []
    for sample in samples[:-1]:
        elapsed = (latest.at - sample.at).total_seconds()
        delta = latest.used - sample.used
        if elapsed >= minimum_span and 0.25 <= delta <= 50:
            rates.append(delta * 3600 / elapsed)
    return float(median(rates)) if rates else None


def _window_average_rate(window: LimitWindow, now: datetime) -> float | None:
    if window.used_percent is None or window.used_percent < 1 or not window.resets_at or not window.duration_minutes:
        return None
    reset = window.resets_at.astimezone(timezone.utc)
    started = reset - timedelta(minutes=window.duration_minutes)
    elapsed_hours = (now - started).total_seconds() / 3600
    if elapsed_hours < 0.25 or elapsed_hours >= window.duration_minutes / 60:
        return None
    return float(window.used_percent) / elapsed_hours


def _confidence(periods: int, scores: int) -> str:
    if periods >= 8 and scores >= 8:
        return "high"
    if periods >= 3 and scores >= 3:
        return "medium"
    return "low" if periods else "learning"


def _sample_to_json(sample: Sample) -> dict:
    return {"at": sample.at.isoformat(), "used": sample.used, "reset": sample.reset.isoformat() if sample.reset else None, "projected": sample.projected.isoformat() if sample.projected else None}


def _sample_from_json(value: dict) -> Sample | None:
    at, used = _parse_time(value.get("at")), value.get("used")
    return Sample(at, float(used), _parse_time(value.get("reset")), _parse_time(value.get("projected"))) if at is not None and isinstance(used, (int, float)) else None


def _completed_to_json(period: CompletedWindow) -> dict:
    return {
        "started_at": period.started_at.isoformat(), "ended_at": period.ended_at.isoformat(), "reset": period.reset.isoformat() if period.reset else None,
        "max_used": period.max_used, "consumed": period.consumed, "rate_per_hour": period.rate_per_hour,
        "exhausted_at": period.exhausted_at.isoformat() if period.exhausted_at else None,
        "forecast_scores": list(period.forecast_scores), "calibration_ratios": list(period.calibration_ratios),
    }


def _completed_from_json(value: dict) -> CompletedWindow | None:
    started, ended = _parse_time(value.get("started_at")), _parse_time(value.get("ended_at"))
    if started is None or ended is None:
        return None
    scores = tuple(float(item) for item in value.get("forecast_scores", []) if isinstance(item, (int, float)))
    ratios = tuple(float(item) for item in value.get("calibration_ratios", []) if isinstance(item, (int, float)))
    rate = float(value["rate_per_hour"]) if isinstance(value.get("rate_per_hour"), (int, float)) else None
    return CompletedWindow(started, ended, _parse_time(value.get("reset")), float(value.get("max_used", 0)), float(value.get("consumed", 0)), rate, _parse_time(value.get("exhausted_at")), scores, ratios)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
