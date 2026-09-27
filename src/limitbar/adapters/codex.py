from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from limitbar.adapters.base import ProviderError, UsageAdapter
from limitbar.models import LimitWindow, UsageSnapshot


class CodexUsageAdapter(UsageAdapter):
    provider_id = "codex"
    provider_name = "ChatGPT"

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> UsageSnapshot:
        executable = find_codex()
        if not executable:
            raise ProviderError("Install Codex CLI and sign in with ChatGPT", retryable=False)
        command = _command(executable)
        try:
            response, stderr = _exchange(command, self.timeout_seconds)
        except TimeoutError as error:
            raise ProviderError("Codex usage request timed out") from error
        except OSError as error:
            raise ProviderError("Codex CLI could not be started", retryable=False) from error

        if response is None:
            detail = _safe_detail(stderr)
            raise ProviderError(detail or "Codex CLI returned no usage data")
        if isinstance(response.get("error"), dict):
            message = str(response["error"].get("message", "Codex app-server error"))
            retryable = not any(word in message.lower() for word in ("login", "auth", "permission"))
            raise ProviderError(message, retryable=retryable)
        return parse_codex_usage(response)


def find_codex() -> str | None:
    override = os.environ.get("LIMITBAR_CODEX_PATH")
    if override and Path(override).is_file():
        return override
    for name in ("codex.exe", "codex.cmd", "codex"):
        found = shutil.which(name)
        if found:
            return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for path in (
            Path(local) / "pnpm" / "codex.cmd",
            Path(local) / "npm" / "codex.cmd",
        ):
            if path.is_file():
                return str(path)
    return None


def parse_codex_usage(response: dict) -> UsageSnapshot:
    result = response.get("result")
    if not isinstance(result, dict):
        raise ProviderError("Codex usage response has no result", retryable=False)
    buckets = result.get("rateLimitsByLimitId") or result.get("rate_limits_by_limit_id")
    if isinstance(buckets, dict):
        bucket = buckets.get("codex") or next(iter(buckets.values()), None)
    else:
        bucket = result.get("rateLimits") or result.get("rate_limits") or result
    if not isinstance(bucket, dict):
        raise ProviderError("Codex usage response has an unsupported shape", retryable=False)

    windows: list[LimitWindow] = []
    for key in ("primary", "secondary"):
        item = bucket.get(key)
        if not isinstance(item, dict):
            continue
        duration = _integer(item.get("windowDurationMins") or item.get("window_duration_mins"))
        window_id, label = _window_identity(duration, key)
        used = _number(item.get("usedPercent"))
        if used is None:
            used = _number(item.get("used_percent"))
        windows.append(
            LimitWindow(
                id=window_id,
                label=label,
                used_percent=used,
                resets_at=_time(item.get("resetsAt") or item.get("resets_at")),
                duration_minutes=duration,
            )
        )

    # ChatGPT/Codex can expose an additional plan bucket alongside the normal
    # 5-hour and weekly windows.  In the current app-server response this is
    # `base_model_inference` with limitName `gpt-reserve`.
    if isinstance(buckets, dict):
        for limit_id, reserve in buckets.items():
            if not isinstance(reserve, dict) or limit_id == "codex":
                continue
            name = str(reserve.get("limitName") or reserve.get("limit_name") or "").strip().lower()
            if limit_id != "base_model_inference" and name != "gpt-reserve":
                continue
            item = reserve.get("primary")
            if not isinstance(item, dict):
                continue
            duration = _integer(item.get("windowDurationMins") or item.get("window_duration_mins")) or 10_080
            windows.append(
                LimitWindow(
                    id="gpt_reserve",
                    label="GPT reserve",
                    used_percent=_number(item.get("usedPercent") if "usedPercent" in item else item.get("used_percent")),
                    resets_at=_time(item.get("resetsAt") or item.get("resets_at")),
                    duration_minutes=duration,
                )
            )
            break
    if not windows:
        raise ProviderError("Codex did not report a current usage window", retryable=False)
    return UsageSnapshot(
        provider_id="codex",
        provider_name="ChatGPT",
        windows=tuple(windows),
        source="Codex local app-server adapter",
    )


def _command(executable: str) -> list[str]:
    arguments = '-s read-only -a never app-server'
    if executable.lower().endswith((".cmd", ".bat")):
        command_processor = os.environ.get("ComSpec", "cmd.exe")
        return [command_processor, "/d", "/s", "/c", f'""{executable}" {arguments}"']
    return [executable, "-s", "read-only", "-a", "never", "app-server"]


def _exchange(command: list[str], timeout_seconds: int) -> tuple[dict | None, str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output: queue.Queue[dict | None] = queue.Queue()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                output.put(value)
        output.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()
    deadline = time.monotonic() + timeout_seconds
    try:
        _send(process, {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "limitbar", "version": "0.1.0"}, "capabilities": {"experimentalApi": True, "optOutNotificationMethods": []}}})
        _wait_for_id(output, 1, deadline)
        _send(process, {"method": "initialized", "params": {}})
        _send(process, {"id": 2, "method": "account/rateLimits/read", "params": None})
        response = _wait_for_id(output, 2, deadline)
    finally:
        if process.poll() is None:
            process.kill()
        _, stderr = process.communicate(timeout=2)
    return response, stderr


def _send(process: subprocess.Popen[str], message: dict) -> None:
    if process.stdin is None:
        raise OSError("Codex app-server stdin is unavailable")
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _wait_for_id(output: queue.Queue[dict | None], response_id: int, deadline: float) -> dict:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            value = output.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError from error
        if value is None:
            raise OSError("Codex app-server closed before responding")
        if value.get("id") == response_id:
            return value


def _response_by_id(output: str, response_id: int) -> dict | None:
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id") == response_id:
            return value
    return None


def _window_identity(duration: int | None, fallback: str) -> tuple[str, str]:
    if duration == 300:
        return "five_hour", "5h"
    if duration == 10_080:
        return "seven_day", "7d"
    if duration:
        return f"{duration}_minutes", f"{duration}m"
    return fallback, fallback.title()


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return max(0.0, min(100.0, float(value)))
    return None


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


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


def _safe_detail(stderr: str) -> str | None:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return None
    detail = lines[-1]
    if len(detail) > 180:
        detail = detail[:177] + "..."
    return detail
