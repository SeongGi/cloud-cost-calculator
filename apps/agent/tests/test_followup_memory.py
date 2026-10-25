"""Follow-up questions build on the previous estimate; every model call ends with an answer;
conversations survive a restart."""

import asyncio
import json

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import guard
from src.cost_tool import estimate_cost
from src.persistent_saver import PersistentMemorySaver


def run_tool(state, **args):
    return json.loads(estimate_cost.func(state=state, **args))


def first_turn():
    r = run_tool({"messages": [HumanMessage("Azure 코리아 센트럴에 D2s v5 VM 3대랑 MySQL")]}, cloud="azure", region="koreacentral",
                 items=[{"service": "virtual machines", "spec": "D2s v5", "count": 3}, {"service": "database for mysql"}])
    assert r["total_cents"] == 29050
    return [HumanMessage("Azure 코리아 센트럴에 D2s v5 VM 3대랑 MySQL"),
            ToolMessage(content=json.dumps(r, ensure_ascii=False), name="estimate_cost", tool_call_id="t1")]


def test_add_keeps_previous_items_even_when_model_sends_only_the_change():
    history = first_turn()
    # The model forgot follow_up and even picked another cloud's name for the VPN.
    r = run_tool({"messages": history + [HumanMessage("여기에 vpn 추가하면 얼마야?")]}, cloud="gcp", items=[{"service": "cloud vpn"}])
    keys = {line["key"] for line in r["lines"]}
    assert r["cloud"] == "azure" and {"vm.d2s_v5", "mysql.b1ms", "vpn.vpngw1"} <= keys
    assert r["total_cents"] > 29050 and "앞에서 계산한 구성" in r["notes"][0]


def test_modify_remove_and_recalculate_all():
    history = first_turn()
    r = run_tool({"messages": history + [HumanMessage("VM을 5대로 바꾸고 MySQL은 빼줘")]}, cloud="azure",
                 follow_up=True, items=[{"service": "virtual machines", "count": 5}], remove=["mysql"])
    assert {l["key"]: l["qty"] for l in r["lines"]} == {"vm.d2s_v5": 5}
    r = run_tool({"messages": history + [HumanMessage("전체 구성으로 다시 계산해줘")]}, cloud="azure", items=[])
    assert r["total_cents"] == 29050


def test_new_question_or_other_cloud_starts_over():
    history = first_turn()
    r = run_tool({"messages": history + [HumanMessage("AWS EC2 비용은?")]}, cloud="aws", items=[{"service": "ec2"}])
    assert r["cloud"] == "aws" and [l["key"] for l in r["lines"]] == ["ec2.t3.medium"]
    r = run_tool({"messages": history + [HumanMessage("Blob 1TB 비용")]}, cloud="azure", follow_up=False,
                 items=[{"service": "blob storage", "gb": 1000}])
    assert "vm.d2s_v5" not in {l["key"] for l in r["lines"]}


def call(name, cloud):
    return {"name": name, "args": {"cloud": cloud, "items": []}, "id": cloud, "type": "tool_call"}


def test_guard_keeps_one_estimate_per_named_cloud_and_never_returns_nothing():
    two = ModelResponse(result=[AIMessage(content="", tool_calls=[call("estimate_cost", "azure"), call("estimate_cost", "gcp")])])
    kept = guard.tidy(two, "여기에 vpn 추가").result[-1].tool_calls
    assert [c["args"]["cloud"] for c in kept] == ["azure"]
    both = guard.tidy(two, "AWS랑 GCP 비교").result[-1].tool_calls
    assert len(both) == 2
    empty = guard.tidy(ModelResponse(result=[AIMessage(content="")]), "음").result[-1]
    assert empty.content == guard.EMPTY


def test_guard_answers_on_failure_and_timeout(monkeypatch):
    class Req:
        state = {"messages": [HumanMessage("x")]}

    async def boom(_):
        raise RuntimeError("both models down")

    async def slow(_):
        await asyncio.sleep(5)

    g = guard.ResponseGuard()
    assert asyncio.run(g.awrap_model_call(Req(), boom)).result[-1].content == guard.FAILED
    monkeypatch.setattr(guard, "DEADLINE_S", 0.05)
    assert asyncio.run(g.awrap_model_call(Req(), slow)).result[-1].content == guard.FAILED


class Fake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


def test_conversation_survives_restart(tmp_path):
    path = tmp_path / "threads.pkl"
    cfg = {"configurable": {"thread_id": "t-1"}}
    saver = PersistentMemorySaver(path, flush_every_s=60)
    g = create_agent(model=Fake(responses=[AIMessage(content="안녕하세요")]), tools=[estimate_cost], checkpointer=saver)
    asyncio.run(g.ainvoke({"messages": [HumanMessage("첫 질문")]}, cfg))
    saver.close()
    restored = PersistentMemorySaver(path, flush_every_s=60)
    g2 = create_agent(model=Fake(responses=[AIMessage(content="이어서")]), tools=[estimate_cost], checkpointer=restored)
    out = asyncio.run(g2.ainvoke({"messages": [HumanMessage("두 번째")]}, cfg))
    assert [m.content for m in out["messages"]] == ["첫 질문", "안녕하세요", "두 번째", "이어서"]
    restored.close()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_old_checkpoints_are_pruned(tmp_path):
    saver = PersistentMemorySaver(tmp_path / "t.pkl", flush_every_s=60)
    g = create_agent(model=Fake(responses=[AIMessage(content=str(i)) for i in range(10)]), tools=[estimate_cost], checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t"}}
    for i in range(5):
        asyncio.run(g.ainvoke({"messages": [HumanMessage(f"q{i}")]}, cfg))
    assert all(len(cps) <= 6 for cps in saver.storage["t"].values())
    assert len(asyncio.run(g.aget_state(cfg)).values["messages"]) == 10
    saver.close()


def test_other_cloud_services_are_not_merged_into_the_previous_one():
    history = first_turn()
    # No cloud named, but the model chose AWS and the services do not exist on Azure.
    r = run_tool({"messages": history + [HumanMessage("EC2 2대랑 S3 같이 쓰면?")]}, cloud="aws",
                 items=[{"service": "ec2", "count": 2}, {"service": "s3"}])
    assert r["cloud"] == "aws" and "vm.d2s_v5" not in {l["key"] for l in r["lines"]}


def test_change_goes_to_the_line_with_the_same_spec_and_unmatched_removal_is_reported():
    history = [HumanMessage("Azure VM D2s v5 3대, D4s v5 2대"),
               ToolMessage(content=json.dumps(run_tool({"messages": []}, cloud="azure", items=[
                   {"service": "virtual machines", "spec": "D2s v5", "count": 3},
                   {"service": "virtual machines", "spec": "D4s v5", "count": 2}])), name="estimate_cost", tool_call_id="t")]
    r = run_tool({"messages": history + [HumanMessage("D4s v5를 4대로 바꿔줘")]}, cloud="azure", follow_up=True,
                 items=[{"service": "virtual machines", "spec": "D4s v5", "count": 4}], remove=["cosmos db"])
    req = {i["spec"]: i["count"] for i in r["request"]["items"]}
    assert req == {"D2s v5": 3, "D4s v5": 4}
    assert any("빼지 못한 서비스: cosmos db" in n for n in r["notes"])


def test_follow_up_finds_the_last_good_estimate_even_after_a_failed_one():
    history = first_turn() + [HumanMessage("음"), ToolMessage(content=json.dumps({"state": "invalid_arguments", "request": {"cloud": "azure"}}),
                                                              name="estimate_cost", tool_call_id="t2")]
    r = run_tool({"messages": history + [HumanMessage("여기에 vpn 추가")]}, cloud="azure", items=[{"service": "vpn gateway"}])
    assert {"vm.d2s_v5", "vpn.vpngw1"} <= {l["key"] for l in r["lines"]}


def test_restored_tool_messages_without_a_name_still_count():
    history = first_turn()
    history[-1] = ToolMessage(content=history[-1].content, tool_call_id="t1")  # as rebuilt from the browser
    r = run_tool({"messages": history + [HumanMessage("여기에 vpn 추가")]}, cloud="azure", items=[{"service": "vpn"}])
    assert "vm.d2s_v5" in {l["key"] for l in r["lines"]}


def test_guard_prefers_the_named_cloud_over_call_order():
    two = ModelResponse(result=[AIMessage(content="", tool_calls=[call("estimate_cost", "gcp"), call("estimate_cost", "aws")])])
    assert [c["args"]["cloud"] for c in guard.tidy(two, "AWS EC2 비용").result[-1].tool_calls] == ["aws"]


def test_storage_stays_linear_in_conversation_length(tmp_path):
    saver = PersistentMemorySaver(tmp_path / "t.pkl", flush_every_s=60)
    g = create_agent(model=Fake(responses=[AIMessage(content=str(i)) for i in range(40)]), tools=[estimate_cost], checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t"}}
    counts = []
    for i in range(20):
        asyncio.run(g.ainvoke({"messages": [HumanMessage(f"q{i}")]}, cfg))
        counts.append(sum(1 for k in saver.blobs if k[0] == "t"))
    assert counts[-1] == counts[5]  # bounded by the kept checkpoints, not by the number of turns
    assert len(asyncio.run(g.aget_state(cfg)).values["messages"]) == 40
    saver.close()
    import pickle
    path = tmp_path / "evil.pkl"
    path.write_bytes(pickle.dumps({"format": 1, "storage": {}, "writes": {}, "blobs": {}, "order": [], "x": PersistentMemorySaver}))
    assert len(PersistentMemorySaver(path, flush_every_s=60).storage) == 0  # refused, starts empty


def aws_turn():
    r = run_tool({"messages": []}, cloud="aws", items=[{"service": "ec2", "spec": "t3.medium", "count": 2}, {"service": "rds"}])
    return [HumanMessage("AWS EC2 t3.medium 2대 + RDS"), ToolMessage(content=json.dumps(r), name="estimate_cost", tool_call_id="a")]


def test_changing_the_spec_replaces_the_line_instead_of_adding_one():
    r = run_tool({"messages": aws_turn() + [HumanMessage("EC2를 m7i.large로 바꾸면?")]}, cloud="aws", follow_up=True,
                 items=[{"service": "ec2", "spec": "m7i.large"}])
    ec2 = [i for i in r["request"]["items"] if i["service"] == "ec2"]
    assert ec2 == [{"service": "ec2", "spec": "m7i.large", "count": 2, "gb": None, "million": None}]
    # "추가" with another spec keeps both.
    r = run_tool({"messages": aws_turn() + [HumanMessage("m7i.large 1대 추가")]}, cloud="aws", follow_up=True,
                 items=[{"service": "ec2", "spec": "m7i.large", "count": 1}])
    assert len([i for i in r["request"]["items"] if i["service"] == "ec2"]) == 2


def test_ambiguous_change_on_several_lines_is_reported_not_guessed():
    two = run_tool({"messages": []}, cloud="aws", items=[{"service": "ec2", "spec": "t3.medium", "count": 2},
                                                          {"service": "ec2", "spec": "m7i.large", "count": 1}])
    history = [HumanMessage("x"), ToolMessage(content=json.dumps(two), name="estimate_cost", tool_call_id="b")]
    r = run_tool({"messages": history + [HumanMessage("EC2를 5대로 늘려")]}, cloud="aws", follow_up=True, items=[{"service": "ec2", "count": 5}])
    assert r["total_cents"] == two["total_cents"] and any("여러 줄" in n for n in r["notes"])


def test_lookups_of_unknown_threads_do_not_evict_real_conversations(tmp_path):
    saver = PersistentMemorySaver(tmp_path / "t.pkl", max_threads=3, flush_every_s=60)
    g = create_agent(model=Fake(responses=[AIMessage(content=str(i)) for i in range(10)]), tools=[estimate_cost], checkpointer=saver)
    for t in ("A", "B"):
        asyncio.run(g.ainvoke({"messages": [HumanMessage(t)]}, {"configurable": {"thread_id": t}}))
    for i in range(5):
        asyncio.run(g.aget_state({"configurable": {"thread_id": f"empty-{i}"}}))
    asyncio.run(g.ainvoke({"messages": [HumanMessage("C")]}, {"configurable": {"thread_id": "C"}}))
    assert {"A", "B", "C"} <= set(saver.storage)
    saver.close()


def test_user_scenario_gcp_then_vpn_to_aws_then_cloud_sql():
    """GCP config → "aws와 vpn으로 연결 추가하면?" → "Cloud SQL도 추가해줘" (reported 2026-10-25)."""
    q1 = "GCP 서울에 e2-standard-2 VM 하나와 Cloud Storage 1000GB"
    g1 = run_tool({"messages": [HumanMessage(q1)]}, cloud="gcp", region="asia-northeast3",
                  items=[{"service": "compute engine", "spec": "e2-standard-2", "count": 1}, {"service": "cloud storage", "gb": 1000}])
    h = [HumanMessage(q1), ToolMessage(content=json.dumps(g1), name="estimate_cost", tool_call_id="g1")]
    q2 = "aws와 vpn으로 연결 추가하면 얼마야?"
    # Two named clouds: one call per cloud. GCP keeps its config and gains a VPN tunnel; AWS gets its VPN.
    g2 = run_tool({"messages": h + [HumanMessage(q2)]}, cloud="gcp", follow_up=True, items=[{"service": "cloud vpn"}])
    a2 = run_tool({"messages": h + [HumanMessage(q2)]}, cloud="aws", follow_up=True, items=[{"service": "site-to-site vpn"}])
    assert {"gce.e2_standard_2", "gcs.standard.storage", "cloud_vpn.tunnel"} <= {l["key"] for l in g2["lines"]}
    assert g2["total_cents"] == g1["total_cents"] + 5475  # 0.075 × 730 = 54.75
    assert a2["cloud"] == "aws" and "vpn.hours" in {l["key"] for l in a2["lines"]}
    h += [HumanMessage(q2), ToolMessage(content=json.dumps(g2), name="estimate_cost", tool_call_id="g2"),
          ToolMessage(content=json.dumps(a2), name="estimate_cost", tool_call_id="a2")]
    # The last estimate is AWS, but Cloud SQL belongs to the GCP configuration.
    g3 = run_tool({"messages": h + [HumanMessage("Cloud SQL도 추가해줘")]}, cloud="gcp", items=[{"service": "cloud sql"}])
    keys = {l["key"] for l in g3["lines"]}
    assert {"gce.e2_standard_2", "cloud_vpn.tunnel", "cloudsql_storage.ssd"} <= keys
    assert g3["total_cents"] == g2["total_cents"] + 6411 + 2210


def test_link_to_another_cloud_adds_vpn_on_both_ends():
    g1 = run_tool({"messages": []}, cloud="gcp", items=[{"service": "compute engine", "spec": "e2-standard-2"}])
    state = {"messages": [HumanMessage("GCP e2-standard-2"), ToolMessage(content=json.dumps(g1), name="estimate_cost", tool_call_id="g")]}
    only_aws = ModelResponse(result=[AIMessage(content="", tool_calls=[
        {"name": "estimate_cost", "args": {"cloud": "aws", "items": [{"service": "vpn"}]}, "id": "a", "type": "tool_call"}])])
    calls = guard.tidy(only_aws, "aws와 vpn으로 연결 추가하면 얼마야?", state).result[-1].tool_calls
    assert [c["args"]["cloud"] for c in calls] == ["aws", "gcp"]
    r = run_tool({"messages": state["messages"] + [HumanMessage("aws와 vpn으로 연결 추가하면 얼마야?")]}, **calls[1]["args"])
    assert {"gce.e2_standard_2", "cloud_vpn.tunnel"} <= {l["key"] for l in r["lines"]}
    # Not a link question: nothing is added.
    assert len(guard.tidy(only_aws, "AWS VPN 요금은?", state).result[-1].tool_calls) == 1
