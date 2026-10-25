"""Build the GCP on-demand catalog from the Cloud Billing Catalog API.

Needs GCP_ACCESS_TOKEN (`gcloud auth print-access-token`). Each spec component must match exactly one OnDemand
SKU in the region; the price is the first paid tier. Multi-component items (Compute Engine
vCPU + RAM) store sum(unit price x multiplier) as the hourly price and keep the components.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pricing.paths import SPEC_DIR, catalog_path

log = logging.getLogger(__name__)
API = "https://cloudbilling.googleapis.com/v1"


class MissingCredentials(Exception):
    pass


def auth() -> tuple[dict, dict]:
    if token := os.environ.get("GCP_ACCESS_TOKEN", "").strip():
        return {}, {"Authorization": f"Bearer {token}"}
    raise MissingCredentials("GCP_ACCESS_TOKEN is required (gcloud auth print-access-token)")


def get(path: str, params: dict) -> dict:
    query, headers = auth()
    url = f"{API}/{path}?" + urllib.parse.urlencode({**params, **query})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403) or attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def list_all(path: str, field: str, params: dict) -> list[dict]:
    out: list[dict] = []
    token = ""
    while True:
        data = get(path, {**params, **({"pageToken": token} if token else {})})
        out += data.get(field, [])
        token = data.get("nextPageToken", "")
        if not token:
            return out


def first_paid_price(sku: dict) -> Decimal:
    rates = sku["pricingInfo"][0]["pricingExpression"]["tieredRates"]
    for t in sorted(rates, key=lambda t: float(t.get("startUsageAmount", 0))):
        p = t["unitPrice"]
        v = Decimal(p.get("units") or "0") + Decimal(p.get("nanos", 0)) / Decimal(10**9)
        if v > 0:
            return v
    return Decimal(0)


def to_decimal(v) -> Decimal:
    s = str(v)
    if "/" in s:
        a, b = s.split("/")
        return Decimal(a) / Decimal(b)
    return Decimal(s)


def pick(skus: list[dict], comp: dict, region: str) -> tuple[dict | None, str]:
    pattern = re.compile(comp["match"])
    rows = [
        s for s in skus
        if s.get("category", {}).get("usageType") == "OnDemand"
        and region in s.get("serviceRegions", [])
        and s.get("pricingInfo")
        and pattern.search(s["description"])
    ]
    if len(rows) != 1:
        return None, f"{comp['match']!r}: {len(rows)} SKUs matched (need exactly 1)"
    unit = rows[0]["pricingInfo"][0]["pricingExpression"]["usageUnit"]
    if unit != comp["unit"]:
        return None, f"{rows[0]['skuId']} unit {unit!r} != {comp['unit']!r}"
    return rows[0], ""


def price_region(spec: dict, skus: list[dict], region: str) -> tuple[dict | None, str]:
    lookup = "global" if spec["scope"] == "global" else region
    total = Decimal(0)
    parts = []
    for comp in spec["components"]:
        sku, why = pick(skus, comp, lookup)
        if sku is None:
            return None, why
        unit_price = first_paid_price(sku)
        total += unit_price * to_decimal(comp.get("multiplier", 1))
        parts.append({
            "role": comp.get("role", "price"),
            "sku_id": sku["skuId"],
            "description": sku["description"],
            "unit": sku["pricingInfo"][0]["pricingExpression"]["usageUnit"],
            "unit_price_usd": f"{unit_price:.10f}",
            "multiplier": str(comp.get("multiplier", 1)),
            "effective_time": sku["pricingInfo"][0].get("effectiveTime", ""),
        })
    if total <= 0:
        return None, "price is 0 (free tier only)"
    convert = to_decimal(spec.get("convert", 1))
    entry = {
        "usd": f"{total * convert:.10f}",
        "sku_id": "+".join(p["sku_id"] for p in parts),
        "description": " + ".join((f"{p['multiplier']} x " if len(parts) > 1 else "") + p["description"] for p in parts),
        "unit": spec["expect_unit"],
        "price_scope": spec["scope"],
    }
    if len(parts) > 1:
        entry["components"] = parts
    else:
        entry["unit_price_usd"] = parts[0]["unit_price_usd"]
        entry["effective_time"] = parts[0]["effective_time"]
    return entry, ""


def spec_regions(spec: dict, regions: list[str]) -> list[str]:
    """Regions an item is priced in: its own "regions" list, else every catalog region."""
    return spec.get("regions", regions)


def build(spec_path: Path, out_path: Path) -> dict:
    cfg = json.loads(spec_path.read_text(encoding="utf-8"))
    services = {s["displayName"]: s["serviceId"] for s in list_all("services", "services", {"pageSize": 5000})}
    sku_cache: dict[str, list[dict]] = {}
    items: dict[str, dict] = {}
    warnings: list[str] = []
    for key, spec in cfg["specs"].items():
        name = spec["catalog_service"]
        if name not in services:
            warnings.append(f"{key}: service {name!r} not found")
            continue
        sid = services[name]
        if sid not in sku_cache:
            sku_cache[sid] = list_all(f"services/{sid}/skus", "skus", {"currencyCode": "USD", "pageSize": 5000})
            log.info("fetched %s: %d skus", name, len(sku_cache[sid]))
        prices: dict[str, dict] = {}
        for region in spec_regions(spec, cfg["regions"]):
            entry, why = price_region(spec, sku_cache[sid], region)
            if entry is None:
                warnings.append(f"{key} {region}: {why}")
                continue
            prices[region] = entry
        if len(prices) != len(spec_regions(spec, cfg["regions"])):
            warnings.append(f"{key}: skipped (priced in {sorted(prices)})")
            continue
        items[key] = {
            "service": spec["service"],
            "label": spec["label"],
            "hours": spec["hours"],
            "per": spec["per"],
            "quantity_unit": spec["quantity_unit"],
            "quantity_scale": spec["quantity_scale"],
            "slider": spec["slider"],
            "prices": prices,
        }
    catalog = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "aliases": cfg["aliases"],
        "currency": "USD",
        "price_type": "on_demand_list",
        "source": "gcp_cloud_billing_catalog_api",
        "source_url": "https://cloud.google.com/billing/docs/reference/rest",
        "regions": cfg["regions"],
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for w in warnings:
        log.warning(w)
    log.info("wrote %s: %d/%d items, %d warnings", out_path, len(items), len(cfg["specs"]), len(warnings))
    return {"items": len(items), "specs": len(cfg["specs"]), "warnings": warnings}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=catalog_path("gcp"))
    build(SPEC_DIR / "gcp_spec.json", ap.parse_args().out)


if __name__ == "__main__":
    main()
