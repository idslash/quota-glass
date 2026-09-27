from __future__ import annotations

import json
from pathlib import Path

from limitbar.config import app_data_dir
from limitbar.models import UsageSnapshot


class SnapshotCache:
    """Atomic, secret-free cache of the last successful provider snapshots."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "snapshots.json"

    def load(self) -> dict[str, UsageSnapshot]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {
                provider_id: UsageSnapshot.from_cache_dict(value)
                for provider_id, value in raw.items()
                if isinstance(value, dict)
            }
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return {}

    def save(self, snapshots: dict[str, UsageSnapshot]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            provider_id: snapshot.as_cache_dict()
            for provider_id, snapshot in snapshots.items()
            if snapshot.windows
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)

