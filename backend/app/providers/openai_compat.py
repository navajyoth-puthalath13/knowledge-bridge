"""OpenAI-compatible chat completions client (Ollama, Google Gemini, Groq, OpenAI, xAI).

All of them expose POST {base}/chat/completions with the same request shape, so
one client covers them; only the base URL, key and default model differ.
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import LLMProvider, ProviderError

log = logging.getLogger(__name__)

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",  # free tier
    "groq": "https://api.groq.com/openai/v1",  # free tier
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, name: str, model: str, api_key: str | None, base_url: str = "",
                 temperature: float = 0.0, timeout: float = 90.0, max_attempts: int = 4,
                 transport: httpx.BaseTransport | None = None):
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or BASE_URLS[name]).rstrip("/")
        self.temperature = temperature
        self.max_attempts = max_attempts
        self.sends_data_off_device = name != "ollama"
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._sleep = time.sleep

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, path: str, body: dict) -> dict:
        if self.name != "ollama" and not self.api_key:
            raise ProviderError(f"No API key configured for {self.name}.")
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self._client.post(self.base_url + path, json=body, headers=self._headers())
            except httpx.TimeoutException as e:
                last = ProviderError(f"{self.name} timed out", retryable=True)
            except httpx.TransportError as e:
                last = ProviderError(f"Could not reach {self.name}: {e}", retryable=True)
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (401, 403):
                    raise ProviderError(f"{self.name} rejected the API key (HTTP {r.status_code}).")
                if r.status_code == 404:
                    raise ProviderError(f"{self.name}: model or endpoint not found ({self.model}).")
                if r.status_code == 429 or r.status_code >= 500:
                    last = ProviderError(f"{self.name} returned HTTP {r.status_code}", retryable=True)
                    ra = r.headers.get("retry-after")
                    if ra and ra.replace(".", "", 1).isdigit():
                        delay = max(delay, min(float(ra), 30.0))
                else:
                    raise ProviderError(f"{self.name} returned HTTP {r.status_code}: {r.text[:300]}")
            if attempt < self.max_attempts:
                log.info("provider attempt %d failed (%s); retrying in %.1fs", attempt, last, delay)
                self._sleep(delay)
                delay *= 2
        assert last is not None
        raise last

    def complete_json(self, system: str, user: str, meta: dict | None = None) -> str:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        data = self._post("/chat/completions", body)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name} returned an unexpected response shape") from e

    def test(self) -> tuple[bool, str]:
        try:
            out = self.complete_json(
                'Reply with JSON only.', 'Return {"ok": true}.', None)
            return True, f"{self.name} / {self.model} responded: {out[:60]}"
        except ProviderError as e:
            return False, str(e)
