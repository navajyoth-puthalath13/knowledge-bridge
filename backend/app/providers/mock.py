"""Deterministic provider for tests and offline development.

Responses are looked up by note title from a JSON mapping
{"<note title>": <NoteAnalysis-shaped dict or raw string>}. Unknown notes get
an empty analysis. Set KB_MOCK_RESPONSES=/path/to/file.json to use it from the
running app (provider name "mock").
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import LLMProvider, ProviderError


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, responses: dict | None = None, path: str | None = None, fail_times: int = 0):
        self.model = "mock-1"
        self.responses = dict(responses or {})
        if path:
            self.responses.update(json.loads(Path(path).read_text("utf-8")))
        self.fail_times = fail_times
        self.calls: list[dict] = []

    def complete_json(self, system: str, user: str, meta: dict | None = None) -> str:
        meta = meta or {}
        self.calls.append(meta)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderError("mock provider failure", retryable=True)
        resp = self.responses.get(meta.get("note_title", ""), {"note_category": "other", "entities": []})
        return resp if isinstance(resp, str) else json.dumps(resp)

    def test(self) -> tuple[bool, str]:
        return True, f"mock provider with {len(self.responses)} canned responses"
