from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


APP_NAME = "LimitBar"


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    result = Path(root) / APP_NAME
    result.mkdir(parents=True, exist_ok=True)
    return result


@dataclass(slots=True)
class Settings:
    poll_seconds: int = 180
    show_widget: bool = True
    dock_in_taskbar: bool = True
    show_desktop_widget: bool = True
    desktop_x: int | None = None
    desktop_y: int | None = None
    start_with_windows: bool = False
    interface_scale: float = 1.0
    text_scale: float = 1.0
    allow_screenshots: bool = False
    notifications_enabled: bool = True
    notify_reset_soon: bool = True
    notify_fast_usage: bool = True
    notify_reset_complete: bool = True
    notify_auth_errors: bool = True
    quiet_hours: bool = True
    reset_notice_minutes: int = 30
    reset_notice_remaining: int = 25
    providers: dict[str, bool] = field(default_factory=lambda: {"claude": True, "codex": True})

    @classmethod
    def load(cls) -> "Settings":
        path = app_data_dir() / "config.json"
        if not path.exists():
            return cls()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                poll_seconds=max(120, min(3600, int(value.get("poll_seconds", 180)))),
                show_widget=bool(value.get("show_widget", True)),
                dock_in_taskbar=bool(value.get("dock_in_taskbar", True)),
                show_desktop_widget=bool(value.get("show_desktop_widget", True)),
                desktop_x=int(value["desktop_x"]) if isinstance(value.get("desktop_x"), int) else None,
                desktop_y=int(value["desktop_y"]) if isinstance(value.get("desktop_y"), int) else None,
                start_with_windows=bool(value.get("start_with_windows", False)),
                interface_scale=_choice(value.get("interface_scale"), (1.0, 1.25, 1.5), 1.0),
                text_scale=_choice(value.get("text_scale"), (1.0, 1.15, 1.3), 1.0),
                allow_screenshots=bool(value.get("allow_screenshots", False)),
                notifications_enabled=bool(value.get("notifications_enabled", True)),
                notify_reset_soon=bool(value.get("notify_reset_soon", True)),
                notify_fast_usage=bool(value.get("notify_fast_usage", True)),
                notify_reset_complete=bool(value.get("notify_reset_complete", True)),
                notify_auth_errors=bool(value.get("notify_auth_errors", True)),
                quiet_hours=bool(value.get("quiet_hours", True)),
                reset_notice_minutes=max(5, min(120, int(value.get("reset_notice_minutes", 30)))),
                reset_notice_remaining=max(5, min(90, int(value.get("reset_notice_remaining", 25)))),
                providers={
                    "claude": bool(value.get("providers", {}).get("claude", True)),
                    "codex": bool(value.get("providers", {}).get("codex", True)),
                },
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls()

    def save(self) -> None:
        value = {
            "poll_seconds": self.poll_seconds,
            "show_widget": self.show_widget,
            "dock_in_taskbar": self.dock_in_taskbar,
            "show_desktop_widget": self.show_desktop_widget,
            "desktop_x": self.desktop_x,
            "desktop_y": self.desktop_y,
            "start_with_windows": self.start_with_windows,
            "interface_scale": self.interface_scale,
            "text_scale": self.text_scale,
            "allow_screenshots": self.allow_screenshots,
            "notifications_enabled": self.notifications_enabled,
            "notify_reset_soon": self.notify_reset_soon,
            "notify_fast_usage": self.notify_fast_usage,
            "notify_reset_complete": self.notify_reset_complete,
            "notify_auth_errors": self.notify_auth_errors,
            "quiet_hours": self.quiet_hours,
            "reset_notice_minutes": self.reset_notice_minutes,
            "reset_notice_remaining": self.reset_notice_remaining,
            "providers": self.providers,
        }
        path = app_data_dir() / "config.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(path)


def _choice(value: object, choices: tuple[float, ...], fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(choices, key=lambda candidate: abs(candidate - number))


def executable_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m limitbar'
