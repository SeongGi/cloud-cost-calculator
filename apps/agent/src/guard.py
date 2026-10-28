"""Every question gets an answer.

Wraps each model call (outside the Gemini/local router): a hard deadline, a readable reply when
both models fail, a reply when the model returns nothing, and at most one estimate per cloud the
user actually named (a model once sent "Azure VPN" and "GCP Cloud VPN" for one Azure question)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

from src import usage
from src.cost_tool import last_user_text, named_clouds, previous_request

logger = logging.getLogger(__name__)

# Gemini (30 s) failing, then a cold local model (~85 s load + generation) must still fit.
DEADLINE_S = float(os.environ.get("MODEL_CALL_DEADLINE_S", "200"))
FAILED = ("지금 답변을 만들지 못했습니다. 잠시 후 같은 질문을 다시 보내 주세요. "
          "계속 안 되면 '새 대화'로 시작하면 됩니다.")
EMPTY = ("질문을 계산 항목으로 바꾸지 못했습니다. 클라우드와 서비스 이름을 함께 적어 주세요. "
         "예: 'Azure D2s v5 VM 3대와 MySQL', '여기에 VPN 추가'.")


CONNECT = re.compile(r"연결|이어|잇|인터커넥트|interconnect|피어링|peering|하이브리드", re.IGNORECASE)


def peer_vpn_calls(calls: list[dict], user_text: str, state: dict) -> list[dict]:
    """"(GCP 구성 뒤) aws와 vpn으로 연결하면?" is a link with two ends: when the model priced only
    the named cloud, also add a VPN to the configuration already estimated on the other side."""
    if not CONNECT.search(user_text):
        return []
    called = {(c.get("args") or {}).get("cloud") for c in calls if c["name"] == "estimate_cost"}
    extra = []
    for cloud in ("aws", "azure", "gcp"):
        if cloud in called:
            continue
        prev = previous_request(state, cloud)
        if prev is not None and called:
            extra.append({"name": "estimate_cost", "id": f"peer-{uuid.uuid4().hex[:12]}", "type": "tool_call",
                          "args": {"cloud": cloud, "follow_up": True, "items": [{"service": "vpn"}]}})
    return extra


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
    peers = peer_vpn_calls(msg.tool_calls or [], user_text, state or {})
    if peers:
        logger.info("added VPN on the other side of the link: %s", [p["args"]["cloud"] for p in peers])
        msg = msg.model_copy(update={"tool_calls": [*(msg.tool_calls or []), *peers]})
    text = msg.content if isinstance(msg.content, str) else "".join(
        b.get("text", "") for b in msg.content if isinstance(b, dict))
    if not msg.tool_calls and not text.strip():
        msg = msg.model_copy(update={"content": EMPTY})
    return ModelResponse(result=[*response.result[:-1], msg], structured_response=response.structured_response)


def _note_empty(response: ModelResponse, blocking: bool) -> ModelResponse:
    """tidy() stays pure; the record is made here when it swapped in the EMPTY notice."""
    last = response.result[-1] if response.result else None
    if isinstance(last, AIMessage) and last.content == EMPTY:
        usage.record("answer_empty", thread_id=usage.current_thread_id(), blocking=blocking)
    return response


class ResponseGuard(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        try:
            return _note_empty(tidy(handler(request), last_user_text(request.state or {}), request.state), True)
        except Exception:
            logger.exception("model call failed; answering with a notice")
            usage.record("answer_failed", thread_id=usage.current_thread_id())
            return ModelResponse(result=[AIMessage(content=FAILED)])

    async def awrap_model_call(self, request, handler):
        try:
            response = await asyncio.wait_for(handler(request), timeout=DEADLINE_S)
            return _note_empty(tidy(response, last_user_text(request.state or {}), request.state), False)
        except Exception:  # includes the deadline (TimeoutError)
            logger.exception("model call failed or timed out; answering with a notice")
            usage.record("answer_failed", thread_id=usage.current_thread_id(), blocking=False)
            return ModelResponse(result=[AIMessage(content=FAILED)])
