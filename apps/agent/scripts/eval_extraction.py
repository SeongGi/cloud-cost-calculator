"""Measure how well the local model turns questions into estimate_cost arguments.

A case passes when the engine total from the model's arguments equals the total from the
hand-written arguments. Needs local Ollama. Usage:
  uv run python -m scripts.eval_extraction [--reasoning] [--model NAME] [--repeat N]
"""

import argparse
import json
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage

from pricing.engine import estimate
from src.cost_tool import estimate_cost, parse_items
from src.llm import build_model
from src.prompt import SYSTEM_PROMPT

CASES = [
    ("AWS 서울에 t3.medium 2대, RDS PostgreSQL, S3 500GB, ALB 월 비용 알려줘",
     "aws", [{"service": "ec2", "spec": "t3.medium", "count": 2}, {"service": "rds", "spec": "postgresql"}, {"service": "s3", "gb": 500}, {"service": "alb"}]),
    ("AWS에 EC2, RDS, Lambda 쓰면 한 달에 얼마야?",
     "aws", [{"service": "ec2"}, {"service": "rds"}, {"service": "lambda"}]),
    ("AWS 서울 m7i.large 3대, NAT 게이트웨이 2개에 데이터 500GB, EBS 300GB 비용",
     "aws", [{"service": "ec2", "spec": "m7i.large", "count": 3}, {"service": "nat gateway", "count": 2, "gb": 500}, {"service": "ebs", "gb": 300}]),
    ("Azure 코리아 센트럴에 D2s v5 VM 3대랑 MySQL 월 비용은?",
     "azure", [{"service": "virtual machines", "spec": "D2s v5", "count": 3}, {"service": "mysql"}]),
    ("애저 Blob 스토리지 1TB, 로드밸런서, Functions 비용",
     "azure", [{"service": "blob storage", "gb": 1000}, {"service": "load balancer"}, {"service": "functions"}]),
    ("Azure B2s VM 2대, P10 디스크 2개, PostgreSQL 200GB",
     "azure", [{"service": "vm", "spec": "B2s", "count": 2}, {"service": "managed disks", "spec": "P10", "count": 2}, {"service": "postgresql", "gb": 200}]),
 # Held-out cases written after the prompt was tuned (not used for tuning).
    ("AWS 도쿄에 c7i.large 4대랑 ElastiCache 비용", "aws", [{"service": "ec2", "spec": "c7i.large", "count": 4}, {"service": "elasticache"}], "ap-northeast-1"),
    ("Azure 미국 동부 D4s v5 2대, Redis, Blob 500GB", "azure", [{"service": "vm", "spec": "D4s v5", "count": 2}, {"service": "redis"}, {"service": "blob storage", "gb": 500}], "eastus"),
    ("AWS Fargate랑 DynamoDB, CloudWatch 로그 50GB", "aws", [{"service": "fargate"}, {"service": "dynamodb"}, {"service": "cloudwatch", "gb": 50}]),
    # GCP cases, added with the GCP catalog (prompt unchanged).
    ("GCP 서울에 e2-standard-2 2대, Cloud SQL PostgreSQL, Cloud Storage 500GB 월 비용",
     "gcp", [{"service": "compute engine", "spec": "e2-standard-2", "count": 2}, {"service": "cloud sql", "spec": "postgresql"}, {"service": "cloud storage", "gb": 500}], "asia-northeast3"),
    ("구글 클라우드 Compute Engine이랑 Cloud SQL, Cloud Storage 쓰면 얼마야?",
     "gcp", [{"service": "compute engine"}, {"service": "cloud sql"}, {"service": "cloud storage"}]),
    ("GCP GKE 클러스터, Cloud NAT 데이터 200GB, SSD 영구 디스크 300GB, Memorystore",
     "gcp", [{"service": "gke"}, {"service": "cloud nat", "gb": 200}, {"service": "persistent disk", "spec": "ssd", "gb": 300}, {"service": "memorystore"}]),
]


def total(cloud, items, region=None):
    return estimate(cloud, items, region)["total_cents"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reasoning", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model
    model = build_model()
    model.reasoning = args.reasoning
    bound = model.bind_tools([estimate_cost])
    passed, times = 0, []
    for case in CASES * args.repeat:
        question, cloud, items = case[:3]
        region = case[3] if len(case) > 3 else None
        t0 = time.time()
        msg = bound.invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(question)])
        times.append(time.time() - t0)
        calls = [c for c in msg.tool_calls if c["name"] == "estimate_cost"]
        got = None
        if calls:
            a = calls[0]["args"]
            try:
                got = estimate(a.get("cloud", ""), parse_items(a.get("items", [])), a.get("region"),
                               user_text=question)["total_cents"]
            except Exception as exc:  # report, do not hide
                got = f"error: {exc}"
        ok = got == total(cloud, items, region)
        passed += ok
        print(f"{'OK ' if ok else 'BAD'} {times[-1]:5.1f}s {question[:40]} -> {json.dumps(calls[0]['args'] if calls else msg.content[:80], ensure_ascii=False)}")
    n = len(CASES) * args.repeat
    print(f"model={model.model} reasoning={args.reasoning} pass={passed}/{n} avg={sum(times)/n:.1f}s max={max(times):.1f}s")


if __name__ == "__main__":
    main()
