from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from limitbar.models import UsageSnapshot


@dataclass(slots=True)
class ProviderError(Exception):
    message: str
    retryable: bool = True
    retry_after_seconds: int | None = None

    def __str__(self) -> str:
        return self.message


class UsageAdapter(ABC):
    provider_id: str
    provider_name: str

    @abstractmethod
    def fetch(self) -> UsageSnapshot:
        """Return a normalized usage snapshot without persisting any secret."""
        raise NotImplementedError

