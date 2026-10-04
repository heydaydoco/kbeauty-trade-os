"""선적 오케스트레이션 — SO 참조 생성(미리보기·생성)·라인·출고지시·취소·헤더·당사자 (S3-2 PR-3a / ADR-0074·0075·0078·0079 / design-C C1~C6).

모든 동작은 **사람 1클릭 + 한 트랜잭션**이다(T1·T3·T4·T5 — design-C C1). 트랜잭션 안 외부 호출 0(알림은 아웃박스 이벤트뿐).

■ 수출선적 생성(T1) 잠금 순서(ADR-0078 LOCK_ORDER): 멱등 claim → partners `FOR KEY SHARE`(바이어 + 지정 당사자, **id 오름차순**)
  → SO `FOR UPDATE`(**선점** — 같은 TX의 SO 수렴이 SHARE→UPDATE 승격 교착을 일으키지 않게, `lock_lines_for_consumption`의 헤더 `FOR SHARE`는
  기보유 잠금에 흡수) → SO 라인 `FOR UPDATE` id순(소속 필터 — 본문 라인이 이 SO 소속이 아니면 422 LINE_MISMATCH) → 잔량 재계산·초과 409
  EXCEEDS_OPEN → 채번(마지막) → INSERT(선적·라인·당사자·탄생 이력·`.created`) → **같은 TX에서 SO 수렴**(첫 살아 있는 선적이면 CONFIRMED→IN_SHIPMENT).
  같은 SO의 동시 선적은 SO 행 잠금이 직렬화한다 — 두 번째는 첫 번째 커밋 뒤 잔량을 본다(GC-F4).
■ 선적 기점 경로(라인 T4·출고지시/취소 T5): 멱등 → `lock_chain(SHIPMENT)`(원천 SO **또는** PO `FOR UPDATE` → 선적 `FOR UPDATE`+version, R-08)
  → (라인) 원천 라인 `FOR UPDATE` → 변경 → 헤더 version +1. 취소·라인 삭제는 생성과 **같은 수렴 함수**(`converge_parent(SHIPMENT)`)를 부른다(X-13).
■ 헤더(T3)·당사자: 멱등 → (당사자) partners `FOR KEY SHARE` → 선적 `FOR UPDATE`(R-08 — 거래처 검증이 선적 잠금 뒤로 가지 않는다).
■ 원천 값은 **복사**만 한다(통화·환율·결제조건·Incoterms·거래 상대·SKU·단가 — 마스터 재조회 금지). 증빙일은 생성 시 KST 오늘 1회(R-15).
■ 권한은 라우터가 경로 단위로 건다(생성·라인·취소 = 무역, 헤더·출고지시·당사자 = 무역+물류 — ADR-0079). 이 모듈은 역할을 다시 판정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import minor_units
from app.core.time import today_kst
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners import service as partners
from app.modules.partners.models import PARTNER_TYPES, Partner
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine
from app.modules.shipments import service as shipments
from app.modules.shipments.models import AUTO_PARTY_ROLES, Shipment, ShipmentLine, ShipmentParty
from app.modules.trade_chain.chain_ops import converge_parent, lock_chain
from app.modules.trade_chain.shipment_view import (
    PARTY_EDITABLE_STATES,
    detail_body,
    dg_map,
    sku_body,
)
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind, PartyRole, ShipmentKind
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.quantities import (
    CONSUMABLE_STATUSES,
    OpenQuantity,
    lock_lines_for_consumption,
    open_quantity,
    require_within_open,
)
from app.modules.trade_docs.snapshot import validate_quantity
from app.modules.trade_docs.transition import record_transition
from app.modules.trade_docs.validation import invalid, require_active_user
from app.modules.trade_docs.views import incoterm_body, money_text, payment_terms_body, rate_text

KIND = DocKind.SHIPMENT
SO_KIND = DocKind.SALES_ORDER

CREATE_ENDPOINT = "POST /api/v1/sales-orders/{id}/shipments"
LINE_ADD_ENDPOINT = "POST /api/v1/shipments/{id}/lines"
RELEASE_ENDPOINT = "POST /api/v1/shipments/{id}/release-order"
TRANSITION_ENDPOINT = "POST /api/v1/shipments/{id}/transitions"
PARTY_ADD_ENDPOINT = "POST /api/v1/shipments/{id}/parties"

#: 당사자 역할 → 거래처 유형(유형 무관 = 전 유형 — NOTIFY는 통지처라 유형을 묻지 않는다).
PARTY_TYPES: dict[str, tuple[str, ...]] = {
    PartyRole.NOTIFY.value: tuple(PARTNER_TYPES),
    PartyRole.FORWARDER.value: ("FORWARDER",),
    PartyRole.CUSTOMS_BROKER.value: ("CUSTOMS_BROKER",),
}
_PARTY_LABELS = {
    PartyRole.NOTIFY.value: "통지처",
    PartyRole.FORWARDER.value: "포워더",
    PartyRole.CUSTOMS_BROKER.value: "관세사",
}


# ── 참조 생성 (T1) ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Plan:
    so: SalesOrder
    header: dict[str, Any]
    lines: list[shipments.NewShipmentLine]
    parties: list[shipments.NewParty]
    open_before: dict[int, int]
    source_line_no: dict[int, int]


def _role_not_allowed(field: str, role: str) -> AppError:
    return AppError(
        ErrorCode.SHIPMENTS_PARTY_ROLE_NOT_ALLOWED,
        detail={
            field: f"{role} 역할은 직접 지정할 수 없습니다(수하인 = 수주 바이어 자동, 송하인 = 자사)."
        },
    )


def _check_party_roles(requested: list[dict[str, Any]]) -> None:
    """수출선적의 자동·자사 역할(CONSIGNEE·SHIPPER)은 본문으로 받지 않는다(422), 같은 역할 2번은 입력 오류(422)."""
    seen: set[str] = set()
    for index, item in enumerate(requested):
        role = str(item["role"])
        if role in AUTO_PARTY_ROLES:
            raise _role_not_allowed(f"parties[{index}].role", role)
        if role in seen:
            raise invalid(f"parties[{index}].role", "같은 역할을 두 번 지정할 수 없습니다.")
        seen.add(role)


def _lock_partners(
    session: Session, buyer_id: int, requested: list[dict[str, Any]], *, lock: bool
) -> tuple[Partner, dict[int, Partner]]:
    """바이어 + 지정 당사자 거래처를 **id 오름차순**으로 확인·잠금(FOR KEY SHARE, ADR-0067) — 쓰임마다 유형을 검사한다(유형 불일치는 기존 422 코드).

    같은 거래처가 여러 역할로 쓰이면 쓰임마다 다시 검사한다(같은 TX의 같은 행 재잠금은 대기 없이 통과 — 순서는 id 오름차순 그대로).
    """
    uses: dict[int, list[tuple[str, tuple[str, ...], str]]] = {
        buyer_id: [("so_id", ("BUYER",), "바이어")]
    }
    for index, item in enumerate(requested):
        role = str(item["role"])
        uses.setdefault(int(item["partner_id"]), []).append(
            (f"parties[{index}].partner_id", PARTY_TYPES[role], _PARTY_LABELS[role])
        )
    found: dict[int, Partner] = {}
    for partner_id in sorted(uses):
        for field, types, label in uses[partner_id]:
            found[partner_id] = partners.require_partner_of_any_type(
                session, partner_id, types, field=field, type_label=label, lock=lock
            )
    return found[buyer_id], found


def _require_source_so(session: Session, so_id: int) -> SalesOrder:
    row = session.execute(
        select(SalesOrder).where(SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"sales_order_id": so_id})
    return row


def _select_so_lines(
    session: Session, so: SalesOrder, requested: list[dict[str, Any]], *, lock: bool
) -> tuple[list[SalesOrderLine], dict[int, int], dict[int, int]]:
    """원천 SO 라인 확정 → (라인 행들[라인 번호 순], 라인별 요청 수량, 라인별 소비 전 잔량).

    생성(`lock=True`)은 `lock_lines_for_consumption`(헤더 FOR SHARE — 기보유 FOR UPDATE에 흡수 → 소비 가능 상태 검증 → 라인 FOR UPDATE id순·
    헤더 소속 필터)이고, 미리보기는 같은 검증을 잠금 없이 한다. 요청 id 중 소속이 아닌(삭제 포함) 것은 422 LINE_MISMATCH(존재 여부 비공개).
    """
    wanted = [int(item["so_line_id"]) for item in requested]
    if len(set(wanted)) != len(wanted):
        raise invalid("lines", "같은 수주 라인을 두 번 지정할 수 없습니다.")
    if lock:
        owned = {int(i) for i in lock_lines_for_consumption(session, SO_KIND, so.id, wanted)}
    else:
        if so.status not in CONSUMABLE_STATUSES[SO_KIND]:
            raise AppError(
                ErrorCode.TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE,
                log_context={"doc_kind": SO_KIND.value, "status": so.status},
            )
        owned = set(
            session.execute(
                select(SalesOrderLine.id).where(
                    SalesOrderLine.so_id == so.id,
                    SalesOrderLine.id.in_(wanted),
                    SalesOrderLine.deleted_at.is_(None),
                )
            ).scalars()
        )
    missing = [index for index, line_id in enumerate(wanted) if line_id not in owned]
    if missing:
        raise AppError(
            ErrorCode.SHIPMENTS_SOURCE_LINE_MISMATCH,
            detail={f"lines[{missing[0]}].so_line_id": "이 수주의 라인이 아닙니다."},
        )
    take: dict[int, int] = {}
    for index, item in enumerate(requested):
        take[int(item["so_line_id"])] = validate_quantity(
            item["quantity"], field=f"lines[{index}].quantity"
        )
    quantities = open_quantity(session, "SO_LINE", sorted(take))
    require_within_open(quantities, take)
    rows = {
        row.id: row
        for row in session.execute(
            select(SalesOrderLine).where(SalesOrderLine.id.in_(sorted(take)))
        ).scalars()
    }
    ordered = sorted(rows.values(), key=lambda r: r.line_no)
    return ordered, take, {i: quantities[i].open for i in take}


def _line_from_so(source: SalesOrderLine, quantity: int) -> shipments.NewShipmentLine:
    """SO 라인 → 선적 라인 사본(SKU·단가·무상 표식 복사 — 마스터 재조회 금지)."""
    return shipments.NewShipmentLine(
        so_line_id=source.id,
        po_line_id=None,
        sku_id=source.sku_id,
        sku_code=source.sku_code,
        sku_name_ko=source.sku_name_ko,
        sku_name_en=source.sku_name_en,
        sku_kind=source.sku_kind,
        unit_price_amount=source.unit_price_amount,
        is_free=source.is_free,
        quantity=quantity,
    )


def _plan(
    session: Session, actor: AuthenticatedUser, so_id: int, payload: dict[str, Any], *, lock: bool
) -> _Plan:
    requested_parties = list(payload.get("parties") or [])
    # 오류 우선순위(ADR-0079 ⑧ 404→409→422) — 잠금 순서(거래처 → SO, R-08)는 그대로 두고 거래처를 잠그기 전에 **무잠금 peek**로
    # SO 존재(404)·소비 가능 상태(409 DOCUMENT_NOT_CONSUMABLE)를 먼저 판정한다. 잠근 뒤 `lock_lines_for_consumption`이 상태를 다시 본다(TOCTOU).
    peek = _require_source_so(
        session, so_id
    )  # 거래처는 ORIGIN(불변)이라 잠금 전 무잠금 조회로 얻어도 안전하다
    if peek.status not in CONSUMABLE_STATUSES[SO_KIND]:
        raise AppError(
            ErrorCode.TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE,
            log_context={"doc_kind": SO_KIND.value, "status": peek.status},
        )
    _check_party_roles(requested_parties)
    buyer, partner_rows = _lock_partners(
        session, peek.buyer_partner_id, requested_parties, lock=lock
    )
    so = (
        lock_document(session, SalesOrder, so_id) if lock else peek
    )  # SO FOR UPDATE 선점(승격 금지)
    source_lines, take, open_before = _select_so_lines(
        session, so, list(payload["lines"]), lock=lock
    )
    editing.require_line_capacity(0, len(source_lines))
    consignee_name = shipments.require_english_name(buyer.name_en, field="so_id")
    parties = [
        shipments.NewParty(
            role=PartyRole.CONSIGNEE.value,
            partner_id=buyer.id,
            name_en=consignee_name,
            address_en=shipments.clean_address(buyer.address_en, field="so_id"),
            is_auto=True,
        )
    ]
    for index, item in enumerate(requested_parties):
        partner = partner_rows[int(item["partner_id"])]
        parties.append(
            shipments.NewParty(
                role=str(item["role"]),
                partner_id=partner.id,
                name_en=shipments.require_english_name(
                    partner.name_en, field=f"parties[{index}].partner_id"
                ),
                address_en=shipments.clean_address(
                    partner.address_en, field=f"parties[{index}].partner_id"
                ),
                is_auto=False,
            )
        )
    lines = [_line_from_so(line, take[line.id]) for line in source_lines]
    editing.compute_total([item.amount for item in lines])  # 2^53 초과 422 — 미리보기도 같은 검증
    note = payload.get("internal_note")
    header: dict[str, Any] = {
        "doc_date": today_kst(),  # R-15 — 원천 복사가 아니라 생성 시 1회(이후 ORIGIN 불변)
        "currency": so.currency,
        "fx_rate": so.fx_rate,
        "fx_rate_date": so.fx_rate_date,
        "payment_type": so.payment_type,
        "advance_pct_bp": so.advance_pct_bp,
        "balance_anchor": so.balance_anchor,
        "balance_days": so.balance_days,
        "incoterm_code": so.incoterm_code,
        "incoterm_place": so.incoterm_place,
        "incoterm_year": so.incoterm_year,
        "assignee_id": so.assignee_id,  # 원천 담당자 사본(이후 FREE)
        "shipment_kind": ShipmentKind.EXPORT.value,
        "so_id": so.id,
        "counterparty_partner_id": so.buyer_partner_id,
        "counterparty_name": so.buyer_name,
        "origin_country_code": payload["origin_country_code"],
        "dest_country_code": payload["dest_country_code"],
        "internal_note": (str(note).strip() or None) if note is not None else None,
    }
    return _Plan(
        so=so,
        header=header,
        lines=lines,
        parties=parties,
        open_before=open_before,
        source_line_no={line.id: line.line_no for line in source_lines},
    )


def create_shipment_from_sales_order(
    *, actor: AuthenticatedUser, idempotency_key: str, so_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """SO → 수출선적 참조 생성(PLANNED) + 같은 TX SO 수렴 — 한 트랜잭션. 같은 키 재수신은 최초 결과를 그대로 돌려준다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={"so_id": so_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        plan = _plan(session, actor, so_id, payload, lock=True)
        row = shipments.insert_planned(
            session, actor_id=actor.id, header=plan.header, lines=plan.lines, parties=plan.parties
        )
        converge_parent(
            session, KIND, row, actor_user_id=actor.id
        )  # CONFIRMED→IN_SHIPMENT(첫 선적)
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def preview_shipment_from_sales_order(
    *, actor: AuthenticatedUser, so_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """비저장 미리보기 — 채번·상태이력·이벤트·멱등 키·잠금 소비가 **전혀 없다**. 생성과 같은 검증을 같은 순서로 한다."""
    with unit_of_work() as uow:
        session = uow.session
        plan = _plan(session, actor, so_id, payload, lock=False)
        cur = plan.header["currency"]
        dg = dg_map(session, {item.sku_id for item in plan.lines})
        lines = [
            {
                "so_line_id": item.so_line_id,
                "line_no": plan.source_line_no[item.so_line_id or 0],
                "sku": sku_body(item),
                "quantity": item.quantity,
                "open_quantity_before": plan.open_before[item.so_line_id or 0],
                "remaining_after": plan.open_before[item.so_line_id or 0] - item.quantity,
                "unit_price_amount": item.unit_price_amount,
                "unit_price_text": money_text(item.unit_price_amount, cur),
                "is_free": item.is_free,
                "line_amount": item.amount,
                "line_amount_text": money_text(item.amount, cur),
                "dg": dg.get(item.sku_id, {"flag": False, "un_number": None, "dg_class": None}),
            }
            for item in plan.lines
        ]
        total = editing.compute_total([item.amount for item in plan.lines])
        holder = _Holder(plan.header)
        return {
            "so_id": plan.so.id,
            "so_doc_number": plan.so.doc_number,
            "so_status": plan.so.status,
            "doc_date": plan.header["doc_date"].isoformat(),
            "shipment_kind": plan.header["shipment_kind"],
            "counterparty": {
                "partner_id": plan.header["counterparty_partner_id"],
                "name": plan.header["counterparty_name"],
            },
            "origin_country_code": plan.header["origin_country_code"],
            "dest_country_code": plan.header["dest_country_code"],
            "currency": cur,
            "minor_units": minor_units(cur),
            "fx_rate": rate_text(plan.header["fx_rate"]),
            "fx_rate_date": (
                plan.header["fx_rate_date"].isoformat() if plan.header["fx_rate_date"] else None
            ),
            "payment_terms": payment_terms_body(holder),
            "incoterm": incoterm_body(holder),
            "total_amount": total,
            "total_text": money_text(total, cur),
            "lines": lines,
            "parties": [
                {
                    "role": p.role,
                    "partner_id": p.partner_id,
                    "name_en": p.name_en,
                    "address_en": p.address_en,
                    "auto": p.is_auto,
                }
                for p in plan.parties
            ],
        }


class _Holder:
    """헤더 dict를 속성 접근으로 읽게 하는 얇은 어댑터 — 저장 전 값을 같은 응답 조립 함수에 태운다."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.__dict__.update(values)


# ── 선적 기점 잠금 (T4·T5) ──────────────────────────────────────────────────────


def _lock_shipment_chain(session: Session, shipment_id: int, version: int | None) -> Shipment:
    """원천(SO 또는 PO) `FOR UPDATE` → 선적 `FOR UPDATE`(+version 대조) — `lock_chain` 그대로(R-08, 승격 없음)."""
    row: Shipment = lock_chain(session, KIND, shipment_id, expected_version=version)[KIND]
    return row


# ── 라인 (T4 — 계획 중에만, 무역) ───────────────────────────────────────────────


def add_line(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """라인 추가 — 원천 SO 라인 1줄·잔량 안에서(초과 409 EXCEEDS_OPEN), 같은 원천 라인이 이미 있으면 409 DUPLICATE_SOURCE."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=LINE_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        row = _lock_shipment_chain(session, shipment_id, payload["version"])
        editing.assert_editable(KIND, row.status, fields=["lines"])
        if (
            row.so_id is None
        ):  # 수입선적 라인 편집은 PR-5a(수입 생성 경로와 함께) — 지금은 행이 생길 수 없다
            raise editing_frozen()
        source_id = int(payload["source_line_id"])
        quantity = validate_quantity(payload["quantity"])
        owned = lock_lines_for_consumption(session, SO_KIND, row.so_id, [source_id])
        if not owned:
            raise AppError(
                ErrorCode.SHIPMENTS_SOURCE_LINE_MISMATCH,
                detail={"source_line_id": "이 수주의 라인이 아닙니다."},
            )
        duplicate = session.execute(
            select(func.count())
            .select_from(ShipmentLine)
            .where(
                ShipmentLine.shipment_id == row.id,
                ShipmentLine.so_line_id == source_id,
                ShipmentLine.deleted_at.is_(None),
            )
        ).scalar_one()
        if duplicate:
            raise AppError(ErrorCode.SHIPMENTS_LINE_DUPLICATE_SOURCE)
        editing.require_line_capacity(editing.count_live_lines(session, KIND, row.id, ShipmentLine))
        require_within_open(open_quantity(session, "SO_LINE", [source_id]), {source_id: quantity})
        source = session.get(SalesOrderLine, source_id)
        assert source is not None
        line_no = shipments.insert_line(
            session, row, _line_from_so(source, quantity), actor_id=actor.id
        )
        shipments.finish_line_change(session, row, actor_id=actor.id, last_line_no=line_no)
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def editing_frozen() -> AppError:
    return AppError(ErrorCode.TRADE_DOCS_DOCUMENT_FROZEN, detail={"fields": ["lines"]})


def update_line(
    *, actor: AuthenticatedUser, shipment_id: int, line_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """라인 수량 수정 — 원천 잔량 + 이 라인의 현재 수량 안에서(감소는 언제나 가능 — 잔량이 파생으로 복원된다)."""
    with unit_of_work() as uow:
        session = uow.session
        row = _lock_shipment_chain(session, shipment_id, payload["version"])
        line = shipments.require_line(session, row.id, line_id)  # 부모-자식 404(부작용 0)
        editing.assert_editable(KIND, row.status, fields=["quantity"])
        quantity = validate_quantity(payload["quantity"])
        if line.so_line_id is None:
            raise editing_frozen()
        lock_lines_for_consumption(session, SO_KIND, row.so_id or 0, [line.so_line_id])
        current = open_quantity(session, "SO_LINE", [line.so_line_id])[line.so_line_id]
        # 이 라인의 현재 수량은 자기 자신의 소비라 다시 쓸 수 있다(잔량 + 현재 수량이 이 라인의 상한).
        own = OpenQuantity(current.ordered, current.consumed - line.quantity)
        require_within_open({line.so_line_id: own}, {line.so_line_id: quantity})
        shipments.set_line_quantity(line, quantity, actor_id=actor.id)
        shipments.finish_line_change(session, row, actor_id=actor.id)
        return detail_body(session, row, actor.roles)


def remove_line(
    *, actor: AuthenticatedUser, shipment_id: int, line_id: int, version: int
) -> dict[str, Any]:
    """라인 제외(soft delete) — 마지막 라인은 409 LAST_LINE(선적 취소로 안내). 잔량은 파생이라 자동 복원된다."""
    with unit_of_work() as uow:
        session = uow.session
        row = _lock_shipment_chain(session, shipment_id, version)
        line = shipments.require_line(session, row.id, line_id)
        editing.assert_editable(KIND, row.status, fields=["lines"])
        if editing.count_live_lines(session, KIND, row.id, ShipmentLine) <= 1:
            raise AppError(ErrorCode.SHIPMENTS_LINE_LAST_LINE)
        shipments.remove_line(line, actor_id=actor.id)
        shipments.finish_line_change(session, row, actor_id=actor.id)
        # X-13 — 생성·취소와 같은 수렴 함수(정의 공유). 라인 ≥ 1 불변식이라 선적이 살아 있어 결과는 항상 no-op이다.
        converge_parent(session, KIND, row, actor_user_id=actor.id)
        return detail_body(session, row, actor.roles)


# ── 출고지시·취소 (T5) ─────────────────────────────────────────────────────────


def release_shipment_order(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """출고지시(동결 액션 PLANNED→RELEASE_ORDERED) — `frozen_at` 기록, 이후 라인·국가 편집 409 FROZEN. 사람 1클릭(물류 가능)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=RELEASE_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        row = _lock_shipment_chain(session, shipment_id, version)
        record_transition(
            session,
            row,
            "RELEASE_ORDERED",
            actor_user_id=actor.id,
            reason=None,
            automatic=False,
            via_freeze_action=True,
        )
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def _require_no_live_facts(session: Session, row: Shipment) -> None:
    """취소 차단 — 살아 있는 통관 기록(R-16)·ETD·B/L 발행·ETA 실적(R-01). 호출자가 선적 `FOR UPDATE`를 쥐고 있어야 한다."""
    blockers = shipments.cancel_blockers(session, row.id)
    if blockers.customs_numbers:
        raise AppError(
            ErrorCode.SHIPMENTS_SHIPMENT_CUSTOMS_RECORD_ALIVE,
            detail={"declaration_nos": blockers.customs_numbers},
        )
    if blockers.actual_types:
        raise AppError(
            ErrorCode.SHIPMENTS_SHIPMENT_ACTUAL_RECORDED,
            detail={"milestone_types": blockers.actual_types},
        )


def transition_shipment(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    shipment_id: int,
    to: str,
    version: int,
    reason: str | None,
) -> tuple[int, dict[str, Any]]:
    """선적 범용 사람 전이 — 취소(계획·출고지시 → 취소, 사유 필수)뿐. 마지막 살아 있는 선적이면 같은 TX에서 SO가 CONFIRMED로 복귀한다.

    **생존 사실 가드(PR-4a — R-01·R-16, 역순 원칙의 사실 기록판)**: 살아 있는 통관 기록이 있으면 409 `CUSTOMS_RECORD_ALIVE`, ETD·B/L 발행·ETA
    실적이 살아 있으면 409 `ACTUAL_RECORDED`(`detail.milestone_types`). 판정은 선적 `FOR UPDATE` 아래에서 한다 — 통관·마일스톤 쓰기도 같은 헤더를
    `FOR UPDATE`로 잡으므로 판정과 취소 사이에 새 사실이 끼어들 수 없다. 탈출로 = 통관 기록·실적을 사유와 함께 삭제(이력 남음) 후 취소.
    오류 우선순위: 404 → version 409 → 생존 409 → 전이 409·사유 422(커널).
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=TRANSITION_ENDPOINT,
            key=idempotency_key,
            request_body={
                "shipment_id": shipment_id,
                "to": to,
                "version": version,
                "reason": reason,
            },
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        row = _lock_shipment_chain(session, shipment_id, version)
        _require_no_live_facts(session, row)
        record_transition(session, row, to, actor_user_id=actor.id, reason=reason, automatic=False)
        converge_parent(
            session, KIND, row, actor_user_id=actor.id
        )  # 마지막 선적이면 IN_SHIPMENT→CONFIRMED
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


# ── 헤더 편집 (T3 — 무역·물류) ────────────────────────────────────────────────


_FREE_FIELDS = ("internal_note", "assignee_id")
_COUNTRY_FIELDS = ("origin_country_code", "dest_country_code")


def update_shipment(
    *, actor: AuthenticatedUser, shipment_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """헤더 편집 — FREE 2열(메모·담당자)은 상태 무관, 국가 2열은 계획 중에만(값이 바뀌면 409 FROZEN). 보낸 필드만 바뀐다."""
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, Shipment, shipment_id, expected_version=payload["version"])
        countries = {
            k: str(payload[k])
            for k in _COUNTRY_FIELDS
            if k in payload and payload[k] is not None and payload[k] != getattr(row, k)
        }
        if countries:
            editing.assert_editable(KIND, row.status, fields=sorted(countries))
        assignee_id = None
        if "assignee_id" in payload:
            if payload["assignee_id"] is None:
                raise invalid("assignee_id", "담당자를 비울 수 없습니다.")
            require_active_user(session, payload["assignee_id"])
            assignee_id = int(payload["assignee_id"])
        note: tuple[bool, str | None] = (False, None)
        if "internal_note" in payload:
            raw = payload["internal_note"]
            note = (True, (str(raw).strip() or None) if raw is not None else None)
        shipments.apply_header_edit(
            row, actor_id=actor.id, countries=countries, note=note, assignee_id=assignee_id
        )
        session.flush()
        return detail_body(session, row, actor.roles)


# ── 당사자 (무역·물류) ─────────────────────────────────────────────────────────


def _require_party_editable(row: Shipment) -> None:
    if row.status not in PARTY_EDITABLE_STATES:
        raise AppError(
            ErrorCode.SHIPMENTS_SHIPMENT_NOT_ACTIVE,
            log_context={"shipment_id": row.id, "status": row.status},
        )


def add_party(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """당사자 추가 — 역할별 거래처 유형 검증(KEY SHARE, 선적 잠금보다 먼저 — R-08)·영문명 스냅샷·(선적, 역할) 유일(409)·audit.

    오류 우선순위(ADR-0079 ⑧ 404→409→422): 잠금 순서(거래처 → 선적)는 그대로 두고, 거래처를 잠그기 전에 **무잠금 peek**로 선적 존재(404)·
    활성(409 NOT_ACTIVE)을 먼저 판정한다 — 그 뒤 역할(422)·거래처 유형(422). 선적 `FOR UPDATE` 뒤 활성을 다시 본다(TOCTOU).
    """
    role = str(payload["role"])
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PARTY_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        _require_party_editable(shipments.require_shipment(session, shipment_id))  # 무잠금 peek
        if role in AUTO_PARTY_ROLES:
            raise _role_not_allowed("role", role)
        partner = partners.require_partner_of_any_type(
            session,
            int(payload["partner_id"]),
            PARTY_TYPES[role],
            field="partner_id",
            type_label=_PARTY_LABELS[role],
            lock=True,
        )
        row = lock_document(session, Shipment, shipment_id)
        _require_party_editable(row)  # 잠금 뒤 재확인(peek와 잠금 사이 취소 — TOCTOU)
        name_en = shipments.require_english_name(partner.name_en, field="partner_id")
        party = ShipmentParty(
            shipment_id=row.id,
            role=role,
            partner_id=partner.id,
            name_en=name_en,
            address_en=shipments.clean_address(partner.address_en, field="partner_id"),
            is_auto=False,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(party)
        shipments.flush_translated(session)
        audit.record(
            session,
            action=AuditAction.SHIPMENTS_PARTY_ADDED,
            actor_user_id=actor.id,
            entity_type="shipments",
            entity_id=row.id,
            detail={"doc_number": row.doc_number, "role": role, "partner_id": partner.id},
        )
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def remove_party(
    *, actor: AuthenticatedUser, shipment_id: int, party_id: int, version: int
) -> dict[str, Any]:
    """당사자 제외(soft delete) — 취소된 선적은 409 NOT_ACTIVE, 당사자 version 대조(409), 자동 스냅샷 행은 422 ROLE_NOT_ALLOWED·audit.

    오류 우선순위(ADR-0079 ⑧): 선적·당사자 404 → 선적 활성 409 → 당사자 version 409 → 자동 행 422.
    """
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, Shipment, shipment_id)
        party = shipments.require_party(
            session, row.id, party_id
        )  # shipment_children — 선적 뒤(LOCK_ORDER)
        _require_party_editable(row)
        if party.version != version:
            raise VersionConflictError(log_context={"party_id": party_id})
        if party.is_auto:
            raise _role_not_allowed("party_id", party.role)
        shipments.remove_party(party, actor_id=actor.id)
        session.flush()
        audit.record(
            session,
            action=AuditAction.SHIPMENTS_PARTY_REMOVED,
            actor_user_id=actor.id,
            entity_type="shipments",
            entity_id=row.id,
            detail={
                "doc_number": row.doc_number,
                "role": party.role,
                "partner_id": party.partner_id,
            },
        )
        return detail_body(session, row, actor.roles)
