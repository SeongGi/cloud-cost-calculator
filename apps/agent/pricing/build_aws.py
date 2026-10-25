"""Build the AWS on-demand catalog from the public Price List bulk files (no credentials).

Every spec item must match exactly one product (attribute filters + product family +
region) with exactly one OnDemand term; the price is the dimension starting at 0.
Large offer files (EC2 is ~300 MB per region) are streamed with ijson.
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import ijson

from pricing.paths import SPEC_DIR, catalog_path

log = logging.getLogger(__name__)
BASE = "https://pricing.us-east-1.amazonaws.com"


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def download(url: str, cache_dir: Path) -> Path:
    """Download an offer file once per published version (the URL contains the version)."""
    target = cache_dir / url.strip("/").replace("/", "_")
    if target.exists():
        return target
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=600) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(target)
    return target


def render(filters: dict[str, Any], prefix: str) -> dict[str, str]:
    return {k: str(v).replace("{prefix}", prefix) for k, v in filters.items()}


def wanted_families(expect: dict) -> list[str] | None:
    fam = expect.get("product_family")
    if fam is None:
        return None
    return fam if isinstance(fam, list) else [fam]


def scan_offer(path: Path, targets: dict[str, dict]) -> tuple[str, dict[str, list[dict]], dict[str, dict]]:
    """Return (publicationDate, item_key -> matching products, sku -> OnDemand terms)."""
    matches: dict[str, list[dict]] = {k: [] for k in targets}
    with open(path, "rb") as f:
        published = next(ijson.items(f, "publicationDate"), "")
    with open(path, "rb") as f:
        for sku, product in ijson.kvitems(f, "products"):
            attrs = product.get("attributes", {})
            family = product.get("productFamily") or attrs.get("productFamily")
            # Query API filters can name productFamily, which bulk files keep beside the attributes.
            fields = {**attrs, "productFamily": family}
            for key, t in targets.items():
                if all(fields.get(a) == v for a, v in t["filters"].items()):
                    fams = t["families"]
                    if fams is None or family in fams:
                        matches[key].append({"sku": sku, "attributes": attrs, "family": family})
    skus = {m["sku"] for ms in matches.values() for m in ms}
    terms: dict[str, dict] = {}
    with open(path, "rb") as f:
        for sku, offer_terms in ijson.kvitems(f, "terms.OnDemand"):
            if sku in skus:
                terms[sku] = offer_terms
    return published, matches, terms


def price_entry(key: str, region: str, spec: dict, products: list[dict], terms: dict[str, dict],
                published: str, is_global: bool) -> tuple[dict | None, str]:
    if len(products) != 1:
        return None, f"{len(products)} products matched (need exactly 1)"
    prod = products[0]
    attrs = prod["attributes"]
    if not is_global and attrs.get("regionCode") and attrs["regionCode"] != region:
        return None, f"regionCode {attrs['regionCode']} != {region}"
    offer = terms.get(prod["sku"], {})
    if len(offer) != 1:
        return None, f"{len(offer)} OnDemand terms (need exactly 1)"
    dims = next(iter(offer.values()))["priceDimensions"]
    first = next((d for d in dims.values() if d.get("beginRange") == "0"), None)
    if first is None:
        return None, "no price dimension starting at 0"
    usd = str(first["pricePerUnit"]["USD"])
    if Decimal(usd) <= 0:
        return None, f"non-positive price {usd}"
    units = spec["expect"].get("aws_unit")
    units = units if isinstance(units, list) or units is None else [units]
    if units and first["unit"] not in units:
        return None, f"unit {first['unit']!r} not in {units}"
    keep = ("usagetype", "operation", "instanceType", "cacheEngine", "databaseEngine")
    return {
        "usd": usd,
        "aws_unit": first["unit"],
        "published": published,
        "tier_begin": "0",
        "tier_end": first.get("endRange", "Inf"),
        "sku": prod["sku"],
        "rate_code": first.get("rateCode", ""),
        "description": first.get("description", ""),
        "attributes": {"productFamily": prod["family"], **{k: attrs[k] for k in keep if k in attrs}},
    }, ""


def build(spec_path: Path, out_path: Path, cache_dir: Path) -> dict:
    cfg = json.loads(spec_path.read_text(encoding="utf-8"))
    regions: list[str] = cfg["regions"]
    specs: dict[str, dict] = cfg["specs"]
    by_service: dict[str, list[str]] = {}
    for key, spec in specs.items():
        by_service.setdefault(spec["service_code"], []).append(key)

    prices: dict[str, dict[str, dict]] = {k: {} for k in specs}
    warnings: list[str] = []
    used: set[Path] = set()
    for code, keys in sorted(by_service.items()):
        index = fetch_json(f"{BASE}/offers/v1.0/aws/{code}/current/region_index.json")["regions"]
        for region in regions:
            todo = [k for k in keys if not (specs[k].get("global") and prices[k])]
            if not todo:
                continue
            src_region = region
            if any(specs[k].get("global") for k in todo):
                # Global services (Route 53) publish their prices under "aws-other".
                src_region = next((r for r in ("global", "aws-other") if r in index), region)
            if src_region not in index:
                warnings.append(f"{code}: no offer file for {src_region}")
                continue
            path = download(BASE + index[src_region]["currentVersionUrl"], cache_dir)
            used.add(path)
            targets = {}
            for k in todo:
                spec = specs[k]
                prefixes = spec.get("region_prefixes") or cfg["region_prefixes"]
                targets[k] = {
                    "filters": render(spec["filters"], prefixes.get(region, "")),
                    "families": wanted_families(spec["expect"]),
                }
            published, matches, terms = scan_offer(path, targets)
            for k in todo:
                is_global = bool(specs[k].get("global"))
                entry, why = price_entry(k, region, specs[k], matches[k], terms, published, is_global)
                if entry is None:
                    warnings.append(f"{k} [{region}]: {why}")
                    continue
                targets_regions = regions if is_global else [region]
                for r in targets_regions:
                    prices[k][r] = dict(entry)
            log.info("%s %s: %d items scanned", code, region, len(todo))

    # Offer files are versioned by URL; drop superseded ones so the cache does not grow forever.
    for old in cache_dir.glob("https:__pricing.us-east-1.amazonaws.com_offers_*"):
        if old.is_file() and old not in used:
            old.unlink()

    items = {}
    for key, spec in sorted(specs.items()):
        if len(prices[key]) != len(regions):
            warnings.append(f"{key}: skipped (priced in {sorted(prices[key])})")
            continue
        items[key] = {
            "service": spec["service"],
            "label": spec["label"],
            "quantity_unit": spec["quantity_unit"],
            "per": spec["per"],
            "quantity_scale": spec["quantity_scale"],
            "hours": spec["hours"],
            "slider": spec["slider"],
            "aws_query": {"service_code": spec["service_code"], "filters": spec["filters"], "expect": spec["expect"]},
            "prices": prices[key],
        }
    catalog = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "source": "aws_price_list_bulk",
        "source_url": f"{BASE}/offers/v1.0/aws/index.json",
        "price_type": "on_demand_list",
        "currency": "USD",
        "regions": regions,
        "aliases": cfg["aliases"],
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    for w in warnings:
        log.warning(w)
    log.info("wrote %s: %d/%d items, %d warnings", out_path, len(items), len(specs), len(warnings))
    return {"items": len(items), "specs": len(specs), "warnings": warnings}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=catalog_path("aws"))
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "aws-price-cache")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    build(SPEC_DIR / "aws_spec.json", args.out, args.cache)


if __name__ == "__main__":
    main()
