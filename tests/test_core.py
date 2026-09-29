import json
import logging
from datetime import datetime, timedelta, timezone

from limitbar.cache import SnapshotCache
from limitbar.logging_setup import SecretFilter
from limitbar.models import Health, LimitWindow, UsageSnapshot, format_reset
from limitbar.notifications import NotificationEngine, _robust_burn_rate
from limitbar.config import Settings
from limitbar.poller import Backoff, _next_success_delay


def snapshot() -> UsageSnapshot:
    return UsageSnapshot(
        provider_id="claude",
        provider_name="Claude",
        source="test",
        windows=(LimitWindow("five_hour", "5h", 22.4, datetime(2026, 9, 27, tzinfo=timezone.utc), 300),),
    )


def test_cache_is_secret_free_and_loaded_as_stale(tmp_path):
    path = tmp_path / "cache.json"
    cache = SnapshotCache(path)
    cache.save({"claude": snapshot()})
    raw = path.read_text("utf-8")
    assert "token" not in raw.lower()
    loaded = cache.load()["claude"]
    assert loaded.health == Health.STALE
    assert loaded.primary.remaining_percent == 78


def test_cache_ignores_corruption(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("not json", encoding="utf-8")
    assert SnapshotCache(path).load() == {}


def test_cache_retain_removes_disabled_provider(tmp_path):
    cache = SnapshotCache(tmp_path / "cache.json")
    codex = UsageSnapshot(
        provider_id="codex",
        provider_name="ChatGPT",
        source="test",
        windows=(LimitWindow("five_hour", "5h", 10, None, 300),),
    )
    cache.save({"claude": snapshot(), "codex": codex})
    retained = cache.retain({"codex"})
    assert set(retained) == {"codex"}
    assert set(cache.load()) == {"codex"}


def test_backoff_respects_server_hint_and_resets():
    backoff = Backoff(180, maximum_seconds=1800)
    assert backoff.failure(jitter=1) == 180
    assert backoff.failure(jitter=1) == 360
    assert backoff.failure(retry_after_seconds=900, jitter=1) == 900
    assert backoff.success() == 180
    assert backoff.failure(jitter=1) == 180


def test_poller_refreshes_immediately_around_reset():
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    current = UsageSnapshot(
        "codex",
        "ChatGPT",
        (LimitWindow("five_hour", "5h", 100, now + timedelta(seconds=8), 300),),
        fetched_at=now,
    )
    assert _next_success_delay(current, 180, now) == 10

    upcoming = UsageSnapshot(
        "codex",
        "ChatGPT",
        (LimitWindow("five_hour", "5h", 99, now + timedelta(seconds=75), 300),),
        fetched_at=now,
    )
    assert _next_success_delay(upcoming, 180, now) == 78


def test_taskbar_layout_setting_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = Settings(taskbar_mode="compact", language="en")
    settings.save()
    assert Settings.load().taskbar_mode == "compact"
    assert Settings.load().language == "en"


def test_format_reset_is_compact():
    now = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    assert format_reset(now + timedelta(minutes=42), now) == "reset 42m"
    assert format_reset(now + timedelta(hours=3, minutes=9), now) == "reset 3h 9m"
    assert format_reset(now + timedelta(days=2, hours=4), now) == "reset 2d 4h"


def test_log_filter_redacts_bearer_and_api_key():
    record = logging.LogRecord("x", logging.INFO, "", 0, "Bearer abc.secret and sk-thisisaverylongsecret", (), None)
    SecretFilter().filter(record)
    assert "abc.secret" not in record.getMessage()
    assert "sk-this" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


def test_notifications_reset_soon_are_actionable_and_deduplicated():
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    settings = Settings(quiet_hours=False, language="ru")
    engine = NotificationEngine(settings)
    current = UsageSnapshot(
        "claude",
        "Claude",
        (LimitWindow("five_hour", "5h", 55, now + timedelta(minutes=24), 300),),
        fetched_at=now,
    )
    notices = engine.evaluate(current, now)
    assert len(notices) == 1
    assert "осталось 45%" in notices[0].message
    assert engine.evaluate(current, now + timedelta(minutes=3)) == []


def test_notifications_detect_reset_completion():
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    settings = Settings(quiet_hours=False, notify_reset_soon=False, notify_fast_usage=False, language="ru")
    engine = NotificationEngine(settings)
    before = UsageSnapshot("codex", "ChatGPT", (LimitWindow("five_hour", "5h", 82, now + timedelta(minutes=2), 300),), fetched_at=now)
    after = UsageSnapshot("codex", "ChatGPT", (LimitWindow("five_hour", "5h", 3, now + timedelta(hours=5), 300),), fetched_at=now + timedelta(minutes=3))
    assert engine.evaluate(before, now) == []
    notices = engine.evaluate(after, now + timedelta(minutes=3))
    assert len(notices) == 1
    assert "восстановлен" in notices[0].message


def test_notifications_support_english_and_reserve_label():
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    settings = Settings(quiet_hours=False, language="en")
    engine = NotificationEngine(settings)
    current = UsageSnapshot(
        "codex",
        "ChatGPT",
        (LimitWindow("gpt_reserve", "GPT reserve", 55, now + timedelta(minutes=20), 10_080),),
        fetched_at=now,
    )
    notices = engine.evaluate(current, now)
    assert len(notices) == 1
    assert "Resets in 20 min" in notices[0].message
    assert "GPT reserve" in notices[0].message


def test_burn_rate_uses_recent_median_slopes():
    start = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    samples = [
        (start, 10),
        (start + timedelta(minutes=10), 12),
        (start + timedelta(minutes=20), 14),
        (start + timedelta(minutes=30), 40),
    ]
    rate = _robust_burn_rate(samples)
    assert rate is not None
    assert 10 <= rate <= 14
