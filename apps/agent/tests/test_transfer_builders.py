"""Builder behaviour for items with their own "regions" list and Azure "by_region" meters.
No network: the fetch functions are replaced."""

import json
from pathlib import Path

from pricing import build_aws, build_azure, build_gcp

REGIONS = ["r-a", "r-b", "r-c"]


def test_aws_render_fills_region_and_prefix():
    out = build_aws.render({"fromRegionCode": "{region}", "usagetype": "{prefix}X", "toRegionCode": "r-b"}, "APN2-", "r-a")
    assert out == {"fromRegionCode": "r-a", "usagetype": "APN2-X", "toRegionCode": "r-b"}
    assert build_aws.render({"usagetype": "{prefix}X"}, "P-") == {"usagetype": "P-X"}


def test_aws_item_regions_limit_lookups_and_completeness(tmp_path, monkeypatch):
    spec = {"service": "data_transfer", "label": {"ko": "x"}, "quantity_unit": "gb", "per": "gb", "quantity_scale": 1,
            "hours": 1, "slider": {"min": 0, "max": 10, "step": 1}, "service_code": "AWSDataTransfer",
            "filters": {"fromRegionCode": "{region}", "toRegionCode": "r-c"}, "expect": {"product_family": "Data Transfer", "aws_unit": "GB"}}
    (tmp_path / "spec.json").write_text(json.dumps({
        "regions": REGIONS, "region_prefixes": {}, "aliases": {},
        "specs": {"t.to_c": {**spec, "regions": ["r-a", "r-b"]}, "t.all": {**spec, "filters": {"fromRegionCode": "{region}"}}}}))
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(build_aws, "fetch_json", lambda url: {"regions": {r: {"currentVersionUrl": f"/{r}"} for r in REGIONS}})
    monkeypatch.setattr(build_aws, "download", lambda url, cache: Path(url))

    def scan(path, targets):
        region = path.name
        seen.extend((region, t["filters"]) for t in targets.values())
        dims = {"d": {"beginRange": "0", "unit": "GB", "pricePerUnit": {"USD": "0.08"}, "rateCode": "rc"}}
        matches = {k: [{"sku": f"{k}{region}", "attributes": {}, "family": "Data Transfer"}] for k in targets}
        return "2026-01-01", matches, {f"{k}{region}": {"t": {"priceDimensions": dims}} for k in targets}

    monkeypatch.setattr(build_aws, "scan_offer", scan)
    result = build_aws.build(tmp_path / "spec.json", tmp_path / "out.json", tmp_path)
    assert result["warnings"] == [] and result["items"] == 2
    assert ("r-c", {"fromRegionCode": "r-c", "toRegionCode": "r-c"}) not in seen  # not looked up in r-c
    assert ("r-a", {"fromRegionCode": "r-a", "toRegionCode": "r-c"}) in seen
    prices = json.loads((tmp_path / "out.json").read_text())["items"]
    assert set(prices["t.to_c"]["prices"]) == {"r-a", "r-b"} and set(prices["t.all"]["prices"]) == set(REGIONS)


def row(tier, price, meter):
    return {"type": "Consumption", "isPrimaryMeterRegion": True, "productName": "P", "skuName": "S", "meterName": meter,
            "tierMinimumUnits": tier, "retailPrice": price, "unitOfMeasure": "1 GB", "meterId": "m", "skuId": "s",
            "effectiveStartDate": "2026-01-01T00:00:00Z"}


def test_azure_by_region_overrides_filter_and_tier_and_regions_limit(tmp_path, monkeypatch):
    spec = {"service": "data_transfer", "label": {"ko": "x"}, "quantity_unit": "gb", "per": "gb", "quantity_scale": 1,
            "hours": 1, "slider": {"min": 0, "max": 10, "step": 1}, "filter": "BASE", "tier_min": 0,
            "expect": {"unitOfMeasure": "1 GB"}, "regions": ["r-a", "r-c"],
            "by_region": {"r-c": {"filter": "OTHER", "tier_min": 5, "allow_non_primary": True}}}
    (tmp_path / "spec.json").write_text(json.dumps({"regions": REGIONS, "aliases": {}, "specs": {"t.x": spec}}))
    filters: list[str] = []

    def fetch(flt):
        filters.append(flt)
        return [row(0, 0.0, "m"), row(5, 0.05 if "OTHER" in flt else 0.08, "m")]

    monkeypatch.setattr(build_azure, "fetch", fetch)
    result = build_azure.build(tmp_path / "spec.json", tmp_path / "out.json")
    assert result["warnings"] == [] and result["items"] == 1
    assert len(filters) == 2 and filters[0].startswith("BASE") and filters[1].startswith("OTHER")  # r-b is never fetched
    # r-a keeps the base filter and tier 0 (a free row, so the price is 0 and the row is the tier-0 one).
    prices = json.loads((tmp_path / "out.json").read_text())["items"]["t.x"]["prices"]
    assert set(prices) == {"r-a", "r-c"} and prices["r-c"]["usd"] == "0.0500000000" and prices["r-a"]["usd"] == "0.0000000000"


def test_gcp_item_regions_limit_lookups(tmp_path, monkeypatch):
    def sku(sid, desc, region):
        return {"skuId": sid, "description": desc, "category": {"usageType": "OnDemand"}, "serviceRegions": [region],
                "pricingInfo": [{"effectiveTime": "t", "pricingExpression": {"usageUnit": "GiBy", "tieredRates": [
                    {"startUsageAmount": 0, "unitPrice": {"units": "0", "nanos": 0}},
                    {"startUsageAmount": 1, "unitPrice": {"units": "0", "nanos": 80000000}}]}}]}

    skus = [sku("1", "Net from A to C", "r-a"), sku("2", "Net from B to C", "r-b")]
    comp = {"match": "^Net from (A|B) to C$", "unit": "GiBy"}
    spec = {"service": "data_transfer", "label": {"ko": "x"}, "quantity_unit": "gb", "per": "gb", "quantity_scale": 1,
            "hours": 1, "slider": {"min": 0, "max": 10, "step": 1}, "catalog_service": "Compute Engine", "scope": "regional",
            "expect_unit": "GiBy", "convert": 1, "components": [comp], "regions": ["r-a", "r-b"]}
    (tmp_path / "spec.json").write_text(json.dumps({"regions": REGIONS, "aliases": {}, "specs": {"t.to_c": spec}}))
    monkeypatch.setattr(build_gcp, "list_all", lambda path, field, params:
                        [{"displayName": "Compute Engine", "serviceId": "S"}] if field == "services" else skus)
    result = build_gcp.build(tmp_path / "spec.json", tmp_path / "out.json")
    assert result["warnings"] == [] and result["items"] == 1
    assert set(json.loads((tmp_path / "out.json").read_text())["items"]["t.to_c"]["prices"]) == {"r-a", "r-b"}
