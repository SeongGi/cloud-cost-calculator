"""estimate_cost: the only tool the model sees. The model maps words to services and
quantities; prices and totals come from pricing.engine."""

import json
import logging
import re
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, field_validator

from langchain_core.messages import ToolMessage

from pricing.engine import (CLOUDS, CatalogUnavailable, estimate, load_catalog, normalize_transfer_items, region_in_text,
                            resolve_service, service_menu)

log = logging.getLogger(__name__)


class Item(BaseModel):
    service: str = Field(description="서비스 이름. 예: ec2, rds, s3, alb, lambda, virtual machines, blob storage, compute engine, cloud sql, cloud storage")
    spec: str | None = Field(default=None, description="사용자가 말한 사양·등급·엔진 이름만. 예: t3.medium, postgresql, D2s v5, P10, e2-standard-2. 말하지 않았으면 비움")
    count: int | None = Field(default=None, description="사용자가 말한 대수/개수만")
    gb: int | None = Field(default=None, description="사용자가 말한 용량(GB)만. 1TB=1000")
    million: int | None = Field(default=None, description="사용자가 말한 요청 수(백만 단위)만")


def parse_items(raw: Any) -> list[dict]:
    """Accept a list, a JSON string, or a JSON string that lost its closing brackets."""
    if isinstance(raw, str):
        text = raw.strip()
        for candidate in (text, text + "]", text + "}]"):
            try:
                raw = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            raise ValueError("items 인자를 해석하지 못했습니다")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("items는 목록이어야 합니다")
    return [Item.model_validate(x).model_dump() for x in raw]


CLOUD_RUN_ALONE = re.compile(r"cloud\s*run(?!\s*(?:functions?|함수))", re.I)
FUNCTION_WORDS = re.compile(r"functions?|함수", re.I)
RUN_FUNCTIONS_NAME = re.compile(r"(?:google\s+)?cloud\s*run\s+functions?", re.I)


def fix_cloud_run(cloud: str, items: list[dict], text: str) -> list[dict]:
    """"Cloud Run" alone is the container service, but the model often sends "cloud run functions"."""
    if cloud != "gcp" or not CLOUD_RUN_ALONE.search(text) or FUNCTION_WORDS.search(text):
        return items
    return [{**i, "service": "cloud run"} if RUN_FUNCTIONS_NAME.fullmatch(str(i.get("service", "")).strip()) else i
            for i in items]


def has_transfer(cloud: str, items: list[dict]) -> bool:
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return False
    return any(resolve_service(cloud, str(i.get("service", "")), catalog)[0] == "data_transfer" for i in items)


def last_user_text(state: dict) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            content = message.content
            return content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content if isinstance(b, dict))
    return ""


# Words that make a question continue the previous estimate ("여기에 VPN 추가하면", "MySQL 빼고").
FOLLOW_UP = re.compile(
    r"여기에|거기에|이거에|이것에|위\s*구성|앞\s*구성|기존|추가|더하|더\s*넣|포함|빼|제외|없애|삭제|바꾸|바꿔|변경|"
    r"늘리|늘려|줄이|줄여|대신|전체|합쳐|같이|그대로|으로\s*하면|로\s*하면")
# Cloud names only; "Microsoft SQL Server" or "Google Workspace" must not switch the cloud.
CLOUD_WORDS = {
    # Latin-letter boundaries: Korean particles attach directly ("AWS랑", "GCP에").
    "aws": r"(?<![a-z])aws(?![a-z])|아마존|amazon web services",
    "azure": r"azure|애저|마이크로소프트\s*클라우드",
    "gcp": r"(?<![a-z])gcp(?![a-z])|google\s*cloud|구글\s*클라우드|구글|지씨피",
}
# "바꿔/대신/으로" replaces the line of that service; "추가/더" adds another one.
REPLACE_WORDS = re.compile(r"바꾸|바꿔|변경|대신|교체|으로\s*하면|로\s*하면|으로\s*해|로\s*해")
# "인터넷도", "여기에 ... 추가": the user adds another line rather than naming the default's destination.
ADD_WORDS = re.compile(r"추가|더하|더\s*넣|으로도|로도|도\s*추가")


def previous_request(state: dict, cloud: str | None = None) -> dict | None:
    """The request behind the latest successful estimate (of `cloud`, when given). Recognized by
    its content, not the tool name: messages restored from the browser arrive without a name."""
    for message in reversed(state.get("messages", [])):
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        try:
            req = (json.loads(message.content) or {}).get("request") or {}
        except (TypeError, ValueError, AttributeError):
            continue
        if req.get("items") and (cloud is None or req.get("cloud") == cloud):
            return req
    return None


def named_clouds(text: str) -> set[str]:
    return {c for c, pattern in CLOUD_WORDS.items() if re.search(pattern, text, re.IGNORECASE)}


def resolves_on(cloud: str, items: list[dict]) -> bool:
    """Every item names a service of this cloud's catalog."""
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return False
    return all(resolve_service(cloud, i["service"], catalog)[0] for i in items)


def _norm_spec(spec: str | None) -> str:
    return re.sub(r"[\s_\-.]+", "", (spec or "").lower())


def merge_items(cloud: str, base: list[dict], changes: list[dict], remove: list[str],
                replace: bool = False, add: bool = False) -> tuple[list[dict], list[str]]:
    """Previous items updated by the new ones and minus the removed services.

    A change goes to the line with the same service and spec; else, when the user asked to change
    (`replace`) or gave no new spec, to the only line of that service; with several such lines and
    no spec match it is not guessed. Otherwise it is added. Returns the items and notes about
    what could not be applied. A data transfer line whose destination is only a default
    (`transfer_default`) counts as having no spec: the user naming a destination replaces it,
    unless the user is adding (`add`)."""
    try:
        catalog = load_catalog(cloud)
    except CatalogUnavailable:
        return base + changes, list(remove)

    def key(name: str) -> str:
        return resolve_service(cloud, name, catalog)[0] or name.strip().lower()

    merged = [dict(i) for i in base]
    notes = []
    unmatched = []
    for name in remove:
        kept = [m for m in merged if key(m["service"]) != key(name)]
        if len(kept) == len(merged):
            unmatched.append(name)
        merged = kept
    if unmatched:
        notes.append(f"앞 구성에 없어 빼지 못한 서비스: {', '.join(unmatched)}")
    for change in changes:
        same = [m for m in merged if key(m["service"]) == key(change["service"])]
        exact = [m for m in same if change.get("spec") and _norm_spec(m.get("spec")) == _norm_spec(change["spec"])]
        if exact:
            target = exact[0]
        elif len(same) == 1 and (replace or not change.get("spec") or not same[0].get("spec")
                                 or (same[0].get("transfer_default") and not add)):
            target = same[0]
        elif len(same) > 1 and not change.get("spec"):
            notes.append(f"{change['service']} 항목이 여러 줄이라 어느 것을 바꿀지 알 수 없어 그대로 두었습니다. 사양을 함께 말해 주세요.")
            continue
        else:
            target = None
        if target is None:
            merged.append(dict(change))
        else:
            was_default = target.get("transfer_default")
            target.update({k: v for k, v in change.items() if v is not None and k != "service"})
            if was_default and change.get("spec"):
                # update() skips None, so the flag is set explicitly: a stated destination is not a default.
                target["transfer_default"] = bool(change.get("transfer_default", False))
                target.pop("transfer_dropped", None)
    return merged, notes


class EstimateArgs(BaseModel):
    cloud: Literal["aws", "azure", "gcp"]
    items: list[Item] = Field(default_factory=list, description="계산할 서비스 목록. 이어지는 질문이면 새로 말한 서비스만 넣어도 된다")
    follow_up: bool | None = Field(default=None, description="앞에서 계산한 구성에 더하거나 바꾸거나 빼는 질문이면 true, 새 구성이면 false")
    remove: list[str] = Field(default_factory=list, description="이어지는 질문에서 빼라고 한 서비스 이름")
    region: str | None = Field(default=None, description="사용자가 말한 리전만. 서울은 ap-northeast-2(AWS), koreacentral(Azure), asia-northeast3(GCP). 도쿄는 ap-northeast-1(AWS), japaneast(Azure), asia-northeast1(GCP)")
    state: Annotated[dict, InjectedState] = Field(default_factory=dict)

    @field_validator("items", mode="before")
    @classmethod
    def _recover_items(cls, v: Any) -> Any:
        # Local models sometimes send the array as a string, occasionally without its closing brackets.
        return parse_items(v) if isinstance(v, (str, dict)) else v


TOOL_DESCRIPTION = (
    "AWS, Azure, GCP 구성의 월 비용을 공개 가격표로 계산한다. 사용자가 클라우드 서비스나 구성을 말하면 반드시 호출한다. "
    "사용자의 말을 서비스 이름과 사양, 수량으로만 옮긴다. 말하지 않은 수량·사양·리전은 넣지 않는다. "
    "service에는 아래 목록의 이름을 쓴다.\n" + "\n".join(service_menu(c) for c in CLOUDS)
)


@tool(args_schema=EstimateArgs, return_direct=True, description=TOOL_DESCRIPTION)
def estimate_cost(cloud: str, items: list[Item] | None = None, region: str | None = None,
                  follow_up: bool | None = None, remove: list[str] | None = None, state: dict | None = None) -> str:
    items, remove, state = items or [], remove or [], state or {}
    log.info("estimate_cost args cloud=%s region=%s follow_up=%s remove=%s items=%s", cloud, region, follow_up, remove,
             [i.model_dump(exclude_none=True) if isinstance(i, BaseModel) else i for i in items])
    text = last_user_text(state)
    try:
        parsed = fix_cloud_run(cloud, parse_items([i.model_dump() if isinstance(i, BaseModel) else i for i in items]), text)
        said = named_clouds(text)
        # Each cloud keeps its own configuration in the conversation. A follow-up goes to the cloud
        # the user named; else to the cloud the model chose if it was estimated before; else to the
        # latest estimate when the new services exist there ("cloud vpn" in an Azure chat).
        cue = follow_up is True or bool(remove) or bool(FOLLOW_UP.search(text))
        prev = None
        if follow_up is not False or FOLLOW_UP.search(text):
            # "aws와 vpn으로 연결": the GCP call builds on the GCP config, the AWS call on AWS's.
            prev = previous_request(state, cloud)
            if prev is None and len(said) == 1 and cloud not in said:
                prev = previous_request(state, next(iter(said)))
            if prev is None and not said:
                last = previous_request(state)
                if last is not None and resolves_on(last["cloud"], parsed):
                    prev = last
        merge = prev is not None and cue
        notes = []
        if merge:
            cloud = prev["cloud"]
            region = region or prev.get("region")
        # The model copies a region from examples ("GCP 도쿄" sent as asia-northeast3), so a region the
        # sentence names wins. Transfers are left alone: there a named region may be the destination.
        if not has_transfer(cloud, parsed):
            region = region_in_text(cloud, text) or region
        # Before merging only the model's own spec is read, plus the sentence when the user is adding a
        # line: otherwise an empty spec still means "only the quantity changes". Defaults come after.
        parsed = normalize_transfer_items(cloud, parsed, region, text, fill_default=False,
                                          read_text=bool(ADD_WORDS.search(text)))
        if merge:
            parsed, merge_notes = merge_items(cloud, prev["items"], parsed, remove, replace=bool(REPLACE_WORDS.search(text)),
                                              add=bool(ADD_WORDS.search(text)))
            notes.append("앞에서 계산한 구성에 이번 요청(추가·변경·제외)을 반영해 전체를 다시 계산했습니다.")
            notes.extend(merge_notes)
        if not parsed:
            raise ValueError("계산할 서비스가 없습니다")
        # Saved with the result, so a later turn keeps the destination (and a default stays a default).
        parsed = normalize_transfer_items(cloud, parsed, region, text)
        result = estimate(cloud, parsed, region, user_text=text)
        result["notes"] = notes + result.get("notes", [])
        # The resolved items travel with the result so the next follow-up can build on them.
        result["request"] = {"cloud": cloud, "region": region, "items": parsed}
    except (ValueError, TypeError) as exc:
        log.warning("estimate_cost rejected arguments: %s", exc)
        result = {"state": "invalid_arguments", "cloud": cloud, "message": str(exc),
                  "lines": [], "excluded": [], "notes": [], "total_cents": 0,
                  "request": {"cloud": cloud, "region": region}}
    return json.dumps(result, ensure_ascii=False)
