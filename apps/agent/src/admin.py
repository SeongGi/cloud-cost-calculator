"""Internal admin API for the LLM settings. Only the app container can reach the agent, and every
call must carry ADMIN_TOKEN; the app checks the Google login before forwarding."""

from __future__ import annotations

import hmac
import json
import os
import re
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src import router, settings, usage

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,80}$")


def require_token(x_admin_token: str = Header(default="")) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    if len(expected) < 32 or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="forbidden")


api = APIRouter(prefix="/admin", dependencies=[Depends(require_token)])


class LLMUpdate(BaseModel):
    gemini_api_key: str | None = Field(default=None, max_length=400)
    gemini_model: str | None = Field(default=None, max_length=80)
    gemini_enabled: bool | None = None


def state() -> dict:
    return {**settings.load().public(), **router.STATUS.public()}


@api.get("/llm")
def get_llm() -> dict:
    return state()


@api.put("/llm")
def put_llm(body: LLMUpdate) -> dict:
    cur = settings.load()
    # Pasted keys often carry spaces or line breaks; key formats change (e.g. "AIza…", "AQ.…"),
    # so accept any printable ASCII and say which check failed without echoing the key.
    key = cur.gemini_api_key if body.gemini_api_key is None else re.sub(r"\s+", "", body.gemini_api_key)
    model = cur.gemini_model if body.gemini_model is None else body.gemini_model.strip()
    if key and not 20 <= len(key) <= 300:
        raise HTTPException(status_code=422, detail=f"키 길이가 맞지 않습니다({len(key)}자, 20~300자)")
    if key and not re.fullmatch(r"[\x21-\x7e]+", key):
        raise HTTPException(status_code=422, detail="키에 영문·숫자·기호가 아닌 문자가 들어 있습니다(한글이나 특수 공백 등)")
    if not MODEL_NAME.fullmatch(model):
        raise HTTPException(status_code=422, detail="모델 이름이 올바르지 않습니다")
    enabled = cur.gemini_enabled if body.gemini_enabled is None else body.gemini_enabled
    settings.save(settings.LLMSettings(gemini_api_key=key, gemini_model=model, gemini_enabled=enabled))
    router.reset_cooldown()
    return state()


@api.delete("/llm/key")
def delete_key() -> dict:
    cur = settings.load()
    settings.save(settings.LLMSettings(gemini_api_key="", gemini_model=cur.gemini_model,
                                       gemini_enabled=cur.gemini_enabled))
    router.reset_cooldown()
    return state()


def _gemini_get(path: str, key: str) -> dict:
    # The key goes in a header, not the URL, so it never lands in access logs.
    req = urllib.request.Request(f"{GEMINI_API}/{path}", headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


@api.get("/llm/models")
def list_models() -> dict:
    key = settings.load().gemini_api_key
    if not key:
        raise HTTPException(status_code=409, detail="저장된 키가 없습니다")
    try:
        data = _gemini_get("models?pageSize=200", key)
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini 응답 {exc.code}") from None
    except urllib.error.URLError:
        raise HTTPException(status_code=502, detail="Gemini에 연결하지 못했습니다") from None
    names = sorted(
        m["name"].removeprefix("models/") for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m.get("name", "")
    )
    return {"models": names}


@api.post("/llm/test")
def test_llm() -> dict:
    """One tiny call with the saved key and model, outside the visitor path."""
    s = settings.load()
    if not s.gemini_api_key:
        raise HTTPException(status_code=409, detail="저장된 키가 없습니다")
    try:
        reply = router.gemini_model(s.gemini_api_key, s.gemini_model).invoke("Reply with: ok")
    except Exception as exc:
        quota = router.is_quota_error(exc)
        return {"ok": False, "quota": quota, "error": str(exc)[:300]}
    router.reset_cooldown()
    return {"ok": True, "model": s.gemini_model, "reply": (reply.text or "")[:60]}


class QuestionEvent(BaseModel):
    ip: str = Field(max_length=64)
    thread_id: str | None = Field(default=None, max_length=200)
    limited: bool = False


@api.post("/usage/question")
def post_usage_question(body: QuestionEvent) -> dict:
    """The app knows the visitor's IP, the agent holds the salt: the app forwards, the agent hashes."""
    usage.record("limited" if body.limited else "question", ip=body.ip, thread_id=body.thread_id)
    return {"ok": True}


@api.get("/usage")
def get_usage(days: int = 30) -> dict:
    if days not in usage.PERIODS:
        raise HTTPException(status_code=422, detail="days는 7, 30, 90 중 하나입니다")
    return usage.summary(days)
