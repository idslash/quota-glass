from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from limitbar.adapters.base import ProviderError, UsageAdapter
from limitbar.credentials import ClaudeOAuthCredentials, read_claude_oauth_credentials
from limitbar.models import LimitWindow, UsageSnapshot


LOG = logging.getLogger(__name__)
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


class ClaudeUsageAdapter(UsageAdapter):
    provider_id = "claude"
    provider_name = "Claude"

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> UsageSnapshot:
        credentials = read_claude_oauth_credentials()
        if not credentials:
            fallback = _claude_desktop_snapshot()
            if fallback:
                return fallback
            raise ProviderError("Sign in with Claude Code or open Claude Desktop", retryable=False)
        try:
            if credentials.expires_at_ms and credentials.expires_at_ms <= int(time.time() * 1000) + 30_000:
                credentials = _refresh_credentials(credentials)
        except ProviderError:
            fallback = _claude_desktop_snapshot()
            if fallback:
                return fallback
            raise
        try:
            value = self._fetch_usage(credentials.access_token)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403) and credentials.refresh_token:
                credentials = _refresh_credentials(credentials)
                try:
                    value = self._fetch_usage(credentials.access_token)
                except urllib.error.HTTPError as retry_error:
                    raise _http_error(retry_error) from retry_error
            else:
                fallback = _claude_desktop_snapshot()
                if fallback:
                    return fallback
                raise _http_error(error) from error
        return parse_claude_usage(value)

    def _fetch_usage(self, token: str) -> dict:
        request = urllib.request.Request(
            USAGE_URL,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": f"claude-code/{_claude_version()}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            raise ProviderError(f"Claude is temporarily unreachable: {error.reason}") from error
        except (TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError("Claude returned an invalid or timed-out usage response") from error


def _refresh_credentials(credentials: ClaudeOAuthCredentials) -> ClaudeOAuthCredentials:
    if not credentials.refresh_token:
        raise ProviderError("Claude login expired; run `claude login`", retryable=False)
    payload = json.dumps({"grant_type": "refresh_token", "refresh_token": credentials.refresh_token, "client_id": CLIENT_ID}).encode()
    request = urllib.request.Request(TOKEN_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ProviderError("Claude refresh token expired; run `claude login`", retryable=False) from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError("Claude login refresh is temporarily unavailable") from error
    access = result.get("access_token")
    if not isinstance(access, str) or not access:
        raise ProviderError("Claude returned an invalid token refresh response", retryable=False)
    refresh = result.get("refresh_token") if isinstance(result.get("refresh_token"), str) else credentials.refresh_token
    expires = int(time.time() * 1000) + int(result.get("expires_in", 3600)) * 1000
    if not credentials.persist_rotated(access, refresh, expires):
        latest = read_claude_oauth_credentials()
        if latest and latest.access_token != credentials.access_token:
            return latest
        raise ProviderError("Claude token refreshed but could not be saved safely", retryable=False)
    latest = read_claude_oauth_credentials()
    return latest or ClaudeOAuthCredentials({}, access, refresh, expires)


def _http_error(error: urllib.error.HTTPError) -> ProviderError:
    retry_after = _retry_after(error.headers.get("Retry-After"))
    if error.code in (401, 403):
        return ProviderError("Claude login expired; run `claude login`", retryable=False)
    if error.code == 429:
        return ProviderError("Claude usage endpoint is rate-limiting requests", True, retry_after)
    return ProviderError(f"Claude usage endpoint returned HTTP {error.code}", error.code >= 500, retry_after)


def parse_claude_usage(value: dict) -> UsageSnapshot:
    windows: list[LimitWindow] = []
    known = (
        ("five_hour", "5h", 300),
        ("seven_day", "7d", 10_080),
        ("seven_day_opus", "Opus 7d", 10_080),
        ("seven_day_sonnet", "Sonnet 7d", 10_080),
        ("seven_day_cowork", "Work 7d", 10_080),
    )
    for key, label, duration in known:
        item = value.get(key)
        if isinstance(item, dict):
            windows.append(_window(key, label, duration, item))

    # Newer responses can add model-scoped windows without changing the top-level schema.
    existing = {window.id for window in windows}
    for index, item in enumerate(value.get("limits", [])):
        if not isinstance(item, dict):
            continue
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        title = model.get("display_name") or item.get("name")
        kind = item.get("kind")
        if kind == "session":
            window_id, label, duration = "five_hour", "Session", 300
        elif kind == "weekly_all" or (isinstance(title, str) and title.lower() == "all models"):
            window_id, label, duration = "seven_day", "7d", 10_080
        elif isinstance(title, str):
            window_id, label, duration = str(item.get("id") or model.get("id") or f"scoped_{index}"), f"{title} 7d", 10_080
        else:
            continue
        if window_id in existing:
            continue
        windows.append(
            LimitWindow(
                id=window_id,
                label=label,
                used_percent=_percent(item),
                resets_at=_time(item.get("resets_at") or item.get("reset_at")),
                duration_minutes=duration,
            )
        )
        existing.add(window_id)

    if not windows:
        raise ProviderError("Claude usage response has an unsupported shape", retryable=False)
    return UsageSnapshot(
        provider_id="claude",
        provider_name="Claude",
        windows=tuple(windows),
        source="Anthropic OAuth usage adapter",
    )


def _claude_desktop_snapshot() -> UsageSnapshot | None:
    """Read Claude Desktop's non-secret local usage history as a graceful fallback."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidates = Path(local, "Packages").glob("Claude_*/*/Roaming/Claude/plan-usage-history.json")
    newest: tuple[float, list[tuple[float, dict]]] | None = None
    for path in candidates:
        try:
            root = json.loads(path.read_text(encoding="utf-8"))
            samples = sorted(
                (
                    (float(sample.get("t", 0)), sample["u"])
                    for sample in root.get("samples", [])
                    if isinstance(sample, dict) and isinstance(sample.get("u"), dict)
                ),
                key=lambda item: item[0],
            )
            if samples and (newest is None or samples[-1][0] > newest[0]):
                newest = (samples[-1][0], samples)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if newest is None or time.time() * 1000 - newest[0] > 2 * 60 * 60 * 1000:
        return None
    usage = newest[1][-1][1]
    windows: list[LimitWindow] = []
    if isinstance(usage.get("fh"), (int, float)):
        reset = _infer_reset_from_history(newest[1], "fh", 300)
        windows.append(LimitWindow("five_hour_estimated" if reset else "five_hour", "5h", float(usage["fh"]), reset, 300))
    if isinstance(usage.get("sd"), (int, float)):
        reset = _infer_reset_from_history(newest[1], "sd", 10_080)
        windows.append(LimitWindow("seven_day_estimated" if reset else "seven_day", "7d", float(usage["sd"]), reset, 10_080))
    if not windows:
        return None
    return UsageSnapshot(
        provider_id="claude",
        provider_name="Claude",
        windows=tuple(windows),
        fetched_at=datetime.fromtimestamp(newest[0] / 1000, timezone.utc),
        source="Claude Desktop local usage history",
    )


def _infer_reset_from_history(
    samples: list[tuple[float, dict]], key: str, duration_minutes: int, now_ms: float | None = None
) -> datetime | None:
    """Estimate a reset only when a sharp usage reset is visible in local history."""
    anchor_ms: float | None = None
    waiting_for_activity = False
    previous: float | None = None
    for timestamp, usage in samples:
        raw = usage.get(key)
        if not isinstance(raw, (int, float)):
            continue
        current = float(raw)
        if previous is not None and previous - current >= max(5.0, previous * 0.25) and current <= 5.0:
            anchor_ms = timestamp if current > 0 else None
            waiting_for_activity = current <= 0
        elif waiting_for_activity and current > 0:
            anchor_ms = timestamp
            waiting_for_activity = False
        previous = current

    if anchor_ms is None:
        return None
    reset_ms = anchor_ms + duration_minutes * 60_000
    reference_ms = time.time() * 1000 if now_ms is None else now_ms
    if reset_ms <= reference_ms:
        return None
    return datetime.fromtimestamp(reset_ms / 1000, timezone.utc)


def _window(key: str, label: str, duration: int, item: dict) -> LimitWindow:
    return LimitWindow(
        id=key,
        label=label,
        used_percent=_percent(item),
        resets_at=_time(item.get("resets_at") or item.get("reset_at")),
        duration_minutes=_int(item.get("duration_minutes")) or duration,
    )


def _percent(item: dict) -> float | None:
    for key in ("utilization", "used_percent", "usedPercent", "percent"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(100.0, float(value)))
    remaining = item.get("remaining_percent")
    if isinstance(remaining, (int, float)):
        return max(0.0, min(100.0, 100.0 - float(remaining)))
    return None


def _time(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
            return max(0, int((instant - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _claude_version() -> str:
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (result.stdout or result.stderr).strip().split()
        return next((part for part in text if part[:1].isdigit()), "unknown")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
