"""Usage statistics: the sqlite store and its summary, the recording points (middleware, guard,
run limiter), the admin API, and that no personal text ever reaches the file."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain.agents.middleware import ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from pricing import engine
from src import admin, guard, usage
from src.cost_tool import estimate_cost
from src.usage_middleware import UsageMiddleware

TOKEN = "t" * 40
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=usage.KST).timestamp()  # 03:00 UTC
DAY = 86400


def at(day: str, hour: int = 12) -> float:
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hour, tzinfo=usage.KST).timestamp()


def rows(sql: str = "SELECT * FROM events ORDER BY rowid") -> list[tuple]:
    path = Path(os.environ["SETTINGS_DIR"]) / "usage.sqlite3"
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def salted(value: str) -> str:
    salt = (Path(os.environ["SETTINGS_DIR"]) / "usage-salt").read_bytes()
    return hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:16]


def db_bytes() -> bytes:
    """The database file and its write-ahead log, as they sit on disk."""
    usage._reset()  # closing checkpoints the log into the main file
    base = Path(os.environ["SETTINGS_DIR"])
    return b"".join(p.read_bytes() for p in sorted(base.glob("usage.sqlite3*")))


def fill() -> None:
    est = lambda day, cloud, state, services: usage.record(  # noqa: E731
        "estimate", cloud=cloud, state=state, services=services, now=at(day))
    q = lambda day, ip, thread: usage.record("question", ip=ip, thread_id=thread, now=at(day))  # noqa: E731
    q("2026-07-01", "ip-a", "t0")  # outside 90 days
    q("2026-10-21", "ip-f", "t6")  # inside 30 days only
    est("2026-10-21", "aws", "ok", "ec2")
    q("2026-10-22", "ip-e", "t5")  # first day of the 7-day window
    q("2026-10-27", "ip-a", "t1")
    q("2026-10-27", "ip-d", "t4")
    est("2026-10-27", "gcp", "ok", "gce")
    est("2026-10-27", "aws", "error", "ec2")
    q("2026-10-28", "ip-a", "t1")
    q("2026-10-28", "ip-a", "t2")
    q("2026-10-28", "ip-b", "t3")
    usage.record("limited", ip="ip-c", now=at("2026-10-28"))
    est("2026-10-28", "aws", "ok", "ec2,s3")
    est("2026-10-28", "aws", "ok", "ec2")
    est("2026-10-28", "azure", "invalid_arguments", "unknown")
    usage.record("estimate_peer", cloud="gcp", state="ok", services="cloud_vpn", now=at("2026-10-28"))
    usage.record("answer_failed", thread_id="t1", now=at("2026-10-28"))
    usage.record("answer_empty", thread_id="t2", now=at("2026-10-28"))
    usage.record("busy", now=at("2026-10-28"))


def test_summary_matches_hand_counts():
    fill()
    s = usage.summary(7, now=NOW)
    assert (s["days"], s["from"], s["to"]) == (7, "2026-10-22", "2026-10-28")
    # questions: 3 on 10-28, 2 on 10-27, 1 on 10-22. visitors come from question rows only: a, b, d, e.
    # conversations: t1 (twice), t2, t3, t4, t5. estimate_peer is left out of every count.
    assert s["totals"] == {"questions": 6, "limited": 1, "visitors": 4, "limited_visitors": 1, "conversations": 5,
                           "estimates": 5, "estimate_errors": 2, "answer_failures": 2, "busy": 1}
    daily = {d["day"]: (d["questions"], d["visitors"]) for d in s["daily"]}
    assert len(s["daily"]) == 7 and [d["day"] for d in s["daily"]][0] == "2026-10-22"
    assert daily["2026-10-22"] == (1, 1) and daily["2026-10-27"] == (2, 2) and daily["2026-10-28"] == (3, 2)
    assert sum(q for q, _ in daily.values()) == 6 and daily["2026-10-23"] == (0, 0)
    assert s["clouds"] == {"aws": 3, "azure": 1, "gcp": 1}
    assert s["services"] == [{"service": "ec2", "label": "EC2", "count": 3},
                             {"service": "gce", "label": "Compute Engine", "count": 1},
                             {"service": "s3", "label": "S3", "count": 1},
                             {"service": "unknown", "label": "unknown", "count": 1}]
    assert s["all_time"] == {"questions": 8, "visitors": 5, "since": "2026-07-01"}


def test_longer_periods_include_older_days():
    fill()
    s30, s90 = usage.summary(30, now=NOW), usage.summary(90, now=NOW)
    assert (s30["from"], len(s30["daily"])) == ("2026-09-29", 30)
    assert s30["totals"]["questions"] == 7 and s30["totals"]["visitors"] == 5
    assert s30["totals"]["conversations"] == 6 and s30["totals"]["estimates"] == 6
    assert s30["services"][0] == {"service": "ec2", "label": "EC2", "count": 4}
    assert (s90["from"], len(s90["daily"])) == ("2026-07-31", 90)
    assert s90["totals"] == s30["totals"]  # 07-01 is older than 90 days


def test_summary_without_data_has_the_same_shape(tmp_path):
    s = usage.summary(30, now=NOW)
    assert s["all_time"] == {"questions": 0, "visitors": 0, "since": None} and s["services"] == [] and s["clouds"] == {"aws": 0, "azure": 0, "gcp": 0}
    assert len(s["daily"]) == 30 and set(s["totals"].values()) == {0}
    assert not (Path(os.environ["SETTINGS_DIR"]) / "usage.sqlite3").exists()  # reading creates nothing
    with pytest.raises(ValueError):
        usage.summary(5)


def test_kst_day_boundary():
    for hour, minute in ((14, 59), (15, 0)):
        usage.record("question", ip="x", now=datetime(2026, 10, 27, hour, minute, tzinfo=timezone.utc).timestamp())
    assert [r[0] for r in rows("SELECT day FROM events ORDER BY rowid")] == ["2026-10-27", "2026-10-28"]
    daily = {d["day"]: d["questions"] for d in usage.summary(7, now=NOW)["daily"]}
    assert daily["2026-10-27"] == 1 and daily["2026-10-28"] == 1


def test_rows_older_than_400_days_are_pruned():
    usage.record("question", ip="old", now=NOW - 401 * DAY)
    usage.record("question", ip="edge", now=NOW - 399 * DAY)
    assert len(rows()) == 2  # nothing is pruned within the same day
    usage.record("question", ip="new", now=NOW)  # more than a day since the last prune
    assert [r[0] for r in rows("SELECT day FROM events ORDER BY rowid")] == [at_day(NOW - 399 * DAY), at_day(NOW)]


def at_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, usage.KST).strftime("%Y-%m-%d")


def test_first_blocking_record_after_a_restart_prunes():
    usage.record("question", ip="old", now=NOW - 401 * DAY)
    usage._reset()
    usage.record("question", ip="new", now=NOW)
    assert len(rows()) == 1


def test_event_loop_records_never_prune_even_right_after_a_restart():
    usage.record("question", ip="old", now=NOW - 401 * DAY)
    usage._reset()
    usage.record("busy", now=NOW, blocking=False)
    assert len(rows()) == 2


def test_files_are_private_and_hashes_are_stable_hmacs():
    usage.record("question", ip="203.0.113.5", thread_id="thread-1", now=NOW)
    base = Path(os.environ["SETTINGS_DIR"])
    assert stat.S_IMODE(base.stat().st_mode) == 0o700
    for name in ("usage.sqlite3", "usage-salt"):
        assert stat.S_IMODE((base / name).stat().st_mode) == 0o600
    assert len((base / "usage-salt").read_bytes()) == 32
    visitor, thread = rows("SELECT visitor, thread FROM events")[0]
    assert visitor == salted("203.0.113.5") and thread == salted("thread-1") and len(visitor) == 16
    usage._reset()  # a restart keeps the salt, so the same visitor stays the same value
    usage.record("question", ip="203.0.113.5", now=NOW)
    assert rows("SELECT DISTINCT visitor FROM events") == [(visitor,)]
    usage.record("question", ip="", thread_id=None, now=NOW)
    assert rows("SELECT visitor, thread FROM events ORDER BY rowid")[-1] == (None, None)


def test_record_only_stores_whitelisted_values():
    usage.record("estimate", cloud="누구에게 보낼까요", state="내 사양은 이것입니다", services="ec2,나는 문장입니다", now=NOW)
    usage.record("estimate", cloud="aws", state="price_data_unavailable", services=["s3", "ec2", "ec2"], now=NOW)
    usage.record("estimate", state="ok", now=NOW)
    usage.record("not_a_kind", ip="x", now=NOW)
    usage.record("question", ip="x", cloud="aws", state="ok", services="ec2", now=NOW)  # estimate-only fields
    assert rows("SELECT kind, cloud, state, services FROM events ORDER BY rowid") == [
        ("estimate", "other", "other", "ec2,unknown"),
        ("estimate", "aws", "price_data_unavailable", "ec2,s3"),
        ("estimate", None, "ok", "unknown"),
        ("question", None, None, None),
    ]


def test_result_states_cover_every_state_the_engine_returns():
    found = set(re.findall(r'"state":\s*"([a-z_]+)"', Path(engine.__file__).read_text(encoding="utf-8")))
    assert found and found <= engine.RESULT_STATES
    assert {"invalid_arguments", "error"} <= usage.STATES


def test_unwritable_directory_never_raises_and_logs_no_personal_values(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("SETTINGS_DIR", str(blocker / "settings"))  # a path below a regular file
    with caplog.at_level(logging.DEBUG):
        usage.record("question", ip="203.0.113.9", thread_id="secret-thread", now=NOW)
        usage.record("busy")
    assert "203.0.113.9" not in caplog.text and "secret-thread" not in caplog.text
    assert usage.summary(7, now=NOW)["totals"]["questions"] == 0


def test_non_blocking_record_is_skipped_while_the_lock_is_held():
    usage.record("question", ip="a", now=NOW)
    assert usage._lock.acquire(timeout=1)
    try:
        usage.record("question", ip="b", now=NOW, blocking=False)
    finally:
        usage._lock.release()
    assert len(rows()) == 1
    usage.record("question", ip="b", now=NOW, blocking=False)
    assert len(rows()) == 2


# --- admin API -------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(admin.api)
    return TestClient(app)


AUTH = {"x-admin-token": TOKEN}


def test_admin_usage_requires_the_token(client):
    assert client.get("/admin/usage").status_code == 403
    assert client.get("/admin/usage", headers={"x-admin-token": "wrong"}).status_code == 403
    assert client.post("/admin/usage/question", json={"ip": "1.2.3.4"}).status_code == 403


def test_admin_usage_validates_days(client):
    for bad in ("5", "0", "abc", "-7", "365"):
        assert client.get(f"/admin/usage?days={bad}", headers=AUTH).status_code == 422
    assert client.get("/admin/usage?days=7", headers=AUTH).json()["days"] == 7
    assert client.get("/admin/usage", headers=AUTH).json()["days"] == 30


def test_admin_question_post_shows_up_in_the_summary(client):
    body = {"ip": "198.51.100.7", "thread_id": "abc"}
    assert client.post("/admin/usage/question", json=body, headers=AUTH).json() == {"ok": True}
    client.post("/admin/usage/question", json={**body, "thread_id": "def"}, headers=AUTH)
    client.post("/admin/usage/question", json={"ip": "198.51.100.8", "limited": True}, headers=AUTH)
    totals = client.get("/admin/usage?days=7", headers=AUTH).json()["totals"]
    assert (totals["questions"], totals["visitors"], totals["conversations"]) == (2, 1, 2)
    assert (totals["limited"], totals["limited_visitors"]) == (1, 1)
    assert "198.51.100" not in db_bytes().decode("latin-1")


def test_admin_question_post_rejects_oversized_values(client):
    assert client.post("/admin/usage/question", json={"ip": "1" * 65}, headers=AUTH).status_code == 422
    assert client.post("/admin/usage/question", json={"ip": "1", "thread_id": "t" * 201}, headers=AUTH).status_code == 422
    assert client.post("/admin/usage/question", json={}, headers=AUTH).status_code == 422


# --- recording points ------------------------------------------------------------------------

def totals() -> dict:
    return usage.summary(7)["totals"]


def test_guard_records_failures_and_empty_answers_but_tidy_stays_pure():
    class Req:
        state = {"messages": [HumanMessage("x")]}

    async def boom(_):
        raise RuntimeError("both models down")

    def boom_sync(_):
        raise RuntimeError("both models down")

    async def empty(_):
        return ModelResponse(result=[AIMessage(content="")])

    g = guard.ResponseGuard()
    guard.tidy(ModelResponse(result=[AIMessage(content="")]), "음")
    assert not (Path(os.environ["SETTINGS_DIR"]) / "usage.sqlite3").exists()
    assert asyncio.run(g.awrap_model_call(Req(), boom)).result[-1].content == guard.FAILED
    assert asyncio.run(g.awrap_model_call(Req(), empty)).result[-1].content == guard.EMPTY
    assert g.wrap_model_call(Req(), boom_sync).result[-1].content == guard.FAILED
    assert g.wrap_model_call(Req(), lambda r: ModelResponse(result=[AIMessage(content="")])).result[-1].content == guard.EMPTY
    assert g.wrap_model_call(Req(), lambda r: ModelResponse(result=[AIMessage(content="답")])).result[-1].content == "답"
    assert [r[0] for r in rows("SELECT kind FROM events ORDER BY rowid")] == [
        "answer_failed", "answer_empty", "answer_failed", "answer_empty"]
    assert totals()["answer_failures"] == 4


def test_guard_timeout_is_recorded_as_a_failure(monkeypatch):
    class Req:
        state = {"messages": [HumanMessage("x")]}

    async def slow(_):
        await asyncio.sleep(5)

    monkeypatch.setattr(guard, "DEADLINE_S", 0.05)
    asyncio.run(guard.ResponseGuard().awrap_model_call(Req(), slow))
    assert totals()["answer_failures"] == 1


def test_run_limiter_records_busy_when_it_rejects():
    from src.concurrency import RunLimiter

    gate = asyncio.Event()

    async def slow_app(scope, receive, send):
        await gate.wait()

    async def send(msg):
        pass

    async def scenario():
        limiter = RunLimiter(slow_app, max_running=1, max_waiting=1)
        scope = {"type": "http", "method": "POST", "path": "/"}
        running = asyncio.create_task(limiter(scope, None, send))
        waiting = asyncio.create_task(limiter(scope, None, send))
        await asyncio.sleep(0.01)
        await limiter(scope, None, send)
        gate.set()
        await asyncio.gather(running, waiting)

    asyncio.run(scenario())
    assert totals()["busy"] == 1 and totals()["questions"] == 0


class Fake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


def call(args: dict, call_id: str = "call-1") -> dict:
    return {"name": "estimate_cost", "args": args, "id": call_id, "type": "tool_call"}


def graph(tools, *calls):
    model = Fake(responses=[AIMessage(content="", tool_calls=list(calls)), AIMessage(content="끝")])
    return create_agent(model=model, tools=tools, middleware=[UsageMiddleware()])


CFG = {"configurable": {"thread_id": "thread-xyz"}}


def test_graph_run_records_estimates_with_the_thread_hash_and_marks_peer_calls():
    g = graph([estimate_cost],
              call({"cloud": "aws", "items": [{"service": "ec2", "count": 2}, {"service": "s3"}]}),
              call({"cloud": "azure", "items": [{"service": "vpn"}]}, "peer-abc123"),
              call({"cloud": "gcp", "items": [{"service": "cloud sql"}]}, "call-2"))
    asyncio.run(g.ainvoke({"messages": [HumanMessage("aws ec2 2대와 s3")]}, CFG))
    # The calls of one step run concurrently, so the row order is not fixed.
    got = {r[2]: r for r in rows("SELECT kind, thread, cloud, state, services FROM events")}
    h = salted("thread-xyz")
    assert got["aws"] == ("estimate", h, "aws", "ok", "ec2,s3")
    assert got["azure"][:3] == ("estimate_peer", h, "azure")
    assert got["gcp"] == ("estimate", h, "gcp", "ok", "cloudsql")
    s = usage.summary(7)
    assert s["totals"]["estimates"] == 2 and s["clouds"] == {"aws": 1, "azure": 0, "gcp": 1}


def test_sync_graph_run_records_too():
    g = graph([estimate_cost], call({"cloud": "aws", "items": [{"service": "ec2"}]}))
    g.invoke({"messages": [HumanMessage("ec2")]}, CFG)
    assert rows("SELECT kind, thread, cloud, state, services FROM events") == [
        ("estimate", salted("thread-xyz"), "aws", "ok", "ec2")]


def test_run_without_a_thread_leaves_the_hash_empty():
    g = graph([estimate_cost], call({"cloud": "aws", "items": [{"service": "ec2"}]}))
    asyncio.run(g.ainvoke({"messages": [HumanMessage("ec2")]}))
    assert rows("SELECT thread FROM events") == [(None,)]


def test_other_tools_are_not_recorded():
    @tool
    def other(x: str) -> str:
        """other"""
        return x

    g = graph([other], {"name": "other", "args": {"x": "hi"}, "id": "c", "type": "tool_call"})
    asyncio.run(g.ainvoke({"messages": [HumanMessage("hi")]}, CFG))
    assert not (Path(os.environ["SETTINGS_DIR"]) / "usage.sqlite3").exists()


def test_tool_exception_is_recorded_as_error_and_reraised():
    @tool("estimate_cost")
    def broken(cloud: str) -> str:
        """broken"""
        raise RuntimeError("tool exploded")

    g = graph([broken], call({"cloud": "aws"}))
    with pytest.raises(RuntimeError, match="tool exploded"):
        asyncio.run(g.ainvoke({"messages": [HumanMessage("x")]}, CFG))
    assert rows("SELECT kind, thread, cloud, state, services FROM events") == [
        ("estimate", salted("thread-xyz"), "aws", "error", "unknown")]


class Req:
    def __init__(self, args, call_id="c1", name="estimate_cost"):
        self.tool_call = {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def wrap(request, result=None, exc=None):
    async def handler(_):
        if exc:
            raise exc
        return result

    return asyncio.run(UsageMiddleware().awrap_tool_call(request, handler))


def test_sync_wrapper_records_result_and_exception():
    ok = ToolMessage(json.dumps({"state": "ok", "request": {"cloud": "aws", "items": [{"service": "ec2"}]}}),
                     tool_call_id="c1", name="estimate_cost")
    assert UsageMiddleware().wrap_tool_call(Req({"cloud": "aws"}), lambda r: ok) is ok
    with pytest.raises(ValueError):
        UsageMiddleware().wrap_tool_call(Req({"cloud": "gcp", "items": [{"service": "gce"}]}),
                                         lambda r: (_ for _ in ()).throw(ValueError("x")))
    assert rows("SELECT cloud, state, services FROM events ORDER BY rowid") == [
        ("aws", "ok", "ec2"), ("gcp", "error", "gce")]


def test_non_tool_message_results_are_recorded_as_other_and_returned_untouched():
    marker = object()
    assert wrap(Req({"cloud": "aws", "items": [{"service": "ec2"}]}), marker) is marker
    assert rows("SELECT state, cloud, services FROM events") == [("other", "aws", "ec2")]


def test_non_json_result_is_other_and_args_may_carry_items_as_a_string():
    result = ToolMessage("not json", tool_call_id="c1", name="estimate_cost")
    wrap(Req({"cloud": "aws", "items": '[{"service": "ec2"}'}), result)  # lost its closing bracket
    wrap(Req({"cloud": "aws", "items": 12345}), result)
    assert rows("SELECT state, services FROM events ORDER BY rowid") == [("other", "ec2"), ("other", "unknown")]


def test_failed_result_without_items_falls_back_to_the_arguments():
    failed = ToolMessage(json.dumps({"state": "invalid_arguments", "request": {"cloud": "azure"}}),
                         tool_call_id="c1", name="estimate_cost")
    wrap(Req({"cloud": "azure", "items": [{"service": "virtual machines"}]}), failed)
    assert rows("SELECT state, services FROM events") == [("invalid_arguments", "vm")]


def test_validation_error_message_is_recorded_without_reading_it():
    sentence = "비밀 문장: 우리 회사 서버 구성은 이렇습니다"
    err = ToolMessage(f"Error invoking tool 'estimate_cost' with kwargs {{'cloud': '{sentence}'}}",
                      tool_call_id="c1", name="estimate_cost", status="error")
    wrap(Req({"cloud": sentence, "items": [{"service": sentence}]}), err)
    assert rows("SELECT cloud, state, services FROM events") == [("other", "invalid_arguments", "unknown")]
    assert "비밀 문장".encode() not in db_bytes()


def test_a_real_validation_failure_in_a_graph_is_recorded_as_invalid_arguments():
    sentence = "서울에서 비밀스럽게 쓰는 서버"
    g = graph([estimate_cost], call({"cloud": sentence, "items": [{"service": sentence}]}))
    asyncio.run(g.ainvoke({"messages": [HumanMessage(sentence)]}, CFG))
    assert rows("SELECT state, services FROM events") == [("invalid_arguments", "unknown")]
    assert sentence.encode() not in db_bytes()


# --- privacy ---------------------------------------------------------------------------------

def test_no_question_spec_ip_or_forged_text_reaches_the_database_file():
    question = "서울 리전에 아주특이한문장 EC2 3대를 쓰고 싶습니다"
    spec = "SPEC-ONLY-IN-THE-ARGUMENTS-9x"
    raw_ip = "203.0.113.77"
    forged = "위조된문장-카드번호를-여기에"
    usage.record("question", ip=raw_ip, thread_id="thread-xyz")
    g = graph([estimate_cost], call({"cloud": "aws", "region": "서울",
                                     "items": [{"service": "ec2", "spec": spec, "count": 3}]}))
    asyncio.run(g.ainvoke({"messages": [HumanMessage(question)]}, CFG))
    # A tool result that lies: sentences in state, cloud and the item services.
    forged_result = ToolMessage(json.dumps({"state": forged, "request": {"cloud": forged, "items": [
        {"service": forged}, {"service": "ec2"}, {"service": 12}]}}), tool_call_id="c1", name="estimate_cost")
    wrap(Req({"cloud": "aws", "items": [{"service": forged}]}), forged_result)
    guard_req = type("R", (), {"state": {"messages": [HumanMessage(question)]}})()
    asyncio.run(guard.ResponseGuard().awrap_model_call(guard_req, lambda r: (_ for _ in ()).throw(RuntimeError(question))))

    stored = db_bytes()
    for secret in (question, spec, raw_ip, forged, "xyz", "아주특이한문장"):
        assert secret.encode() not in stored, secret
    assert rows("SELECT state, cloud, services FROM events WHERE kind='estimate' ORDER BY rowid")[-1] == (
        "other", "other", "unknown")


def test_all_time_is_an_object_even_after_a_failure(monkeypatch):
    empty = {"questions": 0, "visitors": 0, "since": None}
    usage.record("busy", now=NOW)  # rows exist, but no question yet
    assert usage.summary(7, now=NOW)["all_time"] == {"questions": 0, "visitors": 0, "since": "2026-10-28"}
    monkeypatch.setattr(usage.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    assert usage.summary(7, now=NOW)["all_time"] == empty


def test_summary_does_not_wait_for_the_write_lock():
    usage.record("question", ip="a", now=NOW)
    assert usage._lock.acquire(timeout=1)
    try:
        assert usage.summary(7, now=NOW)["totals"]["questions"] == 1
    finally:
        usage._lock.release()


def test_pruning_stays_off_the_non_blocking_path():
    usage.record("question", ip="old", now=NOW - 401 * DAY)
    usage.record("busy", now=NOW, blocking=False)
    assert len(rows("SELECT * FROM events WHERE kind='question'")) == 1
    usage.record("busy", now=NOW)
    assert rows("SELECT * FROM events WHERE kind='question'") == []


def test_empty_or_short_salt_is_replaced_atomically_and_busy_does_not_need_it(caplog):
    base = Path(os.environ["SETTINGS_DIR"])
    usage.record("busy", now=NOW)
    usage.record("estimate", cloud="aws", state="ok", services="ec2", now=NOW)
    assert not (base / "usage-salt").exists()  # nothing to hash, no salt yet
    (base / "usage-salt").write_bytes(b"")  # e.g. a crash left an empty file
    usage.record("busy", now=NOW)
    usage.record("estimate", cloud="aws", state="ok", services="ec2", now=NOW)
    assert len(rows()) == 4 and (base / "usage-salt").read_bytes() == b""  # still untouched
    with caplog.at_level(logging.WARNING):
        usage.record("question", ip="1.2.3.4", now=NOW)
    salt = (base / "usage-salt").read_bytes()
    assert len(salt) == 32 and stat.S_IMODE((base / "usage-salt").stat().st_mode) == 0o600
    assert rows("SELECT visitor FROM events WHERE kind='question'") == [(salted("1.2.3.4"),)]
    assert "usage salt" in caplog.text and "1.2.3.4" not in caplog.text
    assert [p.name for p in base.iterdir() if p.name.startswith(".usage-salt")] == []  # no temp files left


def test_existing_salt_is_never_overwritten():
    base = Path(os.environ["SETTINGS_DIR"])
    usage.record("question", ip="1.2.3.4", now=NOW)
    salt = (base / "usage-salt").read_bytes()
    usage._reset()
    usage.record("question", ip="5.6.7.8", now=NOW)
    assert (base / "usage-salt").read_bytes() == salt
