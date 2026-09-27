from __future__ import annotations

import ctypes
import os
import winreg
from ctypes import wintypes

from limitbar.config import APP_NAME, executable_command


SPI_GETWORKAREA = 0x0030
ERROR_ALREADY_EXISTS = 183
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def work_area() -> tuple[int, int, int, int]:
    rect = RECT()
    if os.name == "nt" and ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, 1920, 1040


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        # PER_MONITOR_AWARE_V2 keeps Tk geometry aligned with physical taskbar pixels.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def taskbar_rects() -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    """Return primary taskbar and notification-area rectangles in physical pixels."""
    if os.name != "nt":
        return None, None
    user32 = ctypes.windll.user32
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        return None, None
    taskbar_rect = _window_rect(taskbar)
    tray_rect: tuple[int, int, int, int] | None = None
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        class_name = buffer.value.lower()
        if class_name in {"traynotifywnd", "systemtray_main", "controlcenterbutton"}:
            matches.append(hwnd)
        return True

    user32.EnumChildWindows(taskbar, callback, 0)
    rectangles = [_window_rect(hwnd) for hwnd in matches]
    rectangles = [rect for rect in rectangles if rect and rect[2] > rect[0]]
    if rectangles:
        tray_rect = min(rectangles, key=lambda rect: rect[0])
    return taskbar_rect, tray_rect


def keep_window_on_taskbar(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    place_window(hwnd, x, y, width, height, topmost=True)


def place_window(hwnd: int, x: int, y: int, width: int, height: int, topmost: bool = False) -> None:
    if os.name == "nt" and hwnd:
        hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT: Tk exposes a child HWND.
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST if topmost else 0,
            x,
            y,
            width,
            height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )


def style_widget_window(hwnd: int) -> None:
    if os.name != "nt" or not hwnd:
        return
    hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd
    try:
        dwm = ctypes.windll.dwmapi
        dark = wintypes.BOOL(True)
        corners = ctypes.c_int(2)  # DWMWCP_ROUND
        backdrop = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW: Desktop Acrylic on Windows 11.
        border = wintypes.DWORD(0xFFFFFFFE)  # DWMWA_COLOR_NONE
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    except (AttributeError, OSError):
        pass


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def set_start_with_windows(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, executable_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def acquire_single_instance() -> int | None:
    if os.name != "nt":
        return 1
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\LimitBar.SingleInstance")
    if not handle or kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle: int | None) -> None:
    if handle and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(handle)
