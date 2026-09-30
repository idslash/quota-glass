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


def test_forecast_requires_real_observed_burn(tmp_path):
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    reset = start + timedelta(hours=4)
    forecaster = UsageForecaster(tmp_path / "forecast.json")
    first = forecaster.enrich(reading(start, 20, reset))
    assert first.primary.projected_exhaustion_at is None
    second = forecaster.enrich(reading(start + timedelta(minutes=30), 25, reset))
    projected = second.primary.projected_exhaustion_at
    assert projected is not None
    assert projected == start + timedelta(hours=8)


def test_forecast_clears_history_after_reset(tmp_path):
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    forecaster = UsageForecaster(tmp_path / "forecast.json")
    forecaster.enrich(reading(start, 80, start + timedelta(hours=1)))
    forecaster.enrich(reading(start + timedelta(minutes=10), 90, start + timedelta(minutes=50)))
    reset_reading = forecaster.enrich(
        reading(start + timedelta(minutes=20), 2, start + timedelta(hours=5, minutes=20))
    )
    assert reset_reading.primary.projected_exhaustion_at is None


def test_forecast_history_persists_between_instances(tmp_path):
    path = tmp_path / "forecast.json"
    start = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    reset = start + timedelta(days=5)
    UsageForecaster(path).enrich(reading(start, 10, reset))
    projected = UsageForecaster(path).enrich(reading(start + timedelta(hours=1), 12, reset))
    assert projected.primary.projected_exhaustion_at == start + timedelta(hours=45)
