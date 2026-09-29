"""시장 준비도 매트릭스 — 사실 수집과 조립 (DESIGN.md §5.3 / S2-3 PR-3 안건 ①).

색을 정하는 규칙은 `rules.py`(순수 함수)이고, 이 파일은 그 규칙에 먹일 **사실**을
DB에서 모아 온다. 한 화면(SKU 50행 × 시장 N열)을 그리는 데 쓰는 질의는 행 수와
무관한 고정 개수다(N+1 금지 — §18.4): SKU 쪽, 세트 구성, 제품, 요건 세트, 인증
인스턴스, 시장 — 각 한 번씩.

★ **아무것도 저장하지 않는다.** 이 모듈은 쓰기 경로가 없다(session.add·insert·
  update·delete·outbox 발행 0 — 아키텍처 테스트가 소스를 스캔해 고정한다). 집계를
  저장하면 "만료일을 고쳤는데 매트릭스가 어제 값"이라는 재계산 버그의 한 부류가
  통째로 생긴다 — 계산값 미저장이 스펙 자체다(§5.3 "저장하지 않고 계산").

■ 필수 요건 집합 (판정 요청 2 (나) — ADR-0047)

  대상 단위 4축이 모집합이다. SKU·제품 축은 §4.8 **자동 적용과 같은 축**이라
  (ADR-0042) 매트릭스가 "자동 생성된 인스턴스가 미착수로 남아 있다"를 그대로
  🔴로 읽는다 — 축이 다르면 구조적으로 영원히 🔴이거나 영원히 ⚪인 칸이 생긴다.
    SKU 요건      = 그 SKU **자신의** 품목군 세트 중 적용단위 SKU
    PRODUCT 요건  = **소속 제품 자신의** 품목군 세트 중 적용단위 PRODUCT
    COMPANY 요건  = 위 두 품목군 세트의 적용단위 COMPANY — 자사 인스턴스(target 없음)와 대조
    FACILITY 요건 = 위 두 품목군 세트의 적용단위 FACILITY — SKU의 제조사 파트너 인스턴스와
                    대조. **제조사 미지정이면 🔴(fail-closed)** — 확인할 대상이 없는 것을
                    "해당 없음"으로 읽으면 🟢 과신이 된다.
  INGREDIENT 단위는 포함하지 않는다(관찰 등재 — 성분별 요건 모집합은 성분×제품 전개가
  필요하고 S2-3 범위 밖이다). 모집합 밖 축은 범례가 공개한다(조건 A — `SCOPE_NOTE`).
  세트 SKU는 자기 요건(SKU·COMPANY 축)에 **구성품 롤업**을 더한다(§4.2).

  요건으로 세는 템플릿은 **확정(CONFIRMED)** 뿐이다 — 초안은 검증되지 않은 정의이고
  폐기(RETIRED)는 더 이상 요구되지 않는 요건이다. 인스턴스는 **활성**(삭제 아님·
  종결 2태 아님) 하나만 본다 — 반려·중단만 남은 대상은 "활성 인스턴스 부재"(🔴)다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.time import today_kst
from app.modules.catalog.models import Product, SetComponent, Sku
from app.modules.certifications.machine import TERMINAL_STATUSES
from app.modules.certifications.models import Certification
from app.modules.markets.models import Market
from app.modules.readiness.rules import (
    AXES,
    CellSummary,
    InstanceFacts,
    Requirement,
    RequirementResult,
    evaluate_requirement,
    merge_component_results,
    summarize,
)
from app.modules.requirements.models import ItemProfileRequirementTemplate, RequirementTemplate

#: 모집합에 드는 적용단위 — INGREDIENT는 제외(모듈 독스트링).
_COUNTED_APPLIES_TO = AXES


@dataclass(frozen=True, slots=True)
class MarketColumn:
    id: int
    code: str
    name_ko: str


@dataclass(frozen=True, slots=True)
class MatrixCellView:
    market_id: int
    market_code: str
    summary: CellSummary
    items: list[RequirementResult]


@dataclass(frozen=True, slots=True)
class ComponentRef:
    sku_id: int
    sku_code: str


@dataclass(frozen=True, slots=True)
class MatrixRowView:
    sku_id: int
    sku_code: str
    name_ko: str
    kind: str
    status: str
    item_profile_id: int | None
    cells: list[MatrixCellView]
    components: list[ComponentRef]


@dataclass(frozen=True, slots=True)
class MatrixView:
    rows: list[MatrixRowView]
    total: int
    markets: list[MarketColumn]
    as_of: date


@dataclass(frozen=True, slots=True)
class _TemplateRef:
    template_id: int
    market_id: int
    applies_to: str
    name: str


@dataclass(slots=True)
class _Facts:
    """한 화면을 그리는 데 필요한 사실 전부 — 질의 결과를 한 번에 모아 둔다."""

    skus: dict[int, Sku]
    #: 세트 SKU id → 구성품 SKU id(구성 순).
    components: dict[int, list[int]]
    #: 제품 id → 제품의 품목군 id.
    product_profile: dict[int, int | None]
    #: 품목군 id → 그 세트의 (확정·모집합) 템플릿.
    templates: dict[int, list[_TemplateRef]]
    #: (템플릿, 대상 유형, 대상 id|None) → 활성 인스턴스.
    instances: dict[tuple[int, str, int | None], InstanceFacts]


# ── 사실 수집 ────────────────────────────────────────────────────────────────


def _load_markets(session: Session) -> list[MarketColumn]:
    rows = session.execute(
        select(Market.id, Market.code, Market.name_ko)
        .where(Market.deleted_at.is_(None))
        .order_by(Market.code)
    ).all()
    return [MarketColumn(id=row.id, code=row.code, name_ko=row.name_ko) for row in rows]


def _sku_page(
    session: Session,
    *,
    offset: int,
    limit: int,
    q: str | None,
    item_profile_id: int | None,
    kind: str | None,
) -> tuple[list[Sku], int]:
    conditions: list[ColumnElement[bool]] = [Sku.deleted_at.is_(None)]
    if q:
        # autoescape — 사용자가 친 %·_가 와일드카드로 해석되지 않게 한다.
        conditions.append(
            or_(
                Sku.sku_code.icontains(q, autoescape=True),
                Sku.name_ko.icontains(q, autoescape=True),
            )
        )
    if item_profile_id is not None:
        conditions.append(Sku.item_profile_id == item_profile_id)
    if kind is not None:
        conditions.append(Sku.kind == kind)
    total = session.execute(select(func.count()).select_from(Sku).where(*conditions)).scalar_one()
    rows = list(
        session.execute(
            select(Sku)
            .where(*conditions)
            .order_by(Sku.sku_code, Sku.id)
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return rows, total


def _load_facts(session: Session, page_skus: Sequence[Sku]) -> _Facts:
    skus: dict[int, Sku] = {sku.id: sku for sku in page_skus}

    # 세트 → 구성품 (한 번). 삭제된 구성품 SKU는 롤업에서 뺀다.
    set_ids = [sku.id for sku in page_skus if sku.kind == "SET"]
    components: dict[int, list[int]] = defaultdict(list)
    if set_ids:
        links = session.execute(
            select(SetComponent.set_sku_id, SetComponent.component_sku_id)
            .where(SetComponent.set_sku_id.in_(set_ids), SetComponent.deleted_at.is_(None))
            .order_by(SetComponent.id)
        ).all()
        component_ids = {link.component_sku_id for link in links} - skus.keys()
        if component_ids:
            for sku in session.execute(
                select(Sku).where(Sku.id.in_(component_ids), Sku.deleted_at.is_(None))
            ).scalars():
                skus[sku.id] = sku
        for link in links:
            if link.component_sku_id in skus:
                components[link.set_sku_id].append(link.component_sku_id)

    # 제품 → 품목군 (한 번).
    product_ids = {sku.product_id for sku in skus.values() if sku.product_id is not None}
    product_profile: dict[int, int | None] = {}
    if product_ids:
        product_profile = {
            row.id: row.item_profile_id
            for row in session.execute(
                select(Product.id, Product.item_profile_id).where(
                    Product.id.in_(product_ids), Product.deleted_at.is_(None)
                )
            )
        }

    # 품목군 → 요건 세트(확정·모집합 축) (한 번).
    profile_ids = {sku.item_profile_id for sku in skus.values() if sku.item_profile_id is not None}
    profile_ids |= {pid for pid in product_profile.values() if pid is not None}
    templates: dict[int, list[_TemplateRef]] = defaultdict(list)
    if profile_ids:
        for row in session.execute(
            select(
                ItemProfileRequirementTemplate.item_profile_id,
                RequirementTemplate.id,
                RequirementTemplate.market_id,
                RequirementTemplate.applies_to,
                RequirementTemplate.name,
            )
            .join(
                RequirementTemplate,
                RequirementTemplate.id == ItemProfileRequirementTemplate.requirement_template_id,
            )
            .where(
                ItemProfileRequirementTemplate.item_profile_id.in_(profile_ids),
                ItemProfileRequirementTemplate.deleted_at.is_(None),
                RequirementTemplate.deleted_at.is_(None),
                RequirementTemplate.status == "CONFIRMED",
                RequirementTemplate.applies_to.in_(_COUNTED_APPLIES_TO),
            )
            .order_by(RequirementTemplate.id)
        ):
            templates[row.item_profile_id].append(
                _TemplateRef(
                    template_id=row.id,
                    market_id=row.market_id,
                    applies_to=row.applies_to,
                    name=row.name,
                )
            )

    instances = _load_instances(session, skus, product_ids, templates)
    return _Facts(
        skus=skus,
        components=dict(components),
        product_profile=product_profile,
        templates=dict(templates),
        instances=instances,
    )


def _load_instances(
    session: Session,
    skus: dict[int, Sku],
    product_ids: set[int],
    templates: dict[int, list[_TemplateRef]],
) -> dict[tuple[int, str, int | None], InstanceFacts]:
    """활성 인스턴스 — 이 화면의 요건 템플릿과 대상에 걸리는 것만 한 번에."""
    template_ids = {ref.template_id for refs in templates.values() for ref in refs}
    if not template_ids:
        return {}
    manufacturer_ids = {
        sku.manufacturer_partner_id for sku in skus.values() if sku.manufacturer_partner_id
    }
    targets: list[ColumnElement[bool]] = [
        and_(Certification.target_type == "SKU", Certification.target_id.in_(list(skus))),
        Certification.target_type == "COMPANY",
    ]
    if product_ids:
        targets.append(
            and_(Certification.target_type == "PRODUCT", Certification.target_id.in_(product_ids))
        )
    if manufacturer_ids:
        targets.append(
            and_(
                Certification.target_type == "FACILITY",
                Certification.target_id.in_(manufacturer_ids),
            )
        )
    found: dict[tuple[int, str, int | None], InstanceFacts] = {}
    # id 오름차순 — 활성 유니크가 대상당 1건을 보장하지만, 어긋난 데이터가 있어도
    # 결정적이도록 가장 최근(큰 id)이 이긴다.
    for row in session.execute(
        select(
            Certification.id,
            Certification.template_id,
            Certification.target_type,
            Certification.target_id,
            Certification.status,
            Certification.expires_on,
            Certification.renewal_lead_days,
        )
        .where(
            Certification.template_id.in_(template_ids),
            Certification.deleted_at.is_(None),
            Certification.status.not_in(sorted(TERMINAL_STATUSES)),
            or_(*targets),
        )
        .order_by(Certification.id)
    ):
        found[(row.template_id, row.target_type, row.target_id)] = InstanceFacts(
            certification_id=row.id,
            status=row.status,
            expires_on=row.expires_on,
            renewal_lead_days=row.renewal_lead_days,
        )
    return found


# ── 필수 요건 도출 ───────────────────────────────────────────────────────────


def required_items(sku: Sku, facts: _Facts) -> list[tuple[int, Requirement]]:
    """이 SKU 한 행의 필수 요건 — (시장 id, 요건). 도출 규칙은 모듈 독스트링."""
    own_profile = sku.item_profile_id
    product_profile = (
        facts.product_profile.get(sku.product_id) if sku.product_id is not None else None
    )
    is_set = sku.kind == "SET"
    items: list[tuple[int, Requirement]] = []

    for ref in facts.templates.get(own_profile, []) if own_profile is not None else []:
        if ref.applies_to == "SKU":
            items.append(
                (ref.market_id, Requirement(ref.template_id, ref.name, "SKU", "SKU", sku.id))
            )
    if sku.product_id is not None and product_profile is not None:
        for ref in facts.templates.get(product_profile, []):
            if ref.applies_to == "PRODUCT":
                items.append(
                    (
                        ref.market_id,
                        Requirement(
                            ref.template_id, ref.name, "PRODUCT", "PRODUCT", sku.product_id
                        ),
                    )
                )

    # 기업·시설 축은 두 품목군 세트의 합집합이다(같은 템플릿이 양쪽에 있으면 1건).
    seen: set[int] = set()
    for profile in (own_profile, product_profile):
        if profile is None:
            continue
        for ref in facts.templates.get(profile, []):
            if ref.template_id in seen:
                continue
            if ref.applies_to == "COMPANY":
                seen.add(ref.template_id)
                items.append(
                    (
                        ref.market_id,
                        Requirement(ref.template_id, ref.name, "COMPANY", "COMPANY", None),
                    )
                )
            elif ref.applies_to == "FACILITY" and not is_set:
                # 세트는 제조사가 없다 — 시설 요건은 구성품 롤업이 진다.
                seen.add(ref.template_id)
                manufacturer = sku.manufacturer_partner_id
                items.append(
                    (
                        ref.market_id,
                        Requirement(
                            ref.template_id,
                            ref.name,
                            "FACILITY",
                            "FACILITY",
                            manufacturer,
                            target_missing=manufacturer is None,
                        ),
                    )
                )
    return items


def _evaluate(sku: Sku, facts: _Facts, base_date: date) -> dict[int, list[RequirementResult]]:
    """SKU 한 행의 요건 결과 — 시장 id별. 자기 요건만(세트 롤업은 호출자)."""
    per_market: dict[int, list[RequirementResult]] = defaultdict(list)
    for market_id, requirement in required_items(sku, facts):
        instance = (
            None
            if requirement.target_missing
            else facts.instances.get(
                (requirement.template_id, requirement.target_type, requirement.target_id)
            )
        )
        per_market[market_id].append(
            evaluate_requirement(requirement, instance, base_date=base_date)
        )
    axis_order = {axis: index for index, axis in enumerate(AXES)}
    for results in per_market.values():
        results.sort(key=lambda item: (axis_order[item.axis], item.template_id))
    return per_market


def _build_row(
    sku: Sku,
    facts: _Facts,
    markets: Sequence[MarketColumn],
    base_date: date,
    cache: dict[int, dict[int, list[RequirementResult]]],
) -> MatrixRowView:
    def evaluated(target: Sku) -> dict[int, list[RequirementResult]]:
        if target.id not in cache:
            cache[target.id] = _evaluate(target, facts, base_date)
        return cache[target.id]

    own = evaluated(sku)
    component_ids = facts.components.get(sku.id, []) if sku.kind == "SET" else []
    component_refs = [
        ComponentRef(sku_id=cid, sku_code=facts.skus[cid].sku_code) for cid in component_ids
    ]

    cells: list[MatrixCellView] = []
    for market in markets:
        results = own.get(market.id, [])
        if component_ids:
            results = merge_component_results(
                results,
                [
                    (cid, facts.skus[cid].sku_code, evaluated(facts.skus[cid]).get(market.id, []))
                    for cid in component_ids
                ],
            )
        cells.append(
            MatrixCellView(
                market_id=market.id,
                market_code=market.code,
                summary=summarize(results),
                items=results,
            )
        )
    return MatrixRowView(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        name_ko=sku.name_ko,
        kind=sku.kind,
        status=sku.status,
        item_profile_id=sku.item_profile_id,
        cells=cells,
        components=component_refs,
    )


# ── 공개 진입점 ──────────────────────────────────────────────────────────────


def get_matrix(
    *,
    offset: int,
    limit: int,
    q: str | None = None,
    item_profile_id: int | None = None,
    kind: str | None = None,
    base_date: date | None = None,
) -> MatrixView:
    """SKU 한 쪽 × 전 시장의 준비도 — 읽기 전용·계산값.

    기준일 기본은 KST 오늘(§22 렌즈 6). `base_date`는 테스트·관통에서 시간을 고정하는
    통로이고 API에는 노출하지 않는다(임의 날짜로 계산한 값을 화면이 오늘 것으로
    오인하지 않게).
    """
    as_of = base_date or today_kst()
    with unit_of_work() as uow:
        session = uow.session
        markets = _load_markets(session)
        page_skus, total = _sku_page(
            session, offset=offset, limit=limit, q=q, item_profile_id=item_profile_id, kind=kind
        )
        facts = _load_facts(session, page_skus)
        cache: dict[int, dict[int, list[RequirementResult]]] = {}
        rows = [_build_row(sku, facts, markets, as_of, cache) for sku in page_skus]
    return MatrixView(rows=rows, total=total, markets=markets, as_of=as_of)
