"""T1 시드 투입 서비스 (§5.5 / S2-4 PR-2 — ADR-0049).

★ 마이그레이션 시드가 아니라 **앱 경로**다 — markets·requirement_templates는 ActorMixin(users FK)을
  달고 있어 마이그레이션 시드가 TRUNCATE CASCADE로 보존되지 않는다(함정 ⑩, S2-1 조건 12).
★ 투입은 항상 **초안(DRAFT)**이고 확인일을 비운다 — 확정은 사람이 링크를 열어 확인한 뒤 기존
  확정 게이트(ADR-0033)로 한다. 같은 (시장, 이름)이 이미 있으면 **건너뛴다**(멱등 — 사람이 고친
  템플릿·확정한 템플릿을 덮지 않는다). 요청 1건 = 트랜잭션 1건(시장이 여러 개여도 전부 성공하거나
  전부 실패).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets.models import Market
from app.modules.requirements.models import RequirementTemplate, TemplatePrerequisite
from app.modules.requirements.service import add_draft_template
from app.modules.seeds.catalog import Catalog, CatalogMarket, load_catalog

APPLY_ENDPOINT = "POST /api/v1/seeds/t1/apply"
#: 투입 직렬화용 어드바이저리 락 키 — 서로 다른 멱등 키의 동시 투입이 같은 (시장, 이름)에서 부딪혀
#: 유일 제약 위반(500)이 되지 않게 한다(트랜잭션 종료 시 자동 해제).
_APPLY_LOCK_KEY = 4_900_001


@dataclass(frozen=True, slots=True)
class TemplateState:
    key: str
    name: str
    applies_to: str
    requirement_type: str
    source_url: str
    #: 이미 있는 (시장, 이름) 템플릿의 상태 — 없으면 None(투입 대상).
    existing_status: str | None


@dataclass(frozen=True, slots=True)
class MarketState:
    code: str
    name_ko: str
    registered: bool
    templates: list[TemplateState]


@dataclass(frozen=True, slots=True)
class CatalogStatus:
    version: str
    notice: list[str]
    markets: list[MarketState]


@dataclass(slots=True)
class MarketApplyResult:
    code: str
    market_created: bool = False
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _active_market(session: Session, code: str) -> Market | None:
    return session.execute(
        select(Market).where(Market.code == code, Market.deleted_at.is_(None))
    ).scalar_one_or_none()


def _existing_templates(session: Session, market_id: int) -> dict[str, RequirementTemplate]:
    rows = session.execute(
        select(RequirementTemplate).where(
            RequirementTemplate.market_id == market_id, RequirementTemplate.deleted_at.is_(None)
        )
    ).scalars()
    return {row.name: row for row in rows}


def catalog_status() -> CatalogStatus:
    """카탈로그와 시장별 투입 현황 — 읽기 전용(질의 수는 시장 수와 무관: 시장 1+템플릿 1)."""
    catalog = load_catalog()
    codes = [market.code for market in catalog.markets]
    with unit_of_work() as uow:
        session = uow.session
        market_rows = {
            row.code: row
            for row in session.execute(
                select(Market).where(Market.code.in_(codes), Market.deleted_at.is_(None))
            ).scalars()
        }
        by_market: dict[int, dict[str, str]] = {}
        if market_rows:
            for market_id, name, status in session.execute(
                select(
                    RequirementTemplate.market_id,
                    RequirementTemplate.name,
                    RequirementTemplate.status,
                ).where(
                    RequirementTemplate.market_id.in_([row.id for row in market_rows.values()]),
                    RequirementTemplate.deleted_at.is_(None),
                )
            ).all():
                by_market.setdefault(market_id, {})[name] = status
        markets: list[MarketState] = []
        for market in catalog.markets:
            row = market_rows.get(market.code)
            present = by_market.get(row.id, {}) if row is not None else {}
            markets.append(
                MarketState(
                    code=market.code,
                    name_ko=market.name_ko,
                    registered=row is not None,
                    templates=[
                        TemplateState(
                            key=item.key,
                            name=item.name,
                            applies_to=item.applies_to,
                            requirement_type=item.requirement_type,
                            source_url=item.source_url,
                            existing_status=present.get(item.name),
                        )
                        for item in market.templates
                    ],
                )
            )
        return CatalogStatus(version=catalog.version, notice=list(catalog.notice), markets=markets)


def _apply_market(session: Session, *, actor_id: int, market: CatalogMarket) -> MarketApplyResult:
    result = MarketApplyResult(code=market.code)
    row = _active_market(session, market.code)
    if row is None:
        taken = session.execute(
            select(Market.id).where(Market.code == market.code)
        ).scalar_one_or_none()
        if taken is not None:
            # 전역 UNIQUE라 삭제된 시장이 코드를 점유한다 — 복원 액션이 없어 시드가 되살리지 않는다.
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "markets": f"시장 코드 {market.code}는 삭제된 시장이 점유하고 있어 투입할 수 없습니다. "
                    "관리자에게 복원을 요청해 주세요."
                },
                log_context={"market_code": market.code},
            )
        row = Market(code=market.code, name_ko=market.name_ko, created_by_id=actor_id)
        session.add(row)
        session.flush()
        result.market_created = True

    existing = _existing_templates(session, row.id)
    created_by_key: dict[str, int] = {}
    for item in market.templates:
        if item.name in existing:
            result.skipped.append(item.key)
            continue
        template = add_draft_template(
            session, market=row, actor_id=actor_id, payload=item.payload()
        )
        created_by_key[item.key] = template.id
        result.created.append(item.key)

    # 선행요건 — 이번에 만든 템플릿에만 건다(기존 템플릿은 사람의 것이라 건드리지 않는다).
    # 선행 대상은 이번에 만든 것이거나 이미 있던 같은 이름의 템플릿이다. 카탈로그가 비순환임은
    # 로드 시점에 검증됐고, 이번 투입은 새 템플릿에서 나가는 간선만 더하므로 순환이 생길 수 없다.
    name_by_key = {item.key: item.name for item in market.templates}
    for item in market.templates:
        template_id = created_by_key.get(item.key)
        if template_id is None:
            continue
        for prerequisite_key in item.prerequisites:
            target_id = created_by_key.get(prerequisite_key)
            if target_id is None:
                existing_target = existing.get(name_by_key[prerequisite_key])
                if existing_target is None:  # pragma: no cover — 카탈로그 계약상 도달 불가
                    continue
                target_id = existing_target.id
            session.add(
                TemplatePrerequisite(
                    template_id=template_id,
                    prerequisite_template_id=target_id,
                    created_by_id=actor_id,
                )
            )
    session.flush()
    return result


def _unknown_markets(catalog: Catalog, codes: list[str]) -> list[str]:
    known = {market.code for market in catalog.markets}
    return [code for code in codes if code not in known]


def apply_t1(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """선택한 시장의 T1 초안을 투입한다 — 시장 없으면 등록, 있는 템플릿은 건너뜀."""
    catalog = load_catalog()
    requested = [str(code).strip().upper() for code in payload["markets"]]
    unknown = _unknown_markets(catalog, requested)
    if unknown:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"markets": f"카탈로그에 없는 시장입니다: {', '.join(unknown)}"},
            log_context={"unknown": unknown},
        )
    ordered = list(dict.fromkeys(requested))
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=APPLY_ENDPOINT,
            key=idempotency_key,
            request_body={"markets": ordered},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _APPLY_LOCK_KEY})
        results = [
            _apply_market(session, actor_id=actor.id, market=catalog.market(code))  # type: ignore[arg-type]
            for code in ordered
        ]
        body = {"version": catalog.version, "results": [asdict(item) for item in results]}
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body
