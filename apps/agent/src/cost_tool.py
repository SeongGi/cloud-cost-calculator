"""estimate_cost: the only tool the model sees. The model maps words to services and
quantities; prices and totals come from pricing.engine."""

import json
import logging
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, field_validator

from pricing.engine import CLOUDS, estimate, service_menu

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


def last_user_text(state: dict) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            content = message.content
            return content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content if isinstance(b, dict))
    return ""


class EstimateArgs(BaseModel):
    cloud: Literal["aws", "azure", "gcp"]
    items: list[Item] = Field(description="계산할 서비스 목록")
    region: str | None = Field(default=None, description="사용자가 말한 리전만. 예: ap-northeast-2, koreacentral, asia-northeast3, 서울")
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
def estimate_cost(cloud: str, items: list[Item], region: str | None = None, state: dict | None = None) -> str:
    log.info("estimate_cost args cloud=%s region=%s items=%s", cloud, region,
             [i.model_dump(exclude_none=True) if isinstance(i, BaseModel) else i for i in items])
    try:
        parsed = parse_items([i.model_dump() if isinstance(i, BaseModel) else i for i in items])
        result = estimate(cloud, parsed, region, user_text=last_user_text(state or {}))
    except (ValueError, TypeError) as exc:
        log.warning("estimate_cost rejected arguments: %s", exc)
        result = {"state": "invalid_arguments", "cloud": cloud, "message": str(exc),
                  "lines": [], "excluded": [], "notes": [], "total_cents": 0}
    result["request"] = {"cloud": cloud, "region": region}
    return json.dumps(result, ensure_ascii=False)
