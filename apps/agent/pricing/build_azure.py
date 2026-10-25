"""Build the Azure on-demand catalog from the public Retail Prices API (no credentials).

Each spec item must leave exactly one Consumption meter per region (Windows, Spot and
Low Priority excluded). Spec flags: allow_non_primary, global_price, tier_min.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pricing.paths import SPEC_DIR, catalog_path

log = logging.getLogger(__name__)
API = "https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter="
EXCLUDE = ("Windows", "Spot", "Low Priority")


def fetch(flt: str) -> list[dict]:
    items: list[dict] = []
    url: str | None = API + urllib.parse.quote(flt)
    while url:
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = json.load(r)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        items += data.get("Items", [])
        url = data.get("NextPageLink")
    return items


def select(spec: dict, region: str) -> tuple[dict | None, str]:
    flt = spec["filter"].replace("{region}", region) + " and priceType eq 'Consumption'"
    tier_min = float(spec.get("tier_min", 0))
    rows = [
        it for it in fetch(flt)
        if it.get("type") == "Consumption"
        and (spec.get("allow_non_primary") or it.get("isPrimaryMeterRegion", False))
        and not any(w in it["productName"] + it["skuName"] for w in EXCLUDE)
        and float(it["tierMinimumUnits"]) == tier_min
    ]
    if len(rows) != 1:
        return None, f"{len(rows)} meters matched (need exactly 1)"
    it = rows[0]
    if it["unitOfMeasure"] != spec["expect"]["unitOfMeasure"]:
        return None, f"unit {it['unitOfMeasure']!r} != {spec['expect']['unitOfMeasure']!r}"
    return it, ""


def entry(it: dict) -> dict:
    return {
        "usd": f"{Decimal(str(it['retailPrice'])):.10f}",
        "unit": it["unitOfMeasure"],
        "meter_id": it["meterId"],
        "sku_id": it["skuId"],
        "product_name": it["productName"],
        "sku_name": it["skuName"],
        "meter_name": it["meterName"],
        "effective_start": it["effectiveStartDate"],
    }


def build(spec_path: Path, out_path: Path) -> dict:
    cfg = json.loads(spec_path.read_text(encoding="utf-8"))
    items: dict[str, dict] = {}
    warnings: list[str] = []
    for key, spec in cfg["specs"].items():
        prices: dict[str, dict] = {}
        if spec.get("global_price"):
            it, why = select(spec, "Global")
            if it is None:
                warnings.append(f"{key} Global: {why}")
            else:
                prices = {r: entry(it) for r in cfg["regions"]}
        else:
            for region in cfg["regions"]:
                it, why = select(spec, region)
                if it is None:
                    warnings.append(f"{key} {region}: {why}")
                    continue
                prices[region] = entry(it)
        if len(prices) != len(cfg["regions"]):
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
        log.info("ok %s: %s", key, ", ".join(f"{r}={p['usd']}" for r, p in prices.items()))
    catalog = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "aliases": cfg["aliases"],
        "currency": "USD",
        "price_type": "on_demand_retail",
        "source": "azure_retail_prices_api",
        "source_url": "https://prices.azure.com/api/retail/prices",
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
    ap.add_argument("--out", type=Path, default=catalog_path("azure"))
    build(SPEC_DIR / "azure_spec.json", ap.parse_args().out)


if __name__ == "__main__":
    main()
