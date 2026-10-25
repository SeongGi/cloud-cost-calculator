"""Gemini first, local Ollama when Gemini is unavailable.

The agent is built with the local model. This middleware swaps in Gemini for each model call when
an admin has saved a key; if that call fails (quota, network, bad key), the same request goes to the
local model and Gemini rests for a while so visitors do not wait on a failing call every time.
Prices are unaffected either way: the model only maps words to items, the tool does the math.

Known limit: a failure after Gemini already streamed part of a text answer is rare (quota errors
arrive before the first token) but would show both partial answers in the same message."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI

from src import settings

logger = logging.getLogger(__name__)

QUOTA_COOLDOWN_S = 600
ERROR_COOLDOWN_S = 120


def is_quota_error(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 429 or getattr(exc, "status_code", None) == 429:
        return True
    text = f"{type(exc).__name__} {exc}"
    return bool(re.search(r"\b429\b|RESOURCE_EXHAUSTED|quota|rate limit", text, re.IGNORECASE))


@dataclass
class RouterStatus:
    last_model: str = ""
    last_error: str = ""
    last_error_at: float = 0.0
    cooldown_until: float = 0.0
    gemini_calls: int = 0
    local_calls: int = 0
    fallbacks: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def public(self) -> dict:
        now = time.time()
        return {
            "last_model": self.last_model,
            "last_error": self.last_error,
            "last_error_at": int(self.last_error_at) or None,
            "cooldown_seconds": max(0, int(self.cooldown_until - now)),
            "gemini_calls": self.gemini_calls,
            "local_calls": self.local_calls,
            "fallbacks": self.fallbacks,
        }


STATUS = RouterStatus()
_models: dict[tuple[str, str], ChatGoogleGenerativeAI] = {}


_models_lock = threading.Lock()


def gemini_model(key: str, model: str) -> ChatGoogleGenerativeAI:
    with _models_lock:
        llm = _models.get((key, model))
        if llm is None:
            _models.clear()  # a new key or model replaces the old client
            # max_retries=1 is a single attempt (0 would mean the SDK default of several retries
            # with backoff, so a visitor could wait minutes before the local fallback).
            llm = ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=0, max_retries=1, timeout=30)
            _models[(key, model)] = llm
        return llm


def reset_cooldown() -> None:
    with STATUS.lock:
        STATUS.cooldown_until = 0.0
        STATUS.last_error = ""


class LLMRouter(AgentMiddleware):
    def _choose(self):
        s = settings.load()
        if s.gemini_active and time.time() >= STATUS.cooldown_until:
            return gemini_model(s.gemini_api_key, s.gemini_model), s.gemini_model
        return None, ""

    def _failed(self, name: str, exc: BaseException) -> None:
        quota = is_quota_error(exc)
        with STATUS.lock:
            STATUS.fallbacks += 1
            STATUS.last_error = ("한도 초과: " if quota else "오류: ") + str(exc)[:300]
            STATUS.last_error_at = time.time()
            STATUS.cooldown_until = time.time() + (QUOTA_COOLDOWN_S if quota else ERROR_COOLDOWN_S)
        logger.warning("Gemini %s failed (%s); using local model", name, type(exc).__name__)

    def _used(self, name: str, gemini: bool) -> None:
        with STATUS.lock:
            STATUS.last_model = name
            if gemini:
                STATUS.gemini_calls += 1
            else:
                STATUS.local_calls += 1

    def _local_name(self, request) -> str:
        return f"local:{getattr(request.model, 'model', 'ollama')}"

    def wrap_model_call(self, request, handler):
        model, name = self._choose()
        if model is not None:
            try:
                result = handler(request.override(model=model))
                self._used(name, True)
                return result
            except Exception as exc:  # any Gemini failure falls back to the local model
                self._failed(name, exc)
        result = handler(request)
        self._used(self._local_name(request), False)
        return result

    async def awrap_model_call(self, request, handler):
        model, name = self._choose()
        if model is not None:
            try:
                result = await handler(request.override(model=model))
                self._used(name, True)
                return result
            except Exception as exc:
                self._failed(name, exc)
        result = await handler(request)
        self._used(self._local_name(request), False)
        return result
