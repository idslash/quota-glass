from __future__ import annotations

import queue
import os
import tkinter as tk
import webbrowser
from datetime import datetime
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
import pystray

from limitbar.config import Settings, app_data_dir
from limitbar.models import Health, LimitWindow, UsageSnapshot, format_reset
from limitbar.windows import (
    keep_window_on_taskbar,
    place_window,
    set_start_with_windows,
    style_widget_window,
    taskbar_rects,
    work_area,
)


BG = "#10151D"
TASKBAR_BG = "#121820"
GLASS = "#18212D"
GLASS_SOFT = "#151C26"
GLASS_EDGE = "#344258"
TRACK = "#273142"
TEXT = "#F4F7FB"
MUTED = "#8F9BAA"
GREEN = "#61E6A7"
BLUE = "#70A7FF"
AMBER = "#FFB85C"
RED = "#FF6F7D"
CLAUDE = "#FFB568"
OPENAI = "#82B1FF"

TASKBAR_WIDTH = 330
TASKBAR_HEIGHT = 38
FLOAT_WIDTH = 362
FLOAT_HEIGHT = 68
DESKTOP_WIDTH = 430


class LimitBarUI:
    def __init__(
        self,
        settings: Settings,
        on_refresh: Callable[[], None],
        on_exit: Callable[[], None],
        on_providers_changed: Callable[[], None],
    ) -> None:
        self.settings = settings
        self.on_refresh = on_refresh
        self.on_exit = on_exit
        self.on_providers_changed = on_providers_changed
        self.snapshots: dict[str, UsageSnapshot] = {}
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._embedded = False
        self._compact_bounds = (0, 0, FLOAT_WIDTH, FLOAT_HEIGHT)
        self._animated_used: dict[tuple[str, str], float] = {}
        self._target_used: dict[tuple[str, str], float] = {}
        self._animation_running = False
        self._drag_origin: tuple[int, int, int, int] | None = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.option_add("*Font", "Segoe UI 9")

        self.compact = tk.Toplevel(self.root)
        self.compact.overrideredirect(True)
        self.compact.attributes("-topmost", True)
        self.compact.attributes("-alpha", 0.96)
        self.compact.configure(bg=TASKBAR_BG)
        self.compact_canvas = tk.Canvas(
            self.compact,
            bg=TASKBAR_BG,
            highlightthickness=0,
            cursor="hand2",
        )
        self.compact_canvas.pack(fill="both", expand=True)
        self.compact_canvas.bind("<Button-1>", lambda _event: self.toggle_desktop())

        self.desktop = tk.Toplevel(self.root)
        self.desktop.overrideredirect(True)
        self.desktop.attributes("-topmost", os.environ.get("LIMITBAR_DESKTOP_TOPMOST") == "1")
        self.desktop.attributes("-alpha", 0.96)
        self.desktop.configure(bg=BG)
        self.desktop_canvas = tk.Canvas(
            self.desktop,
            width=DESKTOP_WIDTH,
            height=300,
            bg=BG,
            highlightthickness=0,
        )
        self.desktop_canvas.pack(fill="both", expand=True)
        self.desktop_canvas.bind("<Button-1>", self._desktop_press)
        self.desktop_canvas.bind("<B1-Motion>", self._desktop_drag)
        self.desktop_canvas.bind("<ButtonRelease-1>", self._desktop_release)
        self.desktop_canvas.bind("<Double-Button-1>", lambda _event: self.on_refresh())

        self._tray = self._create_tray()
        self._tray.run_detached()
        self._position_compact()
        self._position_desktop()
        self._render_compact()
        self._render_desktop()
        self.root.after(100, self._style_windows)

        if not settings.show_widget:
            self.compact.withdraw()
        if not settings.show_desktop_widget:
            self.desktop.withdraw()
        self.root.after(150, self._drain_events)
        self.root.after(5_000, self._tick)

    def run(self) -> None:
        self.root.mainloop()

    def _style_windows(self) -> None:
        style_widget_window(self.desktop.winfo_id())
        style_widget_window(self.compact.winfo_id())

    def post_snapshot(self, provider_id: str, snapshot: UsageSnapshot) -> None:
        self.events.put((provider_id, snapshot))

    def close(self) -> None:
        try:
            self._tray.stop()
        finally:
            self.root.destroy()

    def toggle_desktop(self) -> None:
        visible = self.desktop.state() != "withdrawn"
        self.settings.show_desktop_widget = not visible
        self.settings.save()
        if visible:
            self.desktop.withdraw()
        else:
            self.desktop.deiconify()
            self.desktop.lift()
            self._start_animation()
        self._tray.update_menu()

    def _position_compact(self) -> None:
        taskbar, tray = taskbar_rects()
        embedded = False
        if self.settings.dock_in_taskbar and taskbar:
            left, top, right, bottom = taskbar
            taskbar_width = right - left
            taskbar_height = bottom - top
            if taskbar_width > taskbar_height * 4 and taskbar_height >= 34:
                tray_left = tray[0] if tray else right - 220
                width = min(TASKBAR_WIDTH, max(250, tray_left - left - 360))
                x = tray_left - width - 8
                if x >= left + 320:
                    height = min(TASKBAR_HEIGHT, taskbar_height - 4)
                    y = top + (taskbar_height - height) // 2
                    self.compact_canvas.configure(width=width, height=height, bg=TASKBAR_BG)
                    self.compact.geometry(f"{width}x{height}+{x}+{y}")
                    self.compact.update_idletasks()
                    keep_window_on_taskbar(self.compact.winfo_id(), x, y, width, height)
                    self._compact_bounds = (x, y, width, height)
                    embedded = True
        if not embedded:
            _, _, right, bottom = work_area()
            x = right - FLOAT_WIDTH - 12
            y = bottom - FLOAT_HEIGHT - 6
            self.compact_canvas.configure(width=FLOAT_WIDTH, height=FLOAT_HEIGHT, bg=TASKBAR_BG)
            self.compact.geometry(f"{FLOAT_WIDTH}x{FLOAT_HEIGHT}+{x}+{y}")
            self._compact_bounds = (x, y, FLOAT_WIDTH, FLOAT_HEIGHT)
        self._embedded = embedded

    def _position_desktop(self) -> None:
        _, top, right, bottom = work_area()
        height = self._desktop_height()
        x = self.settings.desktop_x if self.settings.desktop_x is not None else right - DESKTOP_WIDTH - 24
        y = self.settings.desktop_y if self.settings.desktop_y is not None else top + 24
        x = max(0, min(x, right - DESKTOP_WIDTH))
        y = max(top, min(y, bottom - height))
        self.desktop_canvas.configure(width=DESKTOP_WIDTH, height=height)
        self.desktop.geometry(f"{DESKTOP_WIDTH}x{height}+{x}+{y}")
        self.desktop.update_idletasks()
        place_window(self.desktop.winfo_id(), x, y, DESKTOP_WIDTH, height, topmost=False)

    def _desktop_height(self) -> int:
        rows = 0
        for provider_id in ("claude", "codex"):
            snapshot = self.snapshots.get(provider_id)
            rows += max(1, min(4, len(snapshot.windows) if snapshot else 0))
        return max(310, 112 + rows * 58 + 44)

    def _render_compact(self) -> None:
        canvas = self.compact_canvas
        canvas.delete("all")
        width = self._compact_bounds[2]
        height = self._compact_bounds[3]
        _round_rect(canvas, 1, 1, width - 1, height - 1, 12, fill=TASKBAR_BG, outline=GLASS_EDGE)
        canvas.create_line(12, 3, width - 12, 3, fill="#52627A")
        column = width / 2
        canvas.create_line(column, 8, column, height - 8, fill="#344054")
        for index, provider_id in enumerate(("claude", "codex")):
            x1 = index * column
            x2 = x1 + column
            snapshot = self.snapshots.get(provider_id)
            primary = snapshot.primary if snapshot else None
            remaining = primary.remaining_percent if primary else None
            used = None if remaining is None else 100 - remaining
            label = "Claude" if provider_id == "claude" else ("GPT" if column < 140 else "ChatGPT")
            accent = CLAUDE if provider_id == "claude" else OPENAI
            label_y = 11 if self._embedded else 20
            canvas.create_oval(x1 + 9, label_y - 4, x1 + 17, label_y + 4, fill=_mix(accent, TASKBAR_BG, 0.58), outline="")
            canvas.create_oval(x1 + 11, label_y - 2, x1 + 15, label_y + 2, fill=accent, outline="")
            canvas.create_text(x1 + 22, label_y, text=label, fill=TEXT, anchor="w", font=("Segoe UI Semibold", 8))
            value = "—" if remaining is None else (f"{remaining}%" if column < 150 else f"{remaining}% left")
            canvas.create_text(x2 - 9, label_y, text=value, fill=_remaining_color(remaining), anchor="e", font=("Segoe UI Semibold", 8))
            bar_y = height - (7 if self._embedded else 10)
            _canvas_progress(canvas, x1 + 9, bar_y - 2, x2 - 9, bar_y + 2, used)
            if snapshot and snapshot.health == Health.STALE:
                canvas.create_oval(x2 - 7, bar_y - 1, x2 - 3, bar_y + 3, fill=AMBER, outline="")
        self._update_tray()

    def _render_desktop(self) -> None:
        canvas = self.desktop_canvas
        canvas.delete("all")
        width = DESKTOP_WIDTH
        height = self._desktop_height()
        _draw_glass_background(canvas, width, height)
        canvas.create_text(18, 17, text="LIMITBAR", fill=BLUE, anchor="w", font=("Segoe UI Semibold", 7))
        canvas.create_text(18, 38, text="Plan usage", fill=TEXT, anchor="w", font=("Segoe UI Semibold", 15))
        canvas.create_text(112, 39, text="Claude + ChatGPT", fill=MUTED, anchor="w", font=("Segoe UI", 9))
        _round_rect(canvas, width - 119, 14, width - 70, 34, 9, fill="#18372F", outline="#285646")
        canvas.create_oval(width - 110, 22, width - 104, 28, fill=GREEN, outline="")
        canvas.create_text(width - 88, 24, text="LIVE", fill=GREEN, anchor="center", font=("Segoe UI Semibold", 7))
        _round_rect(canvas, width - 62, 13, width - 37, 38, 10, fill=GLASS, outline=GLASS_EDGE)
        _round_rect(canvas, width - 32, 13, width - 7, 38, 10, fill=GLASS, outline=GLASS_EDGE)
        canvas.create_text(width - 49, 25, text="↻", fill=TEXT, anchor="center", font=("Segoe UI Symbol", 11))
        canvas.create_text(width - 20, 24, text="×", fill=TEXT, anchor="center", font=("Segoe UI", 12))
        canvas.create_line(14, 58, width - 14, 58, fill=GLASS_EDGE)

        y = 76
        for provider_index, provider_id in enumerate(("claude", "codex")):
            snapshot = self.snapshots.get(provider_id)
            name = snapshot.provider_name if snapshot else ("Claude" if provider_id == "claude" else "ChatGPT / Codex")
            accent = CLAUDE if provider_id == "claude" else OPENAI
            canvas.create_oval(16, y - 6, 28, y + 6, fill=_mix(accent, BG, 0.60), outline="")
            canvas.create_oval(19, y - 3, 25, y + 3, fill=accent, outline="")
            canvas.create_text(35, y, text=name, fill=TEXT, anchor="w", font=("Segoe UI Semibold", 10))
            if snapshot and snapshot.health == Health.STALE:
                canvas.create_text(width - 18, y, text="cached", fill=AMBER, anchor="e", font=("Segoe UI", 8))
            y += 22
            if not snapshot or not snapshot.windows:
                message = snapshot.message if snapshot else "Connecting…"
                canvas.create_text(18, y, text=message, fill=MUTED, anchor="nw", width=390, font=("Segoe UI", 9))
                y += 40
            else:
                for window in snapshot.windows[:4]:
                    y = self._draw_limit_row(canvas, provider_id, window, y)
            if provider_index == 0:
                canvas.create_line(14, y + 3, width - 14, y + 3, fill=GLASS_EDGE)
                y += 20

        source_time = max((s.fetched_at for s in self.snapshots.values()), default=None)
        footer = "Waiting for data" if source_time is None else f"Updated {source_time.astimezone().strftime('%H:%M')}  ·  double-click to refresh"
        canvas.create_text(18, height - 18, text=footer, fill=MUTED, anchor="w", font=("Segoe UI", 8))
        canvas.create_text(width - 18, height - 18, text="Drag to move", fill="#667386", anchor="e", font=("Segoe UI", 7))

    def _draw_limit_row(self, canvas: tk.Canvas, provider_id: str, window: LimitWindow, y: int) -> int:
        remaining = window.remaining_percent
        target = 0.0 if remaining is None else float(100 - remaining)
        key = (provider_id, window.id)
        self._target_used[key] = target
        current = self._animated_used.setdefault(key, 0.0)
        label = _ellipsize(_friendly_label(window), 31)
        reset = format_reset(window.resets_at).capitalize()
        used_text = "—" if remaining is None else f"{round(current)}% used"
        left_text = "—" if remaining is None else f"{remaining}% left"
        _round_rect(canvas, 14, y - 9, DESKTOP_WIDTH - 14, y + 39, 10, fill=GLASS_SOFT, outline="#2A3748")
        canvas.create_line(25, y - 7, DESKTOP_WIDTH - 25, y - 7, fill="#35455A")
        canvas.create_text(26, y + 1, text=label, fill=TEXT, anchor="w", font=("Segoe UI Semibold", 9))
        canvas.create_text(DESKTOP_WIDTH - 26, y + 1, text=used_text, fill=_usage_color(current), anchor="e", font=("Segoe UI Semibold", 8))
        canvas.create_text(26, y + 16, text=reset, fill=MUTED, anchor="w", font=("Segoe UI", 8))
        canvas.create_text(DESKTOP_WIDTH - 26, y + 16, text=left_text, fill=_remaining_color(remaining), anchor="e", font=("Segoe UI Semibold", 8))
        _canvas_progress(canvas, 26, y + 28, DESKTOP_WIDTH - 26, y + 33, current)
        return y + 58

    def _start_animation(self) -> None:
        if not self._animation_running:
            self._animation_running = True
            self.root.after(16, self._animate_frame)

    def _animate_frame(self) -> None:
        active = False
        for key, target in self._target_used.items():
            current = self._animated_used.get(key, 0.0)
            delta = target - current
            if abs(delta) > 0.15:
                self._animated_used[key] = current + delta * 0.16
                active = True
            else:
                self._animated_used[key] = target
        self._render_desktop()
        if active:
            self.root.after(16, self._animate_frame)
        else:
            self._animation_running = False

    def _desktop_press(self, event: tk.Event) -> None:
        if event.y < 54 and event.x > DESKTOP_WIDTH - 34:
            self.toggle_desktop()
            return
        if event.y < 54 and event.x > DESKTOP_WIDTH - 67:
            self.on_refresh()
            self._start_animation()
            return
        if event.y < 58:
            self._drag_origin = (event.x_root, event.y_root, self.desktop.winfo_x(), self.desktop.winfo_y())

    def _desktop_drag(self, event: tk.Event) -> None:
        if not self._drag_origin:
            return
        root_x, root_y, window_x, window_y = self._drag_origin
        self.desktop.geometry(f"+{window_x + event.x_root - root_x}+{window_y + event.y_root - root_y}")

    def _desktop_release(self, _event: tk.Event) -> None:
        if self._drag_origin:
            self.settings.desktop_x = self.desktop.winfo_x()
            self.settings.desktop_y = self.desktop.winfo_y()
            self.settings.save()
            self._drag_origin = None

    def _create_tray(self) -> pystray.Icon:
        menu = pystray.Menu(
            pystray.MenuItem("Desktop widget", lambda _i, _m: self.events.put(("_desktop", True)), default=True, checked=lambda _m: self.settings.show_desktop_widget),
            pystray.MenuItem("Refresh", lambda _i, _m: self.events.put(("_refresh", True))),
            pystray.MenuItem("Show taskbar strip", lambda _i, _m: self.events.put(("_show", not self.settings.show_widget)), checked=lambda _m: self.settings.show_widget),
            pystray.MenuItem("Monitor Claude", lambda _i, _m: self.events.put(("_provider", "claude")), checked=lambda _m: self.settings.providers.get("claude", True)),
            pystray.MenuItem("Monitor ChatGPT", lambda _i, _m: self.events.put(("_provider", "codex")), checked=lambda _m: self.settings.providers.get("codex", True)),
            pystray.MenuItem("Dock strip inside taskbar", lambda _i, _m: self.events.put(("_dock", not self.settings.dock_in_taskbar)), checked=lambda _m: self.settings.dock_in_taskbar),
            pystray.MenuItem("Start with Windows", lambda _i, _m: self.events.put(("_startup", not self.settings.start_with_windows)), checked=lambda _m: self.settings.start_with_windows),
            pystray.MenuItem("Open logs", lambda _i, _m: webbrowser.open((app_data_dir() / "limitbar.log").as_uri())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda _i, _m: self.events.put(("_exit", True))),
        )
        return pystray.Icon("LimitBar", _tray_image(None), "LimitBar — connecting", menu)

    def _update_tray(self) -> None:
        readings = [snapshot.primary.remaining_percent for snapshot in self.snapshots.values() if snapshot.primary and snapshot.primary.remaining_percent is not None]
        remaining = min(readings) if readings else None
        parts = []
        for provider_id in ("claude", "codex"):
            if not self.settings.providers.get(provider_id, True):
                continue
            snapshot = self.snapshots.get(provider_id)
            primary = snapshot.primary if snapshot else None
            value = f"{primary.remaining_percent}%" if primary and primary.remaining_percent is not None else "—"
            parts.append(f"{snapshot.provider_name if snapshot else provider_id.title()}: {value}")
        self._tray.icon = _tray_image(remaining)
        self._tray.title = "LimitBar | " + " | ".join(parts)

    def _drain_events(self) -> None:
        try:
            while True:
                key, value = self.events.get_nowait()
                if key == "_desktop":
                    self.toggle_desktop()
                elif key == "_refresh":
                    self.on_refresh()
                elif key == "_show":
                    self.settings.show_widget = bool(value)
                    self.settings.save()
                    self.compact.deiconify() if value else self.compact.withdraw()
                    self._tray.update_menu()
                elif key == "_dock":
                    self.settings.dock_in_taskbar = bool(value)
                    self.settings.save()
                    self._position_compact()
                    self._render_compact()
                    self._tray.update_menu()
                elif key == "_startup":
                    self.settings.start_with_windows = bool(value)
                    set_start_with_windows(bool(value))
                    self.settings.save()
                    self._tray.update_menu()
                elif key == "_provider":
                    provider_id = str(value)
                    self.settings.providers[provider_id] = not self.settings.providers.get(provider_id, True)
                    self.settings.save()
                    self.on_providers_changed()
                    self._tray.update_menu()
                elif key == "_exit":
                    self.on_exit()
                elif isinstance(value, UsageSnapshot):
                    self.snapshots[key] = value
                    self._position_desktop()
                    self._render_compact()
                    self._render_desktop()
                    self._start_animation()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def remove_disabled_providers(self, enabled: set[str]) -> None:
        self.snapshots = {
            provider_id: snapshot
            for provider_id, snapshot in self.snapshots.items()
            if provider_id in enabled
        }
        self._position_desktop()
        self._render_compact()
        self._render_desktop()
        self._update_tray()

    def _tick(self) -> None:
        self._position_compact()
        self._render_compact()
        self._render_desktop()
        self.root.after(5_000, self._tick)


def _friendly_label(window: LimitWindow) -> str:
    if window.id == "five_hour":
        return "5-hour limit"
    if window.id == "seven_day":
        return "Weekly · all models"
    if "7d" in window.label:
        return f"Weekly · {window.label.replace('7d', '').strip()}"
    return window.label


def _ellipsize(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


def _usage_color(used: float) -> str:
    if used >= 85:
        return RED
    if used >= 65:
        return AMBER
    return BLUE


def _remaining_color(remaining: int | None) -> str:
    if remaining is None:
        return MUTED
    if remaining <= 15:
        return RED
    if remaining <= 35:
        return AMBER
    return GREEN


def _canvas_progress(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, used: float | None) -> None:
    _pill(canvas, x1, y1, x2, y2, TRACK)
    if used is not None:
        fill_x = x1 + (x2 - x1) * max(0.0, min(100.0, used)) / 100.0
        if fill_x - x1 >= y2 - y1:
            color = _usage_color(used)
            _pill(canvas, x1, y1, fill_x, y2, _mix(color, BG, 0.70))
            _pill(canvas, x1, y1 + 1, fill_x, y2 - 1, color)


def _pill(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, fill: str) -> None:
    radius = max(1.0, (y2 - y1) / 2)
    if x2 - x1 <= radius * 2:
        canvas.create_oval(x1, y1, x2, y2, fill=fill, outline="")
        return
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="")
    canvas.create_oval(x1, y1, x1 + radius * 2, y2, fill=fill, outline="")
    canvas.create_oval(x2 - radius * 2, y1, x2, y2, fill=fill, outline="")


def _draw_glass_background(canvas: tk.Canvas, width: int, height: int) -> None:
    _round_rect(canvas, 1, 1, width - 1, height - 1, 18, fill=BG, outline=GLASS_EDGE)
    # Low-cost banded gradient and ambient color pools emulate layered glass while
    # DWM supplies the real Desktop Acrylic backdrop on supported Windows 11 builds.
    bands = 18
    for index in range(bands):
        y1 = 2 + index * (height - 4) / bands
        y2 = 2 + (index + 1) * (height - 4) / bands
        color = _mix("#172333", "#0F141C", index / max(1, bands - 1))
        canvas.create_rectangle(4, y1, width - 4, y2 + 1, fill=color, outline="")
    canvas.create_oval(-85, -105, 160, 112, fill="#172B3E", outline="")
    canvas.create_oval(width - 125, -90, width + 75, 95, fill="#202744", outline="")
    canvas.create_line(18, 4, width - 18, 4, fill="#5C6E87")


def _mix(first: str, second: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    values = tuple(round(a[channel] * (1 - amount) + b[channel] * amount) for channel in range(3))
    return "#" + "".join(f"{value:02X}" for value in values)


def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: object) -> int:
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _tray_image(remaining: int | None) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = _remaining_color(remaining)
    draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill="#111318", outline=color, width=4)
    text = "—" if remaining is None else str(remaining)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 28 if len(text) <= 2 else 22)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((64 - box[2]) / 2, (64 - box[3]) / 2 - 2), text, font=font, fill=TEXT)
    return image
