from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(ABC):
    """Returns raw JSON text. Validation happens in the intelligence layer."""

    name: str = "base"
    model: str = ""
    sends_data_off_device: bool = False

    @abstractmethod
    def complete_json(self, system: str, user: str, meta: dict | None = None) -> str: ...

    @abstractmethod
    def test(self) -> tuple[bool, str]: ...

    def cache_key(self) -> str:
        return f"{self.name}:{self.model}"
