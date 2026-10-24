"""Provider abstraction and API error handling (no network: httpx.MockTransport)."""
import json
import os

import httpx
import pytest

from app.config import ProviderConfig
from app.providers import make_provider
from app.providers.base import ProviderError
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.mock import MockProvider
from app.security.secrets import get_secret, set_secret

from .conftest import PODCAST


def _provider(handler, name="openai", key="sk-test"):
    p = OpenAICompatibleProvider(name, "m", key, transport=httpx.MockTransport(handler))
    p._sleep = lambda s: None
    return p


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_request_shape_and_success():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return _ok('{"ok": true}')

    p = _provider(h, "xai", "xai-key")
    assert p.complete_json("sys", "user") == '{"ok": true}'
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer xai-key"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert p.sends_data_off_device


def test_retries_rate_limits_and_server_errors():
    replies = iter([httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(503), _ok("{}")])
    p = _provider(lambda r: next(replies))
    assert p.complete_json("s", "u") == "{}"


def test_gives_up_after_max_attempts():
    p = _provider(lambda r: httpx.Response(500))
    with pytest.raises(ProviderError) as e:
        p.complete_json("s", "u")
    assert e.value.retryable


def test_bad_key_is_not_retried():
    calls = []

    def h(r):
        calls.append(1)
        return httpx.Response(401)

    p = _provider(h)
    with pytest.raises(ProviderError, match="rejected the API key"):
        p.complete_json("s", "u")
    assert len(calls) == 1


def test_missing_key_fails_fast():
    with pytest.raises(ProviderError, match="No API key"):
        _provider(lambda r: _ok("{}"), key=None).complete_json("s", "u")


def test_ollama_needs_no_key_and_is_local():
    p = _provider(lambda r: _ok("{}"), name="ollama", key=None)
    assert p.base_url.startswith("http://127.0.0.1:11434") and not p.sends_data_off_device
    assert p.complete_json("s", "u") == "{}"


def test_factory_and_secrets():
    assert make_provider(ProviderConfig(name="none")) is None
    set_secret("openai", "sk-abc")
    assert get_secret("openai") == "sk-abc"
    p = make_provider(ProviderConfig(name="openai"))
    assert p.api_key == "sk-abc" and p.model == "gpt-4.1-mini"
    set_secret("openai", None)
    assert get_secret("openai") is None
    secrets_file = os.path.join(os.environ["KB_DATA_DIR"], "secrets.json")
    assert oct(os.stat(secrets_file).st_mode & 0o777) == "0o600"


def test_invalid_model_output_is_retried_then_recorded(env):
    env.provider = MockProvider({"Design Matters — Debbie Millman with a product designer": "not json at all"})
    rep = env.sync()
    assert rep.status == "partial"
    err = env.db.scalar("SELECT last_error FROM notes WHERE id=?", (PODCAST,))
    assert "failed validation" in err
    assert sum(1 for c in env.provider.calls if c["note_id"] == PODCAST) == 2  # one corrective retry
    # next sync retries the failed note and succeeds once the provider behaves
    env.provider.responses = {}
    rep = env.sync()
    assert rep.status == "ok" and env.db.scalar("SELECT last_error FROM notes WHERE id=?", (PODCAST,)) is None


def test_provider_outage_leaves_note_for_retry(env):
    env.provider = MockProvider({}, fail_times=100)
    rep = env.sync()
    assert rep.stats.created == 6 and rep.stats.processed == 0  # mirroring still completes
    assert env.db.scalar("SELECT COUNT(*) FROM notes WHERE last_error IS NOT NULL") == 6
    env.provider.fail_times = 0
    assert env.sync().stats.processed == 6


def test_markdown_fenced_json_is_accepted(env):
    env.provider = MockProvider({"Reading list": '```json\n{"note_category": "other", "entities": []}\n```'})
    env.sync()
    assert env.db.scalar("SELECT category FROM notes WHERE title='Reading list'") == "other"


@pytest.mark.provider
@pytest.mark.skipif(not os.environ.get("KB_PROVIDER_TESTS"), reason="set KB_PROVIDER_TESTS=1 and an API key")
def test_real_provider_roundtrip():
    name = os.environ.get("KB_PROVIDER", "openai")
    os.environ.pop("KB_DISABLE_KEYRING", None)
    p = make_provider(ProviderConfig(name=name))
    ok, msg = p.test()
    assert ok, msg


@pytest.mark.parametrize("name,url,model", [
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    ("groq", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
])
def test_free_cloud_providers(name, url, model):
    from app.config import FREE_PROVIDERS

    set_secret(name, f"{name}-key")
    p = make_provider(ProviderConfig(name=name))
    assert p.model == model and p.api_key == f"{name}-key" and p.sends_data_off_device
    assert name in FREE_PROVIDERS
    seen = {}

    def h(req):
        seen["url"], seen["auth"] = str(req.url), req.headers["authorization"]
        return _ok("{}")

    p._client = httpx.Client(transport=httpx.MockTransport(h))
    p.complete_json("s", "u")
    assert seen == {"url": url, "auth": f"Bearer {name}-key"}


def test_dotenv_file_supplies_keys(tmp_path, monkeypatch):
    from app.security import dotenv

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\nGROQ_API_KEY="gsk_test"\nexport GEMINI_API_KEY=abc\n')
    monkeypatch.setenv("GEMINI_API_KEY", "already-set")
    assert dotenv.load([f]) == [f]
    assert get_secret("groq") == "gsk_test"
    assert get_secret("gemini") == "already-set"  # real environment wins
    monkeypatch.delenv("GROQ_API_KEY")


def test_env_file_is_git_ignored():
    import subprocess

    from .conftest import REPO

    r = subprocess.run(["git", "check-ignore", "-q", str(REPO / ".env")], cwd=REPO)
    assert r.returncode == 0, ".env must be ignored by git"
    r = subprocess.run(["git", "check-ignore", "-q", str(REPO / ".env.example")], cwd=REPO)
    assert r.returncode == 1, ".env.example should be tracked"
