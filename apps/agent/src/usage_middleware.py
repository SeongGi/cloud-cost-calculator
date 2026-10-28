"""Records one usage event per estimate_cost call without touching the tool itself.

Runs around the tool call, reads cloud, result state and price-list service ids from what the
tool returned, and passes the result on unchanged. What is read from the result is never stored
as is: usage.record() only accepts fixed strings, and service names go through the price list."""

from __future__ import annotations

import json
import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from pricing.engine import CLOUDS, CatalogUnavailable, load_catalog, resolve_service
from src import usage
from src.cost_tool import parse_items

logger = logging.getLogger(__name__)


def _service_ids(cloud: object, items: object) -> list[str]:
    """Price-list ids for the items; "unknown" for anything that does not resolve."""
    if cloud not in CLOUDS or not isinstance(items, list):
        return ["unknown"]
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return ["unknown"]
    ids = []
    for item in items:
        name = item.get("service") if isinstance(item, dict) else None
        ids.append((resolve_service(cloud, name, catalog)[0] if isinstance(name, str) else None) or "unknown")
    return ids or ["unknown"]


def _args_items(args: dict) -> list[dict]:
    try:
        return parse_items(args.get("items"))
    except Exception:
        return []


def _event(request, result: object, failed: bool) -> dict | None:
    call = request.tool_call
    if call.get("name") != "estimate_cost":
        return None
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    cloud, items = args.get("cloud"), _args_items(args)
    state = "error"
    if not failed:
        if not isinstance(result, ToolMessage):
            state = "other"
        elif result.status == "error":
            # Argument validation failed: the content echoes the arguments, so it is not read.
            state = "invalid_arguments"
        else:
            state = "other"
            try:
                data = json.loads(result.content) if isinstance(result.content, str) else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                state = data.get("state") if isinstance(data.get("state"), str) else "other"
                req = data.get("request")
                if isinstance(req, dict):
                    cloud = req.get("cloud", cloud)
                    if isinstance(req.get("items"), list) and req["items"]:
                        items = req["items"]
    kind = "estimate_peer" if str(call.get("id") or "").startswith("peer-") else "estimate"
    return {"kind": kind, "cloud": cloud if isinstance(cloud, str) else None, "state": state,
            "services": _service_ids(cloud, items)}


def _note(request, result: object, failed: bool, blocking: bool) -> None:
    try:
        event = _event(request, result, failed)
        if event is not None:
            usage.record(event.pop("kind"), thread_id=usage.current_thread_id(), blocking=blocking, **event)
    except Exception as exc:  # statistics must never change the tool's outcome
        logger.warning("usage middleware skipped: error=%s", type(exc).__name__)


class UsageMiddleware(AgentMiddleware):
    def wrap_tool_call(self, request, handler):
        try:
            result = handler(request)
        except Exception:
            _note(request, None, True, True)
            raise
        _note(request, result, False, True)
        return result

    async def awrap_tool_call(self, request, handler):
        try:
            result = await handler(request)
        except Exception:
            _note(request, None, True, False)
            raise
        _note(request, result, False, False)
        return result
