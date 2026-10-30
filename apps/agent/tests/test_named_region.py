"""The sentence's own region and "Cloud Run" win over what the local model copies from examples."""

import json

import pytest
from langchain_core.messages import HumanMessage

from pricing.engine import region_in_text
from src.cost_tool import estimate_cost


def run_tool(text, **args):
    return json.loads(estimate_cost.func(state={"messages": [HumanMessage(text)]}, **args))


@pytest.mark.parametrize("cloud,text,want", [
    ("gcp", "GCP 도쿄에 Cloud Run과 Cloud SQL PostgreSQL 월 비용은?", "asia-northeast1"),
    ("gcp", "GCP 도쿄 리전에 e2-standard-4 VM 2대", "asia-northeast1"),
    ("azure", "Azure 일본 동부(도쿄)에 AKS", "japaneast"),
    ("aws", "AWS 미국 동부에 c6i.large 4대", "us-east-1"),
    ("aws", "AWS 서울에서 도쿄로 1TB 복제", None),
    ("gcp", "GCP 서울에 VM 2대, 도쿄 사용자 대상", "asia-northeast3"),
    ("aws", "EC2 2대 비용", None),
])
def test_region_in_text(cloud, text, want):
    assert region_in_text(cloud, text) == want


def test_named_region_overrides_the_models_copy():
    r = run_tool("GCP 도쿄에 Cloud SQL PostgreSQL 월 비용은?", cloud="gcp", region="asia-northeast3",
                 items=[{"service": "cloud sql", "spec": "postgresql"}])
    assert r["region"] == "asia-northeast1"


def test_transfer_keeps_the_models_region():
    r = run_tool("AWS 도쿄로 1TB 복제", cloud="aws", region="ap-northeast-2",
                 items=[{"service": "data transfer", "spec": "도쿄", "gb": 1000}])
    assert r["region"] == "ap-northeast-2"


def test_cloud_run_is_not_cloud_run_functions():
    r = run_tool("GCP 도쿄에 Cloud Run과 Cloud SQL", cloud="gcp", items=[{"service": "cloud run functions"}])
    assert {l["service"] for l in r["lines"]} == {"cloud_run"}


def test_cloud_run_functions_said_by_the_user_stays():
    r = run_tool("GCP Cloud Run functions 비용", cloud="gcp", items=[{"service": "cloud run functions"}])
    assert {l["service"] for l in r["lines"]} == {"cloud_functions"}
