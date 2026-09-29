from __future__ import annotations

import ctypes
import os


_TEXT = {
    "en": {
        "desktop": "Show desktop widget",
        "refresh": "Refresh",
        "taskbar": "Show taskbar island",
        "taskbar_layout": "Taskbar layout",
        "standard": "Standard",
        "compact": "Compact",
        "interface_size": "Interface size",
        "text_size": "Text size",
        "normal": "Normal",
        "large": "Large",
        "extra_large": "Extra large",
        "notifications": "Notifications",
        "monitoring": "Monitoring",
        "monitor_claude": "Claude",
        "monitor_chatgpt": "ChatGPT / Codex",
        "enabled": "Enabled",
        "reset_soon": "Reset soon",
        "fast_usage": "Fast usage",
        "reset_completed": "Reset completed",
        "quiet_hours": "Quiet hours 23:00–08:00",
        "language": "Language",
        "auto": "Auto",
        "allow_screenshots": "Allow screenshots",
        "start_windows": "Start with Windows",
        "open_logs": "Open logs",
        "exit": "Exit",
        "connecting": "connecting",
        "stale": "Usage data has not updated for a while",
        "five_hour": "5-hour limit",
        "weekly": "weekly limit",
        "reserve": "GPT reserve",
        "soon": "Resets in {duration} · {remaining}% of {label} remains",
        "pace": "Weekly usage is ahead of pace: {used}% used, resets in {duration}",
        "complete": "{label} restored · {remaining}% available",
        "burn": "At the current pace, {label} will run out in about {duration}",
        "minute": "min",
        "hour": "h",
        "day": "d",
    },
    "ru": {
        "desktop": "Показать виджет на рабочем столе",
        "refresh": "Обновить",
        "taskbar": "Показывать панель в taskbar",
        "taskbar_layout": "Размер панели",
        "standard": "Обычный",
        "compact": "Компактный",
        "interface_size": "Размер интерфейса",
        "text_size": "Размер текста",
        "normal": "Обычный",
        "large": "Крупный",
        "extra_large": "Очень крупный",
        "notifications": "Уведомления",
        "monitoring": "Мониторинг",
        "monitor_claude": "Claude",
        "monitor_chatgpt": "ChatGPT / Codex",
        "enabled": "Включены",
        "reset_soon": "Скорый сброс",
        "fast_usage": "Быстрый расход",
        "reset_completed": "Лимит восстановлен",
        "quiet_hours": "Не беспокоить 23:00–08:00",
        "language": "Язык",
        "auto": "Автоматически",
        "allow_screenshots": "Разрешить скриншоты",
        "start_windows": "Запускать с Windows",
        "open_logs": "Открыть журнал",
        "exit": "Выход",
        "connecting": "подключение",
        "stale": "Данные давно не обновлялись",
        "five_hour": "5-часовой лимит",
        "weekly": "недельный лимит",
        "reserve": "GPT reserve",
        "soon": "Сброс через {duration} · осталось {remaining}% ({label})",
        "pace": "Недельный расход выше темпа: использовано {used}%, до сброса {duration}",
        "complete": "{label} восстановлен · доступно {remaining}%",
        "burn": "При текущем темпе {label} закончится примерно через {duration}",
        "minute": "мин",
        "hour": "ч",
        "day": "д",
    },
}


def effective_language(setting: str) -> str:
    if setting in {"ru", "en"}:
        return setting
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            return "ru" if buffer.value.lower().startswith("ru") else "en"
    return "en"


def tr(key: str, setting: str, **values: object) -> str:
    language = effective_language(setting)
    template = _TEXT[language].get(key, _TEXT["en"].get(key, key))
    return template.format(**values)
