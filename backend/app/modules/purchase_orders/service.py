"""구매 발주서(PO) 서비스 — 생성 착지·미리보기·FREE 메타·조회·CSV (S3-1 ADR-0057·0052·0053 / design-A A12 / design-F F2~F5 / design-B B2·B8).

L1: 이 모듈은 **전이를 하지 않는다**(공급사 확인[OC]·취소는 trade_chain L2 — 전이 통로는 transition.py 하나). 여기서 하는 일은
(1) **`create_purchase_order` — PO 생성의 단일 착지이자 발주 확정 통로**(생성 = 발행 = 동결), (2) 비저장 `preview`, (3) FREE 열 메타 편집, (4) 조회·CSV다.

■ **자동 생성 경로는 없다**(§15 L3 4금 ① — 발주 확정은 사람 1클릭). `create_purchase_order`의 호출처는 이 모듈의 라우터 1곳이고
  스케줄러·CLI·outbox 핸들러·imports·order_intake·order_board·approvals·gates 어디서도 임포트하지 않는다(test_no_auto_confirm_code_path_exists).
■ 생성 트랜잭션 순서(잠금 순서 §2.9): 멱등 claim → 공급사 행 `FOR KEY SHARE`(유형 검증 — 유형 해제 임포트[FOR UPDATE]와 직렬화, 여신 NO KEY UPDATE와는 비충돌)
  → (복제면) 원본 PO `FOR UPDATE` → 검증·라인 구성(단가: 미입력=`price_at(PURCHASE, 증빙일)`·입력=MANUAL) → **채번(마지막)** → INSERT+탄생 이력+created 이벤트.
■ **원가 마스킹**(ADR-0057 ⑤·ADR-0024): 서비스는 `include_cost`로 응답 dict에서 원가 키를 **아예 만들지 않고**(키 삭제 아님), 라우터가 역할별 스키마로 나간다.
  원가 값은 에러 detail·로그·예외 문구·이벤트 payload·audit에 싣지 않는다(이름 기반 로그 마스킹은 값 추론을 막지 못한다).
■ 감사: 전표 생성·전이·편집은 audit_log에 기록하지 않는다(상태이력+아웃박스가 정본 — X-24). 원가 전후 값이 기록될 곳이 구조적으로 없다.
■ 마스터(SKU·매입가)를 읽는 곳은 `trade_docs.lines`(SKU 검사)와 여기의 `price_at` 호출뿐이고 `price_type="PURCHASE"` 리터럴은 이 모듈 밖에 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError, NotFoundError
from app.core.money import minor_units, parse_minor_amount
from app.core.time import today_kst
from app.modules.catalog.pricing import may_see_cost, price_at
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import Role, User, UserRole
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners import service as partners
from app.modules.purchase_orders.models import PurchaseOrder, PurchaseOrderLine
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import MAX_SAFE_INTEGER, DocKind, PoKind, PriceBasis
from app.modules.trade_docs.doc_number import issue_document_number
from app.modules.trade_docs.fx import require_known_currency, resolve_fx
from app.modules.trade_docs.incoterms import Incoterm, build_incoterm
from app.modules.trade_docs.lines import require_sellable_sku
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import DEAD_STATUSES, STATUSES
from app.modules.trade_docs.models import PurchaseOrderStatusLog
from app.modules.trade_docs.payment_terms import (
    PaymentTerms,
    advance_pct_text,
    build_payment_terms,
    require_lc_enabled,
)
from app.modules.trade_docs.snapshot import validate_quantity
from app.modules.trade_docs.transition import record_birth
from app.modules.trade_docs.validation import (
    blank_to_none,
    check_doc_date,
    invalid,
    require_active_user,
)
from app.modules.trade_docs.views import created_date_kst, money_text, rate_text

KIND = DocKind.PURCHASE_ORDER
PO_CREATE_ENDPOINT = "POST /api/v1/purchase-orders"
EXPORT_MAX_ROWS = 50_000

#: 매입가 가격 종류 — `price_type="PURCHASE"` 리터럴은 이 모듈 밖에 두지 않는다(K 스캔).
_PURCHASE_PRICE_TYPE = "PURCHASE"

#: PO의 죽은 상태 — 공용 `DEAD_STATUSES`(CANCELLED·EXPIRED)에서 **PO 상태 집합에 있는 것만** 파생한다(PO에는 EXPIRED가 없다 — 하드코딩 금지).
PO_DEAD_STATUSES: tuple[str, ...] = tuple(s for s in DEAD_STATUSES if s in STATUSES[KIND])

#: 담당자가 될 수 있는 역할 — PO를 고칠 수 있는(발주·전이 권한) 역할이다.
ASSIGNEE_ROLES = (
    "TRADE",
    "ADMIN",
)  # 역할 코드 문자열 — 원가 노출 판정(may_see_cost)과 별개의 업무 규칙이라 RoleCode 직접 비교 금지 스캔을 피해 코드값으로 둔다

_FREE_FIELDS = ("internal_note", "assignee_id", "oc_received_on", "oc_reference")
_OC_FIELDS = ("oc_received_on", "oc_reference")


def may_see_po_cost(actor: AuthenticatedUser) -> bool:
    """PO 원가 노출 판정 — 매입가와 **같은 역할 목록**(`pricing.may_see_cost`)이 단일 출처다. 호출은 라우터 경계 1곳(+쓰기 서비스의 방어 가드)뿐."""
    return may_see_cost(actor)


# ── 계획(생성·미리보기 공용 검증) ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewPoLine:
    """생성 트랜잭션이 넣을 PO 라인 한 줄 — SKU 스냅샷·수량·서버가 정한 단가/기준."""

    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    requested_delivery_date: date | None
    unit_cost: int
    line_cost: int
    price_basis: str


@dataclass(frozen=True, slots=True)
class PoPlan:
    """검증을 마친 생성 계획 — 미리보기와 생성이 **같은 함수**(`_plan`)로 만든다(조용한 분기 방지)."""

    supplier_partner_id: int
    supplier_name: str
    po_kind: str
    currency: str
    doc_date: date
    fx_rate: Decimal
    fx_rate_date: date
    payment: PaymentTerms
    incoterm: Incoterm
    internal_note: str | None
    assignee_id: int
    copied_from_id: int | None
    lines: list[NewPoLine]
    total_cost: int


def _parse_unit_cost(raw: object, currency: str, field: str) -> int:
    """사람 표기 단가 → 최소단위. 자릿수 초과·형식 오류는 422(값은 메시지에 싣지 않는다), 0 이하는 거부(무상 매입 미지원)."""
    try:
        unit = parse_minor_amount(raw, currency, field=field, max_digits=16)
    except ValueError as exc:
        raise invalid(field, str(exc).split(": ", 1)[-1]) from None
    if unit <= 0:
        raise invalid(field, "매입 단가는 0보다 커야 합니다(무상 매입은 지원하지 않습니다).")
    if unit > MAX_SAFE_INTEGER:
        raise invalid(field, "매입 단가가 너무 큽니다.")
    return unit


def _master_unit_cost(sku_id: int, currency: str, doc_date: date, field: str) -> int:
    """마스터 매입가 — 증빙일 기준 `price_at(PURCHASE)`. 부재는 **0·NULL로 대체하지 않고** 422 NOT_EFFECTIVE(fail-visible)다."""
    try:
        money = price_at(
            sku_id=sku_id, price_type=_PURCHASE_PRICE_TYPE, currency=currency, on=doc_date
        )
    except AppError as exc:
        if exc.code == ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE:
            raise AppError(
                ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE,
                detail={
                    field: f"{doc_date.isoformat()} 기준으로 적용되는 {currency} 매입 단가가 없습니다. "
                    "마스터에 매입 단가를 등록하거나 단가를 직접 입력해 주세요."
                },
                log_context={"sku_id": sku_id, "currency": currency},
            ) from None
        raise
    if money.amount <= 0:
        raise invalid(field, "마스터 매입 단가가 0원입니다. 단가를 직접 입력해 주세요.")
    return int(money.amount)


def _line_cost(quantity: int, unit_cost: int, field: str) -> int:
    cost = quantity * unit_cost
    if cost > MAX_SAFE_INTEGER:
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE,
            detail={field: "라인 금액이 허용 범위를 넘었습니다."},
        )
    return cost


def build_po_lines(
    session: Session, *, currency: str, doc_date: date, lines_in: list[dict[str, Any]]
) -> list[NewPoLine]:
    """라인 입력 → 스냅샷+단가. 같은 SKU 중복은 409, 삭제·단종 SKU는 422, 요청납기는 증빙일 이후여야 한다."""
    out: list[NewPoLine] = []
    seen: set[int] = set()
    for index, raw in enumerate(lines_in):
        prefix = f"lines[{index}]."
        sku = require_sellable_sku(session, raw["sku_id"], KIND, field=f"{prefix}sku_id")
        if sku.id in seen:
            raise AppError(
                ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={f"{prefix}sku_id": sku.sku_code}
            )
        seen.add(sku.id)
        quantity = validate_quantity(raw["quantity"], field=f"{prefix}quantity")
        if raw.get("unit_cost") is not None:
            unit = _parse_unit_cost(raw["unit_cost"], currency, f"{prefix}unit_cost")
            basis = PriceBasis.MANUAL.value  # 마스터 값과 같아도 MANUAL — 입력 경로가 기준이다
        else:
            unit = _master_unit_cost(sku.id, currency, doc_date, f"{prefix}unit_cost")
            basis = PriceBasis.MASTER.value
        delivery = raw.get("requested_delivery_date")
        if delivery is not None and delivery < doc_date:
            raise invalid(f"{prefix}requested_delivery_date", "요청납기는 증빙일 이후여야 합니다.")
        out.append(
            NewPoLine(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name_ko=sku.name_ko,
                sku_name_en=sku.name_en,
                sku_kind=sku.kind,
                quantity=quantity,
                requested_delivery_date=delivery,
                unit_cost=unit,
                line_cost=_line_cost(quantity, unit, f"{prefix}unit_cost"),
                price_basis=basis,
            )
        )
    return out


def has_live_copy(session: Session, source_id: int) -> bool:
    """원본에서 복제된 **살아 있는** PO가 이미 있는가(취소된 복제본은 세지 않는다 — X-08)."""
    return bool(
        session.execute(
            select(func.count())
            .select_from(PurchaseOrder)
            .where(
                PurchaseOrder.copied_from_id == source_id,
                PurchaseOrder.deleted_at.is_(None),
                PurchaseOrder.status.notin_(PO_DEAD_STATUSES),
            )
        ).scalar_one()
    )


def _check_copy_source(
    session: Session, source_id: int, supplier_partner_id: int, *, lock: bool
) -> None:
    """복제 원본 자격 — 같은 유형(PO)·같은 공급사·**원본 상태 ∈ {CANCELLED, EXPIRED}**(살아 있는 전표 복제=중복 발주 차단).

    생성 경로는 원본 행을 `FOR UPDATE`로 잠가 같은 원본을 동시에 복제하는 두 요청을 직렬화한다(뒤 요청은 앞 커밋 후 살아 있는 복제본을 보고 409).
    """
    query = select(PurchaseOrder).where(
        PurchaseOrder.id == source_id, PurchaseOrder.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    source = session.execute(query).scalar_one_or_none()
    if (
        source is None
        or source.supplier_partner_id != supplier_partner_id
        or source.status not in PO_DEAD_STATUSES
        or has_live_copy(session, source_id)
    ):
        raise AppError(
            ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE, log_context={"source_id": source_id}
        )


def _plan(
    session: Session, *, actor: AuthenticatedUser, payload: dict[str, Any], lock: bool
) -> PoPlan:
    """요청 본문 → 검증된 생성 계획. `lock=True`(생성)는 공급사·복제 원본을 잠그고, `lock=False`(미리보기)는 읽기만 한다."""
    currency = require_known_currency(payload["currency"])
    doc_date = payload.get("doc_date") or today_kst()
    check_doc_date(doc_date)

    try:
        po_kind = PoKind(payload.get("po_kind", PoKind.PURCHASE.value)).value
    except ValueError:
        raise invalid("po_kind", "발주 구분을 확인해 주세요.") from None
    # 공급사 유형 검증(생성 1회 — F3): PURCHASE=SUPPLIER∨OEM, OEM_PRODUCTION=OEM 필수. FOR KEY SHARE(lock=True).
    partner = partners.require_partner_of_any_type(
        session,
        payload["supplier_partner_id"],
        ("OEM",) if po_kind == PoKind.OEM_PRODUCTION.value else ("SUPPLIER", "OEM"),
        field="supplier_partner_id",
        type_label="OEM" if po_kind == PoKind.OEM_PRODUCTION.value else "공급사 또는 OEM",
        lock=lock,
    )

    copied_from = payload.get("copied_from_id")
    if copied_from is not None:
        _check_copy_source(session, copied_from, partner.id, lock=lock)

    supplier_name = blank_to_none(payload.get("supplier_name")) or (
        partner.name_en or partner.name_ko
    )

    rate, rate_date = resolve_fx(
        currency, payload.get("fx_rate"), payload.get("fx_rate_date"), doc_date
    )
    terms = build_payment_terms(payload.get("payment_terms"), allow_receipt_anchor=True)
    require_lc_enabled(session, terms)
    incoterm = build_incoterm(payload.get("incoterm"))

    assignee_id = payload.get("assignee_id") or actor.id
    require_assignee(session, assignee_id)

    lines_in = payload.get("lines") or []
    lines = build_po_lines(session, currency=currency, doc_date=doc_date, lines_in=lines_in)
    editing.require_line_capacity(0, len(lines))

    # 완결성 — 생성=발행=동결이라 동결 시 필수 값이 전부 있어야 한다(없으면 500이 아니라 422로 안내).
    missing: dict[str, str] = {}
    if terms is None:
        missing["payment_terms"] = "결제조건을 입력해 주세요."
    if incoterm is None:
        missing["incoterm"] = "Incoterms를 입력해 주세요."
    if rate is None or rate_date is None:
        missing["fx_rate"] = "환율을 입력해 주세요."
    if not lines:
        missing["lines"] = "라인을 1개 이상 추가해 주세요."
    if missing:
        raise AppError(ErrorCode.TRADE_DOCS_DOCUMENT_INCOMPLETE, detail=missing)
    assert terms is not None and incoterm is not None and rate is not None and rate_date is not None

    return PoPlan(
        supplier_partner_id=partner.id,
        supplier_name=supplier_name,
        po_kind=po_kind,
        currency=currency,
        doc_date=doc_date,
        fx_rate=rate,
        fx_rate_date=rate_date,
        payment=terms,
        incoterm=incoterm,
        internal_note=blank_to_none(payload.get("internal_note")),
        assignee_id=assignee_id,
        copied_from_id=copied_from,
        lines=lines,
        total_cost=editing.compute_total([line.line_cost for line in lines]),
    )


def require_assignee(session: Session, user_id: int, *, field: str = "assignee_id") -> None:
    """PO 담당자 — **활성 사용자이면서 TRADE 또는 ADMIN 역할 보유자**(VIEWER·역할 없음은 422). PO에만 적용한다(QT·PI·SO는 활성 여부만 본다 — PROGRESS 미결)."""
    require_active_user(session, user_id, field=field)
    has_role = session.execute(
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id == user_id,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            Role.code.in_(ASSIGNEE_ROLES),
        )
        .limit(1)
    ).scalar_one_or_none()
    if has_role is None:
        raise invalid(field, "발주를 처리할 수 있는 담당자(무역 또는 관리자)를 선택해 주세요.")


def _require_cost_role(actor: AuthenticatedUser) -> None:
    """쓰기 서비스의 방어 가드 — 원가를 볼 수 없는 역할은 라우터 게이트를 우회해도 원가를 만들거나 받아 볼 수 없다(층 이중화)."""
    if not may_see_po_cost(actor):
        raise ForbiddenError()


# ── 생성 착지 ───────────────────────────────────────────────────────────────


def insert_issued(session: Session, *, actor_id: int, plan: PoPlan) -> PurchaseOrder:
    """PO 1건 INSERT — 채번은 **마지막 단계**(모든 검증·라인 구성 뒤, INSERT 직전). 생성 = 발행 = 동결.

    헤더 열은 전부 **명시 키워드**로 넣는다(`**dict` 전개 없음 — 전표 쓰기 통로 스캔의 허용 항목이 `total_cost`·`doc_number` 두 열뿐이 되게).
    """
    number = issue_document_number(session, KIND)  # 잠금 순서 (9) — 항상 마지막
    row = PurchaseOrder(
        doc_number=number,
        total_cost=plan.total_cost,
        last_line_no=len(plan.lines),
        created_by_id=actor_id,
        updated_by_id=actor_id,
        supplier_partner_id=plan.supplier_partner_id,
        supplier_name=plan.supplier_name,
        po_kind=plan.po_kind,
        currency=plan.currency,
        doc_date=plan.doc_date,
        fx_rate=plan.fx_rate,
        fx_rate_date=plan.fx_rate_date,
        payment_type=plan.payment.payment_type,
        advance_pct_bp=plan.payment.advance_pct_bp,
        balance_anchor=plan.payment.balance_anchor,
        balance_days=plan.payment.balance_days,
        incoterm_code=plan.incoterm.code,
        incoterm_place=plan.incoterm.place,
        incoterm_year=plan.incoterm.year,
        internal_note=plan.internal_note,
        assignee_id=plan.assignee_id,
        copied_from_id=plan.copied_from_id,
    )
    record_birth(
        session, row, actor_user_id=actor_id
    )  # status 대입·INSERT·탄생 이력·created 이벤트
    for line_no, item in enumerate(plan.lines, start=1):
        session.add(
            PurchaseOrderLine(
                po_id=row.id,
                currency=row.currency,
                line_no=line_no,
                sku_id=item.sku_id,
                sku_code=item.sku_code,
                sku_name_ko=item.sku_name_ko,
                sku_name_en=item.sku_name_en,
                sku_kind=item.sku_kind,
                quantity=item.quantity,
                requested_delivery_date=item.requested_delivery_date,
                unit_cost=item.unit_cost,
                line_cost=item.line_cost,
                price_basis=item.price_basis,
                created_by_id=actor_id,
                updated_by_id=actor_id,
            )
        )
    session.flush()
    return row


def create_purchase_order(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """PO 생성 = 발행 = **발주 확정(사람 1클릭 + Idempotency-Key)** — 이 함수가 발주 확정의 유일한 통로다.

    응답(Full)은 생성자 스코프(actor×endpoint×key)로 24시간 저장된다 — 원가가 at-rest로 남는 점은 ADR-0057의 수용 사실이다.
    """
    _require_cost_role(actor)
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PO_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        plan = _plan(session, actor=actor, payload=payload, lock=True)
        row = insert_issued(session, actor_id=actor.id, plan=plan)
        body = detail_body(session, row, include_cost=True)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def preview_purchase_order(*, actor: AuthenticatedUser, payload: dict[str, Any]) -> dict[str, Any]:
    """비저장 미리보기 — 생성과 **같은 검증·단가 계산**(`_plan`)을 읽기만으로 수행한다. 채번·이벤트·멱등 키 소비·잠금이 없다."""
    _require_cost_role(actor)
    with unit_of_work() as uow:
        session = uow.session
        plan = _plan(session, actor=actor, payload=payload, lock=False)
        cur = plan.currency
        return {
            "doc_date": plan.doc_date.isoformat(),
            "po_kind": plan.po_kind,
            "supplier_partner_id": plan.supplier_partner_id,
            "supplier_name": plan.supplier_name,
            "currency": cur,
            "minor_units": minor_units(cur),
            "fx_rate": rate_text(plan.fx_rate),
            "fx_rate_date": plan.fx_rate_date.isoformat(),
            "payment_terms": {
                "payment_type": plan.payment.payment_type,
                "advance_pct": advance_pct_text(plan.payment.advance_pct_bp),
                "advance_pct_bp": plan.payment.advance_pct_bp,
                "balance_anchor": plan.payment.balance_anchor,
                "balance_days": plan.payment.balance_days,
            },
            "incoterm": {
                "code": plan.incoterm.code,
                "place": plan.incoterm.place,
                "year": plan.incoterm.year,
            },
            "internal_note": plan.internal_note,
            "assignee_id": plan.assignee_id,
            "copied_from_id": plan.copied_from_id,
            "total_cost": plan.total_cost,
            "total_text": money_text(plan.total_cost, cur),
            "lines": [
                {
                    "line_no": number,
                    "sku_id": line.sku_id,
                    "sku_code": line.sku_code,
                    "sku_name_ko": line.sku_name_ko,
                    "sku_name_en": line.sku_name_en,
                    "sku_kind": line.sku_kind,
                    "quantity": line.quantity,
                    "requested_delivery_date": (
                        line.requested_delivery_date.isoformat()
                        if line.requested_delivery_date
                        else None
                    ),
                    "unit_cost": line.unit_cost,
                    "unit_cost_text": money_text(line.unit_cost, cur),
                    "line_cost": line.line_cost,
                    "line_cost_text": money_text(line.line_cost, cur),
                    "price_basis": line.price_basis,
                }
                for number, line in enumerate(plan.lines, start=1)
            ],
        }


# ── 응답 조립 ───────────────────────────────────────────────────────────────


def _line_body(line: PurchaseOrderLine, *, include_cost: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": line.id,
        "line_no": line.line_no,
        "sku_id": line.sku_id,
        "sku_code": line.sku_code,
        "sku_name_ko": line.sku_name_ko,
        "sku_name_en": line.sku_name_en,
        "sku_kind": line.sku_kind,
        "quantity": line.quantity,
        "requested_delivery_date": (
            line.requested_delivery_date.isoformat() if line.requested_delivery_date else None
        ),
    }
    if include_cost:  # 원가 키는 **만들지 않는다**(만든 뒤 지우지 않는다 — ADR-0024)
        cur = line.currency
        body.update(
            {
                "unit_cost": line.unit_cost,
                "unit_cost_text": money_text(line.unit_cost, cur),
                "line_cost": line.line_cost,
                "line_cost_text": money_text(line.line_cost, cur),
                "price_basis": line.price_basis,
            }
        )
    return body


def _summary_body(row: PurchaseOrder, *, include_cost: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row.id,
        "doc_number": row.doc_number,
        "doc_date": row.doc_date.isoformat(),
        "status": row.status,
        "po_kind": row.po_kind,
        "supplier_partner_id": row.supplier_partner_id,
        "supplier_name": row.supplier_name,
        "assignee_id": row.assignee_id,
        "copied_from_id": row.copied_from_id,
        "oc_received_on": row.oc_received_on.isoformat() if row.oc_received_on else None,
        "oc_reference": row.oc_reference,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }
    if include_cost:
        body.update(
            {
                "currency": row.currency,
                "total_cost": row.total_cost,
                "total_text": money_text(row.total_cost, row.currency),
            }
        )
    return body


def detail_body(session: Session, row: PurchaseOrder, *, include_cost: bool) -> dict[str, Any]:
    lines = (
        session.execute(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.po_id == row.id, PurchaseOrderLine.deleted_at.is_(None))
            .order_by(PurchaseOrderLine.line_no)
        )
        .scalars()
        .all()
    )
    body = _summary_body(row, include_cost=include_cost)
    body.update(
        {
            "payment_terms": {
                "payment_type": row.payment_type,
                "advance_pct": advance_pct_text(row.advance_pct_bp),
                "advance_pct_bp": row.advance_pct_bp,
                "balance_anchor": row.balance_anchor,
                "balance_days": row.balance_days,
            },
            "incoterm": {
                "code": row.incoterm_code,
                "place": row.incoterm_place,
                "year": row.incoterm_year,
            },
            "internal_note": row.internal_note,
            "frozen_at": row.frozen_at.isoformat(),
            "last_line_no": row.last_line_no,
            "lines": [_line_body(line, include_cost=include_cost) for line in lines],
        }
    )
    if include_cost:  # 환율·소수 자릿수는 통화를 역추론하게 하므로 원가 권한자에게만 준다
        body["minor_units"] = minor_units(row.currency)
        body["fx_rate"] = rate_text(row.fx_rate)
        body["fx_rate_date"] = row.fx_rate_date.isoformat() if row.fx_rate_date else None
    return body


def require_purchase_order(session: Session, po_id: int) -> PurchaseOrder:
    row = session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"purchase_order_id": po_id})
    return row


# ── 조회 ───────────────────────────────────────────────────────────────────


def get_purchase_order(po_id: int, *, include_cost: bool) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(
            uow.session, require_purchase_order(uow.session, po_id), include_cost=include_cost
        )


def _list_filters(
    *,
    status: str | None,
    supplier_partner_id: int | None,
    po_kind: str | None,
    assignee_id: int | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conditions: list[Any] = [PurchaseOrder.deleted_at.is_(None)]
    if status:
        conditions.append(PurchaseOrder.status == status)
    if supplier_partner_id:
        conditions.append(PurchaseOrder.supplier_partner_id == supplier_partner_id)
    if po_kind:
        conditions.append(PurchaseOrder.po_kind == po_kind)
    if assignee_id:
        conditions.append(PurchaseOrder.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                PurchaseOrder.doc_number.icontains(needle, autoescape=True),
                PurchaseOrder.supplier_name.icontains(needle, autoescape=True),
                # SKU 코드 부분 일치(원가 무관 — 검색은 원가 값을 대상으로 하지 않는다)
                exists().where(
                    PurchaseOrderLine.po_id == PurchaseOrder.id,
                    PurchaseOrderLine.deleted_at.is_(None),
                    PurchaseOrderLine.sku_code.icontains(needle, autoescape=True),
                ),
            )
        )
    if date_from:
        conditions.append(PurchaseOrder.doc_date >= date_from)
    if date_to:
        conditions.append(PurchaseOrder.doc_date <= date_to)
    return conditions


#: 정렬 키 → 열. **원가 열(total_cost 등)은 없다**(ADR-0057 ⑤-1) — 라우터의 Literal과 같은 집합이고 K 테스트가 대사한다.
SORT_COLUMNS: dict[str, Any] = {
    "doc_date": PurchaseOrder.doc_date,
    "doc_number": PurchaseOrder.doc_number,
    "status": PurchaseOrder.status,
    "supplier_name": PurchaseOrder.supplier_name,
    "created_at": PurchaseOrder.created_at,
}


def list_purchase_orders(
    *,
    offset: int,
    limit: int,
    include_cost: bool,
    status: str | None = None,
    supplier_partner_id: int | None = None,
    po_kind: str | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str | None = None,
    descending: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    conditions = _list_filters(
        status=status,
        supplier_partner_id=supplier_partner_id,
        po_kind=po_kind,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    order: list[Any] = []
    if sort is not None:
        column = SORT_COLUMNS[
            sort
        ]  # 화이트리스트 밖은 라우터의 Literal이 이미 422로 걸렀다(KeyError=프로그래밍 오류)
        order.append(column.desc() if descending else column.asc())
    order.append(PurchaseOrder.id.desc() if descending else PurchaseOrder.id.asc())
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(PurchaseOrder).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(PurchaseOrder)
                .where(*conditions)
                .order_by(*order)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_summary_body(row, include_cost=include_cost) for row in rows], total


#: CSV 헤더 상수 2종 — 원가 열 포함(ADMIN·TRADE·LOGISTICS·CERT)·미포함(VIEWER 등). 역할별 **빌더 2개**(한 빌더의 조건 분기가 아니다).
EXPORT_HEADER_WITH_COST: tuple[str, ...] = (
    "발주번호",
    "증빙일",
    "상태",
    "구분",
    "공급사",
    "통화",
    "합계",
    "결제유형",
    "선수금(%)",
    "Incoterms",
    "OC일자",
    "OC참조",
    "담당자",
    "생성일",
)
EXPORT_HEADER_NO_COST: tuple[str, ...] = (
    "발주번호",
    "증빙일",
    "상태",
    "구분",
    "공급사",
    "결제유형",
    "선수금(%)",
    "Incoterms",
    "OC일자",
    "OC참조",
    "담당자",
    "생성일",
)


def _csv_row_with_cost(r: PurchaseOrder, name: str | None) -> tuple[Any, ...]:
    return (
        r.doc_number,
        r.doc_date.isoformat(),
        r.status,
        r.po_kind,
        r.supplier_name,
        r.currency,
        money_text(r.total_cost, r.currency),
        r.payment_type or "",
        advance_pct_text(r.advance_pct_bp) or "",
        f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
        r.oc_received_on.isoformat() if r.oc_received_on else "",
        r.oc_reference or "",
        name or "",
        created_date_kst(r.created_at),
    )


def _csv_row_no_cost(r: PurchaseOrder, name: str | None) -> tuple[Any, ...]:
    return (
        r.doc_number,
        r.doc_date.isoformat(),
        r.status,
        r.po_kind,
        r.supplier_name,
        r.payment_type or "",
        advance_pct_text(r.advance_pct_bp) or "",
        f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
        r.oc_received_on.isoformat() if r.oc_received_on else "",
        r.oc_reference or "",
        name or "",
        created_date_kst(r.created_at),
    )


def export_header(*, include_cost: bool) -> tuple[str, ...]:
    return EXPORT_HEADER_WITH_COST if include_cost else EXPORT_HEADER_NO_COST


def export_rows(
    *,
    include_cost: bool,
    status: str | None = None,
    supplier_partner_id: int | None = None,
    po_kind: str | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[tuple[Any, ...]]:
    """CSV 행(헤더 단위) — 목록과 **같은 필터·권한**. 상한 초과는 422(조용한 잘림 금지). 원가 없는 역할은 원가·통화 열 자체가 없다."""
    conditions = _list_filters(
        status=status,
        supplier_partner_id=supplier_partner_id,
        po_kind=po_kind,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(PurchaseOrder).where(*conditions)
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({total:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        rows = session.execute(
            select(PurchaseOrder, User.display_name)
            .outerjoin(User, User.id == PurchaseOrder.assignee_id)
            .where(*conditions)
            .order_by(PurchaseOrder.id)
        ).all()
        build = _csv_row_with_cost if include_cost else _csv_row_no_cost
        return [build(r, name) for r, name in rows]


def list_status_log(*, po_id: int, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_purchase_order(session, po_id)
        total = session.execute(
            select(func.count())
            .select_from(PurchaseOrderStatusLog)
            .where(PurchaseOrderStatusLog.purchase_order_id == po_id)
        ).scalar_one()
        rows = session.execute(
            select(PurchaseOrderStatusLog, User.display_name)
            .outerjoin(User, User.id == PurchaseOrderStatusLog.actor_user_id)
            .where(PurchaseOrderStatusLog.purchase_order_id == po_id)
            .order_by(PurchaseOrderStatusLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            {
                "id": log.id,
                "occurred_at": log.occurred_at.isoformat(),
                "from_status": log.from_status,
                "to_status": log.to_status,
                "reason": log.reason,
                "actor_user_id": log.actor_user_id,
                "actor_name": name,
                "automatic": log.automatic,
            }
            for log, name in rows
        ], total


# ── OC 경계 · FREE 메타 ────────────────────────────────────────────────────────

OC_REFERENCE_MAX = 100


def check_oc_received_on(
    value: date | None, doc_date: date, *, field: str = "oc_received_on"
) -> date:
    """OC(공급사 확인) 일자 경계 — `doc_date ≤ oc_received_on ≤ today_kst()`(F2). OC는 공급사 회신의 **사람 기록**이라 발행 전·미래일 수 없다."""
    if value is None:
        raise invalid(field, "공급사 확인(OC) 일자를 입력해 주세요.")
    if value < doc_date:
        raise invalid(field, "OC 일자는 발주 증빙일 이전일 수 없습니다.")
    if value > today_kst():
        raise invalid(field, "OC 일자는 오늘(KST)보다 미래일 수 없습니다.")
    return value


def clean_oc_reference(value: str | None, *, field: str = "oc_reference") -> str | None:
    text = blank_to_none(value)
    if text is not None and len(text) > OC_REFERENCE_MAX:
        raise invalid(field, f"OC 참조는 {OC_REFERENCE_MAX}자 이내로 입력해 주세요.")
    return text


def update_meta(*, actor: AuthenticatedUser, po_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """FREE 열(내부 메모·담당자·OC 일자·OC 참조)만 — 동결 후에도 허용된다(인계·오기 정정). 그 밖의 열은 요청 스키마에 없다.

    OC 두 열은 **공급사 확인(SUPPLIER_CONFIRMED) 상태에서만** 바꾼다(발행 중에는 전이가 기록하고 취소된 발주의 OC는 고치지 않는다 — DB CHECK와 같은 규칙을
    422로 미리 안내). OC 일자는 비울 수 없고 경계(발행일≤OC≤오늘)를 지킨다. 바뀐 것이 없으면 version·updated_by를 건드리지 않는다.
    """
    _require_cost_role(actor)
    values = {k: v for k, v in payload.items() if k in _FREE_FIELDS}
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, PurchaseOrder, po_id, expected_version=payload["version"])
        changed = False
        if "assignee_id" in values:
            if values["assignee_id"] is None:
                raise invalid("assignee_id", "담당자를 비울 수 없습니다.")
            require_assignee(session, values["assignee_id"])
            if row.assignee_id != values["assignee_id"]:
                row.assignee_id = values["assignee_id"]
                changed = True
        if "internal_note" in values:
            note = blank_to_none(values["internal_note"])
            if row.internal_note != note:
                row.internal_note = note
                changed = True
        if any(field in values for field in _OC_FIELDS):
            if row.status != "SUPPLIER_CONFIRMED":
                raise invalid(
                    "oc_received_on",
                    "OC 일자·참조는 공급사 확인 상태에서만 고칠 수 있습니다. "
                    "발행 상태에서는 ‘공급사 확인’ 전이에서 입력해 주세요.",
                )
            if "oc_received_on" in values:
                oc_date = check_oc_received_on(values["oc_received_on"], row.doc_date)
                if row.oc_received_on != oc_date:
                    row.oc_received_on = oc_date
                    changed = True
            if "oc_reference" in values:
                reference = clean_oc_reference(values["oc_reference"])
                if row.oc_reference != reference:
                    row.oc_reference = reference
                    changed = True
        if changed:
            row.updated_by_id = actor.id
            session.flush()
        return detail_body(session, row, include_cost=True)
