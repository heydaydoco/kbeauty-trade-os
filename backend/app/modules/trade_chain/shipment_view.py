"""선적 응답 조립 — 상세·목록·CSV·상태이력·가용 '자리' (S3-2 PR-3a·PR-3c·PR-5a / design-D D3 / design-integrated X-24·X-26).

■ 상세는 **고정 쿼리 수**(라인 1·원천 SO 1·원천 라인 1·잔량 2·SKU DG 1·당사자 1·담당자 1 = 8)다 — 라인 수와 무관(N+1 0, `D:376`).
  수입선적은 원천 라인이 PO 라인이다(라인 번호 1·배정 가능량 2 — 같은 개수).
■ **수입선적 응답 갈래(S3-2 PR-5a — GC-G3·R-3c-2)**: 수입선적의 상세·목록 dict는 통화·소수 자릿수·환율·합계·라인 단가/금액/통화/무상 키를
  **만들지 않는다**(만든 뒤 지우지 않는다 — PO `include_cost` 관례). PO 통화·환율은 PO CostHidden이 원가 비열람 역할에게 가리는 필드라 선적이 우회
  통로가 되지 않게 **전 역할 같은 모양**(원가 열람 역할로도 0)이고, 응답 모델도 구분 판별자 합집합(`ImportShipmentDetail`)이라 담을 자리가 없다.
  CSV는 같은 열을 빈칸으로 둔다(헤더 고정). PO 라인을 읽을 때는 원가 열을 고르지 않는다(`_PURCHASE_ORDER_LINES` — PO 모델 임포트 0).
■ §8.3 가용재고 '자리'(X-26): `AllocationPort`는 **무변경**이다(읽기 메서드 없음 — 소비자 없는 메서드를 더하지 않는다). 선적 라인 응답의
  `availability.status`는 `AllocationStatus.NOT_IMPLEMENTED` 값을 그대로 싣는다(화면 "가용재고 미산정" 배지 — 0·현재고 표시 금지).
  S4-2가 포트에 읽기를 더하면 `availability_of` 한 곳만 바꾼다.
■ `allowed_actions`는 **표시 편의**다(서버가 쓰기 시 다시 검사 — 셸 메뉴 관례). 역할·상태에서 계산해 프런트에 상태 규칙을 복제하지 않게 한다.
■ 금액은 판매가 축(SO 단가 사본)이라 마스킹 비대상이고 원가 열은 없다(수입선적 원가 비복사 — 응답 갈래는 위 PR-5a 항목).
■ CSV(S20)는 목록과 **같은 조건 함수**를 쓰고 전 역할 같은 헤더다(원가·단가 열 0 — 역할별 분기 없음). BOM·수식 이스케이프는 공용 통로
  `core.csv_export`가 한다(라우터가 `csv_response`로만 내보낸다).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, String, column, func, or_, select, table
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.money import minor_units
from app.core.time import to_kst
from app.modules.catalog.models import Sku
from app.modules.identity.models import RoleCode, User
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine
from app.modules.sales_orders.ports import AllocationStatus
from app.modules.shipments.models import Shipment, ShipmentLine, ShipmentParty
from app.modules.shipments.service import live_lines, require_shipment
from app.modules.trade_chain.milestone_view import (
    RECORD_EDITABLE_STATES,
    assemble,
    customs_summary,
    etd_eta_by_shipment,
)
from app.modules.trade_docs.constants import DocKind, ShipmentKind
from app.modules.trade_docs.machine import EDITABLE_STATES, TERMINAL_STATUSES
from app.modules.trade_docs.models import ShipmentStatusLog
from app.modules.trade_docs.quantities import ASSIGNABLE_KINDS, open_quantity
from app.modules.trade_docs.validation import invalid
from app.modules.trade_docs.views import incoterm_body, money_text, payment_terms_body, rate_text

KIND = DocKind.SHIPMENT

#: 당사자·출고지시·헤더(국가·메모·담당) 쓰기 역할 — 물류 첫 전표 쓰기(ADR-0079). 생성·라인·취소는 무역(SO 잔량·수렴 = 상업 사실).
LOGISTICS_WRITERS = frozenset({RoleCode.ADMIN, RoleCode.TRADE, RoleCode.LOGISTICS})
TRADE_WRITERS = frozenset({RoleCode.ADMIN, RoleCode.TRADE})
#: 당사자를 바꿀 수 있는 상태 — S3-2 활성 상태(계획·출고지시). 취소·예약 상태는 409 NOT_ACTIVE. 통관·마일스톤·통보와 같은 집합(PR-4a).
PARTY_EDITABLE_STATES = RECORD_EDITABLE_STATES

_PURCHASE_ORDERS = table(
    "purchase_orders", column("id", Integer), column("doc_number", String), column("status", String)
)
#: 수입선적 원천 라인(PO 라인) — **원가 열을 고르지 않는다**(라인 번호만 — 수량·배정 가능량은 `open_quantity`가 읽는다). PO 모델 임포트 0(ADR-0024).
_PURCHASE_ORDER_LINES = table(
    "purchase_order_lines", column("id", Integer), column("line_no", Integer)
)


def availability_of() -> dict[str, str]:
    """§8.3 '자리' — 포트 무변경, 값은 NOT_IMPLEMENTED 하나(차단 0)."""
    return {"status": AllocationStatus.NOT_IMPLEMENTED.value}


def allowed_actions(row: Shipment, roles: frozenset[RoleCode]) -> list[str]:
    actions: list[str] = []
    logistics = bool(roles & LOGISTICS_WRITERS)
    trade = bool(roles & TRADE_WRITERS)
    planned = row.status in EDITABLE_STATES[KIND]
    live = row.status not in TERMINAL_STATUSES[KIND]
    if planned and logistics:
        actions.append("RELEASE_ORDER")
    if live and row.status in ("PLANNED", "RELEASE_ORDERED") and trade:
        actions.append("CANCEL")
    if planned and trade:
        actions.append("EDIT_LINES")
    if planned and logistics:
        actions.append("EDIT_COUNTRIES")
    if logistics:
        actions.append("EDIT_META")
    if row.status in PARTY_EDITABLE_STATES and logistics:
        actions.append("EDIT_PARTIES")
    # PR-4a — 마일스톤 계획·실적·통보 기록(EDIT_MILESTONES)·계획 초안(PLAN_DRAFT)·통관 기록(EDIT_CUSTOMS) = 무역·물류, 계획/출고지시 중
    if row.status in RECORD_EDITABLE_STATES and logistics:
        actions.extend(("EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"))
    return actions


def _source(session: Session, row: Shipment) -> dict[str, Any]:
    if row.so_id is not None:
        found = session.execute(
            select(SalesOrder.doc_number, SalesOrder.status).where(SalesOrder.id == row.so_id)
        ).one()
        return {"kind": "SALES_ORDER", "id": row.so_id, "doc_number": found[0], "status": found[1]}
    assert row.po_id is not None  # CHECK kind_source — 원천은 정확히 하나
    found = session.execute(
        select(_PURCHASE_ORDERS.c.doc_number, _PURCHASE_ORDERS.c.status).where(
            _PURCHASE_ORDERS.c.id == row.po_id
        )
    ).one()
    return {"kind": "PURCHASE_ORDER", "id": row.po_id, "doc_number": found[0], "status": found[1]}


def dg_map(session: Session, sku_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not sku_ids:
        return {}
    return {
        int(r[0]): {"flag": bool(r[1]), "un_number": r[2], "dg_class": r[3]}
        for r in session.execute(
            select(Sku.id, Sku.dg_flag, Sku.un_number, Sku.dg_class).where(Sku.id.in_(sku_ids))
        ).all()
    }


def sku_body(line: Any) -> dict[str, Any]:
    return {
        "id": line.sku_id,
        "code": line.sku_code,
        "name_ko": line.sku_name_ko,
        "name_en": line.sku_name_en,
        "kind": line.sku_kind,
    }


def _source_lines(session: Session, ids: set[int]) -> dict[int, tuple[int, int]]:
    """원천 SO 라인 id → (라인 번호, 수량)."""
    if not ids:
        return {}
    return {
        int(r[0]): (int(r[1]), int(r[2]))
        for r in session.execute(
            select(SalesOrderLine.id, SalesOrderLine.line_no, SalesOrderLine.quantity).where(
                SalesOrderLine.id.in_(ids)
            )
        ).all()
    }


def _assignee(session: Session, user_id: int) -> dict[str, Any]:
    name = session.execute(select(User.display_name).where(User.id == user_id)).scalar_one_or_none()
    return {"id": user_id, "display_name": name}


def is_import(row: Shipment) -> bool:
    """수입선적 — 응답에서 금액·통화 계열 키를 **만들지 않는** 갈래(G3 — 만든 뒤 지우지 않는다, PO `include_cost` 관례)."""
    return row.shipment_kind == ShipmentKind.IMPORT.value


def _po_source_lines(session: Session, ids: set[int]) -> dict[int, tuple[int, int, int]]:
    """원천 PO 라인 id → (라인 번호, 수량, 배정 가능량[살아 있는 수입선적 전부 반영 후]) — 원가 열 0, 질의 3회(라인 수 무관)."""
    if not ids:
        return {}
    numbers = {
        int(r[0]): int(r[1])
        for r in session.execute(
            select(_PURCHASE_ORDER_LINES.c.id, _PURCHASE_ORDER_LINES.c.line_no).where(
                _PURCHASE_ORDER_LINES.c.id.in_(ids)
            )
        ).all()
    }
    assignable = open_quantity(session, "PO_LINE", sorted(ids), kinds=ASSIGNABLE_KINDS)
    return {
        line_id: (numbers.get(line_id, 0), q.ordered, q.open) for line_id, q in assignable.items()
    }


def _line_bodies(
    session: Session, row: Shipment, lines: list[ShipmentLine], dg: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """라인 응답 — 수출 = SO 라인 잔량·판매가 축 금액, 수입 = PO 라인 배정 가능량·**금액·단가·통화·무상 키 없음**(G3)."""
    bodies: list[dict[str, Any]] = []
    if is_import(row):
        po_sources = _po_source_lines(
            session, {line.po_line_id for line in lines if line.po_line_id is not None}
        )
        for line in lines:
            assert (
                line.po_line_id is not None
            )  # CHECK one_source + 구분 = 원천(수입 라인은 PO 라인만 — 생성 경로가 보증)
            src_no, src_qty, src_assignable = po_sources.get(line.po_line_id, (0, 0, 0))
            bodies.append(
                {
                    "id": line.id,
                    "line_no": line.line_no,
                    "po_line_id": line.po_line_id,
                    "sku": sku_body(line),
                    "quantity": line.quantity,
                    "source_line": {
                        "id": line.po_line_id,
                        "line_no": src_no,
                        "quantity": src_qty,
                        "remaining_after": src_assignable,
                    },
                    "dg": dg.get(line.sku_id, _NO_DG),
                    "availability": availability_of(),
                }
            )
        return bodies
    so_line_ids = {line.so_line_id for line in lines if line.so_line_id is not None}
    sources = _source_lines(session, so_line_ids)
    remaining = (
        {k: v.open for k, v in open_quantity(session, "SO_LINE", sorted(so_line_ids)).items()}
        if so_line_ids
        else {}
    )
    cur = row.currency
    for line in lines:
        src_no, src_qty = sources.get(line.so_line_id or 0, (0, 0))
        bodies.append(
            {
                "id": line.id,
                "line_no": line.line_no,
                "so_line_id": line.so_line_id,
                "sku": sku_body(line),
                "quantity": line.quantity,
                "currency": line.currency,
                "unit_price_amount": line.unit_price_amount,
                "unit_price_text": money_text(line.unit_price_amount, cur),
                "is_free": line.is_free,
                "line_amount": line.line_amount,
                "line_amount_text": money_text(line.line_amount, cur),
                "source_line": {
                    "id": line.so_line_id,
                    "line_no": src_no,
                    "quantity": src_qty,
                    "remaining_after": remaining.get(line.so_line_id or 0, 0),
                },
                "dg": dg.get(line.sku_id, _NO_DG),
                "availability": availability_of(),
            }
        )
    return bodies


_NO_DG: dict[str, Any] = {"flag": False, "un_number": None, "dg_class": None}


def detail_body(session: Session, row: Shipment, roles: frozenset[RoleCode]) -> dict[str, Any]:
    """상세 응답 dict — 수입선적은 금액·통화 계열 키(통화·소수 자릿수·환율·합계·라인 단가/금액/무상)를 **만들지 않는다**(G3·R-3c-2)."""
    lines = live_lines(session, row.id)
    dg = dg_map(session, {line.sku_id for line in lines})
    parties = list(
        session.execute(
            select(ShipmentParty)
            .where(ShipmentParty.shipment_id == row.id, ShipmentParty.deleted_at.is_(None))
            .order_by(ShipmentParty.id)
        ).scalars()
    )
    assembled = assemble(
        session, row
    )  # 마일스톤 보드 + 통관 수리일(요약 재사용) — 고정 질의 수(PR-4a)
    line_bodies = _line_bodies(session, row, lines, dg)
    dg_lines = sum(1 for line in line_bodies if line["dg"]["flag"])
    body: dict[str, Any] = {
        "id": row.id,
        "doc_number": row.doc_number,
        "doc_date": row.doc_date.isoformat(),
        "status": row.status,
        "shipment_kind": row.shipment_kind,
        "version": row.version,
        "frozen_at": row.frozen_at.isoformat() if row.frozen_at else None,
        "source": _source(session, row),
        "counterparty": {"partner_id": row.counterparty_partner_id, "name": row.counterparty_name},
        "origin_country_code": row.origin_country_code,
        "dest_country_code": row.dest_country_code,
        "payment_terms": payment_terms_body(row),
        "incoterm": incoterm_body(row),
        "internal_note": row.internal_note,
        "assignee": _assignee(session, row.assignee_id),
        "last_line_no": row.last_line_no,
        "dg_line_count": dg_lines,
        "lines": line_bodies,
        "parties": [
            {
                "id": p.id,
                "role": p.role,
                "partner_id": p.partner_id,
                "name_en": p.name_en,
                "address_en": p.address_en,
                "auto": p.is_auto,
                "version": p.version,
            }
            for p in parties
        ],
        "milestones": assembled.board,
        "customs_summary": customs_summary(assembled.customs_accepted),
        "allowed_actions": allowed_actions(row, roles),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if not is_import(row):  # 수출만 — 통화·고정 환율·판매가 합계(수입은 키 자체를 만들지 않는다)
        cur = row.currency
        body.update(
            {
                "currency": cur,
                "minor_units": minor_units(cur),
                "fx_rate": rate_text(row.fx_rate),
                "fx_rate_date": row.fx_rate_date.isoformat() if row.fx_rate_date else None,
                "total_amount": row.total_amount,
                "total_text": money_text(row.total_amount, cur),
            }
        )
    return body


def get_shipment(shipment_id: int, roles: frozenset[RoleCode]) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(uow.session, require_shipment(uow.session, shipment_id), roles)


def _list_conditions(
    *,
    statuses: list[str] | None,
    shipment_kind: str | None,
    so_id: int | None,
    po_id: int | None,
    assignee_id: int | None,
    q: str | None,
) -> list[Any]:
    conditions: list[Any] = [Shipment.deleted_at.is_(None)]
    if statuses:
        conditions.append(Shipment.status.in_(statuses))
    if shipment_kind:
        conditions.append(Shipment.shipment_kind == shipment_kind)
    if so_id:
        conditions.append(Shipment.so_id == so_id)
    if po_id:
        conditions.append(Shipment.po_id == po_id)
    if assignee_id:
        conditions.append(Shipment.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                Shipment.doc_number.icontains(needle, autoescape=True),
                Shipment.counterparty_name.icontains(needle, autoescape=True),
            )
        )
    return conditions


def _line_count() -> Any:
    """살아 있는 라인 수 — 헤더 행에 상관 서브쿼리로 붙인다(목록·CSV 공용, N+1 0)."""
    return (
        select(func.count())
        .where(ShipmentLine.shipment_id == Shipment.id, ShipmentLine.deleted_at.is_(None))
        .correlate(Shipment)
        .scalar_subquery()
    )


def list_shipments(
    *,
    offset: int,
    limit: int,
    statuses: list[str] | None = None,
    shipment_kind: str | None = None,
    so_id: int | None = None,
    po_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """목록(Page 50) — 조인 1회로 원천 번호·상태·담당자명·라인 수(상관 서브쿼리)를 함께 읽는다(N+1 0)."""
    conditions = _list_conditions(
        statuses=statuses,
        shipment_kind=shipment_kind,
        so_id=so_id,
        po_id=po_id,
        assignee_id=assignee_id,
        q=q,
    )
    line_count = _line_count()
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Shipment).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(
                Shipment,
                SalesOrder.doc_number,
                SalesOrder.status,
                _PURCHASE_ORDERS.c.doc_number,
                _PURCHASE_ORDERS.c.status,
                User.display_name,
                line_count,
            )
            .outerjoin(SalesOrder, SalesOrder.id == Shipment.so_id)
            .outerjoin(_PURCHASE_ORDERS, _PURCHASE_ORDERS.c.id == Shipment.po_id)
            .outerjoin(User, User.id == Shipment.assignee_id)
            .where(*conditions)
            .order_by(Shipment.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        # ETD·ETA 유효값 — 현재 페이지 선적 id들로 질의 1회(행 수와 무관, design-D D3 — 적대 검토 반영 ②)
        dates = etd_eta_by_shipment(session, [r[0].id for r in rows])
        items = []
        for row, so_no, so_status, po_no, po_status, name, count in rows:
            source = (
                {"kind": "SALES_ORDER", "id": row.so_id, "doc_number": so_no, "status": so_status}
                if row.so_id is not None
                else {
                    "kind": "PURCHASE_ORDER",
                    "id": row.po_id,
                    "doc_number": po_no,
                    "status": po_status,
                }
            )
            item: dict[str, Any] = {
                "id": row.id,
                "doc_number": row.doc_number,
                "doc_date": row.doc_date.isoformat(),
                "status": row.status,
                "shipment_kind": row.shipment_kind,
                "source": source,
                "counterparty_name": row.counterparty_name,
                "origin_country_code": row.origin_country_code,
                "dest_country_code": row.dest_country_code,
                "line_count": int(count),
                "etd": dates[row.id]["etd"],
                "eta": dates[row.id]["eta"],
                "assignee": {"id": row.assignee_id, "display_name": name},
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            if not is_import(
                row
            ):  # 수출만 — 통화·판매가 합계(수입 행은 키 자체가 없다 — 상세·CSV 빈칸과 같은 판정)
                item.update(
                    {
                        "currency": row.currency,
                        "total_amount": row.total_amount,
                        "total_text": money_text(row.total_amount, row.currency),
                    }
                )
            items.append(item)
        return items, int(total)


# ── CSV 내보내기 (S20 — design-integrated X-24 / design-C C8) ────────────────────────────

#: 전 역할 같은 헤더 — **원가·단가 열이 없다**(헤더 단위 행이라 라인 단가도 없다 → 역할별 헤더 분기 불필요, PO CSV와 다름).
#: "합계"는 수출선적의 판매가 축(SO 단가 사본 합 — 마스킹 비대상, SO CSV "합계"와 같은 축)이고 수입선적은 **빈칸**(금액 축 없음 — 0으로 쓰지 않는다).
EXPORT_HEADER: tuple[str, ...] = (
    "선적번호",
    "증빙일",
    "상태",
    "구분",
    "원천전표",
    "원천상태",
    "거래상대",
    "출발국",
    "도착국",
    "통화",
    "합계",
    "라인수",
    "Incoterms",
    "출고지시일",
    "담당자",
    "생성일",
)
#: 상한 초과는 조용히 자르지 않고 422(조건을 좁히게 한다 — 전 목록 내보내기 §12.2, 다른 전표 CSV와 같은 값).
EXPORT_MAX_ROWS = 50_000


def _kst_date(value: datetime | None) -> str:
    """UTC 시각 → 사람이 보는 KST 날짜(없으면 빈칸)."""
    return "" if value is None else to_kst(value).date().isoformat()


def export_rows(
    *,
    statuses: list[str] | None = None,
    shipment_kind: str | None = None,
    so_id: int | None = None,
    po_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
) -> list[tuple[Any, ...]]:
    """CSV 행(선적 헤더 단위, id 오름차순) — 필터는 목록(S1)과 같은 조건 함수(`_list_conditions`)를 쓴다.

    쿼리 1회: CSV에 쓰는 열만 고르고(ORM 엔티티·미사용 `internal_note` 적재 0) `상한 + 1`행까지만 읽어 넘치면 422 — 건수·본문 사이 경합도 없다
    (PR-3c 적대 검토). 수입선적 행은 원천 PO 번호·상태만 — **합계·통화 모두 빈칸**(PO 통화는 원가 비열람 역할에게 가려진 PO 필드 — G3)."""
    conditions = _list_conditions(
        statuses=statuses,
        shipment_kind=shipment_kind,
        so_id=so_id,
        po_id=po_id,
        assignee_id=assignee_id,
        q=q,
    )
    with unit_of_work() as uow:
        session = uow.session
        rows = session.execute(
            select(
                Shipment.doc_number,
                Shipment.doc_date,
                Shipment.status,
                Shipment.shipment_kind,
                Shipment.so_id,
                Shipment.counterparty_name,
                Shipment.origin_country_code,
                Shipment.dest_country_code,
                Shipment.currency,
                Shipment.total_amount,
                Shipment.incoterm_code,
                Shipment.incoterm_place,
                Shipment.frozen_at,
                Shipment.created_at,
                SalesOrder.doc_number.label("so_number"),
                SalesOrder.status.label("so_status"),
                _PURCHASE_ORDERS.c.doc_number.label("po_number"),
                _PURCHASE_ORDERS.c.status.label("po_status"),
                User.display_name.label("assignee_name"),
                _line_count().label("line_count"),
            )
            .outerjoin(SalesOrder, SalesOrder.id == Shipment.so_id)
            .outerjoin(_PURCHASE_ORDERS, _PURCHASE_ORDERS.c.id == Shipment.po_id)
            .outerjoin(User, User.id == Shipment.assignee_id)
            .where(*conditions)
            .order_by(Shipment.id)
            .limit(EXPORT_MAX_ROWS + 1)
        ).all()
        if len(rows) > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 {EXPORT_MAX_ROWS:,}건을 넘습니다. {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        result: list[tuple[Any, ...]] = []
        for r in rows:
            exported = r.shipment_kind == ShipmentKind.EXPORT
            result.append(
                (
                    r.doc_number,
                    r.doc_date.isoformat(),
                    r.status,
                    r.shipment_kind,
                    (r.so_number if r.so_id is not None else r.po_number) or "",
                    (r.so_status if r.so_id is not None else r.po_status) or "",
                    r.counterparty_name,
                    r.origin_country_code,
                    r.dest_country_code,
                    r.currency if exported else "",
                    (money_text(r.total_amount, r.currency) or "") if exported else "",
                    int(r.line_count),
                    f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
                    _kst_date(r.frozen_at),
                    r.assignee_name or "",
                    _kst_date(r.created_at),
                )
            )
        return result


def list_status_log(
    *, shipment_id: int, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_shipment(session, shipment_id)
        total = session.execute(
            select(func.count())
            .select_from(ShipmentStatusLog)
            .where(ShipmentStatusLog.shipment_id == shipment_id)
        ).scalar_one()
        rows = session.execute(
            select(ShipmentStatusLog, User.display_name)
            .outerjoin(User, User.id == ShipmentStatusLog.actor_user_id)
            .where(ShipmentStatusLog.shipment_id == shipment_id)
            .order_by(ShipmentStatusLog.id.desc())
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
        ], int(total)
