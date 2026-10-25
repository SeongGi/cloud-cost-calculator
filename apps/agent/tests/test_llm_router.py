"""Gemini-first routing with local fallback, the settings file and the internal admin API."""

import os
import stat

import pytest
from fastapi.testclient import TestClient

from src import router, settings

KEY = "AIzaTESTKEY0000000000000000000000000abcd"
TOKEN = "t" * 40


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    settings._cache = None
    router.STATUS.__init__()
    router._models.clear()
    yield


class FakeRequest:
    def __init__(self, model):
        self.model = model

    def override(self, model):
        return FakeRequest(model)


class Local:
    model = "qwen3.5:latest"


def run(handler):
    return router.LLMRouter().wrap_model_call(FakeRequest(Local()), handler)


def test_no_key_uses_local_only():
    seen = []
    assert run(lambda r: seen.append(r.model) or "local") == "local"
    assert isinstance(seen[0], Local) and router.STATUS.local_calls == 1


def test_gemini_first_then_fallback_on_quota_and_cooldown():
    settings.save(settings.LLMSettings(gemini_api_key=KEY))
    calls = []

    def handler(r):
        calls.append(type(r.model).__name__)
        if not isinstance(r.model, Local):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return "local"

    assert run(handler) == "local"
    assert calls == ["ChatGoogleGenerativeAI", "Local"]
    st = router.STATUS.public()
    assert st["fallbacks"] == 1 and st["cooldown_seconds"] > 500 and st["last_error"].startswith("한도 초과")
    calls.clear()
    assert run(handler) == "local" and calls == ["Local"]  # resting: Gemini is skipped


def test_gemini_success_and_disabled_switch():
    settings.save(settings.LLMSettings(gemini_api_key=KEY, gemini_model="gemini-x"))
    assert run(lambda r: "gemini" if not isinstance(r.model, Local) else "local") == "gemini"
    assert router.STATUS.last_model == "gemini-x"
    settings.save(settings.LLMSettings(gemini_api_key=KEY, gemini_enabled=False))
    assert run(lambda r: "gemini" if not isinstance(r.model, Local) else "local") == "local"


def test_other_errors_rest_shorter():
    settings.save(settings.LLMSettings(gemini_api_key=KEY))
    run(lambda r: (_ for _ in ()).throw(ValueError("bad request")) if not isinstance(r.model, Local) else "ok")
    assert 0 < router.STATUS.public()["cooldown_seconds"] <= router.ERROR_COOLDOWN_S


def test_settings_file_is_private_and_masked():
    settings.save(settings.LLMSettings(gemini_api_key=KEY))
    path = settings.settings_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    pub = settings.load().public()
    assert KEY not in str(pub) and pub["gemini_key_hint"] == "…abcd" and pub["gemini_key_set"]


def client():
    import main
    return TestClient(main.app)


def test_admin_api_requires_token_and_never_returns_key():
    c = client()
    assert c.get("/admin/llm").status_code == 403
    assert c.get("/admin/llm", headers={"X-Admin-Token": "wrong"}).status_code == 403
    h = {"X-Admin-Token": TOKEN}
    r = c.put("/admin/llm", json={"gemini_api_key": KEY, "gemini_model": "gemini-flash-latest"}, headers=h)
    assert r.status_code == 200 and KEY not in r.text and r.json()["gemini_key_set"]
    assert c.put("/admin/llm", json={"gemini_api_key": "short"}, headers=h).status_code == 422
    assert c.put("/admin/llm", json={"gemini_api_key": "키" * 30}, headers=h).status_code == 422
    # Newer key shapes and pasted whitespace are accepted; the stored key has no whitespace.
    r = c.put("/admin/llm", json={"gemini_api_key": " AQ.Ab8RN6LxTESTkey_0123456789-abcd\n"}, headers=h)
    assert r.status_code == 200 and r.json()["gemini_key_hint"] == "…abcd"
    assert settings.load().gemini_api_key == "AQ.Ab8RN6LxTESTkey_0123456789-abcd"
    assert c.put("/admin/llm", json={"gemini_model": "../etc"}, headers=h).status_code == 422
    assert c.delete("/admin/llm/key", headers=h).json()["gemini_key_set"] is False
    assert c.get("/openapi.json").status_code == 404


def test_admin_api_off_without_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert client().get("/admin/llm", headers={"X-Admin-Token": ""}).status_code == 403


def test_gemini_client_makes_a_single_attempt():
    # 0 would mean the SDK default (several retries with backoff) before the local fallback.
    llm = router.gemini_model(KEY, "gemini-x")
    assert llm.max_retries == 1 and llm.timeout == 30
    assert router.gemini_model(KEY, "gemini-x") is llm


def test_back_to_back_saves_are_seen():
    settings.save(settings.LLMSettings(gemini_api_key=KEY, gemini_enabled=True))
    assert settings.load().gemini_enabled
    settings.save(settings.LLMSettings(gemini_api_key=KEY, gemini_enabled=False))
    assert not settings.load().gemini_enabled


def test_quota_detected_from_error_code():
    class ClientError(Exception):
        code = 429
    assert router.is_quota_error(ClientError("Too Many Requests"))
    assert not router.is_quota_error(ValueError("bad schema"))
