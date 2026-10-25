"""Hand-calculated cases use the unit prices in the bundled catalogs (AWS 2026-10-22, Azure/GCP 2026-10-23):
AWS ap-northeast-2: ec2.t3.medium 0.052/h, ec2.m7i.large 0.1239/h, rds.postgres.t3.medium 0.112/h,
rds.mysql.t3.medium 0.104/h, rds.storage.gp3 0.131/GB-Mo, s3.storage 0.025/GB-Mo, s3.get 0.00000035/req,
s3.put 0.0000045/req, alb.hours 0.0225/h, alb.lcu 0.008/LCU-h, lambda.requests 0.0000002/req,
lambda.duration 0.0000166667/GB-s, nat_gateway.hours 0.059/h, nat_gateway.bytes 0.059/GB, ebs.gp3 0.0912/GB-Mo.
Azure koreacentral: vm.d2s_v5 0.118/h, vm.b2s 0.052/h, mysql.b1ms 0.026/h, mysql.storage 0.131/GB,
postgresql.b1ms 0.026/h, postgresql.storage 0.131/GB, blob storage 0.02/GB, read 0.004/10K, write 0.05/10K,
lb.standard.rules 0.025/h, functions executions 0.000002/10, GB-s 0.000016, disk.p10_lrs 19.71/month.
GCP asia-northeast3: gce.e2_medium 0.04298286/h, gce.e2_standard_2 0.08596572/h, cloudsql db_custom_1_3840
0.087825/h (MySQL = PostgreSQL), cloudsql_storage.ssd 0.221/GB, gcs.standard.storage 0.023/GB, gcs.class_a 0.000005/op,
gcs.class_b 0.0000004/op, gke.cluster 0.10/h, cloud_nat.gateway 0.0014/h, cloud_nat.data 0.045/GB, pd.ssd 0.221/GB,
memorystore.basic_1gb 0.065/h.
"""

import pytest

from pricing.engine import default_qty, estimate, subtotal_cents


def by_key(result):
    return {l["key"]: l for l in result["lines"]}


# --- hand-calculated cases (cents) ---

def test_aws_case1_web_stack():
    r = estimate("aws", [
        {"service": "ec2", "spec": "t3.medium", "count": 2},
        {"service": "rds", "spec": "postgresql"},
        {"service": "s3", "gb": 500},
        {"service": "alb"},
    ], "ap-northeast-2")
    # 0.052*730*2=75.92, 0.112*730=81.76, 100*0.131=13.10, 500*0.025=12.50, 0.35, 4.50,
    # 0.0225*730=16.425 -> 16.43, 0.008*730=5.84
    assert [l["subtotal_cents"] for l in r["lines"]] == [7592, 8176, 1310, 35, 450, 1250, 1643, 584]
    assert r["total_cents"] == 21040


def test_aws_case2_defaults_pick_cheapest():
    r = estimate("aws", [{"service": "EC2"}, {"service": "RDS"}, {"service": "Lambda"}])
    lines = by_key(r)
    assert set(lines) == {"ec2.t3.medium", "rds.mysql.t3.medium", "rds.storage.gp3", "lambda.duration", "lambda.requests"}
    # 37.96 + 75.92 + 13.10 + 0.20 + 16.6667 -> 16.67
    assert r["total_cents"] == 3796 + 7592 + 1310 + 20 + 1667 == 14385
    assert all(l["assumed"] for l in r["lines"])


def test_aws_case3_quantities():
    r = estimate("aws", [
        {"service": "ec2", "spec": "m7i.large", "count": 3},
        {"service": "nat gateway", "count": 2, "gb": 500},
        {"service": "ebs", "gb": 300},
    ])
    # 0.1239*730*3=271.341 -> 271.34, 0.059*730*2=86.14, 500*0.059=29.50, 300*0.0912=27.36
    assert r["total_cents"] == 27134 + 8614 + 2950 + 2736 == 41434


def test_azure_case1_vm_and_mysql():
    r = estimate("azure", [{"service": "virtual machines", "spec": "D2s v5", "count": 3}, {"service": "mysql"}], "koreacentral")
    # 0.118*730*3=258.42, b1ms 0.026*730=18.98, storage 100*0.131=13.10
    assert r["total_cents"] == 25842 + 1898 + 1310 == 29050


def test_azure_case2_storage_lb_functions():
    r = estimate("azure", [{"service": "blob storage", "gb": 1000}, {"service": "load balancer"}, {"service": "functions"}])
    # 20.00 + 0.40 + 5.00 + 18.25 + 0.20 + 16.00
    assert r["total_cents"] == 2000 + 40 + 500 + 1825 + 20 + 1600 == 5985


def test_azure_case3_disk_spec_and_postgres_storage():
    r = estimate("azure", [
        {"service": "vm", "spec": "B2s", "count": 2},
        {"service": "managed disks", "spec": "P10", "count": 2},
        {"service": "postgresql flexible server", "gb": 200},
    ])
    # 0.052*730*2=75.92, 19.71*2=39.42, 0.026*730=18.98, 200*0.131=26.20
    assert r["total_cents"] == 7592 + 3942 + 1898 + 2620 == 16052


def test_gcp_case1_vm_sql_storage():
    r = estimate("gcp", [
        {"service": "compute engine", "spec": "e2-standard-2", "count": 2},
        {"service": "cloud sql", "spec": "postgresql"},
        {"service": "cloud storage", "gb": 500},
    ], "asia-northeast3")
    # 0.08596572*730*2=125.5099 -> 125.51, 0.087825*730=64.11225 -> 64.11, 100*0.221=22.10,
    # class A 5.00, class B 0.40, 500*0.023=11.50
    assert by_key(r)["cloudsql.postgres.db_custom_1_3840"]["included"]
    assert r["total_cents"] == 12551 + 6411 + 2210 + 500 + 40 + 1150 == 22862


def test_gcp_case2_defaults_follow_reference_rules():
    r = estimate("gcp", [{"service": "Compute Engine"}, {"service": "Cloud SQL"}, {"service": "Cloud Storage"},
                         {"service": "Pub/Sub"}])
    lines = by_key(r)
    # Cheapest VM; MySQL and PostgreSQL cost the same, so the key order decides; SQL storage is its own line.
    assert set(lines) == {"gce.e2_medium", "cloudsql.mysql.db_custom_1_3840", "cloudsql_storage.ssd",
                          "gcs.class_a", "gcs.class_b", "gcs.standard.storage"}
    assert len(lines["gce.e2_medium"]["alternatives"]) == 5
    assert [e["service"] for e in r["excluded"]] == ["Pub/Sub"]
    # 0.04298286*730=31.3775 -> 31.38, 64.11, 22.10, 5.00, 0.40, 100*0.023=2.30
    assert r["total_cents"] == 3138 + 6411 + 2210 + 500 + 40 + 230 == 12529


def test_gcp_case3_gke_nat_disk_cache():
    r = estimate("gcp", [
        {"service": "gke"},
        {"service": "cloud nat", "gb": 200},
        {"service": "persistent disk", "spec": "ssd", "gb": 300},
        {"service": "memorystore"},
    ])
    # 0.10*730=73.00, 200*0.045=9.00, 0.0014*730=1.022 -> 1.02, 300*0.221=66.30, 0.065*730=47.45
    assert r["total_cents"] == 7300 + 900 + 102 + 6630 + 4745 == 19677
    assert r["as_of"] == "2026-10-25" and r["source"] == "Google Cloud Billing Catalog API"


# --- rules ---

def test_alternatives_count_once():
    r = estimate("aws", [{"service": "rds"}])
    family = [l for l in r["lines"] if l["family"]]
    assert len(family) == 1
    assert len(family[0]["alternatives"]) == 6  # mysql/postgres x 3 sizes
    assert family[0]["key"] == "rds.mysql.t3.medium"  # cheapest


def test_same_service_without_spec_merges():
    r = estimate("aws", [{"service": "ec2"}, {"service": "ec2", "count": 4}])
    assert [l["key"] for l in r["lines"]] == ["ec2.t3.medium"]
    assert r["lines"][0]["qty"] == 4  # stated quantity replaces the default


def test_repeated_service_quantities_add_up():
    r = estimate("aws", [{"service": "ebs", "gb": 500}, {"service": "ebs", "gb": 300}, {"service": "ec2", "count": 2}, {"service": "ec2", "count": 3}])
    q = {l["key"]: l["qty"] for l in r["lines"]}
    assert q == {"ebs.gp3": 800, "ec2.t3.medium": 5}


def test_missing_spec_is_excluded_not_invented():
    r = estimate("aws", [{"service": "ec2", "spec": "t3.micro", "count": 2}])
    line = r["lines"][0]
    assert line["included"] is False and line["missing_spec"] == "t3.micro"
    assert r["total_cents"] == 0


def test_unknown_service_and_region():
    r = estimate("aws", [{"service": "cloudfront"}], "eu-west-1")
    assert r["excluded"] == [{"service": "cloudfront", "reason": "가격표에 없는 서비스"}]
    assert r["region"] == "ap-northeast-2" and r["requested_region"] == "eu-west-1"


def test_region_alias():
    assert estimate("aws", [{"service": "ec2"}], "도쿄")["region"] == "ap-northeast-1"
    assert estimate("azure", [{"service": "vm"}], "Korea Central")["region"] == "koreacentral"


def test_deterministic():
    args = ("aws", [{"service": "alb"}, {"service": "rds", "spec": "postgres"}, {"service": "s3"}])
    assert estimate(*args) == estimate(*args)


@pytest.mark.parametrize("unit,expected", [("count", 1), ("lcu", 1), ("gb", 100), ("million_requests", 1), ("vcpu", 1)])
def test_default_qty(unit, expected):
    assert default_qty(unit, {"min": 0, "max": 50000, "step": 1})[0] == expected


def test_default_qty_step_fix():
    assert default_qty("gb", {"min": 0, "max": 1000, "step": 30}) == (100, {"min": 0, "max": 1000, "step": 1})


def test_subtotal_half_up():
    assert subtotal_cents("0.0225000000", 1, 730, 1) == 1643  # 16.425
    assert subtotal_cents("0.0000003500", 1_000_000, 1, 1) == 35
    assert subtotal_cents("1", 1, 1, 0) == 0


def test_spec_from_user_text_when_model_omits_it():
    r = estimate("azure", [{"service": "managed disks", "count": 2}], user_text="Azure B2s VM 2대, P10 디스크 2개")
    assert r["lines"][0]["key"] == "disk.p10_lrs" and r["total_cents"] == 3942


def test_spec_from_user_text_ignores_ambiguous_words():
    r = estimate("aws", [{"service": "ec2"}], user_text="t3.medium 하나와 m7i.large 하나")
    assert r["lines"][0]["key"] == "ec2.t3.medium" and "missing_spec" not in r["lines"][0]


def test_unknown_service_name_resolved_by_spec():
    r = estimate("aws", [{"service": "compute instance", "spec": "c7i.large", "count": 4}])
    assert r["lines"][0]["key"] == "ec2.c7i.large" and r["excluded"] == []
    assert estimate("aws", [{"service": "mystery", "spec": "t3.medium"}])["excluded"]  # ec2 and rds both have t3.medium


def test_refresh_carries_over_keys_missing_from_new_build(tmp_path, monkeypatch):
    import json as _json

    from pricing import refresh

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    current = {"as_of": "2999-01-01", "items": {"vm.b2s": {}, "vm.d2s_v5": {}}}
    (tmp_path / "azure.json").write_text(_json.dumps(current))
    monkeypatch.setattr(refresh.build_azure, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": {"vm.b2s": {}}})))
    assert "1 carried over" in refresh.refresh_one("azure")
    kept = _json.loads((tmp_path / "azure.json").read_text())
    assert set(kept["items"]) == {"vm.b2s", "vm.d2s_v5"} and kept["carried_over"] == ["vm.d2s_v5"]
    monkeypatch.setattr(refresh.build_azure, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": {"vm.b2s": {}, "vm.d2s_v5": {}, "x": {}}})))
    assert "updated" in refresh.refresh_one("azure")
    assert len(_json.loads((tmp_path / "azure.json").read_text())["items"]) == 3


def test_bundled_catalog_wins_when_newer(tmp_path, monkeypatch):
    import json as _json

    from pricing.paths import BUNDLED_DIR, readable_catalog_path

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    (tmp_path / "aws.json").write_text(_json.dumps({"as_of": "2000-01-01", "items": {}}))
    assert readable_catalog_path("aws") == BUNDLED_DIR / "aws.json"
    (tmp_path / "aws.json").write_text(_json.dumps({"as_of": "2999-01-01", "items": {}}))
    assert readable_catalog_path("aws") == tmp_path / "aws.json"


def test_refresh_off_uses_bundled_catalog(tmp_path, monkeypatch):
    import json as _json

    from pricing.paths import BUNDLED_DIR, readable_catalog_path

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.delenv("PRICE_REFRESH", raising=False)
    (tmp_path / "aws.json").write_text(_json.dumps({"as_of": "2999-01-01", "items": {}}))
    assert readable_catalog_path("aws") == BUNDLED_DIR / "aws.json"


def test_refresh_schedule():
    from datetime import datetime

    from pricing.refresh import KST, seconds_until

    assert seconds_until(4, datetime(2026, 10, 24, 3, 0, tzinfo=KST)) == 3600
    assert seconds_until(4, datetime(2026, 10, 24, 5, 0, tzinfo=KST)) == 23 * 3600


# --- review findings (each is a concrete wrong-total scenario) ---

def test_million_goes_to_requests_only():
    r = estimate("aws", [{"service": "lambda", "million": 10}])
    q = {l["key"]: l["qty"] for l in r["lines"]}
    assert q == {"lambda.requests": 10, "lambda.duration": 1}
    assert r["total_cents"] == 200 + 1667  # 10 x $0.20 + 1M GB-s default


def test_api_gateway_variants_count_once():
    r = estimate("aws", [{"service": "api gateway", "million": 1}])
    assert len(r["lines"]) == 1 and len(r["lines"][0]["alternatives"]) == 2


def test_unknown_spec_in_plain_service_is_not_priced_as_another_product():
    for cloud, service, spec in [("aws", "ebs", "gp2"), ("aws", "s3", "glacier"), ("azure", "blob storage", "cool")]:
        r = estimate(cloud, [{"service": service, "spec": spec}])
        assert r["total_cents"] == 0 and all(l.get("missing_spec") == spec for l in r["lines"]), service


def test_azure_redis_unknown_tier_flagged():
    r = estimate("azure", [{"service": "redis", "spec": "C2"}])
    assert r["lines"][0]["included"] is False


def test_engine_and_version_words_are_not_specs():
    r = estimate("aws", [{"service": "elasticache", "spec": "redis"}, {"service": "rds", "spec": "MySQL 8.0"}])
    assert all(l["included"] for l in r["lines"])
    assert by_key(r).keys() >= {"elasticache.t3.medium", "rds.mysql.t3.medium"}


def test_partial_spec_match_is_explained():
    r = estimate("aws", [{"service": "ec2", "spec": "large"}])
    assert "가장 저렴" in r["lines"][0]["note"]


def test_count_goes_to_one_line():
    r = estimate("aws", [{"service": "waf", "count": 3}, {"service": "alb", "count": 2}])
    q = {l["key"]: l["qty"] for l in r["lines"]}
    assert q["waf.web_acl"] == 3 and q["waf.rule"] == 1
    assert q["alb.hours"] == 2 and q["alb.lcu"] == 1


def test_gb_goes_to_storage_line():
    r = estimate("aws", [{"service": "s3", "gb": 700, "million": 5}])
    q = {l["key"]: l["qty"] for l in r["lines"]}
    assert q == {"s3.storage": 700, "s3.get": 5, "s3.put": 1}


def test_large_quantities_exact():
    assert subtotal_cents("0.0000166667", 1_000_000, 1, 999_999_999) == 1666669998333  # 999,999,999 x $16.6667 = $16,666,699,983.33


def test_gcp_engine_versions_and_engine_in_service_name():
    # Review: GCP kept "16" as a spec and dropped the instance; "Cloud SQL for PostgreSQL" showed MySQL.
    for item in ({"service": "cloud sql", "spec": "PostgreSQL 16"}, {"service": "Cloud SQL for PostgreSQL"}):
        lines = by_key(estimate("gcp", [item]))
        assert lines["cloudsql.postgres.db_custom_1_3840"]["included"], item
    assert by_key(estimate("gcp", [{"service": "cloud sql", "spec": "db-custom-2-7680"}]))["cloudsql.mysql.db_custom_2_7680"]["included"]
    r = estimate("gcp", [{"service": "persistent disk", "spec": "SSD persistent disk", "gb": 300}], "us")
    assert r["region"] == "us-central1" and r["total_cents"] == 300 * 17 == 5100


def test_bare_engine_versions_are_not_specs():
    r = estimate("aws", [{"service": "rds", "spec": "PostgreSQL 16"}, {"service": "rds", "spec": "MySQL 8"}])
    assert all(l["included"] for l in r["lines"])
