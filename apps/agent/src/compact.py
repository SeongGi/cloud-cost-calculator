"""Keep the model's context small: earlier estimate_cost results (several KB of JSON each) are
sent to the model as a one-line summary. The conversation state and the UI keep the full result."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


def summarize(content) -> str:
    try:
        r = json.loads(content)
    except (TypeError, ValueError):
        return str(content)[:200]
    if r.get("state") != "ok":
        return f"estimate_cost: {r.get('state')}"
    # Keys and quantities let a follow-up ("EC2를 4대로") keep the specs said earlier.
    parts = [f"{line['key']}×{line['qty']}" for line in r.get("lines", []) if line.get("included", True)]
    return (f"estimate_cost 완료: {r.get('cloud')} {r.get('region')}, {', '.join(parts)}, "
            f"합계 {r.get('total_cents', 0) / 100:.2f} USD (화면에 표시됨)")


def compact(messages: list) -> list:
    return [
        m.model_copy(update={"content": summarize(m.content)})
        if isinstance(m, ToolMessage) and m.name == "estimate_cost" else m
        for m in messages
    ]


class CompactToolResults(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=compact(request.messages)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=compact(request.messages)))
