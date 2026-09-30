from datetime import datetime, timedelta, timezone

from limitbar.forecast import UsageForecaster
from limitbar.models import LimitWindow, UsageSnapshot


def reading(at: datetime, used: float, reset: datetime) -> UsageSnapshot:
    return UsageSnapshot(
        "codex",
        "ChatGPT",
        (LimitWindow("five_hour", "5h", used, reset, 300),),
        fetched_at=at,
    )


def test_forecast_prefers_observed_burn_after_initial_window_average(tmp_path):
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    reset = start + timedelta(hours=4)
    forecaster = UsageForecaster(tmp_path / "forecast.json")
    first = forecaster.enrich(reading(start, 20, reset))
    assert first.primary.projected_exhaustion_at == reset
    second = forecaster.enrich(reading(start + timedelta(minutes=30), 25, reset))
    projected = second.primary.projected_exhaustion_at
    assert projected is not None
    assert projected == start + timedelta(hours=8)


def test_forecast_archives_history_and_reuses_it_after_reset(tmp_path):
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    forecaster = UsageForecaster(tmp_path / "forecast.json")
    forecaster.enrich(reading(start, 80, start + timedelta(hours=1)))
    forecaster.enrich(reading(start + timedelta(minutes=10), 90, start + timedelta(minutes=50)))
    reset_reading = forecaster.enrich(
        reading(start + timedelta(minutes=20), 2, start + timedelta(hours=5, minutes=20))
    )
    assert reset_reading.primary.projected_exhaustion_at is not None
    assert reset_reading.primary.historical_windows == 1
    assert reset_reading.primary.forecast_confidence == "low"
    assert reset_reading.primary.historical_rate_per_hour is not None


def test_forecast_history_persists_between_instances(tmp_path):
    path = tmp_path / "forecast.json"
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    reset = start + timedelta(days=5)
    UsageForecaster(path).enrich(reading(start, 10, reset))
    projected = UsageForecaster(path).enrich(reading(start + timedelta(hours=1), 12, reset))
    assert projected.primary.projected_exhaustion_at == start + timedelta(hours=45)


def test_weekly_forecast_falls_back_to_average_since_window_start(tmp_path):
    now = datetime(2026, 9, 30, 8, 30, tzinfo=timezone.utc)
    reset = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    snapshot = UsageSnapshot(
        "codex",
        "ChatGPT",
        (LimitWindow("seven_day", "7d", 83, reset, 10_080),),
        fetched_at=now,
    )
    enriched = UsageForecaster(tmp_path / "forecast.json").enrich(snapshot)
    remaining = enriched.primary.projected_exhaustion_at - now
    assert timedelta(hours=14) < remaining < timedelta(hours=15)


def test_weekly_forecast_ignores_short_quantized_burst(tmp_path):
    path = tmp_path / "forecast.json"
    start = datetime(2026, 9, 30, 8, 30, tzinfo=timezone.utc)
    reset = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    forecaster = UsageForecaster(path)
    first = UsageSnapshot("codex", "ChatGPT", (LimitWindow("seven_day", "7d", 83, reset, 10_080),), fetched_at=start)
    second = UsageSnapshot("codex", "ChatGPT", (LimitWindow("seven_day", "7d", 84, reset, 10_080),), fetched_at=start + timedelta(minutes=10))
    forecaster.enrich(first)
    enriched = forecaster.enrich(second)
    remaining = enriched.primary.projected_exhaustion_at - second.fetched_at
    assert timedelta(hours=13) < remaining < timedelta(hours=14)


def test_completed_windows_and_accuracy_survive_restart(tmp_path):
    path = tmp_path / "forecast.json"
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    first_reset = start + timedelta(hours=5)
    forecaster = UsageForecaster(path)
    forecaster.enrich(reading(start + timedelta(hours=1), 20, first_reset))
    forecaster.enrich(reading(start + timedelta(hours=3), 70, first_reset))
    forecaster.enrich(reading(start + timedelta(hours=4), 100, first_reset))
    next_window = UsageForecaster(path).enrich(
        reading(first_reset + timedelta(minutes=1), 1, first_reset + timedelta(hours=5))
    )
    assert next_window.primary.historical_windows == 1
    assert next_window.primary.forecast_accuracy_percent is not None
    assert next_window.primary.projected_exhaustion_at is not None
