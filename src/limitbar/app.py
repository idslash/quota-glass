from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from limitbar.adapters import ClaudeUsageAdapter, CodexUsageAdapter
from limitbar.cache import SnapshotCache
from limitbar.config import Settings
from limitbar.forecast import UsageForecaster
from limitbar.logging_setup import configure_logging
from limitbar.models import LimitWindow, UsageSnapshot
from limitbar.poller import PollCoordinator
from limitbar.ui import LimitBarUI
from limitbar.native_ui import NativeLimitBarUI, native_frontend_path
from limitbar.windows import acquire_single_instance, enable_dpi_awareness, release_single_instance


def main() -> None:
    enable_dpi_awareness()
    handle = acquire_single_instance()
    if handle is None:
        return
    configure_logging()
    settings = Settings.load()
    ui: LimitBarUI | NativeLimitBarUI | None = None
    poller: PollCoordinator | None = None
    cache = SnapshotCache()
    try:
        def enabled_adapters():
            adapters = []
            if settings.providers.get("claude", True):
                adapters.append(ClaudeUsageAdapter())
            if settings.providers.get("codex", True):
                adapters.append(CodexUsageAdapter())
            return adapters

        def refresh() -> None:
            if poller:
                poller.refresh()

        def providers_changed() -> None:
            nonlocal poller
            if poller:
                poller.stop()
            enabled = {provider_id for provider_id, value in settings.providers.items() if value}
            cache.retain(enabled)
            UsageForecaster().retain(enabled)
            if ui:
                ui.remove_disabled_providers(enabled)
            poller = PollCoordinator(enabled_adapters(), settings.poll_seconds, cache, ui.post_snapshot)
            poller.start()

        def exit_app() -> None:
            if poller:
                poller.stop()
            if ui:
                ui.close()

        use_native = os.name == "nt" and native_frontend_path().exists() and os.environ.get("LIMITBAR_LEGACY_UI") != "1"
        ui = NativeLimitBarUI(settings, refresh, exit_app, providers_changed) if use_native else LimitBarUI(settings, refresh, exit_app, providers_changed)
        if os.environ.get("LIMITBAR_DEMO") == "1":
            for snapshot in _demo_snapshots():
                ui.post_snapshot(snapshot.provider_id, snapshot)
        else:
            enabled = {provider_id for provider_id, value in settings.providers.items() if value}
            cache.retain(enabled)
            poller = PollCoordinator(enabled_adapters(), settings.poll_seconds, cache, ui.post_snapshot)
            poller.start()
        ui.run()
    finally:
        if poller:
            poller.stop()
        release_single_instance(handle)


def _demo_snapshots() -> tuple[UsageSnapshot, UsageSnapshot]:
    now = datetime.now(timezone.utc)
    return (
        UsageSnapshot(
            provider_id="claude",
            provider_name="Claude",
            source="demo",
            windows=(
                LimitWindow("five_hour", "5h", 27, now + timedelta(hours=2, minutes=18), 300),
                LimitWindow("seven_day", "7d", 41, now + timedelta(days=4, hours=7), 10_080),
            ),
        ),
        UsageSnapshot(
            provider_id="codex",
            provider_name="ChatGPT",
            source="demo",
            windows=(
                LimitWindow("five_hour", "5h", 36, now + timedelta(hours=3, minutes=2), 300),
                LimitWindow("seven_day", "7d", 18, now + timedelta(days=5, hours=11), 10_080),
            ),
        ),
    )
