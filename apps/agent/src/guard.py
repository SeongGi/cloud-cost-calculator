"""Every question gets an answer.

Wraps each model call (outside the Gemini/local router): a hard deadline, a readable reply when
both models fail, a reply when the model returns nothing, and at most one estimate per cloud the
user actually named (a model once sent "Azure VPN" and "GCP Cloud VPN" for one Azure question)."""

from __future__ import annotations

import asyncio
import logging
import os

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

from src.cost_tool import last_user_text, named_clouds, previous_request

logger = logging.getLogger(__name__)

# Gemini (30 s) failing, then a cold local model (~85 s load + generation) must still fit.
DEADLINE_S = float(os.environ.get("MODEL_CALL_DEADLINE_S", "200"))
FAILED = ("지금 답변을 만들지 못했습니다. 잠시 후 같은 질문을 다시 보내 주세요. "
          "계속 안 되면 '새 대화'로 시작하면 됩니다.")
EMPTY = ("질문을 계산 항목으로 바꾸지 못했습니다. 클라우드와 서비스 이름을 함께 적어 주세요. "
         "예: 'Azure D2s v5 VM 3대와 MySQL', '여기에 VPN 추가'.")


def tidy(response: ModelResponse, user_text: str, state: dict | None = None) -> ModelResponse:
    if not response.result or not isinstance(response.result[-1], AIMessage):
        return response
    msg = response.result[-1]
    calls = msg.tool_calls or []
    estimates = [c for c in calls if c["name"] == "estimate_cost"]
    if len(estimates) > 1:
        said = named_clouds(user_text)
        allowed = max(1, len(said))
        # Prefer the clouds the user named, else the cloud of the conversation so far.
        prev = previous_request(state or {})
        wanted = said or ({prev["cloud"]} if prev else set())
        ordered = sorted(estimates, key=lambda c: (c.get("args") or {}).get("cloud") not in wanted)
        kept, seen = [], set()
        for c in ordered:
            cloud = (c.get("args") or {}).get("cloud")
            if cloud in seen or len(seen) >= allowed:
                continue
            seen.add(cloud)
            kept.append(c)
        if len(kept) < len(estimates):
            logger.warning("dropped %d extra estimate_cost calls", len(estimates) - len(kept))
            others = [c for c in calls if c["name"] != "estimate_cost"]
            msg = msg.model_copy(update={"tool_calls": kept + others})
    text = msg.content if isinstance(msg.content, str) else "".join(
        b.get("text", "") for b in msg.content if isinstance(b, dict))
    if not msg.tool_calls and not text.strip():
        msg = msg.model_copy(update={"content": EMPTY})
    return ModelResponse(result=[*response.result[:-1], msg], structured_response=response.structured_response)


class ResponseGuard(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        try:
            return tidy(handler(request), last_user_text(request.state or {}), request.state)
        except Exception:
            logger.exception("model call failed; answering with a notice")
            return ModelResponse(result=[AIMessage(content=FAILED)])

    async def awrap_model_call(self, request, handler):
        try:
            response = await asyncio.wait_for(handler(request), timeout=DEADLINE_S)
            return tidy(response, last_user_text(request.state or {}), request.state)
        except Exception:  # includes the deadline (TimeoutError)
            logger.exception("model call failed or timed out; answering with a notice")
            return ModelResponse(result=[AIMessage(content=FAILED)])
