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
# Every "state" estimate() can return; src/usage.py records only these (and a few of its own).
RESULT_STATES = frozenset({"ok", "price_data_unavailable"})
CLOUD_LABEL = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}
DEFAULT_REGION = {"aws": "ap-northeast-2", "azure": "koreacentral", "gcp": "asia-northeast3"}
REGION_LABEL = {
    "ap-northeast-2": "서울", "ap-northeast-1": "도쿄", "us-east-1": "미국 동부(버지니아)",
    "koreacentral": "한국 중부(서울)", "japaneast": "일본 동부(도쿄)", "eastus": "미국 동부",
    "asia-northeast3": "서울", "asia-northeast1": "도쿄", "us-central1": "미국 중부(아이오와)",
}
REGION_ALIASES = {
    "aws": {"seoul": "ap-northeast-2", "서울": "ap-northeast-2", "korea": "ap-northeast-2", "한국": "ap-northeast-2",
            "tokyo": "ap-northeast-1", "도쿄": "ap-northeast-1", "virginia": "us-east-1", "버지니아": "us-east-1", "us-east": "us-east-1", "n. virginia": "us-east-1",
            "n virginia": "us-east-1",
            "미국": "us-east-1", "미국 동부": "us-east-1", "us": "us-east-1", "일본": "ap-northeast-1", "japan": "ap-northeast-1"},
    "azure": {"korea central": "koreacentral", "seoul": "koreacentral", "서울": "koreacentral", "한국": "koreacentral",
              "코리아센트럴": "koreacentral", "코리아 센트럴": "koreacentral", "korea": "koreacentral",
              "japan east": "japaneast", "tokyo": "japaneast", "도쿄": "japaneast", "east us": "eastus", "미국": "eastus", "미국 동부": "eastus",
              "한국 중부": "koreacentral", "일본 동부": "japaneast", "일본": "japaneast", "japan": "japaneast", "virginia": "eastus", "버지니아": "eastus", "us-east": "eastus", "n. virginia": "eastus",
              "n virginia": "eastus"},
    "gcp": {"seoul": "asia-northeast3", "서울": "asia-northeast3", "korea": "asia-northeast3", "한국": "asia-northeast3",
            "tokyo": "asia-northeast1", "도쿄": "asia-northeast1", "iowa": "us-central1", "아이오와": "us-central1",
            "미국": "us-central1", "미국 중부": "us-central1", "us": "us-central1", "us central": "us-central1", "일본": "asia-northeast1",
            "japan": "asia-northeast1"},
}

FAMILY_PATTERNS = {
    "aws": [r"^ec2\.", r"^rds\.(mysql|postgres)\.", r"^aurora\.(mysql|postgres)\.", r"^elasticache\.",
            r"^api_gateway\.", r"^data_transfer\."],
    "azure": [r"^vm\.", r"^(mysql|postgresql)\.(?!storage$)", r"^disk\.", r"^redis\.", r"^data_transfer\."],
    "gcp": [r"^gce\.", r"^cloudsql\.(mysql|postgres)\.", r"^memorystore\.", r"^pd\.", r"^data_transfer\."],
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
               "public_ip", "vpn_gateway", "cloud_lb", "cloud_nat", "cloud_dns", "cloud_vpn", "data_transfer"},
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
    "data_transfer": "데이터 전송",
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
    # Transfer keys are named after regions and "internet"; a spec word alone never means a transfer.
    owners.discard("data_transfer")
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


# Destinations outside the catalog's three regions, said by the model in a spec.
OUT_OF_SCOPE_WORDS = ("싱가포르", "유럽", "오사카", "프랑크푸르트", "런던", "시드니", "홍콩", "뭄바이", "서부", "singapore",
                      "europe", "osaka", "frankfurt", "london", "sydney", "hong kong", "mumbai", "west",
                      "오레곤", "오하이오", "캘리포니아", "중국", "인도", "호주", "캐나다", "브라질", "독일", "프랑스", "영국", "아일랜드",
                      "아프리카", "남미", "오세아니아", "중동", "oregon", "ohio", "california", "china", "india", "australia",
                      "canada", "brazil", "germany", "france", "ireland", "africa", "oceania")
REGION_CODE_SHAPE = re.compile(r"[a-z]{2,}-[a-z]+-?\d")
INTERNET_CUES = re.compile(r"egress|outbound|아웃바운드|외부|다운로드|사용자|고객|트래픽|온프레미스|on-?prem|사무실|해외|"
                           r"(?<![a-z])idc(?![a-z])|users?|public|cloudfront")
REPLICA_CUES = re.compile(r"리전|region|복제|replication|백업|(?<![a-z])dr(?![a-z])")
# A region name followed by these is where the users are, not a destination ("도쿄 사용자").
USER_LOCATION = re.compile(r"\s*(?:리전|region)?\s*(?:사용자|고객|유저|접속|users?\b|customers?\b|clients?\b)")


def _latin(word: str) -> bool:
    return re.fullmatch(r"[a-z0-9 .\-]+", word) is not None


def _word_re(word: str) -> str:
    """Latin words need non-latin neighbours ("us" is not in "business"); a Korean region name
    is not a region when "어" follows ("한국어")."""
    return rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])" if _latin(word) else rf"{re.escape(word)}(?!어|식|인)"


def transfer_destination(cloud: str, text: str, reg: str, catalog: dict, check_scope: bool = False) -> tuple[str, str]:
    """Where a data transfer goes, from a spec or a sentence: (dest, source).

    dest is "internet", a region code, "" (cannot tell) or "?" (a destination outside the catalog,
    only with check_scope, meant for the model's spec). source is the region the text names as the
    origin ("서울에서", "from tokyo") or ""."""
    t = (text or "").lower()
    codes = list(catalog["regions"])
    if check_scope:
        rest = t
        for c in codes:
            rest = rest.replace(c, " ")
        if REGION_CODE_SHAPE.search(rest) or any(re.search(_word_re(w), rest) for w in OUT_OF_SCOPE_WORDS):
            return "?", ""
    aliases = {**REGION_ALIASES[cloud], **{c: c for c in codes}}
    spans: list[tuple[int, int]] = []
    candidates: set[str] = set()
    source = ""
    for alias in sorted(aliases, key=len, reverse=True):
        for m in re.finditer(_word_re(alias), t):
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue
            spans.append((m.start(), m.end()))
            code = aliases[alias]
            after = t[m.end():]
            if USER_LOCATION.match(after) or (alias == "us" and re.match(r"\s*(달러|dollar)", after)):
                continue
            if (re.match(r"\s*(리전|region)?\s*(에서|으?로부터)", after) or re.match(r"\s+to\b", after)
                    or re.search(r"from\s+$", t[:m.start()])):
                source = source or code
            elif code != reg:
                candidates.add(code)
    if re.search(r"인터넷|internet", t):
        return "internet", source
    return (next(iter(candidates)) if len(candidates) == 1 else ""), source


def _default_transfer(catalog: dict, reg: str, cue_text: str) -> str:
    """The destination to use when none is known: the cheapest alternative (the project's fixed
    rule), unless the words only suggest the internet or only suggest copying to another region."""
    keys = sorted(k for k, it in catalog["items"].items() if it["service"] == "data_transfer" and reg in it["prices"])
    opts = sorted((option(k, catalog["items"][k], reg) for k in keys), key=lambda o: (monthly_unit_cents(o), o["key"]))
    internet, replica = bool(INTERNET_CUES.search(cue_text)), bool(REPLICA_CUES.search(cue_text))
    if internet and not replica:
        pick = "data_transfer.internet"
    else:
        pool = [o for o in opts if o["key"] != "data_transfer.internet"] if replica and not internet else opts
        pick = (pool or opts)[0]["key"]
    if pick == "data_transfer.internet":
        return "internet"
    return next(r for r in catalog["regions"] if f"data_transfer.to_{r.replace('-', '_')}" == pick)


def _resolve_transfer(cloud: str, spec: str, reg: str, catalog: dict, user_text: str,
                      read_text: bool = True, fill_default: bool = True) -> tuple[str, str, bool, str]:
    """(dest, source, defaulted, dropped) for one data transfer item.

    The model's spec comes first. The user's sentence is read only when the spec is empty (and
    read_text is on), and only then can it name a source. Without fill_default an unknown
    destination stays "" (before a follow-up is merged, an empty spec still means "only the
    quantity changes"). Otherwise the default applies. dropped is the estimate's own region when the spec named it as
    the destination, which a transfer cannot have."""
    dest, source = transfer_destination(cloud, spec, reg, catalog, check_scope=True)
    if dest == "?":
        return "?", "", False, ""
    if not dest and not spec.strip() and read_text:
        dest, source = transfer_destination(cloud, user_text, reg, catalog)
    if dest or not fill_default:
        return dest, source, False, ""
    dropped = reg if reg in _named_regions(cloud, spec, catalog) else ""
    return _default_transfer(catalog, reg, f"{spec} {user_text}".lower()), source, True, dropped


def _named_regions(cloud: str, text: str, catalog: dict) -> set[str]:
    t = (text or "").lower()
    aliases = {**REGION_ALIASES[cloud], **{c: c for c in catalog["regions"]}}
    return {code for alias, code in aliases.items() if re.search(_word_re(alias), t)}


def normalize_transfer_items(cloud: str, items: list[dict], region: str | None, user_text: str,
                             fill_default: bool = True, read_text: bool = True) -> list[dict]:
    """Copies of the items where a data transfer's spec is its destination ("internet" or a region
    code). An item whose spec is empty is read from the user's sentence (read_text) and, if that
    names nothing, gets the default and "transfer_default": true (fill_default). An out-of-scope
    destination keeps its spec."""
    cloud = (cloud or "").strip().lower()
    out = [dict(i) for i in items]
    if cloud not in CLOUDS:
        return out
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return out
    reg, _ = resolve_region(cloud, region, catalog)
    for item in out:
        if resolve_service(cloud, str(item.get("service", "")), catalog)[0] != "data_transfer":
            continue
        dest, _, defaulted, dropped = _resolve_transfer(cloud, str(item.get("spec") or ""), reg, catalog, user_text,
                                                        read_text=read_text, fill_default=fill_default)
        if dest and dest != "?":
            item["spec"] = dest
            if defaulted:
                item["transfer_default"] = True
                if dropped:
                    item["transfer_dropped"] = dropped
    return out


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
        transfer_notes: list[str] = []
        unknown_dest = out_of_scope = False
        if service == "data_transfer":
            dest, source, defaulted, dropped = _resolve_transfer(cloud, str(raw.get("spec") or ""), reg, catalog, user_text)
            dropped = dropped or (raw.get("transfer_dropped") if raw.get("transfer_dropped") == reg else "")
            out_of_scope = dest == "?"
            if dest and not out_of_scope:
                spec = dest
            if source and source != reg:
                transfer_notes.append(f"말씀하신 출발지({REGION_LABEL.get(source, source)})가 아니라 견적 리전 "
                                      f"{REGION_LABEL.get(reg, reg)} 기준으로 계산했습니다. 출발 리전을 바꾸려면 리전을 지정해 주세요.")
            unknown_dest = (defaulted or bool(raw.get("transfer_default"))) and not out_of_scope
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
            if not want and len(opts) > 1 and service != "data_transfer":
                want = tokens(spec_from_text(cloud, opts, user_text), cloud)
            matched = [o for o in opts if want and want <= key_tokens(o["key"])]
            if not want and service in plain_family_line and not out_of_scope:
                family_line = plain_family_line[service]
            else:
                family_line = _line(service, matched[0] if matched else opts[0], opts if len(opts) > 1 else None)
                foreign = foreign_spec_cloud(spec, cloud) if want and not matched and service != "data_transfer" else None
                if out_of_scope:
                    _mark_missing(family_line, str(raw.get("spec") or ""), has_alternatives=True)
                elif foreign:
                    # Another cloud's size: price this cloud's cheapest option instead of dropping it.
                    family_line["note"] = (f"'{spec}'은 {CLOUD_LABEL[foreign]} 사양이라 {CLOUD_LABEL[cloud]}에서 "
                                           f"가장 저렴한 사양으로 계산했습니다(선택 상자로 변경)")
                elif want and not matched and not want <= set().union(*(key_tokens(k) for k in companions)):
                    _mark_missing(family_line, spec, has_alternatives=len(opts) > 1)
                elif len(matched) > 1:
                    family_line["note"] = f"'{spec}'에 맞는 사양 {len(matched)}개 중 가장 저렴한 항목(선택 상자로 변경)"
                elif len(opts) > 1 and not matched:
                    family_line["note"] = f"사양 대안 {len(opts)}개 중 가장 저렴한 사양(선택 상자로 변경)"
                if unknown_dest:
                    transfer_notes.insert(0, (
                        f"앞에서 정한 도착지({REGION_LABEL.get(dropped, dropped)})가 이번 견적 리전과 같아 "
                        f"'{family_line['label']}' 항목으로 계산했습니다(선택 상자로 변경)") if dropped else
                        f"도착지를 알 수 없어 '{family_line['label']}' 항목으로 계산했습니다(선택 상자로 변경)")
                if transfer_notes and not out_of_scope:
                    family_line["note"] = " ".join(x for x in (family_line.get("note", ""), *transfer_notes) if x)
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
    transfers = [l for l in lines if l["service"] == "data_transfer" and l["included"]]
    if transfers:
        notes.append("데이터 전송은 첫 유료 구간 단가를 전체 수량에 곱했습니다. 무료 구간과 대량 할인 구간은 반영하지 않으므로 "
                     "실제 청구액보다 많게 잡힐 수 있습니다. 들어오는 전송과 같은 리전 안 전송은 계산하지 않습니다.")
        if cloud == "gcp" and any(l["key"] == "data_transfer.internet" for l in transfers):
            notes.append("Google Cloud 인터넷 전송은 한국 사용자 대상 단가입니다.")
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


def foreign_spec_cloud(spec: str, cloud: str) -> str | None:
    """The other cloud whose sizes this spec names ("t3.medium" asked for Azure, as in
    "같은 구성을 Azure로 하면?"), or None."""
    for other in CLOUDS:
        if other == cloud:
            continue
        try:
            items = load_catalog(other)["items"]
        except CatalogUnavailable:
            continue
        want = tokens(spec, other)
        if want and any(family_of(other, k) and want <= key_tokens(k) for k in items):
            return other
    return None


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
