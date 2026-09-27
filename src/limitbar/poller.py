from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from limitbar.adapters.base import ProviderError, UsageAdapter
from limitbar.cache import SnapshotCache
from limitbar.models import Health, UsageSnapshot


LOG = logging.getLogger(__name__)
UpdateCallback = Callable[[str, UsageSnapshot], None]


@dataclass(slots=True)
class Backoff:
    base_seconds: int
    maximum_seconds: int = 1800
    failures: int = 0

    def success(self) -> int:
        self.failures = 0
        return self.base_seconds

    def failure(self, retry_after_seconds: int | None = None, jitter: float | None = None) -> int:
        self.failures += 1
        delay = min(self.maximum_seconds, self.base_seconds * (2 ** (self.failures - 1)))
        if retry_after_seconds is not None:
            delay = max(delay, retry_after_seconds)
        factor = jitter if jitter is not None else random.uniform(0.9, 1.1)
        return max(1, round(delay * factor))


class PollCoordinator:
    def __init__(
        self,
        adapters: list[UsageAdapter],
        poll_seconds: int,
        cache: SnapshotCache,
        callback: UpdateCallback,
    ) -> None:
        self.adapters = adapters
        self.poll_seconds = poll_seconds
        self.cache = cache
        self.callback = callback
        self.snapshots = cache.load()
        self._stop = threading.Event()
        self._refresh = {adapter.provider_id: threading.Event() for adapter in adapters}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        for provider_id, snapshot in self.snapshots.items():
            self.callback(provider_id, snapshot)
        for adapter in self.adapters:
            thread = threading.Thread(
                target=self._run_provider,
                args=(adapter,),
                name=f"limitbar-{adapter.provider_id}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def refresh(self, provider_id: str | None = None) -> None:
        for current_id, event in self._refresh.items():
            if provider_id is None or provider_id == current_id:
                event.set()

    def stop(self) -> None:
        self._stop.set()
        for event in self._refresh.values():
            event.set()
        for thread in self._threads:
            thread.join(timeout=2)

    def _run_provider(self, adapter: UsageAdapter) -> None:
        # These adapters are local/low-cost reads. A half-hour backoff leaves
        # the widget misleadingly stale after a reset, so recover within 10m.
        backoff = Backoff(self.poll_seconds, maximum_seconds=600)
        event = self._refresh[adapter.provider_id]
        delay = 0
        while not self._stop.is_set():
            if delay > 0:
                event.wait(delay)
                event.clear()
                if self._stop.is_set():
                    break
            try:
                snapshot = adapter.fetch()
                self._record(snapshot)
                delay = _next_success_delay(snapshot, backoff.success())
            except ProviderError as error:
                LOG.warning("%s refresh failed: %s", adapter.provider_id, error)
                self._publish_failure(adapter, str(error))
                if error.retryable:
                    delay = backoff.failure(error.retry_after_seconds)
                else:
                    delay = max(900, self.poll_seconds)
            except Exception as error:  # Provider boundaries must not crash the tray process.
                LOG.exception("Unexpected %s adapter failure", adapter.provider_id)
                self._publish_failure(adapter, f"Unexpected adapter error: {type(error).__name__}")
                delay = backoff.failure()

    def _record(self, snapshot: UsageSnapshot) -> None:
        with self._lock:
            self.snapshots[snapshot.provider_id] = snapshot
            self.cache.save(self.snapshots)
        self.callback(snapshot.provider_id, snapshot)

    def _publish_failure(self, adapter: UsageAdapter, message: str) -> None:
        with self._lock:
            existing = self.snapshots.get(adapter.provider_id)
        if existing:
            snapshot = replace(existing, health=Health.STALE, message=message)
        else:
            snapshot = UsageSnapshot(
                provider_id=adapter.provider_id,
                provider_name=adapter.provider_name,
                windows=(),
                fetched_at=datetime.now(timezone.utc),
                health=Health.UNAVAILABLE,
                message=message,
            )
        self.callback(adapter.provider_id, snapshot)


def _next_success_delay(snapshot: UsageSnapshot, normal_delay: int, now: datetime | None = None) -> int:
    """Poll quickly around a reset so an exhausted window never lingers."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reset_seconds = [
        (window.resets_at.astimezone(timezone.utc) - now).total_seconds()
        for window in snapshot.windows
        if window.resets_at is not None
    ]
    if not reset_seconds:
        return normal_delay
    nearest = min(reset_seconds)
    if nearest <= 15:
        return 10
    if nearest < normal_delay:
        return max(10, min(normal_delay, round(nearest) + 3))
    return normal_delay
