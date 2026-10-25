"""Deterministic monthly cost calculation over the item-key price catalogs.

Rules (fixed):
- monthly cents = round_half_up(unit price x quantity_scale x hours x quantity x 100); prices come
  only from the catalog.
- Choosing a service adds every catalog item of that service (companions such as ALB hours + LCU
  are all summed). Spec alternatives of one family (instance sizes, DB engine x size, disk tiers)
  form one line and only the selected one is summed: the cheapest by default, or the one matching
  the user's spec hint. A spec not in the catalog is shown as missing and left out of the total.
- Default quantities: million_* units 1, gb 100, everything else 1. Defaults are marked assumed.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any

from pricing.paths import readable_catalog_path

CLOUDS = ("aws", "azure", "gcp")
CLOUD_LABEL = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}
DEFAULT_REGION = {"aws": "ap-northeast-2", "azure": "koreacentral", "gcp": "asia-northeast3"}
REGION_LABEL = {
    "ap-northeast-2": "서울", "ap-northeast-1": "도쿄", "us-east-1": "미국 동부(버지니아)",
    "koreacentral": "한국 중부(서울)", "japaneast": "일본 동부(도쿄)", "eastus": "미국 동부",
    "asia-northeast3": "서울", "asia-northeast1": "도쿄", "us-central1": "미국 중부(아이오와)",
}
REGION_ALIASES = {
    "aws": {"seoul": "ap-northeast-2", "서울": "ap-northeast-2", "korea": "ap-northeast-2", "한국": "ap-northeast-2",
            "tokyo": "ap-northeast-1", "도쿄": "ap-northeast-1", "virginia": "us-east-1", "버지니아": "us-east-1",
            "미국": "us-east-1", "us": "us-east-1"},
    "azure": {"korea central": "koreacentral", "seoul": "koreacentral", "서울": "koreacentral", "한국": "koreacentral",
              "코리아센트럴": "koreacentral", "코리아 센트럴": "koreacentral", "korea": "koreacentral",
              "japan east": "japaneast", "tokyo": "japaneast", "도쿄": "japaneast", "east us": "eastus", "미국": "eastus"},
    "gcp": {"seoul": "asia-northeast3", "서울": "asia-northeast3", "korea": "asia-northeast3", "한국": "asia-northeast3",
            "tokyo": "asia-northeast1", "도쿄": "asia-northeast1", "iowa": "us-central1", "아이오와": "us-central1",
            "미국": "us-central1", "us": "us-central1", "us central": "us-central1"},
}

FAMILY_PATTERNS = {
    "aws": [r"^ec2\.", r"^rds\.(mysql|postgres)\.", r"^aurora\.(mysql|postgres)\.", r"^elasticache\.",
            r"^api_gateway\."],
    "azure": [r"^vm\.", r"^(mysql|postgresql)\.(?!storage$)", r"^disk\.", r"^redis\."],
    "gcp": [r"^gce\.", r"^cloudsql\.(mysql|postgres)\.", r"^memorystore\.", r"^pd\."],
}
# Words that carry no spec information for a cloud's keys.
STOPWORDS = {
    "aws": {"db", "cache", "amazon", "aws", "instance", "instances", "rds", "ec2", "linux", "on", "demand", "single",
            "az", "standard", "redis", "memcached", "valkey", "engine", "ubuntu"},
    "azure": {"standard", "azure", "vm", "linux", "mysql", "postgresql", "postgres", "flexible", "server", "ubuntu"},
    "gcp": {"gce", "vm", "linux", "google", "gcp", "cloud", "sql", "cloudsql", "redis", "ubuntu", "persistent", "disk"},
}
# Engine words map to a service and also act as spec hints (e.g. "postgresql" -> rds + postgres).
ENGINE_ALIASES = {
    "aws": {"postgresql": ("rds", "postgres"), "postgres": ("rds", "postgres"), "mysql": ("rds", "mysql"),
            "aurora mysql": ("aurora", "mysql"), "redis": ("elasticache", ""), "vpn": ("vpn", "")},
    "azure": {"postgres": ("postgresql", ""), "mssql": ("sql_database", ""), "redis": ("redis", ""),
              "vpn": ("vpn_gateway", "")},
    "gcp": {"postgresql": ("cloudsql", "postgres"), "postgres": ("cloudsql", "postgres"), "mysql": ("cloudsql", "mysql"),
            "redis": ("memorystore", ""), "storage": ("gcs", ""), "object storage": ("gcs", ""), "vm": ("gce", ""),
            "compute": ("gce", ""), "load balancing": ("cloud_lb", ""), "vpn": ("cloud_vpn", "")},
}
TOKEN_SYNONYMS = {"postgresql": "postgres"}
# A stated quantity goes to one line of the service: the listed primary line, else the first
# plain count line (the alternatives line sorts first) / storage-per-month line / request line.
PRIMARY_LINE = {
    "count": {"waf": "waf.web_acl", "cloud_armor": "cloud_armor.policy", "route53": "route53.zone",
              "cloud_dns": "cloud_dns.zone", "app_gateway": "appgw.standard_v2.fixed", "fargate": "fargate.vcpu",
              "cloud_run": "cloud_run.vcpu"},
    "gb": {"cloud_logging": "cloud_logging.ingestion", "bigquery": "bigquery.storage"},
    "million": {"s3": "s3.get", "blob": "blob.hot_lrs.read", "gcs": "gcs.class_b", "dynamodb": "dynamodb.read",
                "functions": "functions.consumption.executions", "lambda": "lambda.requests"},
}
MILLION_PRIORITY = ["million_requests", "million_queries", "million_units", "million_ios", "million_gb_seconds"]
VERSION = re.compile(r"\b\d+(?:\.\d+)+\b")
ENGINE_VERSION = re.compile(r"\b(postgres(?:ql)?|mysql|mariadb|redis|valkey|memcached|aurora)\s*v?\d+\b")

GROUPS = {
    "컴퓨트": {"ec2", "fargate", "eks", "vm", "aks", "gce", "gke"},
    "서버리스": {"lambda", "functions", "cloud_functions", "cloud_run", "api_gateway"},
    "데이터베이스": {"rds", "aurora", "dynamodb", "mysql", "postgresql", "sql_database", "cosmos_db", "cloudsql", "bigquery"},
    "캐시": {"elasticache", "redis", "memorystore"},
    "스토리지": {"s3", "ebs", "efs", "blob", "disk", "gcs", "pd"},
    "네트워크": {"alb", "nlb", "nat_gateway", "vpc_endpoint", "vpn", "route53", "app_gateway", "load_balancer",
               "public_ip", "vpn_gateway", "cloud_lb", "cloud_nat", "cloud_dns", "cloud_vpn"},
    "보안": {"waf", "firewall", "cloud_armor"},
    "모니터링": {"cloudwatch", "log_analytics", "cloud_logging"},
}
SERVICE_LABEL = {
    "ec2": "EC2", "rds": "RDS", "aurora": "Aurora", "s3": "S3", "ebs": "EBS", "efs": "EFS", "alb": "ALB", "nlb": "NLB",
    "nat_gateway": "NAT Gateway", "vpc_endpoint": "VPC Endpoint", "vpn": "Site-to-Site VPN", "route53": "Route 53",
    "lambda": "Lambda", "api_gateway": "API Gateway", "fargate": "Fargate", "eks": "EKS", "elasticache": "ElastiCache",
    "dynamodb": "DynamoDB", "cloudwatch": "CloudWatch", "waf": "WAF",
    "vm": "Virtual Machines", "aks": "AKS", "app_gateway": "Application Gateway", "blob": "Blob Storage",
    "cosmos_db": "Cosmos DB", "disk": "Managed Disks", "firewall": "Azure Firewall", "functions": "Functions",
    "load_balancer": "Load Balancer", "log_analytics": "Log Analytics", "mysql": "Database for MySQL",
    "postgresql": "Database for PostgreSQL", "public_ip": "Public IP", "redis": "Cache for Redis",
    "sql_database": "SQL Database", "vpn_gateway": "VPN Gateway",
    "gce": "Compute Engine", "cloudsql": "Cloud SQL", "memorystore": "Memorystore", "pd": "Persistent Disk",
    "gcs": "Cloud Storage", "cloud_lb": "Cloud Load Balancing", "cloud_nat": "Cloud NAT", "gke": "GKE",
    "cloud_run": "Cloud Run", "cloud_functions": "Cloud Run functions", "cloud_logging": "Cloud Logging",
    "cloud_armor": "Cloud Armor", "cloud_dns": "Cloud DNS", "bigquery": "BigQuery", "cloud_vpn": "Cloud VPN",
}
# Keyed by the catalog's "source" field, so the label follows how the catalog was built.
SOURCE_LABEL = {
    "aws_price_list_query_api": "AWS Price List Query API",
    "aws_price_list_bulk": "AWS Price List (공개 bulk 가격 파일)",
    "azure_retail_prices_api": "Azure Retail Prices API",
    "gcp_cloud_billing_catalog_api": "Google Cloud Billing Catalog API",
}
SOURCE_URL = {
    "aws_price_list_query_api": "https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html",
    "aws_price_list_bulk": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json",
    "azure_retail_prices_api": "https://prices.azure.com/api/retail/prices",
    "gcp_cloud_billing_catalog_api": "https://cloud.google.com/billing/docs/reference/rest",
}

_CACHE: dict[str, tuple[float, dict]] = {}


class CatalogUnavailable(Exception):
    pass


def load_catalog(cloud: str, path: Path | None = None) -> dict:
    path = path or readable_catalog_path(cloud)
    if not path.exists():
        raise CatalogUnavailable(cloud)
    mtime = path.stat().st_mtime
    cached = _CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    _CACHE[str(path)] = (mtime, data)
    return data


def subtotal_cents(usd: str, scale: int, hours: int, qty: int) -> int:
    if qty <= 0:
        return 0
    with localcontext() as ctx:
        ctx.prec = 60  # exact for any 9-digit quantity, like the browser's BigInt math
        cents = Decimal(usd) * scale * hours * qty * 100
        return int(cents.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def default_qty(unit: str | None, slider: dict) -> tuple[int, dict]:
    unit = unit or ""
    q = 1 if unit.startswith("million_") else 100 if unit == "gb" else 1
    return fit_slider(q, slider)


def fit_slider(q: int, slider: dict) -> tuple[int, dict]:
    lo, hi, step = int(slider.get("min", 0)), int(slider.get("max", 100)), int(slider.get("step", 1)) or 1
    q = max(q, lo)
    s = {"min": lo, "max": max(hi, q), "step": step}
    if (q - lo) % step:
        s["step"] = 1
    return q, s


def tokens(text: str, cloud: str) -> set[str]:
    # "MySQL 8.0", "PostgreSQL 16": versions are not specs. Keys never contain dotted numbers or
    # numbers right after an engine word, so this is safe for "e2-standard-2" or "db-custom-2-7680".
    text = ENGINE_VERSION.sub(r"\1", VERSION.sub(" ", (text or "").lower()))
    words = re.findall(r"[a-z0-9]+", text)
    return {TOKEN_SYNONYMS.get(w, w) for w in words} - STOPWORDS[cloud]


def key_tokens(key: str) -> set[str]:
    return {TOKEN_SYNONYMS.get(w, w) for w in re.split(r"[._]", key.lower())}


def family_of(cloud: str, key: str) -> bool:
    return any(re.match(p, key) for p in FAMILY_PATTERNS[cloud])


def group_of(service: str) -> str:
    return next((g for g, members in GROUPS.items() if service in members), "기타")


def resolve_region(cloud: str, region: str | None, catalog: dict) -> tuple[str, str | None]:
    if not region:
        return DEFAULT_REGION[cloud], None
    r = region.strip().lower()
    r = REGION_ALIASES[cloud].get(r, r)
    if r in catalog["regions"]:
        return r, None
    return DEFAULT_REGION[cloud], region


def engine_hint(cloud: str, service: str, name: str) -> str:
    """An engine named inside the service name ("Cloud SQL for PostgreSQL") is a spec hint."""
    words = set(name.split())
    return next((hint for word, (svc, hint) in ENGINE_ALIASES[cloud].items()
                 if svc == service and hint and word in words), "")


def resolve_service(cloud: str, name: str, catalog: dict) -> tuple[str | None, str]:
    """Return (service id, extra spec hint)."""
    n = re.sub(r"[\s_-]+", " ", (name or "").strip().lower())
    services = {it["service"] for it in catalog["items"].values()}
    bare = re.sub(r"^(amazon|aws|azure|microsoft|google cloud|google|gcp) ", "", n)
    # "cloud vpn" said in an Azure conversation still means that cloud's VPN.
    candidates = [n, bare, re.sub(r"^cloud ", "", bare)]
    # Names, labels and aliases are compared in the same form ("site-to-site vpn" = "site to site vpn").
    def norm(s: str) -> str:
        return re.sub(r"[\s_-]+", " ", s.strip().lower())

    labels = {norm(SERVICE_LABEL[svc]): svc for svc in services if svc in SERVICE_LABEL}
    aliases = {norm(k): v for k, v in catalog["aliases"].items()}
    for c in candidates:
        if c in labels:
            return labels[c], engine_hint(cloud, labels[c], c)
        if c in aliases:
            return aliases[c], engine_hint(cloud, aliases[c], c)
        if c.replace(" ", "_") in services:
            return c.replace(" ", "_"), ""
        if c in ENGINE_ALIASES[cloud]:
            return ENGINE_ALIASES[cloud][c]
    return None, ""


def service_menu(cloud: str) -> str:
    """Service names the model may use for a cloud, from the catalog itself."""
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return f"{cloud}: (가격표 없음)"
    services = sorted({it["service"] for it in catalog["items"].values()})
    return f"{cloud}: " + ", ".join(SERVICE_LABEL.get(s, s) for s in services)


def service_from_spec(cloud: str, spec: str, catalog: dict) -> str | None:
    """An unknown service name with a spec found under exactly one service (c7i.large -> ec2)."""
    want = tokens(spec, cloud)
    if not want:
        return None
    owners = {it["service"] for k, it in catalog["items"].items() if family_of(cloud, k) and want <= key_tokens(k)}
    return owners.pop() if len(owners) == 1 else None


def option(key: str, item: dict, region: str) -> dict:
    p = item["prices"][region]
    return {
        "key": key,
        "label": item["label"].get("ko") or item["label"].get("en") or key,
        "usd": p["usd"],
        "unit": p.get("aws_unit") or p.get("unit") or "",
        "quantity_unit": item["quantity_unit"],
        "scale": int(item["quantity_scale"]),
        "hours": int(item["hours"]),
        "per": item["per"],
        "slider": item["slider"],
    }


def monthly_unit_cents(opt: dict) -> Decimal:
    return Decimal(opt["usd"]) * opt["scale"] * opt["hours"]


def spec_from_text(cloud: str, opts: list[dict], text: str) -> str:
    """When the model left out a spec, pick the one family option whose distinctive key tokens
    all appear in the user's own words (e.g. "P10" -> disk.p10_lrs). Ambiguous -> no pick."""
    if not text:
        return ""
    said = tokens(text, cloud)
    shared = set.intersection(*(key_tokens(o["key"]) for o in opts))
    hits = [o for o in opts if (key_tokens(o["key"]) - shared) and (key_tokens(o["key"]) - shared) <= said]
    return " ".join(sorted(key_tokens(hits[0]["key"]) - shared)) if len(hits) == 1 else ""


def estimate(cloud: str, items: list[dict], region: str | None = None, catalog: dict | None = None,
             user_text: str = "") -> dict:
    cloud = (cloud or "").strip().lower()
    if cloud not in CLOUDS:
        raise ValueError(f"cloud must be one of {CLOUDS}")
    try:
        catalog = catalog or load_catalog(cloud)
    except CatalogUnavailable:
        return {"cloud": cloud, "cloud_label": CLOUD_LABEL[cloud], "state": "price_data_unavailable",
                "lines": [], "excluded": [], "notes": [f"{CLOUD_LABEL[cloud]} 가격표가 아직 없습니다."],
                "total_cents": 0}
    reg, requested = resolve_region(cloud, region, catalog)

    lines: list[dict] = []
    excluded: list[dict] = []
    notes: list[str] = []
    companion_line: dict[str, dict] = {}
    plain_family_line: dict[str, dict] = {}

    for raw in items or []:
        name = str(raw.get("service", "")).strip()
        service, hint = resolve_service(cloud, name, catalog)
        if not service:
            service = service_from_spec(cloud, str(raw.get("spec") or ""), catalog)
        if not service:
            excluded.append({"service": name or "(이름 없음)", "reason": "가격표에 없는 서비스"})
            continue
        spec = " ".join(x for x in (str(raw.get("spec") or ""), hint) if x).strip()
        qty_in = {u: _int_or_none(raw.get(u)) for u in ("count", "gb", "million")}
        keys = sorted(k for k, it in catalog["items"].items() if it["service"] == service and reg in it["prices"])
        fam_keys = [k for k in keys if family_of(cloud, k)]
        companions = [k for k in keys if k not in fam_keys]
        want = tokens(spec, cloud)
        item_lines: list[dict] = []
        family_line = None

        if fam_keys:
            opts = sorted((option(k, catalog["items"][k], reg) for k in fam_keys),
                          key=lambda o: (monthly_unit_cents(o), o["key"]))
            if not want and len(opts) > 1:
                want = tokens(spec_from_text(cloud, opts, user_text), cloud)
            matched = [o for o in opts if want and want <= key_tokens(o["key"])]
            if not want and service in plain_family_line:
                family_line = plain_family_line[service]
            else:
                family_line = _line(service, matched[0] if matched else opts[0], opts if len(opts) > 1 else None)
                if want and not matched and not want <= set().union(*(key_tokens(k) for k in companions)):
                    _mark_missing(family_line, spec, has_alternatives=len(opts) > 1)
                elif len(matched) > 1:
                    family_line["note"] = f"'{spec}'에 맞는 사양 {len(matched)}개 중 가장 저렴한 항목(선택 상자로 변경)"
                elif len(opts) > 1 and not matched:
                    family_line["note"] = f"사양 대안 {len(opts)}개 중 가장 저렴한 사양(선택 상자로 변경)"
                lines.append(family_line)
                if not want:
                    plain_family_line[service] = family_line
            item_lines.append(family_line)

        new_companions = []
        for k in companions:
            if k not in companion_line:
                companion_line[k] = _line(service, option(k, catalog["items"][k], reg), None)
                lines.append(companion_line[k])
                new_companions.append(companion_line[k])
            item_lines.append(companion_line[k])
        # A spec that names nothing in a plain service (ebs gp2, s3 glacier) is not priced as something else.
        if not fam_keys and want and not want <= set().union(*(key_tokens(k) for k in companions)):
            for line in new_companions:
                _mark_missing(line, spec, has_alternatives=False)

        for kind, q in qty_in.items():
            target = _target(item_lines, kind, service)
            if q is not None and target is not None:
                _apply_qty(target, q)

    for i, line in enumerate(lines):
        line["id"] = f"{line['service']}:{i}"
        line["subtotal_cents"] = subtotal_cents(line["usd"], line["scale"], line["hours"], line["qty"]) if line["included"] else 0
    if any(l["assumed"] for l in lines):
        notes.append("수량을 말하지 않은 항목은 최소 구성 기본값(개수 1, 백만 단위 1, GB 100)으로 계산했습니다.")
    if requested:
        notes.append(f"요청한 리전 '{requested}'은 가격표에 없어 {REGION_LABEL[reg]} 기준으로 계산했습니다.")
    return {
        "state": "ok",
        "cloud": cloud,
        "cloud_label": CLOUD_LABEL[cloud],
        "region": reg,
        "region_label": REGION_LABEL.get(reg, reg),
        "requested_region": requested,
        "currency": "USD",
        "as_of": catalog["as_of"],
        "source": SOURCE_LABEL.get(catalog.get("source", ""), catalog.get("source", "")),
        "source_url": catalog.get("source_url") or SOURCE_URL.get(catalog.get("source", ""), ""),
        "lines": lines,
        "excluded": excluded,
        "notes": notes,
        "total_cents": sum(l["subtotal_cents"] for l in lines),
    }


def _int_or_none(v: Any) -> int | None:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _unit_kind(quantity_unit: str) -> str:
    if quantity_unit == "gb":
        return "gb"
    if quantity_unit.startswith("million_"):
        return "million"
    return "count"


def _target(item_lines: list[dict], kind: str, service: str) -> dict | None:
    cands = [l for l in item_lines if _unit_kind(l["quantity_unit"]) == kind]
    if not cands:
        return None
    primary = PRIMARY_LINE[kind].get(service)
    for line in cands:
        if line["key"] == primary:
            return line
    if kind == "count":
        plain = [l for l in cands if l["quantity_unit"] in ("count", "endpoint_az")]
        return (plain or cands)[0]
    if kind == "gb":
        return next((l for l in cands if l["per"] == "gb_month"), cands[0])
    return min(cands, key=lambda l: MILLION_PRIORITY.index(l["quantity_unit"])
               if l["quantity_unit"] in MILLION_PRIORITY else len(MILLION_PRIORITY))


def _mark_missing(line: dict, spec: str, has_alternatives: bool) -> None:
    line["missing_spec"] = spec
    line["included"] = False
    line["note"] = (f"가격표에 없는 사양({spec}): 합계에서 제외. "
                    + ("선택 상자에서 대안을 고르거나 '합계에 포함'을 켜면 포함" if has_alternatives
                       else "이 항목 기준으로 보려면 '합계에 포함'을 켜세요"))


def _line(service: str, opt: dict, alternatives: list[dict] | None) -> dict:
    qty, slider = default_qty(opt["quantity_unit"], opt["slider"])
    line = {
        "service": service,
        "service_label": SERVICE_LABEL.get(service, service),
        "group": group_of(service),
        **opt,
        "slider": slider,
        "qty": qty,
        "default_qty": qty,
        "assumed": True,
        "included": True,
        "family": alternatives is not None,
    }
    if alternatives:
        line["alternatives"] = alternatives
    return line


def _apply_qty(line: dict, q: int) -> None:
    """A stated quantity replaces a default; quantities stated twice for one line add up."""
    if not line["assumed"]:
        q += line["qty"]
    line["qty"], line["slider"] = fit_slider(q, line["slider"])
    line["default_qty"] = line["qty"]
    line["assumed"] = False
