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


def test_common_previous_generation_size_is_priced():
    # m5.large was missing once, so "EC2 m5.large 2대" dropped out of the total and the chart.
    r = estimate("aws", [{"service": "ec2", "spec": "m5.large", "count": 2}], "서울")
    line = r["lines"][0]
    assert line["key"] == "ec2.m5.large" and line["included"] and "missing_spec" not in line
    assert r["total_cents"] == 17228  # 0.118 × 730 × 2


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
    from pricing.paths import BUNDLED_DIR

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    bundled = _json.loads((BUNDLED_DIR / "azure.json").read_text())["items"]
    spec_keys = set(_json.loads((BUNDLED_DIR.parent / "specs" / "azure_spec.json").read_text())["specs"])
    current = {"as_of": "2999-01-01", "items": {k: {} for k in bundled}}
    (tmp_path / "azure.json").write_text(_json.dumps(current))
    fresh = {k: {} for k in bundled if k != "vm.d2s_v5"}
    monkeypatch.setattr(refresh.build_azure, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": fresh})))
    assert "1 carried over" in refresh.refresh_one("azure")
    kept = _json.loads((tmp_path / "azure.json").read_text())
    assert set(kept["items"]) == set(bundled) and kept["carried_over"] == ["vm.d2s_v5"]
    monkeypatch.setattr(refresh.build_azure, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": {**{k: {} for k in bundled}, "x": {}}})))
    assert "updated" in refresh.refresh_one("azure")
    assert len(_json.loads((tmp_path / "azure.json").read_text())["items"]) == len(bundled) + 1
    assert spec_keys <= set(bundled)


def test_bundled_catalog_wins_when_newer(tmp_path, monkeypatch):
    import json as _json

    from pricing.paths import BUNDLED_DIR, readable_catalog_path

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    keys = {k: {} for k in _json.loads((BUNDLED_DIR / "aws.json").read_text())["items"]}
    (tmp_path / "aws.json").write_text(_json.dumps({"as_of": "2000-01-01", "items": keys}))
    assert readable_catalog_path("aws") == BUNDLED_DIR / "aws.json"
    (tmp_path / "aws.json").write_text(_json.dumps({"as_of": "2999-01-01", "items": keys}))
    assert readable_catalog_path("aws") == tmp_path / "aws.json"


def test_refreshed_catalog_missing_a_bundled_key_is_outdated_and_carried_over_by_refresh(tmp_path, monkeypatch):
    """A code update adds a key to the bundled catalog: a newer refreshed file without it is not used, and the
    next refresh keeps the bundled value for the key when its build fails, then the refreshed file is used."""
    import json as _json

    from pricing import refresh
    from pricing.paths import BUNDLED_DIR, readable_catalog_path

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    bundled = _json.loads((BUNDLED_DIR / "gcp.json").read_text())["items"]
    key = "data_transfer.internet"
    old = {k: v for k, v in bundled.items() if k != key}
    (tmp_path / "gcp.json").write_text(_json.dumps({"as_of": "2999-01-01", "items": old}))
    assert readable_catalog_path("gcp") == BUNDLED_DIR / "gcp.json"
    monkeypatch.setattr(refresh.build_gcp, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": old})))
    assert "1 carried over" in refresh.refresh_one("gcp")
    kept = _json.loads((tmp_path / "gcp.json").read_text())
    assert kept["items"][key] == bundled[key] and kept["carried_over"] == [key]
    assert readable_catalog_path("gcp") == tmp_path / "gcp.json"


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


def test_another_clouds_size_is_priced_as_this_clouds_cheapest():
    # "같은 구성을 Azure로 하면?" once sent AWS's t3.medium to Azure and the VMs dropped out.
    r = estimate("azure", [{"service": "virtual machines", "spec": "t3.medium", "count": 2}])
    vm = r["lines"][0]
    assert vm["key"] == "vm.b2s" and vm["included"] and vm["qty"] == 2 and "AWS 사양" in vm["note"]
    assert r["total_cents"] == 7592
    # A made-up size is still reported, not guessed.
    assert not estimate("azure", [{"service": "virtual machines", "spec": "Z99 v9"}])["lines"][0]["included"]


# --- data transfer ---

def transfer(cloud, spec="", gb=1000, region=None, text=""):
    return estimate(cloud, [{"service": "data transfer", "spec": spec, "gb": gb}], region, user_text=text)


def transfer_lines(r):
    return {l["key"]: l for l in r["lines"] if l["service"] == "data_transfer"}


def test_transfer_hand_calculated():
    # AWS seoul -> tokyo 0.08, Azure koreacentral internet 0.12, Azure eastus -> koreacentral 0.05, GCP seoul -> americas 0.08
    r = transfer("aws", "도쿄", region="ap-northeast-2")
    assert list(transfer_lines(r)) == ["data_transfer.to_ap_northeast_1"] and r["total_cents"] == 8000
    line = transfer_lines(r)["data_transfer.to_ap_northeast_1"]
    assert (line["group"], line["service_label"]) == ("네트워크", "데이터 전송")
    r = transfer("azure", "인터넷", region="koreacentral")
    assert list(transfer_lines(r)) == ["data_transfer.internet"] and r["total_cents"] == 12000
    r = transfer("azure", "한국 중부", region="eastus")
    assert list(transfer_lines(r)) == ["data_transfer.to_koreacentral"] and r["total_cents"] == 5000
    r = transfer("gcp", "미국", region="asia-northeast3")
    assert list(transfer_lines(r)) == ["data_transfer.to_us_central1"] and r["total_cents"] == 8000
    assert transfer("aws", "인터넷", region="ap-northeast-2")["total_cents"] == 12600
    assert transfer("gcp", "인터넷")["total_cents"] == 19000


@pytest.mark.parametrize("spec", ["도쿄", "tokyo", "ap-northeast-1", "일본", "Tokyo"])
def test_transfer_spec_variants_pick_the_same_key(spec):
    r = transfer("aws", spec, region="ap-northeast-2")
    assert list(transfer_lines(r)) == ["data_transfer.to_ap_northeast_1"] and "note" not in r["lines"][0]


def test_transfer_destinations_per_cloud():
    assert list(transfer_lines(transfer("azure", "일본", region="koreacentral"))) == ["data_transfer.to_japaneast"]
    assert list(transfer_lines(transfer("azure", "virginia", region="koreacentral"))) == ["data_transfer.to_eastus"]
    assert list(transfer_lines(transfer("gcp", "japan"))) == ["data_transfer.to_asia_northeast1"]
    assert transfer("azure", "일본", region="koreacentral")["total_cents"] == 8000
    assert transfer("azure", "미국", region="koreacentral")["total_cents"] == 8000


def test_transfer_destination_from_the_sentence_when_spec_is_empty():
    assert list(transfer_lines(transfer("aws", text="서울에서 도쿄로 매달 1TB 복제"))) == ["data_transfer.to_ap_northeast_1"]
    r = transfer("aws", text="서울에서 도쿄로 매달 1TB 복제")
    assert "note" not in r["lines"][0]
    # two destinations are not guessed: the default (with its note) applies
    r = transfer("aws", text="도쿄와 미국")
    assert "도착지를 알 수 없어" in r["lines"][0]["note"]


def test_transfer_destination_rules():
    from pricing.engine import load_catalog, transfer_destination
    cat = load_catalog("aws")
    seoul, tokyo = "ap-northeast-2", "ap-northeast-1"
    d = lambda text, reg=seoul, **kw: transfer_destination("aws", text, reg, cat, **kw)  # noqa: E731
    assert d("한국 사용자에게 인터넷으로", tokyo) == ("internet", "")
    assert d("도쿄 리전으로 아웃바운드") == (tokyo, "")
    assert d("business 한국어 data") == ("", "")
    assert d("서울에서 도쿄로") == (tokyo, seoul)
    assert d("도쿄 리전에서 서울로") == ("", tokyo)
    assert d("from tokyo to us") == ("us-east-1", tokyo)
    assert d("egress outbound 외부") == ("", "")  # direction words are not "internet"


def test_transfer_scope_check_is_for_the_models_spec_only():
    from pricing.engine import load_catalog, transfer_destination
    cat = load_catalog("aws")
    scoped = lambda text, reg="ap-northeast-2": transfer_destination("aws", text, reg, cat, check_scope=True)[0]  # noqa: E731
    assert scoped("싱가포르") == "?" and scoped("europe") == "?" and scoped("us-west-2") == "?" and scoped("미국 서부") == "?"
    assert scoped("us west") == "?"
    # region codes of the catalog are not out of scope; "west" needs latin word boundaries; codes without hyphens are unknown
    assert scoped("ap-northeast-1") == "ap-northeast-1" and scoped("us-east-1") == "us-east-1"
    assert scoped("westus2") == "" and scoped("northwest") == ""
    assert scoped("다른 리전") == "" and scoped("DR 리전") == "" and scoped("CDN") == ""
    gcp = load_catalog("gcp")
    assert transfer_destination("gcp", "asia-northeast3", "us-central1", gcp, check_scope=True)[0] == "asia-northeast3"
    # a user's sentence is never judged out of scope
    assert transfer_destination("aws", "싱가포르로 보내", "ap-northeast-2", cat)[0] == ""


@pytest.mark.parametrize("spec", ["싱가포르", "europe"])
def test_transfer_out_of_scope_destination_is_missing(spec):
    r = transfer("aws", spec)
    line = r["lines"][0]
    assert line["included"] is False and line["missing_spec"] == spec and r["total_cents"] == 0
    assert "선택 상자" in line["note"]


def test_transfer_unknown_destination_defaults_with_a_note():
    r = transfer("aws", gb=2000, text="egress 2TB")
    assert list(transfer_lines(r)) == ["data_transfer.internet"] and r["total_cents"] == 25200
    assert "도착지를 알 수 없어" in r["lines"][0]["note"]
    r = transfer("aws", text="리전 간 복제 1TB")
    assert list(transfer_lines(r)) == ["data_transfer.to_ap_northeast_1"] and r["total_cents"] == 8000
    assert "도착지를 알 수 없어" in r["lines"][0]["note"]
    # no cue at all: the cheapest alternative (the fixed project rule)
    assert list(transfer_lines(transfer("aws", text="전송")))[0] == "data_transfer.to_ap_northeast_1"


def test_transfer_both_cues_choose_the_cheapest():
    r = transfer("aws", text="서울 리전 사용자 트래픽")
    assert list(transfer_lines(r)) == ["data_transfer.to_ap_northeast_1"]
    assert "도착지를 알 수 없어" in r["lines"][0]["note"]


def test_transfer_source_mismatch_note():
    r = transfer("aws", text="도쿄에서 서울로", region="ap-northeast-2")
    assert "말씀하신 출발지(도쿄)가 아니라 견적 리전 서울 기준" in r["lines"][0]["note"]
    assert "출발지" not in (transfer("aws", "도쿄", text="서울에서 도쿄로").get("lines")[0].get("note") or "")


def test_transfer_internet_and_region_lines_sum_and_notes():
    r = estimate("aws", [{"service": "data transfer", "spec": "인터넷", "gb": 500}, {"service": "data transfer", "spec": "도쿄", "gb": 1000}],
                 "ap-northeast-2")
    assert sorted(l["subtotal_cents"] for l in r["lines"]) == [6300, 8000] and r["total_cents"] == 14300
    assert sum("첫 유료 구간 단가" in n for n in r["notes"]) == 1
    assert not any("첫 유료 구간 단가" in n for n in estimate("aws", [{"service": "ec2"}])["notes"])
    assert any("한국 사용자 대상" in n for n in transfer("gcp", "인터넷")["notes"])
    assert not any("한국 사용자 대상" in n for n in transfer("gcp", "도쿄")["notes"])
    # a transfer left out of the total does not add the note
    assert not any("첫 유료 구간 단가" in n for n in transfer("aws", "싱가포르")["notes"])


def test_service_from_spec_never_returns_data_transfer():
    from pricing.engine import load_catalog, service_from_spec
    assert estimate("aws", [{"service": "internet gateway", "spec": "internet"}])["excluded"]
    assert service_from_spec("aws", "internet", load_catalog("aws")) is None


def test_transfer_alias_names():
    for name in ("data transfer", "데이터 전송", "전송", "리전 간 전송", "inter region", "inter-region data transfer", "egress", "인터넷 전송"):
        assert estimate("gcp", [{"service": name, "spec": "인터넷"}])["lines"][0]["service"] == "data_transfer", name
    assert estimate("azure", [{"service": "bandwidth", "spec": "인터넷"}])["lines"][0]["service"] == "data_transfer"


def test_normalize_transfer_items():
    from pricing.engine import normalize_transfer_items
    items = [{"service": "data transfer", "spec": "도쿄", "gb": 1}, {"service": "ec2", "spec": "t3.medium"},
             {"service": "data transfer", "spec": "싱가포르"}, {"service": "data transfer", "spec": "", "gb": 5}]
    out = normalize_transfer_items("aws", items, "ap-northeast-2", "리전 간 복제")
    assert out[0] == {"service": "data transfer", "spec": "ap-northeast-1", "gb": 1}
    assert out[1] == items[1] and out[2] == items[2]
    assert out[3] == {"service": "data transfer", "spec": "ap-northeast-1", "gb": 5, "transfer_default": True}
    assert normalize_transfer_items("aws", out, "ap-northeast-2", "다른 문장") == out  # applying twice changes nothing
    assert items[0]["spec"] == "도쿄"  # inputs are copied
    # without defaults an unknown destination stays empty; a stated one is still filled in
    assert normalize_transfer_items("aws", [items[3]], "ap-northeast-2", "x", fill_default=False) == [items[3]]
    assert normalize_transfer_items("aws", [{**items[3], "spec": "인터넷으로"}], None, "x", fill_default=False)[0]["spec"] == "internet"
    # the sentence is read only after merging, and only for an empty spec
    assert normalize_transfer_items("aws", [items[3]], None, "인터넷으로", fill_default=False, read_text=False) == [items[3]]
    assert normalize_transfer_items("aws", [items[3]], None, "인터넷으로", fill_default=False)[0]["spec"] == "internet"
    assert normalize_transfer_items("aws", [items[3]], None, "인터넷으로")[0]["spec"] == "internet"
    assert normalize_transfer_items("aws", [{**items[3], "spec": "다른 리전"}], None, "인터넷으로")[0]["spec"] != "internet"
    # estimate adds the notice to a stored default
    r = estimate("aws", [out[3]], "ap-northeast-2")
    assert "도착지를 알 수 없어" in r["lines"][0]["note"]


def test_transfer_user_location_is_not_a_destination():
    from pricing.engine import load_catalog, transfer_destination
    cat = load_catalog("aws")
    d = lambda text: transfer_destination("aws", text, "ap-northeast-2", cat)  # noqa: E731
    assert d("도쿄 사용자에게 1TB") == ("", "") and d("일본 고객용") == ("", "") and d("tokyo users") == ("", "")
    assert d("도쿄에서 접속하는 사용자") == ("", "ap-northeast-1")
    r = transfer("aws", text="도쿄 사용자에게 1TB")
    assert list(transfer_lines(r)) == ["data_transfer.internet"]  # user cue alone suggests the internet, not a copy to tokyo


def test_transfer_scope_is_a_list_not_leftover_words():
    for spec in ("오레곤", "중국", "인도", "캘리포니아", "oregon", "china", "싱가포르"):
        line = transfer("aws", spec)["lines"][0]
        assert line["included"] is False and line["missing_spec"] == spec, spec
    for spec in ("다른 리전", "DR 리전", "CDN", "egress", "재해 복구 리전", "인터넷으로", "도쿄로", "westus2", "잘 모르겠음"):
        assert transfer("aws", spec)["lines"][0]["included"] is True, spec
    assert transfer("azure", "한국 중부", region="eastus")["lines"][0]["key"] == "data_transfer.to_koreacentral"


def test_transfer_internet_cues_and_virginia_aliases():
    for spec in ("온프레미스", "on-prem", "IDC", "사무실", "end users", "public", "CloudFront", "해외"):
        r = transfer("aws", spec)
        assert list(transfer_lines(r)) == ["data_transfer.internet"] and r["lines"][0]["included"], spec
    for spec in ("us-east", "N. Virginia", "n virginia"):
        assert list(transfer_lines(transfer("aws", spec, region="ap-northeast-2"))) == ["data_transfer.to_us_east_1"], spec
        assert list(transfer_lines(transfer("azure", spec, region="koreacentral"))) == ["data_transfer.to_eastus"], spec


def test_transfer_source_note_only_for_sentences_that_were_read():
    # With a spec the sentence is not read, so its "tokyo에서" is not taken as a source.
    r = estimate("aws", [{"service": "data transfer", "spec": "인터넷", "gb": 1000}], "ap-northeast-2",
                 user_text="서울 리전 EC2 1대, 도쿄에서 접속하는 사용자에게 인터넷으로 1TB")
    assert "note" not in r["lines"][0]
    assert "출발지" in transfer("aws", text="도쿄에서 서울로")["lines"][0]["note"]


def test_transfer_source_phrases_and_korean_false_positives():
    from pricing.engine import load_catalog, transfer_destination
    cat = load_catalog("aws")
    d = lambda text: transfer_destination("aws", text, "ap-northeast-2", cat)  # noqa: E731
    assert d("tokyo to seoul") == ("", "ap-northeast-1") and d("도쿄로부터 서울로") == ("", "ap-northeast-1")
    for text in ("미국식 아키텍처", "일본어 서비스", "한국인 대상", "US 달러로 egress"):
        assert d(text) == ("", ""), text


def test_transfer_stored_destination_equal_to_the_new_region_is_explained():
    r = estimate("aws", [{"service": "data transfer", "spec": "ap-northeast-1", "gb": 1000}], "ap-northeast-1")
    note = r["lines"][0]["note"]
    assert "앞에서 정한 도착지(도쿄)가 이번 견적 리전과 같아" in note and "선택 상자로 변경" in note
    from pricing.engine import normalize_transfer_items
    stored = normalize_transfer_items("aws", [{"service": "data transfer", "spec": "도쿄", "gb": 1000}], "ap-northeast-1", "")[0]
    assert stored["transfer_default"] is True and stored["transfer_dropped"] == "ap-northeast-1"
    assert "앞에서 정한 도착지(도쿄)" in estimate("aws", [stored], "ap-northeast-1")["lines"][0]["note"]


def test_paths_cache_follows_size_and_inode_not_only_mtime(tmp_path):
    import os

    from pricing.paths import item_keys

    f = tmp_path / "c.json"
    f.write_text('{"items": {"a": {}}}')
    st = f.stat()
    assert item_keys(f) == {"a"}
    f.write_text('{"items": {"b": {}, "c": {}}}')  # different size, same mtime
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert item_keys(f) == {"b", "c"}


def test_refresh_carry_over_prefers_the_refreshed_file_over_the_bundle(tmp_path, monkeypatch):
    import json as _json

    from pricing import refresh
    from pricing.paths import BUNDLED_DIR

    monkeypatch.setenv("CATALOG_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_REFRESH", "true")
    bundled = _json.loads((BUNDLED_DIR / "azure.json").read_text())["items"]
    mine = {k: {"marker": "refreshed"} if k == "vm.d2s_v5" else v for k, v in bundled.items()}
    (tmp_path / "azure.json").write_text(_json.dumps({"as_of": "2999-01-01", "items": mine}))
    fresh = {k: v for k, v in bundled.items() if k != "vm.d2s_v5"}
    monkeypatch.setattr(refresh.build_azure, "build",
                        lambda spec, out: out.write_text(_json.dumps({"as_of": "2999-01-02", "items": fresh})))
    refresh.refresh_one("azure")
    assert _json.loads((tmp_path / "azure.json").read_text())["items"]["vm.d2s_v5"] == {"marker": "refreshed"}
