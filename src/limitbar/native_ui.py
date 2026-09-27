from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
import pystray

from limitbar.config import Settings, app_data_dir
from limitbar.models import UsageSnapshot
from limitbar.notifications import NotificationEngine
from limitbar.windows import set_start_with_windows


class NativeLimitBarUI:
    """Tray/controller for the isolated DXGI + DirectComposition frontend."""

    def __init__(self, settings: Settings, on_refresh: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self.settings = settings
        self.on_refresh = on_refresh
        self.on_exit = on_exit
        self.snapshots: dict[str, UsageSnapshot] = {}
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self._children: list[subprocess.Popen[bytes]] = []
        self._exe = native_frontend_path()
        self._notifications = NotificationEngine(settings)
        self._start_frontends()
        self._tray = self._create_tray()
        self._tray.run_detached()
        self.root.after(120, self._drain_events)

    def run(self) -> None:
        self.root.mainloop()

    def post_snapshot(self, provider_id: str, snapshot: UsageSnapshot) -> None:
        self.events.put((provider_id, snapshot))

    def close(self) -> None:
        try:
            self._tray.stop()
            _close_window("LimitBarGlassTaskbar")
            _close_window("LimitBarGlassDesktop")
            for child in self._children:
                if child.poll() is None:
                    child.terminate()
        finally:
            self.root.destroy()

    def _launch(self, *arguments: str) -> None:
        if not self._exe.exists():
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        options = [f"--scale={self.settings.interface_scale}", f"--text-scale={self.settings.text_scale}"]
        if self.settings.allow_screenshots:
            options.append("--allow-capture")
        self._children.append(subprocess.Popen([str(self._exe), *arguments, *options], creationflags=flags))

    def _start_frontends(self) -> None:
        if self.settings.show_desktop_widget:
            self._launch()
        if self.settings.show_widget:
            self._launch("--taskbar")

    def _create_tray(self) -> pystray.Icon:
        def choose(key: str, value: float):
            def action(_icon, _item) -> None:
                self.events.put((key, value))
            return action

        def interface_checked(value: float):
            return lambda _item: self.settings.interface_scale == value

        def text_checked(value: float):
            return lambda _item: self.settings.text_scale == value

        interface_menu = pystray.Menu(
            *(
                pystray.MenuItem(
                    label,
                    choose("_interface_scale", value),
                    checked=interface_checked(value),
                    radio=True,
                )
                for label, value in (("100%", 1.0), ("125%", 1.25), ("150%", 1.5))
            )
        )
        text_menu = pystray.Menu(
            *(
                pystray.MenuItem(
                    label,
                    choose("_text_scale", value),
                    checked=text_checked(value),
                    radio=True,
                )
                for label, value in (("Normal", 1.0), ("Large", 1.15), ("Extra large", 1.3))
            )
        )
        notifications_menu = pystray.Menu(
            pystray.MenuItem("Enabled", lambda _i, _m: self.events.put(("_notify_enabled", not self.settings.notifications_enabled)), checked=lambda _m: self.settings.notifications_enabled),
            pystray.MenuItem("Reset soon", lambda _i, _m: self.events.put(("_notify_reset", not self.settings.notify_reset_soon)), checked=lambda _m: self.settings.notify_reset_soon),
            pystray.MenuItem("Fast usage", lambda _i, _m: self.events.put(("_notify_fast", not self.settings.notify_fast_usage)), checked=lambda _m: self.settings.notify_fast_usage),
            pystray.MenuItem("Reset completed", lambda _i, _m: self.events.put(("_notify_complete", not self.settings.notify_reset_complete)), checked=lambda _m: self.settings.notify_reset_complete),
            pystray.MenuItem("Quiet hours 23:00-08:00", lambda _i, _m: self.events.put(("_quiet_hours", not self.settings.quiet_hours)), checked=lambda _m: self.settings.quiet_hours),
        )
        menu = pystray.Menu(
            pystray.MenuItem("Show desktop widget", lambda _i, _m: self.events.put(("_desktop", True)), default=True),
            pystray.MenuItem("Refresh", lambda _i, _m: self.events.put(("_refresh", True))),
            pystray.MenuItem(
                "Show taskbar island",
                lambda _i, _m: self.events.put(("_taskbar", not self.settings.show_widget)),
                checked=lambda _m: self.settings.show_widget,
            ),
            pystray.MenuItem("Interface size", interface_menu),
            pystray.MenuItem("Text size", text_menu),
            pystray.MenuItem("Notifications", notifications_menu),
            pystray.MenuItem(
                "Allow screenshots",
                lambda _i, _m: self.events.put(("_screenshots", not self.settings.allow_screenshots)),
                checked=lambda _m: self.settings.allow_screenshots,
            ),
            pystray.MenuItem(
                "Start with Windows",
                lambda _i, _m: self.events.put(("_startup", not self.settings.start_with_windows)),
                checked=lambda _m: self.settings.start_with_windows,
            ),
            pystray.MenuItem("Open logs", lambda _i, _m: webbrowser.open((app_data_dir() / "limitbar.log").as_uri())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda _i, _m: self.events.put(("_exit", True))),
        )
        return pystray.Icon("LimitBar", _tray_image(None), "LimitBar — connecting", menu)

    def _drain_events(self) -> None:
        try:
            while True:
                key, value = self.events.get_nowait()
                if key == "_desktop":
                    self.settings.show_desktop_widget = True
                    self.settings.save()
                    self._launch()
                elif key == "_refresh":
                    self.on_refresh()
                elif key == "_taskbar":
                    self.settings.show_widget = bool(value)
                    self.settings.save()
                    if value:
                        self._launch("--taskbar")
                    else:
                        _close_window("LimitBarGlassTaskbar")
                    self._tray.update_menu()
                elif key == "_startup":
                    self.settings.start_with_windows = bool(value)
                    set_start_with_windows(bool(value))
                    self.settings.save()
                    self._tray.update_menu()
                elif key == "_interface_scale":
                    self.settings.interface_scale = float(value)
                    self.settings.save()
                    self._restart_frontends()
                    self._tray.update_menu()
                elif key == "_text_scale":
                    self.settings.text_scale = float(value)
                    self.settings.save()
                    self._restart_frontends()
                    self._tray.update_menu()
                elif key == "_screenshots":
                    self.settings.allow_screenshots = bool(value)
                    self.settings.save()
                    self._restart_frontends()
                    self._tray.update_menu()
                elif key in {"_notify_enabled", "_notify_reset", "_notify_fast", "_notify_complete", "_quiet_hours"}:
                    attribute = {
                        "_notify_enabled": "notifications_enabled",
                        "_notify_reset": "notify_reset_soon",
                        "_notify_fast": "notify_fast_usage",
                        "_notify_complete": "notify_reset_complete",
                        "_quiet_hours": "quiet_hours",
                    }[key]
                    setattr(self.settings, attribute, bool(value))
                    self.settings.save()
                    self._tray.update_menu()
                elif key == "_exit":
                    self.on_exit()
                elif isinstance(value, UsageSnapshot):
                    for notice in self._notifications.evaluate(value):
                        try:
                            self._tray.notify(notice.message, notice.title)
                        except Exception:
                            pass
                    self.snapshots[key] = value
                    self._update_tray()
        except queue.Empty:
            pass
        self.root.after(120, self._drain_events)

    def _restart_frontends(self) -> None:
        _close_window("LimitBarGlassTaskbar")
        _close_window("LimitBarGlassDesktop")
        self.root.after(350, self._start_frontends)

    def _update_tray(self) -> None:
        readings = [s.primary.remaining_percent for s in self.snapshots.values() if s.primary and s.primary.remaining_percent is not None]
        remaining = min(readings) if readings else None
        parts: list[str] = []
        for provider_id in ("claude", "codex"):
            snapshot = self.snapshots.get(provider_id)
            primary = snapshot.primary if snapshot else None
            value = f"{primary.remaining_percent}%" if primary and primary.remaining_percent is not None else "—"
            parts.append(f"{snapshot.provider_name if snapshot else provider_id.title()}: {value}")
        self._tray.icon = _tray_image(remaining)
        self._tray.title = "LimitBar | " + " | ".join(parts)


def native_frontend_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "glass" / "LimitBar.Glass.exe"
    return Path(__file__).resolve().parents[2] / "native" / "LimitBar.DX11" / "bin" / "LimitBar.Glass.exe"


def _close_window(class_name: str) -> None:
    if os.name != "nt":
        return
    import ctypes

    hwnd = ctypes.windll.user32.FindWindowW(class_name, None)
    if hwnd:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def _tray_image(remaining: int | None) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = "#FF6F7D" if remaining is not None and remaining <= 15 else "#FFB85C" if remaining is not None and remaining <= 35 else "#61E6A7"
    draw.rounded_rectangle((3, 3, 61, 61), radius=17, fill="#121923", outline=color, width=4)
    text = "—" if remaining is None else str(remaining)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 28 if len(text) <= 2 else 22)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((64 - box[2]) / 2, (64 - box[3]) / 2 - 2), text, font=font, fill="#F5F8FC")
    return image
