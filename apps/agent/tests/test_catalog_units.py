"""Hand-written oracle: catalog unit -> (quantity_scale, hours). Builders never import this."""

import json
from decimal import ROUND_HALF_UP, Decimal

import pytest

from pricing.paths import BUNDLED_DIR

AWS_RULES = {
    ("Hrs", "hour", "count"): (1, 730), ("Hrs", "hour", "endpoint_az"): (1, 730), ("Hours", "hour", "count"): (1, 730),
    ("hours", "hour", "count"): (1, 730), ("hour", "hour", "count"): (1, 730), ("LCU-Hrs", "lcu_hour", "lcu"): (1, 730),
    ("hours", "vcpu_hour", "vcpu"): (1, 730), ("hours", "gb_hour", "memory_gb"): (1, 730),
    ("GB-Mo", "gb_month", "gb"): (1, 1), ("GB", "gb", "gb"): (1, 1), ("GigaBytes", "gb", "gb"): (1, 1),
    ("Requests", "request", "million_requests"): (1_000_000, 1), ("Request", "request", "million_requests"): (1_000_000, 1),
    ("Queries", "query", "million_queries"): (1_000_000, 1), ("IOs", "io", "million_ios"): (1_000_000, 1),
    ("WriteRequestUnits", "request_unit", "million_units"): (1_000_000, 1),
    ("ReadRequestUnits", "request_unit", "million_units"): (1_000_000, 1),
    ("Lambda-GB-Second", "gb_second", "million_gb_seconds"): (1_000_000, 1),
    ("month", "month", "count"): (1, 1), ("HostedZone", "month", "count"): (1, 1),
}
AZURE_RULES = {
    ("1 Hour", "hour", "count"): (1, 730), ("1/Hour", "hour", "count"): (1, 730), ("1/Hour", "capacity_unit_hour", "capacity_unit"): (1, 730),
    ("1 Hour", "vcore_hour", "vcore"): (1, 730), ("1/Hour", "ru_100_hour", "ru_100"): (1, 730),
    ("1 GB/Month", "gb_month", "gb"): (1, 1), ("1 GB", "gb", "gb"): (1, 1), ("1/Month", "month", "count"): (1, 1),
    ("10K", "10k_requests", "million_requests"): (100, 1), ("10", "10_executions", "million_requests"): (100_000, 1),
    ("1 GB Second", "gb_second", "million_gb_seconds"): (1_000_000, 1),
}
GCP_RULES = {
    ("h", "hour", "count"): (1, 730), ("s", "vcpu_hour", "vcpu"): (1, 730), ("GiBy.s", "gb_hour", "memory_gb"): (1, 730),
    ("GiBy.mo", "gb_month", "gb"): (1, 1), ("GiBy", "gb", "gb"): (1, 1), ("TiBy", "gb", "gb"): (1, 1), ("mo", "month", "count"): (1, 1),
    ("count", "request", "million_requests"): (1_000_000, 1), ("count", "query", "million_queries"): (1_000_000, 1),
}
FORBIDDEN = ["Multi-AZ", "UnusedBox", "Windows", "SQL Server", "RHEL", "SUSE", "BYOL", "Dedicated", "Outposts", "Reserved", "Spot"]


def items(cloud):
    return json.loads((BUNDLED_DIR / f"{cloud}.json").read_text())["items"]


@pytest.mark.parametrize("cloud,rules,unit_field", [
    ("aws", AWS_RULES, "aws_unit"), ("azure", AZURE_RULES, "unit"), ("gcp", GCP_RULES, "unit"),
])
def test_units_match_oracle(cloud, rules, unit_field):
    for key, it in items(cloud).items():
        for region, p in it["prices"].items():
            rule = rules.get((p[unit_field], it["per"], it["quantity_unit"]))
            assert rule == (it["quantity_scale"], it["hours"]), f"{key} {region}"


@pytest.mark.parametrize("cloud", ["aws", "azure"])
def test_no_forbidden_products(cloud):
    for key, it in items(cloud).items():
        for p in it["prices"].values():
            text = json.dumps(p)
            assert not [w for w in FORBIDDEN if w in text], key


def test_gcp_unit_conversion():
    # A per-TiB or per-second price becomes per GB or per hour; a VM or DB size is vCPU x n + RAM x m;
    # a fixed-size cache is the per-GB price x its size. Every result is rounded to 10 places.
    specs = json.loads((BUNDLED_DIR.parent / "specs" / "gcp_spec.json").read_text())["specs"]
    for key, it in items("gcp").items():
        for region, p in it["prices"].items():
            if "components" in p:
                exact = sum(Decimal(c["unit_price_usd"]) * Decimal(c["multiplier"]) for c in p["components"])
            else:
                # The factor comes from the API unit, not from the catalog's own convert field.
                factor = {"TiBy": Decimal(1) / 1024, "s": Decimal(3600), "GiBy.s": Decimal(3600)}.get(p["unit"], Decimal(1))
                size = Decimal(str(specs[key]["components"][0].get("multiplier", 1))) if specs[key].get("components") else 1
                exact = Decimal(p["unit_price_usd"]) * factor * size
            assert Decimal(p["usd"]) == exact.quantize(Decimal("1E-10"), ROUND_HALF_UP), f"{key} {region}"


def test_catalogs_complete():
    assert (len(items("aws")), len(items("azure")), len(items("gcp"))) == (46, 31, 39)
    for cloud in ("aws", "azure", "gcp"):
        regions = set(json.loads((BUNDLED_DIR / f"{cloud}.json").read_text())["regions"])
        assert all(set(it["prices"]) == regions for it in items(cloud).values()), cloud
