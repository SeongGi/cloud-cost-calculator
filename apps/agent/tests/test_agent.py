import json

import pytest

from src.cost_tool import estimate_cost, parse_items
from src.llm import ollama_base_url


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://host.docker.internal:11434"])
def test_local_ollama_allowed(monkeypatch, url):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    assert ollama_base_url() == url


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://192.168.1.10:11434", "http://ollama.example.com"])
def test_remote_llm_refused(monkeypatch, url):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    with pytest.raises(ValueError):
        ollama_base_url()


def test_parse_items_recovers_strings():
    assert parse_items('[{"service": "ec2", "count": 2}')[0]["count"] == 2
    assert parse_items('{"service": "s3"}')[0]["service"] == "s3"
    with pytest.raises(ValueError):
        parse_items("not json")


def test_tool_is_direct_and_returns_card_json():
    assert estimate_cost.return_direct is True
    out = json.loads(estimate_cost.invoke({"cloud": "aws", "items": [{"service": "ec2", "spec": "t3.medium", "count": 2}]}))
    assert out["state"] == "ok" and out["total_cents"] == 7592


def test_tool_invalid_cloud_is_reported():
    with pytest.raises(Exception):
        estimate_cost.invoke({"cloud": "oracle", "items": []})


def test_agent_exposes_only_estimate_cost():
    import main

    assert [t.name for t in main.TOOLS] == ["estimate_cost"]


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "qwen3:cloud", "deepseek-v3.1:671b-cloud"])
def test_ollama_cloud_models_refused(monkeypatch, name):
    from src.llm import ollama_model

    monkeypatch.setenv("OLLAMA_MODEL", name)
    with pytest.raises(ValueError):
        ollama_model()


def test_local_model_allowed(monkeypatch):
    from src.llm import ollama_model

    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:latest")
    assert ollama_model() == "qwen3.5:latest"


def test_run_limiter_rejects_when_full():
    import asyncio

    from src.concurrency import RunLimiter

    gate = asyncio.Event()
    statuses = []

    async def slow_app(scope, receive, send):
        await gate.wait()

    async def send(msg):
        if msg["type"] == "http.response.start":
            statuses.append(msg["status"])

    async def scenario():
        limiter = RunLimiter(slow_app, max_running=1, max_waiting=1)
        scope = {"type": "http", "method": "POST", "path": "/"}
        running = asyncio.create_task(limiter(scope, None, send))
        waiting = asyncio.create_task(limiter(scope, None, send))
        await asyncio.sleep(0.01)
        await limiter(scope, None, send)  # third: no slot, queue full
        gate.set()
        await asyncio.gather(running, waiting)

    asyncio.run(scenario())
    assert statuses == [429]


def test_old_tool_results_are_compacted_for_the_model():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from src.compact import compact

    full = estimate_cost.invoke({"cloud": "aws", "items": [{"service": "ec2", "count": 2}]})
    msgs = [HumanMessage("q"), AIMessage(""), ToolMessage(full, tool_call_id="1", name="estimate_cost")]
    out = compact(msgs)
    assert out[2].content.startswith("estimate_cost 완료: aws ap-northeast-2, ec2.t3.medium×2")
    assert "75.92" in out[2].content
    assert len(out[2].content) < 200 < len(full)
    assert msgs[2].content == full  # state itself untouched
