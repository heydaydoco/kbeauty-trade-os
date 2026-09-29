"""T1 요건 템플릿 시드 카탈로그 로더 (§5.5 / S2-4 PR-2 — ADR-0049).

카탈로그(`app/seeds/t1_requirements.json`)는 저장소에 두어 diff·리뷰 가능한 **초안 데이터**다.
시스템은 규제를 단정하지 않는다(§21) — 적용단위·요건유형·주기는 초안 판단이고, 근거링크는
공식 사이트 주소일 뿐 이 카탈로그를 만든 환경에서 검증되지 않았다. 확정은 사람이 링크를 열어
확인일을 입력한 뒤 기존 3층 확정 게이트로 한다 — 그래서 카탈로그에는 확인일 필드가 **없다**.

로드 시점에 계약을 검증한다(빈 근거링크·http·키 중복·없는 선행 키·순환·열거 밖 적용단위·
시장 코드 형식). 계약 위반은 서버 기동이 아니라 첫 사용에서 예외로 드러나되, 계약 테스트가
CI에서 먼저 잡는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.modules.requirements.models import APPLIES_TO_VALUES

CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "seeds" / "t1_requirements.json"

_MARKET_CODE = re.compile(r"^[A-Z]{2}$")
_TYPE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


class CatalogError(ValueError):
    """카탈로그가 계약을 어겼다 — 데이터 결함이라 사용자 입력 오류가 아니다."""


@dataclass(frozen=True, slots=True)
class CatalogTemplate:
    key: str
    name: str
    applies_to: str
    requirement_type: str
    source_url: str
    note: str
    validity_months: int | None = None
    renewal_cycle_months: int | None = None
    renewal_lead_days: int | None = None
    prerequisites: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        """`requirements.service.add_draft_template`에 넘기는 내용 — 확인일은 항상 비운다."""
        return {
            "name": self.name,
            "applies_to": self.applies_to,
            "requirement_type": self.requirement_type,
            "validity_months": self.validity_months,
            "renewal_cycle_months": self.renewal_cycle_months,
            "renewal_lead_days": self.renewal_lead_days,
            "source_url": self.source_url,
            "last_verified_on": None,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class CatalogMarket:
    code: str
    name_ko: str
    templates: tuple[CatalogTemplate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    purpose: str
    notice: tuple[str, ...]
    markets: tuple[CatalogMarket, ...]

    def market(self, code: str) -> CatalogMarket | None:
        return next((market for market in self.markets if market.code == code), None)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def _https_url(value: Any, where: str) -> str:
    _require(
        isinstance(value, str) and value == value.strip() and value != "",
        f"{where}: 근거링크가 비었다",
    )
    parsed = urlparse(value)
    _require(
        parsed.scheme == "https" and bool(parsed.hostname),
        f"{where}: 근거링크는 https 주소여야 한다",
    )
    return str(value)


def _positive(value: Any, where: str) -> int | None:
    if value is None:
        return None
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 1,
        f"{where}: 1 이상 정수여야 한다",
    )
    return int(value)


def _acyclic(templates: tuple[CatalogTemplate, ...], market: str) -> None:
    graph = {item.key: item.prerequisites for item in templates}
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        _require(state.get(node) != 1, f"{market}: 선행요건 순환 — {node}")
        state[node] = 1
        for nxt in graph[node]:
            visit(nxt)
        state[node] = 2

    for key in graph:
        visit(key)


def parse_catalog(raw: dict[str, Any]) -> Catalog:
    _require(isinstance(raw.get("version"), str) and bool(raw["version"]), "version이 없다")
    notice = raw.get("notice")
    if not isinstance(notice, list) or not notice:
        raise CatalogError("notice(고지문)가 없다")
    markets: list[CatalogMarket] = []
    seen_codes: set[str] = set()
    seen_keys: set[str] = set()
    for market_raw in raw.get("markets", []):
        code = market_raw.get("code")
        _require(
            isinstance(code, str) and bool(_MARKET_CODE.fullmatch(code)),
            f"시장 코드 형식 오류: {code!r}",
        )
        _require(code not in seen_codes, f"시장 코드 중복: {code}")
        seen_codes.add(code)
        _require(bool(str(market_raw.get("name_ko", "")).strip()), f"{code}: name_ko가 없다")
        templates: list[CatalogTemplate] = []
        names: set[str] = set()
        for item in market_raw.get("templates", []):
            where = f"{code}/{item.get('key')}"
            key = item.get("key")
            _require(isinstance(key, str) and bool(_KEY.fullmatch(key)), f"{where}: key 형식 오류")
            _require(key not in seen_keys, f"{where}: key 중복")
            seen_keys.add(key)
            name = str(item.get("name", "")).strip()
            _require(name != "" and name not in names, f"{where}: 이름이 비었거나 시장 안에서 중복")
            names.add(name)
            _require(item.get("applies_to") in APPLIES_TO_VALUES, f"{where}: 적용단위가 열거 밖")
            _require(
                isinstance(item.get("requirement_type"), str)
                and bool(_TYPE_CODE.fullmatch(item["requirement_type"])),
                f"{where}: 요건유형 코드 형식 오류",
            )
            note = str(item.get("note", "")).strip()
            _require(note != "", f"{where}: note가 없다(확인 필요 사항을 남긴다)")
            templates.append(
                CatalogTemplate(
                    key=key,
                    name=name,
                    applies_to=item["applies_to"],
                    requirement_type=item["requirement_type"],
                    source_url=_https_url(item.get("source_url"), where),
                    note=note,
                    validity_months=_positive(
                        item.get("validity_months"), f"{where}.validity_months"
                    ),
                    renewal_cycle_months=_positive(
                        item.get("renewal_cycle_months"), f"{where}.renewal_cycle_months"
                    ),
                    renewal_lead_days=_positive(
                        item.get("renewal_lead_days"), f"{where}.renewal_lead_days"
                    ),
                    prerequisites=tuple(item.get("prerequisites", [])),
                )
            )
        by_key = {item.key for item in templates}
        for item in templates:
            for prerequisite in item.prerequisites:
                _require(
                    prerequisite in by_key,
                    f"{code}/{item.key}: 선행 키 {prerequisite!r}가 같은 시장에 없다",
                )
                _require(prerequisite != item.key, f"{code}/{item.key}: 자기 자신이 선행요건")
        _acyclic(tuple(templates), code)
        markets.append(
            CatalogMarket(
                code=code, name_ko=str(market_raw["name_ko"]).strip(), templates=tuple(templates)
            )
        )
    _require(len(markets) > 0, "시장이 하나도 없다")
    return Catalog(
        version=raw["version"],
        purpose=str(raw.get("purpose", "")),
        notice=tuple(str(line) for line in notice),
        markets=tuple(markets),
    )


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    return parse_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
