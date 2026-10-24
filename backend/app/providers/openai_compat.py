"""OpenAI-compatible chat completions client (Ollama, Google Gemini, Groq, OpenAI, xAI).

All of them expose POST {base}/chat/completions with the same request shape, so
one client covers them; only the base URL, key and default model differ.
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import LLMProvider, ModelNotFound, ProviderError

log = logging.getLogger(__name__)

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",  # free tier
    "groq": "https://api.groq.com/openai/v1",  # free tier
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}

# Providers retire and rename models often, so the default is not a fixed name: when the
# configured model is missing, the first available model from this list is used instead.
PREFERRED_MODELS = {
    "groq": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct",
             "meta-llama/llama-4-maverick-17b-128e-instruct", "qwen/qwen3-32b", "openai/gpt-oss-20b",
             "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.1-8b-instant"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash-lite"],
}
_NOT_CHAT = ("whisper", "tts", "guard", "embed", "playai", "orpheus", "compound", "vision-preview", "imagen", "aqa")


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
    except Exception:
        return r.text[:300]
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err)[:300]


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, name: str, model: str, api_key: str | None, base_url: str = "",
                 temperature: float = 0.0, timeout: float = 90.0, max_attempts: int = 6,
                 transport: httpx.BaseTransport | None = None, auto_model: bool = False):
        self.name = name
        self.auto_model = auto_model  # True when the user left the model blank: pick an available one
        self.json_mode = True
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
                if r.status_code == 404 or (r.status_code == 400 and "model" in _error_text(r).lower()
                                            and ("not exist" in _error_text(r).lower()
                                                 or "decommissioned" in _error_text(r).lower())):
                    raise ModelNotFound(f"{self.name}: model '{self.model}' is not available ({_error_text(r)}). "
                                        "Choose another model in Setup.")
                if r.status_code == 429 or r.status_code >= 500:
                    last = ProviderError(f"{self.name} returned HTTP {r.status_code}", retryable=True)
                    ra = r.headers.get("retry-after")
                    if ra and ra.replace(".", "", 1).isdigit():
                        # free tiers (Groq, Gemini) reset per-minute quotas within a minute
                        delay = max(delay, min(float(ra), 60.0))
                elif r.status_code == 400 and "response_format" in _error_text(r) and body.get("response_format"):
                    # this model has no JSON mode; ask without it (the prompt still demands JSON)
                    self.json_mode = False
                    body = {k: v for k, v in body.items() if k != "response_format"}
                    continue
                else:
                    raise ProviderError(f"{self.name} returned HTTP {r.status_code}: {_error_text(r)}")
            if attempt < self.max_attempts:
                log.info("provider attempt %d failed (%s); retrying in %.1fs", attempt, last, delay)
                self._sleep(delay)
                delay *= 2
        assert last is not None
        raise last

    def list_models(self) -> list[str]:
        """Chat models this account can use, as reported by the provider."""
        if self.name != "ollama" and not self.api_key:
            raise ProviderError(f"No API key configured for {self.name}.")
        try:
            r = self._client.get(self.base_url + "/models", headers=self._headers())
        except httpx.HTTPError as e:
            raise ProviderError(f"Could not reach {self.name}: {e}") from e
        if r.status_code in (401, 403):
            raise ProviderError(f"{self.name} rejected the API key (HTTP {r.status_code}).")
        if r.status_code != 200:
            raise ProviderError(f"{self.name} could not list models (HTTP {r.status_code}): {_error_text(r)}")
        ids = [str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", [])]
        return sorted(i for i in ids if i and not any(x in i.lower() for x in _NOT_CHAT))

    def pick_model(self, available: list[str]) -> str | None:
        for m in PREFERRED_MODELS.get(self.name, []):
            if m in available:
                return m
        return available[0] if available else None

    def complete_json(self, system: str, user: str, meta: dict | None = None) -> str:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._post("/chat/completions", body)
        except ModelNotFound:
            if not self.auto_model:
                raise
            choice = self.pick_model(self.list_models())
            if not choice or choice == self.model:
                raise
            log.info("%s: model %s unavailable, using %s", self.name, self.model, choice)
            self.model = choice
            self.auto_model = False
            return self.complete_json(system, user, meta)
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
