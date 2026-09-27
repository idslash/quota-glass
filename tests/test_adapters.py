from datetime import datetime, timezone

import pytest

from limitbar.adapters.base import ProviderError
from limitbar.adapters.claude import _infer_reset_from_history, parse_claude_usage
from limitbar.adapters.codex import parse_codex_usage


def test_claude_parser_accepts_known_and_scoped_windows():
    snapshot = parse_claude_usage(
        {
            "five_hour": {"utilization": 12.5, "resets_at": "2026-09-27T12:00:00Z"},
            "seven_day": {"utilization": 30, "resets_at": "2026-10-01T00:00:00Z"},
            "limits": [
                {
                    "id": "opus-promo",
                    "percent": 7,
                    "resets_at": "2026-10-01T00:00:00Z",
                    "scope": {"model": {"display_name": "Opus"}},
                }
            ],
        }
    )
    assert snapshot.provider_id == "claude"
    assert snapshot.window("five_hour").remaining_percent == 88
    assert snapshot.window("seven_day").duration_minutes == 10_080
    assert snapshot.window("opus-promo").label == "Opus 7d"


def test_claude_parser_rejects_unknown_shape():
    with pytest.raises(ProviderError):
        parse_claude_usage({"surprise": True})


def test_claude_local_history_infers_only_observed_rolling_resets():
    minute = 60_000
    samples = [
        (0, {"fh": 58, "sd": 45}),
        (15 * minute, {"fh": 0, "sd": 0}),
        (30 * minute, {"fh": 4, "sd": 2}),
        (45 * minute, {"fh": 8, "sd": 3}),
    ]
    five_hour = _infer_reset_from_history(samples, "fh", 300, now_ms=45 * minute)
    weekly = _infer_reset_from_history(samples, "sd", 10_080, now_ms=45 * minute)
    assert five_hour == datetime.fromtimestamp((330 * minute) / 1000, timezone.utc)
    assert weekly == datetime.fromtimestamp((10_110 * minute) / 1000, timezone.utc)
    assert _infer_reset_from_history([(0, {"fh": 20}), (minute, {"fh": 30})], "fh", 300, now_ms=minute) is None


def test_codex_parser_selects_duration_instead_of_position():
    response = {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 1790726400},
                    "secondary": {"usedPercent": 15, "windowDurationMins": 300, "resetsAt": 1790600000},
                }
            }
        },
    }
    snapshot = parse_codex_usage(response)
    assert snapshot.window("seven_day").remaining_percent == 58
    assert snapshot.window("five_hour").remaining_percent == 85
    assert snapshot.window("five_hour").resets_at.tzinfo == timezone.utc


def test_codex_parser_accepts_legacy_bucket_and_snake_case():
    snapshot = parse_codex_usage(
        {
            "result": {
                "rate_limits": {
                    "primary": {"used_percent": 6, "window_duration_mins": 300, "resets_at": "2026-09-27T18:00:00Z"}
                }
            }
        }
    )
    assert snapshot.primary.remaining_percent == 94
