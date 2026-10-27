import { describe, expect, it } from "vitest";
import { followUps, latestResult } from "../follow-ups";

const result = (cloud: string, region: string, lines: { key: string; qty: number; quantity_unit?: string }[]) => ({
  role: "tool",
  content: JSON.stringify({ state: "ok", cloud, region, lines, total_cents: 1 }),
});

describe("follow-ups", () => {
  it("suggests what the latest configuration lacks or could change", () => {
    const msgs = [result("azure", "koreacentral", [{ key: "vm.d2s_v5", qty: 3 }, { key: "mysql.b1ms", qty: 1 }])];
    expect(followUps(latestResult(msgs))).toEqual([
      "여기에 VPN 연결 추가하면?",
      "VM을 6대로 늘리면?",
      "도쿄 리전이면 얼마야?",
      "앞에 Application Gateway 붙이면?",
    ]);
  });

  it("uses the latest result and knows what is already there", () => {
    const msgs = [
      result("azure", "koreacentral", [{ key: "vm.b2s", qty: 1 }]),
      { role: "user", content: "x" },
      result("aws", "us-east-1", [{ key: "ec2.t3.medium", qty: 1 }, { key: "vpn.hours", qty: 1 }, { key: "s3.storage", qty: 500, quantity_unit: "gb" }]),
    ];
    expect(followUps(latestResult(msgs), 6)).toEqual([
      "앞에 ALB 붙이면?",
      "EC2를 2대로 늘리면?",
      "서울 리전이면 얼마야?",
      "여기에 RDS PostgreSQL 추가하면?",
      "스토리지를 1,000GB로 늘리면?",
      "같은 구성을 Azure로 하면 얼마야?",
    ]);
  });

  it("offers cache and WAF once a database and a load balancer are there", () => {
    const msgs = [result("aws", "ap-northeast-2", [
      { key: "ec2.t3.medium", qty: 2 }, { key: "vpn.hours", qty: 1 }, { key: "alb.hours", qty: 1 }, { key: "rds.mysql.t3.medium", qty: 1 },
    ])];
    expect(followUps(latestResult(msgs), 20)).toEqual([
      "여기에 ElastiCache 캐시 추가하면?",
      "EC2를 4대로 늘리면?",
      "도쿄 리전이면 얼마야?",
      "AWS WAF로 보안 정책 추가하면?",
      "RDS를 PostgreSQL로 바꾸면?",
      "같은 구성을 Azure로 하면 얼마야?",
      "NAT Gateway 추가하면?",
      "S3 500GB 추가하면?",
      "CloudWatch로 로그 수집하면?",
    ]);
  });

  it("never suggests NAT on Azure", () => {
    const msgs = [result("azure", "koreacentral", [{ key: "vm.b2s", qty: 1 }])];
    expect(followUps(latestResult(msgs), 20).some((q) => q.includes("NAT"))).toBe(false);
  });

  it("suggests storage when the configuration has none", () => {
    const msgs = [result("gcp", "asia-northeast3", [{ key: "gce.e2_medium", qty: 1 }])];
    expect(followUps(latestResult(msgs), 20)).toContain("Cloud Storage 500GB 추가하면?");
  });

  it("offers nothing without a successful result", () => {
    expect(followUps(latestResult([{ role: "tool", content: '{"state":"invalid_arguments","total_cents":0}' }]))).toEqual([]);
  });
});
