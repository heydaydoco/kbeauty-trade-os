"""선적 오케스트레이션 — SO 참조 생성(미리보기·생성)·라인·출고지시·취소·헤더·당사자 (S3-2 PR-3a / ADR-0074·0075·0078·0079 / design-C C1~C6).

모든 동작은 **사람 1클릭 + 한 트랜잭션**이다(T1·T3·T4·T5 — design-C C1). 트랜잭션 안 외부 호출 0(알림은 아웃박스 이벤트뿐).

■ 수출선적 생성(T1) 잠금 순서(ADR-0078 LOCK_ORDER): 멱등 claim → partners `FOR KEY SHARE`(바이어 + 지정 당사자, **id 오름차순**)
  → SO `FOR UPDATE`(**선점** — 같은 TX의 SO 수렴이 SHARE→UPDATE 승격 교착을 일으키지 않게, `lock_lines_for_consumption`의 헤더 `FOR SHARE`는
  기보유 잠금에 흡수) → SO 라인 `FOR UPDATE` id순(소속 필터 — 본문 라인이 이 SO 소속이 아니면 422 LINE_MISMATCH) → 잔량 재계산·초과 409
  EXCEEDS_OPEN → 채번(마지막) → INSERT(선적·라인·당사자·탄생 이력·`.created`) → **같은 TX에서 SO 수렴**(첫 살아 있는 선적이면 CONFIRMED→IN_SHIPMENT).
  같은 SO의 동시 선적은 SO 행 잠금이 직렬화한다 — 두 번째는 첫 번째 커밋 뒤 잔량을 본다(GC-F4).
■ 수입선적 생성(T2 — S3-2 PR-5a / ADR-0077·0078 R-08): 멱등 claim → partners `FOR KEY SHARE`(공급사 + 지정 당사자, id 오름차순) →
  PO **`FOR SHARE`**(`lock_lines_for_consumption` — PO 상태·잔량을 바꾸지 않으므로 승격이 없다, PO 취소[`FOR UPDATE`]와 직렬화) → 소비 가능 상태
  재검사(발행·공급사 확인) → PO 라인 `FOR UPDATE` id순(소속 필터 — 422 LINE_MISMATCH) → **배정 가능량**(PO 라인 수량 − 살아 있는 IN_TRANSIT 합)
  재계산·초과 409 `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE` → 채번(마지막) → INSERT. **PO 상태·PO 잔량(FULFILL)·PO 행 무변경**(4금 ① — GC-A15),
  수렴은 같은 함수(`converge_parent`)가 수입이면 아무것도 하지 않는다. 같은 PO 라인의 동시 수입선적은 라인 `FOR UPDATE`가 직렬화한다(J-06).
  **원가 비복사**(ADR-0024 — 10번째 채널 미개설): PO는 테이블 이름으로 **원가 아닌 열만** 골라 읽는다(PO 모델 임포트 0). 단가 NULL·금액 0.
■ 오류 우선순위(ADR-0079 ⑧ 404→409→422): 원천 SO·PO는 거래처를 잠그기 전에 **무잠금 peek**로 존재(404)·소비 가능 상태(409)를 먼저 판정하고,
  잠근 뒤 `lock_lines_for_consumption`이 상태를 다시 본다(TOCTOU). 메모는 strip 전 원문의 보이지 않는 글자(줄바꿈·탭 외)를 422로 막는다.
■ 선적 기점 경로(라인 T4·출고지시/취소 T5): 멱등 → `lock_chain(SHIPMENT)`(원천 SO **또는** PO `FOR UPDATE` → 선적 `FOR UPDATE`+version, R-08)
  → (라인) 원천 라인 `FOR UPDATE` → 변경 → 헤더 version +1. 취소·라인 삭제는 생성과 **같은 수렴 함수**(`converge_parent(SHIPMENT)`)를 부른다(X-13).
■ 헤더(T3)·당사자: 멱등 → (당사자) partners `FOR KEY SHARE` → 선적 `FOR UPDATE`(R-08 — 거래처 검증이 선적 잠금 뒤로 가지 않는다).
■ 원천 값은 **복사**만 한다(통화·환율·결제조건·Incoterms·거래 상대·SKU·단가 — 마스터 재조회 금지). 증빙일은 생성 시 KST 오늘 1회(R-15).
■ 권한은 라우터가 경로 단위로 건다(생성·라인·취소 = 무역, 헤더·출고지시·당사자 = 무역+물류 — ADR-0079). 이 모듈은 역할을 다시 판정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Integer, String, column, func, select, table
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import minor_units
from app.core.text import is_invisible_char
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
from app.modules.trade_docs.constants import PO_SUPPLIER_TYPES, DocKind, PartyRole, ShipmentKind
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.quantities import (
    ASSIGNABLE_KINDS,
    CONSUMABLE_STATUSES,
    DEFAULT_OPEN_KINDS,
    OpenQuantity,
    lock_lines_for_consumption,
    open_quantity,
    require_within_assignable,
    require_within_open,
)
from app.modules.trade_docs.snapshot import validate_quantity
from app.modules.trade_docs.transition import record_transition
from app.modules.trade_docs.validation import invalid, require_active_user
from app.modules.trade_docs.views import incoterm_body, money_text, payment_terms_body, rate_text

KIND = DocKind.SHIPMENT
SO_KIND = DocKind.SALES_ORDER
PO_KIND = DocKind.PURCHASE_ORDER

CREATE_ENDPOINT = "POST /api/v1/sales-orders/{id}/shipments"
PO_CREATE_ENDPOINT = "POST /api/v1/purchase-orders/{id}/shipments"
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
#: 구분별 자동 스냅샷 역할(원천 거래 상대 — 수출 = SO 바이어가 수하인, 수입 = PO 공급사가 송하인)과 422 안내의 괄호 문구.
_AUTO_ROLE: dict[str, str] = {
    ShipmentKind.EXPORT.value: PartyRole.CONSIGNEE.value,
    ShipmentKind.IMPORT.value: PartyRole.SHIPPER.value,
}
_AUTO_ROLE_NOTE: dict[str, str] = {
    ShipmentKind.EXPORT.value: "수하인 = 수주 바이어 자동, 송하인 = 자사",
    ShipmentKind.IMPORT.value: "송하인 = 발주 공급사 자동, 수하인 = 자사",
}

#: 수입선적 원천 PO 헤더 — **원가 아닌 열만**(통화·환율은 원가가 아니라 복사 대상 — X-05). PO 모델(원가 열 보유)을 임포트하지 않는다(ADR-0024
#: 10번째 채널 미개설 — `total_cost`를 고르는 문장 자체가 없다, 아키텍처 스캔 고정).
_PO = table(
    "purchase_orders",
    column("id", Integer),
    column("doc_number", String),
    column("status", String),
    column("po_kind", String),
    column("supplier_partner_id", Integer),
    column("supplier_name", String),
    column("currency", String),
    column("fx_rate"),
    column("fx_rate_date"),
    column("payment_type", String),
    column("advance_pct_bp", Integer),
    column("balance_anchor", String),
    column("balance_days", Integer),
    column("incoterm_code", String),
    column("incoterm_place", String),
    column("incoterm_year", Integer),
    column("assignee_id", Integer),
    column("deleted_at"),
)
#: 수입선적 원천 PO 라인 — SKU 사본·수량만(단가·라인 원가·가격 기준 열을 고르지 않는다).
_PO_LINES = table(
    "purchase_order_lines",
    column("id", Integer),
    column("po_id", Integer),
    column("line_no", Integer),
    column("sku_id", Integer),
    column("sku_code", String),
    column("sku_name_ko", String),
    column("sku_name_en", String),
    column("sku_kind", String),
    column("quantity", Integer),
    column("deleted_at"),
)


# ── 참조 생성 (T1 수출·T2 수입 공용 조각) ────────────────────────────────────────────


@dataclass(slots=True)
class _Plan:
    """참조 생성 계획 — 미리보기·생성이 같은 함수로 만든다(조용한 분기 방지). `open_before`는 수출 = SO 선적 잔량, 수입 = PO 배정 가능량."""

    source: Any
    header: dict[str, Any]
    lines: list[shipments.NewShipmentLine]
    parties: list[shipments.NewParty]
    open_before: dict[int, int]
    source_line_no: dict[int, int]


def _role_not_allowed(field: str, role: str, kind: str) -> AppError:
    return AppError(
        ErrorCode.SHIPMENTS_PARTY_ROLE_NOT_ALLOWED,
        detail={field: f"{role} 역할은 직접 지정할 수 없습니다({_AUTO_ROLE_NOTE[kind]})."},
    )


def _check_party_roles(requested: list[dict[str, Any]], kind: str) -> None:
    """자동·자사 역할(CONSIGNEE·SHIPPER — 수출·수입 모두 하나는 원천 자동, 하나는 자사)은 본문으로 받지 않는다(422), 같은 역할 2번은 입력 오류(422)."""
    seen: set[str] = set()
    for index, item in enumerate(requested):
        role = str(item["role"])
        if role in AUTO_PARTY_ROLES:
            raise _role_not_allowed(f"parties[{index}].role", role, kind)
        if role in seen:
            raise invalid(f"parties[{index}].role", "같은 역할을 두 번 지정할 수 없습니다.")
        seen.add(role)


@dataclass(frozen=True, slots=True)
class _Counterparty:
    """원천 거래 상대(수출 = SO 바이어 / 수입 = PO 공급사) — 유형 검증 표·안내 문구·오류 필드(원천 경로 id)."""

    partner_id: int
    types: tuple[str, ...]
    label: str
    field: str


def _lock_partners(
    session: Session, counterparty: _Counterparty, requested: list[dict[str, Any]], *, lock: bool
) -> tuple[Partner, dict[int, Partner]]:
    """거래 상대 + 지정 당사자 거래처를 **id 오름차순**으로 확인·잠금(FOR KEY SHARE, ADR-0067) — 쓰임마다 유형을 검사한다(유형 불일치는 기존 422 코드).

    같은 거래처가 여러 역할로 쓰이면 쓰임마다 다시 검사한다(같은 TX의 같은 행 재잠금은 대기 없이 통과 — 순서는 id 오름차순 그대로).
    """
    uses: dict[int, list[tuple[str, tuple[str, ...], str]]] = {
        counterparty.partner_id: [(counterparty.field, counterparty.types, counterparty.label)]
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
    return found[counterparty.partner_id], found


def _plan_parties(
    kind: str,
    counterparty: Partner,
    field: str,
    requested: list[dict[str, Any]],
    partner_rows: dict[int, Partner],
) -> list[shipments.NewParty]:
    """당사자 스냅샷 — 첫 행 = 원천 거래 상대 자동 행(수출 CONSIGNEE·수입 SHIPPER), 뒤 = 지정 역할. 영문명 결측·보이지 않는 글자 = 422."""
    parties = [
        shipments.NewParty(
            role=_AUTO_ROLE[kind],
            partner_id=counterparty.id,
            name_en=shipments.require_english_name(counterparty.name_en, field=field),
            address_en=shipments.clean_address(counterparty.address_en, field=field),
            is_auto=True,
        )
    ]
    for index, item in enumerate(requested):
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
    return parties


#: 메모에서 허용하는 줄 구분 문자(탭·LF·CR — 통관 메모 선례). 그 밖의 보이지 않는 글자(제어·서식·채움·NUL)는 422.
_NOTE_LINE_BREAKS = frozenset("\t\n\r")


def clean_note(raw: object, *, field: str = "internal_note") -> str | None:
    """내부 메모 — **strip 전 원문**의 보이지 않는 글자(줄바꿈·탭 외 Cc·Cf·Zl·Zp·한글 채움 — NUL 포함)는 422, 공백뿐이면 NULL(S3-2 PR-5a)."""
    if raw is None:
        return None
    text = str(raw)
    if any(is_invisible_char(ch) for ch in text if ch not in _NOTE_LINE_BREAKS):
        raise invalid(
            field,
            "메모에 보이지 않는 글자(제어·서식·채움 문자)가 있습니다 — 줄바꿈·탭만 쓸 수 있습니다.",
        )
    return text.strip() or None


def _copied_header(source: Any, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """원천(SO·PO) → 선적 헤더 사본(통화·고정 환율·결제조건 4열·Incoterms 3열·담당자 — 마스터 재조회 금지)·증빙일 = KST 오늘 1회(R-15)·국가·메모."""
    return {
        "doc_date": today_kst(),  # R-15 — 원천 복사가 아니라 생성 시 1회(이후 ORIGIN 불변)
        "currency": source.currency,
        "fx_rate": source.fx_rate,
        "fx_rate_date": source.fx_rate_date,
        "payment_type": source.payment_type,
        "advance_pct_bp": source.advance_pct_bp,
        "balance_anchor": source.balance_anchor,
        "balance_days": source.balance_days,
        "incoterm_code": source.incoterm_code,
        "incoterm_place": source.incoterm_place,
        "incoterm_year": source.incoterm_year,
        "assignee_id": source.assignee_id,  # 원천 담당자 사본(이후 FREE)
        "shipment_kind": kind,
        "origin_country_code": payload["origin_country_code"],
        "dest_country_code": payload["dest_country_code"],
        "internal_note": clean_note(payload.get("internal_note")),
    }


def _not_consumable(kind: DocKind, status: str) -> AppError:
    return AppError(
        ErrorCode.TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE,
        log_context={"doc_kind": kind.value, "status": status},
    )


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
            raise _not_consumable(SO_KIND, so.status)
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
        raise _not_consumable(SO_KIND, peek.status)
    kind = ShipmentKind.EXPORT.value
    _check_party_roles(requested_parties, kind)
    buyer, partner_rows = _lock_partners(
        session,
        _Counterparty(peek.buyer_partner_id, ("BUYER",), "바이어", "so_id"),
        requested_parties,
        lock=lock,
    )
    so = (
        lock_document(session, SalesOrder, so_id) if lock else peek
    )  # SO FOR UPDATE 선점(승격 금지)
    source_lines, take, open_before = _select_so_lines(
        session, so, list(payload["lines"]), lock=lock
    )
    editing.require_line_capacity(0, len(source_lines))
    parties = _plan_parties(kind, buyer, "so_id", requested_parties, partner_rows)
    lines = [_line_from_so(line, take[line.id]) for line in source_lines]
    editing.compute_total([item.amount for item in lines])  # 2^53 초과 422 — 미리보기도 같은 검증
    header = _copied_header(so, kind=kind, payload=payload)
    header.update(
        {
            "so_id": so.id,
            "counterparty_partner_id": so.buyer_partner_id,
            "counterparty_name": so.buyer_name,
        }
    )
    return _Plan(
        source=so,
        header=header,
        lines=lines,
        parties=parties,
        open_before=open_before,
        source_line_no={line.id: line.line_no for line in source_lines},
    )


# ── 수입선적 — PO 참조 생성 (T2 · S3-2 PR-5a) ─────────────────────────────────────────


def _read_po(session: Session, po_id: int) -> Any:
    """원천 PO 헤더(원가 아닌 열만 — `_PO`). 없거나 삭제 = 404(존재 여부만 — 원가·통화를 오류에 싣지 않는다)."""
    row = session.execute(
        select(_PO).where(_PO.c.id == po_id, _PO.c.deleted_at.is_(None))
    ).one_or_none()
    if row is None:
        raise NotFoundError(log_context={"purchase_order_id": po_id})
    return row


def _select_po_lines(
    session: Session, po_id: int, status: str, requested: list[dict[str, Any]], *, lock: bool
) -> tuple[list[Any], dict[int, int], dict[int, int]]:
    """원천 PO 라인 확정 → (라인 행들[라인 번호 순 — 원가 열 0], 라인별 요청 수량, 라인별 배정 가능량[이 선적 전]).

    생성(`lock=True`)은 `lock_lines_for_consumption`(PO 헤더 **FOR SHARE**·소비 가능 상태 재검사·라인 FOR UPDATE id순·헤더 소속 필터)이고, 미리보기는
    같은 검증을 잠금 없이 한다. 소속이 아닌(삭제 포함) 라인 = 422 LINE_MISMATCH(존재 여부 비공개). 초과 = 409 EXCEEDS_ASSIGNABLE(배정 가능량 —
    PO 잔량[FULFILL]이 아니다, ADR-0077 ④).
    """
    wanted = [int(item["po_line_id"]) for item in requested]
    if len(set(wanted)) != len(wanted):
        raise invalid("lines", "같은 발주 라인을 두 번 지정할 수 없습니다.")
    if lock:
        owned = {int(i) for i in lock_lines_for_consumption(session, PO_KIND, po_id, wanted)}
    else:
        if status not in CONSUMABLE_STATUSES[PO_KIND]:
            raise _not_consumable(PO_KIND, status)
        owned = {
            int(i)
            for i in session.execute(
                select(_PO_LINES.c.id).where(
                    _PO_LINES.c.po_id == po_id,
                    _PO_LINES.c.id.in_(wanted),
                    _PO_LINES.c.deleted_at.is_(None),
                )
            ).scalars()
        }
    missing = [index for index, line_id in enumerate(wanted) if line_id not in owned]
    if missing:
        raise AppError(
            ErrorCode.SHIPMENTS_SOURCE_LINE_MISMATCH,
            detail={f"lines[{missing[0]}].po_line_id": "이 발주의 라인이 아닙니다."},
        )
    take: dict[int, int] = {}
    for index, item in enumerate(requested):
        take[int(item["po_line_id"])] = validate_quantity(
            item["quantity"], field=f"lines[{index}].quantity"
        )
    assignable = open_quantity(session, "PO_LINE", sorted(take), kinds=ASSIGNABLE_KINDS)
    require_within_assignable(assignable, take)
    rows = list(
        session.execute(
            select(_PO_LINES).where(_PO_LINES.c.id.in_(sorted(take))).order_by(_PO_LINES.c.line_no)
        ).all()
    )
    return rows, take, {i: assignable[i].open for i in take}


def _line_from_po(source: Any, quantity: int) -> shipments.NewShipmentLine:
    """PO 라인 → 수입선적 라인 사본(SKU·수량만 — **단가·원가 비복사**: 단가 NULL·금액 0·무상 false, CHECK `import_no_price`)."""
    return shipments.NewShipmentLine(
        so_line_id=None,
        po_line_id=int(source.id),
        sku_id=int(source.sku_id),
        sku_code=source.sku_code,
        sku_name_ko=source.sku_name_ko,
        sku_name_en=source.sku_name_en,
        sku_kind=source.sku_kind,
        unit_price_amount=None,
        is_free=False,
        quantity=quantity,
    )


def _plan_import(
    session: Session, actor: AuthenticatedUser, po_id: int, payload: dict[str, Any], *, lock: bool
) -> _Plan:
    """PO → 수입선적 계획(T2). 오류 우선순위 404 → 409 → 422: 무잠금 peek로 PO 존재·소비 가능 상태를 먼저 본 뒤 역할·거래처·라인 순."""
    requested_parties = list(payload.get("parties") or [])
    # 공급사·구분·통화·조건은 발행 = 동결(ORIGIN)이라 무잠금 peek로 읽어도 안전하다
    peek = _read_po(session, po_id)
    if peek.status not in CONSUMABLE_STATUSES[PO_KIND]:
        raise _not_consumable(PO_KIND, peek.status)
    kind = ShipmentKind.IMPORT.value
    _check_party_roles(requested_parties, kind)
    supplier_types, supplier_label = PO_SUPPLIER_TYPES[peek.po_kind]
    supplier, partner_rows = _lock_partners(
        session,
        _Counterparty(peek.supplier_partner_id, supplier_types, supplier_label, "po_id"),
        requested_parties,
        lock=lock,
    )
    source_lines, take, assignable_before = _select_po_lines(
        session, po_id, peek.status, list(payload["lines"]), lock=lock
    )
    # 잠금(FOR SHARE) 뒤 헤더를 다시 읽는다 — 담당자(FREE)는 바뀔 수 있고, 생성은 잠근 시점의 값을 복사한다(미리보기는 peek 그대로)
    po = _read_po(session, po_id) if lock else peek
    editing.require_line_capacity(0, len(source_lines))
    parties = _plan_parties(kind, supplier, "po_id", requested_parties, partner_rows)
    lines = [_line_from_po(line, take[int(line.id)]) for line in source_lines]
    header = _copied_header(po, kind=kind, payload=payload)
    header.update(
        {
            "po_id": int(po.id),
            "counterparty_partner_id": int(po.supplier_partner_id),
            "counterparty_name": po.supplier_name,
        }
    )
    return _Plan(
        source=po,
        header=header,
        lines=lines,
        parties=parties,
        open_before=assignable_before,
        source_line_no={int(line.id): int(line.line_no) for line in source_lines},
    )


def create_shipment_from_purchase_order(
    *, actor: AuthenticatedUser, idempotency_key: str, po_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """PO → 수입선적 참조 생성(PLANNED) — 한 트랜잭션. **PO 상태·PO 잔량 무변경**(배정 가능량만 준다 — GC-A15). 같은 키 재수신은 최초 결과."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PO_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={"po_id": po_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        plan = _plan_import(session, actor, po_id, payload, lock=True)
        row = shipments.insert_planned(
            session, actor_id=actor.id, header=plan.header, lines=plan.lines, parties=plan.parties
        )
        # 수출과 같은 수렴 함수(X-13 정의 공유) — 수입선적은 PO를 바꾸지 않으므로 None(잠금 승격 0: PO는 FOR SHARE 그대로)
        converge_parent(session, KIND, row, actor_user_id=actor.id)
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def preview_shipment_from_purchase_order(
    *, actor: AuthenticatedUser, po_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """수입선적 비저장 미리보기 — 채번·이력·이벤트·멱등 키·잠금 0. 생성과 같은 검증. **금액·통화·환율 키가 없다**(G3 — 수입 상세와 같은 판정)."""
    with unit_of_work() as uow:
        session = uow.session
        plan = _plan_import(session, actor, po_id, payload, lock=False)
        dg = dg_map(session, {item.sku_id for item in plan.lines})
        holder = _Holder(plan.header)
        return {
            "po_id": int(plan.source.id),
            "po_doc_number": plan.source.doc_number,
            "po_status": plan.source.status,
            "doc_date": plan.header["doc_date"].isoformat(),
            "shipment_kind": plan.header["shipment_kind"],
            "counterparty": {
                "partner_id": plan.header["counterparty_partner_id"],
                "name": plan.header["counterparty_name"],
            },
            "origin_country_code": plan.header["origin_country_code"],
            "dest_country_code": plan.header["dest_country_code"],
            "payment_terms": payment_terms_body(holder),
            "incoterm": incoterm_body(holder),
            "lines": [
                {
                    "po_line_id": item.po_line_id,
                    "line_no": plan.source_line_no[item.po_line_id or 0],
                    "sku": sku_body(item),
                    "quantity": item.quantity,
                    "assignable_before": plan.open_before[item.po_line_id or 0],
                    "remaining_after": plan.open_before[item.po_line_id or 0] - item.quantity,
                    "dg": dg.get(item.sku_id, {"flag": False, "un_number": None, "dg_class": None}),
                }
                for item in plan.lines
            ],
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
            "so_id": plan.source.id,
            "so_doc_number": plan.source.doc_number,
            "so_status": plan.source.status,
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


@dataclass(frozen=True, slots=True)
class _LineSource:
    """선적 라인의 원천 종류 — 수출 = SO 라인(선적 잔량, 초과 409 EXCEEDS_OPEN) / 수입 = PO 라인(배정 가능량, 초과 409 EXCEEDS_ASSIGNABLE)."""

    doc_kind: DocKind
    line_kind: Literal["SO_LINE", "PO_LINE"]
    fk: str
    label: str
    kinds: frozenset[str]


_EXPORT_LINES = _LineSource(SO_KIND, "SO_LINE", "so_line_id", "수주", DEFAULT_OPEN_KINDS)
_IMPORT_LINES = _LineSource(PO_KIND, "PO_LINE", "po_line_id", "발주", ASSIGNABLE_KINDS)


def _line_source(row: Shipment) -> tuple[_LineSource, int]:
    """선적 → (원천 라인 종류, 원천 전표 id). CHECK `kind_source`가 원천 FK를 정확히 하나로 보증한다."""
    if row.so_id is not None:
        return _EXPORT_LINES, row.so_id
    assert row.po_id is not None
    return _IMPORT_LINES, row.po_id


def _require_within(
    source: _LineSource, quantities: dict[int, OpenQuantity], want: dict[int, int]
) -> None:
    if source is _IMPORT_LINES:
        require_within_assignable(quantities, want)
    else:
        require_within_open(quantities, want)


def _new_line(
    session: Session, source: _LineSource, source_id: int, quantity: int
) -> shipments.NewShipmentLine:
    if source is _IMPORT_LINES:
        po_line = session.execute(select(_PO_LINES).where(_PO_LINES.c.id == source_id)).one()
        return _line_from_po(po_line, quantity)
    so_line = session.get(SalesOrderLine, source_id)
    assert so_line is not None
    return _line_from_so(so_line, quantity)


def add_line(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """라인 추가 — 원천 라인 1줄(수출 = SO 라인·잔량 안 / 수입 = PO 라인·배정 가능량 안 — S3-2 PR-5a), 같은 원천 라인이 있으면 409 DUPLICATE_SOURCE.

    잠금: 멱등 → `lock_chain`(원천 SO·PO `FOR UPDATE` → 선적 `FOR UPDATE`+version) → 원천 라인 `FOR UPDATE`(소비 가능 상태 재검사·소속 필터).
    """
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
        source, source_doc_id = _line_source(row)
        source_id = int(payload["source_line_id"])
        quantity = validate_quantity(payload["quantity"])
        owned = lock_lines_for_consumption(session, source.doc_kind, source_doc_id, [source_id])
        if not owned:
            raise AppError(
                ErrorCode.SHIPMENTS_SOURCE_LINE_MISMATCH,
                detail={"source_line_id": f"이 {source.label}의 라인이 아닙니다."},
            )
        duplicate = session.execute(
            select(func.count())
            .select_from(ShipmentLine)
            .where(
                ShipmentLine.shipment_id == row.id,
                getattr(ShipmentLine, source.fk) == source_id,
                ShipmentLine.deleted_at.is_(None),
            )
        ).scalar_one()
        if duplicate:
            raise AppError(ErrorCode.SHIPMENTS_LINE_DUPLICATE_SOURCE)
        editing.require_line_capacity(editing.count_live_lines(session, KIND, row.id, ShipmentLine))
        _require_within(
            source,
            open_quantity(session, source.line_kind, [source_id], kinds=source.kinds),
            {source_id: quantity},
        )
        line_no = shipments.insert_line(
            session, row, _new_line(session, source, source_id, quantity), actor_id=actor.id
        )
        shipments.finish_line_change(session, row, actor_id=actor.id, last_line_no=line_no)
        body = detail_body(session, row, actor.roles)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_line(
    *, actor: AuthenticatedUser, shipment_id: int, line_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """라인 수량 수정 — 원천 잔량(수입 = 배정 가능량) + 이 라인의 현재 수량 안에서(감소는 언제나 가능 — 잔량이 파생으로 복원된다)."""
    with unit_of_work() as uow:
        session = uow.session
        row = _lock_shipment_chain(session, shipment_id, payload["version"])
        line = shipments.require_line(session, row.id, line_id)  # 부모-자식 404(부작용 0)
        editing.assert_editable(KIND, row.status, fields=["quantity"])
        quantity = validate_quantity(payload["quantity"])
        source, source_doc_id = _line_source(row)
        source_id = getattr(line, source.fk)
        assert (
            source_id is not None
        )  # 라인 원천 = 헤더 원천 종류(생성·추가 경로가 같은 종류만 넣는다)
        lock_lines_for_consumption(session, source.doc_kind, source_doc_id, [source_id])
        current = open_quantity(session, source.line_kind, [source_id], kinds=source.kinds)[
            source_id
        ]
        # 이 라인의 현재 수량은 자기 자신의 소비라 다시 쓸 수 있다(잔량 + 현재 수량이 이 라인의 상한).
        own = OpenQuantity(current.ordered, current.consumed - line.quantity)
        _require_within(source, {source_id: own}, {source_id: quantity})
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
            # 생성과 같은 메모 규칙(strip 전 원문의 보이지 않는 글자 422 — 줄바꿈·탭 허용, S3-2 PR-5a)
            note = (True, clean_note(payload["internal_note"]))
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
        peek = shipments.require_shipment(session, shipment_id)  # 무잠금 peek
        _require_party_editable(peek)
        if role in AUTO_PARTY_ROLES:
            raise _role_not_allowed("role", role, peek.shipment_kind)
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
            raise _role_not_allowed("party_id", party.role, row.shipment_kind)
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
