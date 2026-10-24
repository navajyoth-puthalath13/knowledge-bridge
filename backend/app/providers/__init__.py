import os

from ..config import CLOUD_PROVIDERS, ProviderConfig
from ..security.secrets import get_secret
from .base import LLMProvider, ProviderError
from .mock import MockProvider
from .openai_compat import OpenAICompatibleProvider


def make_provider(cfg: ProviderConfig) -> LLMProvider | None:
    """None means rules-only mode: no model is called."""
    if cfg.name == "none":
        return None
    if cfg.name == "mock":
        return MockProvider(path=os.environ.get("KB_MOCK_RESPONSES"))
    key = get_secret(cfg.name) if cfg.name in CLOUD_PROVIDERS else None
    return OpenAICompatibleProvider(cfg.name, cfg.resolved_model(), key, cfg.base_url,
                                    cfg.temperature, cfg.timeout_seconds)


__all__ = ["LLMProvider", "ProviderError", "MockProvider", "OpenAICompatibleProvider", "make_provider"]
