"""수주(SO) 서비스 — 생성 착지·CRUD·라인 3함수·조회·CSV (S3-1 ADR-0052·0053 / design-A A4·A11·A13 / design-B B2·B8).

L1: 이 모듈은 **전이를 하지 않는다**(보류·재개·취소는 trade_chain L2, 확정은 PR-12). 여기서 하는 일은
(1) **`create_received_sales_order` — SO 생성의 단일 착지**(참조 생성 오케스트레이터와 인테이크 확정(PR-13)이 같은 함수를 부른다: 채번은
마지막 단계·중복 바이어 PO 선검사·DB 유니크 경합 번역), (2) 접수(RECEIVED) 편집(헤더 화이트리스트·라인 3함수), (3) 조회·CSV다.

■ 모든 쓰기의 순서 — 헤더 행 `FOR UPDATE` → `version` 대조(409) → 편집 가능 상태 검사(동결이면 409 FROZEN) → 변경 → 합계 재계산 →
  헤더 version 상승(라인만 바뀌어도). 채번은 **생성 트랜잭션의 마지막 단계**(잠금 순서 (9)).
■ 참조 수주의 수량 증가·원천 품목 재추가는 원천 라인을 `FOR UPDATE`(id 순)로 잠그고 잔량을 다시 계산한다 — 잠금 순서 SO(5)→라인(8)이라 사슬
  헤더(QT·PI)를 SO보다 먼저 잠글 필요가 없고(원천은 살아 있는 후속이 붙잡고 있어 취소·만료되지 않는다), 참조 생성 경로(원천 헤더→라인)와 교착 고리가 없다.
■ **거래처·참조·통화는 편집 못 한다**(ORIGIN — 요청 스키마에 필드가 없다). 참조 수주는 목적지 시장 변경·원천 외 품목 추가·SKU 변경이 안 된다.
■ SO 접수는 단종 SKU 라인을 **저장은 허용**하고 응답의 `sku_status`로 표시한다 — 확정(PR-12)에서 재검사해 차단한다(A11).
■ 감사: 전표 생성·전이·편집은 audit_log에 이중 기록하지 않는다(상태이력+아웃박스가 정본 — X-24).
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    column,
    func,
    or_,
    select,
    table,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.money import minor_units
from app.core.time import today_kst, utcnow
from app.modules.approvals import service as approvals_service
from app.modules.approvals.machine import ApprovalType, VoidReasonCode
from app.modules.gates.models import EVALUATION_CONFIRMED, SUBJECT_SALES_ORDER, GateEvaluation
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets import service as markets
from app.modules.partners import service as partners
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine
from app.modules.trade_docs import editing
from app.modules.trade_docs.buyer_po import po_columns
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.doc_number import issue_document_number
from app.modules.trade_docs.fx import require_known_currency, resolve_fx
from app.modules.trade_docs.incoterms import EMPTY_INCOTERM_COLUMNS, build_incoterm
from app.modules.trade_docs.lines import require_sellable_sku, sku_statuses
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import DEAD_STATUSES, EDITABLE_STATES
from app.modules.trade_docs.models import SalesOrderStatusLog
from app.modules.trade_docs.payment_terms import (
    EMPTY_TERMS_COLUMNS,
    advance_pct_text,
    build_payment_terms,
    require_lc_enabled,
)
from app.modules.trade_docs.policy import ColumnClass, columns_of
from app.modules.trade_docs.quantities import (
    OpenQuantity,
    lock_source_lines,
    open_quantity,
    require_within_open,
)
from app.modules.trade_docs.snapshot import (
    LineSnapshot,
    line_amount,
    reprice,
    resolve_buyer_item_code,
    snapshot_line_from_master,
    snapshot_line_from_source,
    validate_quantity,
)
from app.modules.trade_docs.transition import record_birth
from app.modules.trade_docs.validation import (
    blank_to_none,
    check_doc_date,
    invalid,
    require_active_user,
)
from app.modules.trade_docs.views import created_date_kst, money_text, rate_text

KIND = DocKind.SALES_ORDER
EXPORT_MAX_ROWS = 50_000

_QUOTATIONS = table(
    "quotations", column("id", Integer), column("doc_number", String), column("doc_date", Date)
)
_PROFORMAS = table(
    "proforma_invoices",
    column("id", Integer),
    column("doc_number", String),
    column("doc_date", Date),
)
_QT_LINES = table(
    "quotation_lines",
    column("id", Integer),
    column("quantity", Integer),
    column("unit_price_amount"),
)
_PI_LINES = table(
    "proforma_invoice_lines",
    column("id", Integer),
    column("quantity", Integer),
    column("unit_price_amount"),
)

#: 생성 착지가 헤더에 전개해도 되는 조건 열 — 결제조건 4열·Incoterms 3열뿐.
PAYMENT_COLUMN_KEYS: frozenset[str] = frozenset(EMPTY_TERMS_COLUMNS)
INCOTERM_COLUMN_KEYS: frozenset[str] = frozenset(EMPTY_INCOTERM_COLUMNS)

_NOT_NULLABLE = frozenset({"doc_date", "buyer_name", "assignee_id", "dest_market_code"})


# ── 생성 입력 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewSoLine:
    """생성 트랜잭션이 넣을 SO 라인 한 줄 — 스냅샷(값 복사)·수량·요청납기·출처 라인(둘 중 하나 또는 없음)."""

    snapshot: LineSnapshot
    quantity: int
    requested_delivery_date: date | None = None
    qt_line_id: int | None = None
    pi_line_id: int | None = None


@dataclass(frozen=True, slots=True)
class SalesOrderDraft:
    """`create_received_sales_order`의 입력 — 참조 생성(QT/PI)·인테이크 확정(PR-13)이 같은 모양으로 채운다.

    결제조건·Incoterms·환율은 참조 수주만 원천에서 복사해 채우고 직접(인테이크) 수주는 비워 둔다 — 접수 편집에서 입력하고 확정 시 필수다.
    """

    buyer_partner_id: int
    currency: str
    dest_market_code: str
    lines: list[NewSoLine]
    doc_date: date | None = None
    buyer_name: str | None = None
    buyer_po_no: str | None = None
    buyer_po_date: date | None = None
    assignee_id: int | None = None
    qt_id: int | None = None
    pi_id: int | None = None
    fx_rate: Decimal | None = None
    fx_rate_date: date | None = None
    payment_columns: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_TERMS_COLUMNS))
    incoterm_columns: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_INCOTERM_COLUMNS))
    internal_note: str | None = None
    copied_from_id: int | None = None


# ── 중복 바이어 PO · 경합 번역 ────────────────────────────────────────────────


def po_occupant(
    session: Session, buyer_partner_id: int, key: str, *, exclude_id: int | None = None
) -> tuple[str, str] | None:
    """(바이어, 정규화 PO 키)를 점유한 살아 있는 비취소 SO의 (문서번호, 상태) — 없으면 None."""
    query = select(SalesOrder.doc_number, SalesOrder.status).where(
        SalesOrder.buyer_partner_id == buyer_partner_id,
        SalesOrder.buyer_po_no_key == key,
        SalesOrder.deleted_at.is_(None),
        SalesOrder.status != "CANCELLED",
    )
    if exclude_id is not None:
        query = query.where(SalesOrder.id != exclude_id)
    found = session.execute(query.limit(1)).one_or_none()
    return (str(found[0]), str(found[1])) if found else None


def po_occupants(
    session: Session, buyer_partner_id: int, keys: Collection[str]
) -> dict[str, tuple[str, str]]:
    """`po_occupant`의 일괄판 — 키 목록을 **한 쿼리**(`IN`)로 본다. 키 → 점유 SO의 (문서번호, 상태), 점유 없으면 키가 없다.

    같은 키를 둘 이상이 점유하는 경우(부분 유니크상 불가)에도 결정적이도록 id가 가장 작은 SO를 고른다.
    """
    wanted = sorted(set(keys))
    if not wanted:
        return {}
    rows = session.execute(
        select(SalesOrder.buyer_po_no_key, SalesOrder.doc_number, SalesOrder.status)
        .where(
            SalesOrder.buyer_partner_id == buyer_partner_id,
            SalesOrder.buyer_po_no_key.in_(wanted),
            SalesOrder.deleted_at.is_(None),
            SalesOrder.status != "CANCELLED",
        )
        .order_by(SalesOrder.id)
    ).all()
    found: dict[str, tuple[str, str]] = {}
    for key, number, status in rows:
        found.setdefault(str(key), (str(number), str(status)))
    return found


def _duplicate_po_error(number: str, status: str) -> AppError:
    return AppError(
        ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO,
        detail={"doc_number": number, "status": status},
    )


def require_po_free(
    session: Session, buyer_partner_id: int, key: str, *, exclude_id: int | None = None
) -> None:
    found = po_occupant(session, buyer_partner_id, key, exclude_id=exclude_id)
    if found is not None:
        raise _duplicate_po_error(*found)


@contextmanager
def guarded_flush(
    session: Session, *, buyer_partner_id: int | None = None, po_key: str | None = None
) -> Iterator[None]:
    """SAVEPOINT 안에서 실행해 DB 유니크 경합 위반(선검사를 빠져나간 동시 요청)을 서비스 검사와 **같은 409**로 번역한다(500 금지).

    IntegrityError 뒤 세션은 SAVEPOINT 롤백으로 되살아나므로 점유 문서를 다시 조회해 detail에 싣는다(금액 미기재).
    """
    try:
        with session.begin_nested():
            yield
    except IntegrityError as exc:
        text = str(exc.orig)
        if "uq_sales_orders_buyer_po_live" in text and buyer_partner_id and po_key:
            found = po_occupant(session, buyer_partner_id, po_key)
            raise _duplicate_po_error(*(found or ("", ""))) from None
        if "uq_sales_orders_pi_id_live" in text:
            raise AppError(ErrorCode.TRADE_DOCS_REFERENCE_ALREADY_CONVERTED) from None
        if "uq_sales_orders_copied_from_id_live" in text:
            raise AppError(ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE) from None
        if "uq_sales_order_lines_so_id_sku_id_is_free_active" in text:
            raise AppError(ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE) from None
        raise


# ── 뷰 ─────────────────────────────────────────────────────────────────────


def _doc_numbers(session: Session, tbl: Any, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        int(r[0]): str(r[1])
        for r in session.execute(select(tbl.c.id, tbl.c.doc_number).where(tbl.c.id.in_(ids))).all()
    }


def _source_map(session: Session, lines: list[SalesOrderLine]) -> dict[int, tuple[str, Any]]:
    """라인 id → (원천 종류, 원천 라인 행) — 배치 조회 2회(N+1 없음)."""
    out: dict[int, tuple[str, Any]] = {}
    qt_ids = {ln.qt_line_id for ln in lines if ln.qt_line_id}
    pi_ids = {ln.pi_line_id for ln in lines if ln.pi_line_id}
    qt_rows = (
        {
            int(r.id): r
            for r in session.execute(
                select(_QT_LINES.c.id, _QT_LINES.c.quantity, _QT_LINES.c.unit_price_amount).where(
                    _QT_LINES.c.id.in_(qt_ids)
                )
            )
        }
        if qt_ids
        else {}
    )
    pi_rows = (
        {
            int(r.id): r
            for r in session.execute(
                select(_PI_LINES.c.id, _PI_LINES.c.quantity, _PI_LINES.c.unit_price_amount).where(
                    _PI_LINES.c.id.in_(pi_ids)
                )
            )
        }
        if pi_ids
        else {}
    )
    for ln in lines:
        if ln.qt_line_id and ln.qt_line_id in qt_rows:
            out[ln.id] = ("QT_LINE", qt_rows[ln.qt_line_id])
        elif ln.pi_line_id and ln.pi_line_id in pi_rows:
            out[ln.id] = ("PI_LINE", pi_rows[ln.pi_line_id])
    return out


def _line_body(
    line: SalesOrderLine, *, sku_status: str | None, source: tuple[str, Any] | None
) -> dict[str, Any]:
    cur = line.currency
    src_body = None
    delta = changed = None
    if source is not None:
        kind, row = source
        src_body = {
            "kind": kind,
            "line_id": int(row.id),
            "quantity": int(row.quantity),
            "unit_price_amount": int(row.unit_price_amount),
            "unit_price_text": money_text(int(row.unit_price_amount), cur),
        }
        delta = line.quantity - int(row.quantity)
        changed = line.unit_price_amount != int(row.unit_price_amount)
    return {
        "id": line.id,
        "line_no": line.line_no,
        "sku_id": line.sku_id,
        "sku_code": line.sku_code,
        "sku_name_ko": line.sku_name_ko,
        "sku_name_en": line.sku_name_en,
        "sku_kind": line.sku_kind,
        "sku_status": sku_status,
        "quantity": line.quantity,
        "requested_delivery_date": (
            line.requested_delivery_date.isoformat() if line.requested_delivery_date else None
        ),
        "buyer_item_code": line.buyer_item_code,
        "unit_price_amount": line.unit_price_amount,
        "unit_price_text": money_text(line.unit_price_amount, cur),
        "list_price_amount": line.list_price_amount,
        "list_price_text": money_text(line.list_price_amount, cur),
        "line_amount": line.line_amount,
        "line_amount_text": money_text(line.line_amount, cur),
        "price_basis": line.price_basis,
        "is_free": line.is_free,
        "price_reason": line.price_reason,
        "qt_line_id": line.qt_line_id,
        "pi_line_id": line.pi_line_id,
        "source": src_body,
        "quantity_delta": delta,
        "price_changed": changed,
    }


def _summary_body(row: SalesOrder, qt_number: str | None, pi_number: str | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "doc_number": row.doc_number,
        "doc_date": row.doc_date.isoformat(),
        "status": row.status,
        "buyer_partner_id": row.buyer_partner_id,
        "buyer_name": row.buyer_name,
        "buyer_po_no": row.buyer_po_no,
        "buyer_po_date": row.buyer_po_date.isoformat() if row.buyer_po_date else None,
        "dest_market_code": row.dest_market_code,
        "currency": row.currency,
        "total_amount": row.total_amount,
        "total_text": money_text(row.total_amount, row.currency),
        "qt_id": row.qt_id,
        "qt_doc_number": qt_number,
        "pi_id": row.pi_id,
        "pi_doc_number": pi_number,
        "assignee_id": row.assignee_id,
        "copied_from_id": row.copied_from_id,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        # 확정 증적(M10) — 접수 SO는 전부 None. 상세 증적(비PASS 결과·사용한 override)은 `gate_evaluations` CONFIRMED 행이 원천이다.
        "credit_verdict": row.credit_verdict,
        "credit_approval_id": row.credit_approval_id,
        "pi_gate_verdict": row.pi_gate_verdict,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }


def _confirm_evaluation_id(session: Session, row: SalesOrder) -> int | None:
    """확정 증거 스냅샷(`gate_evaluations` CONFIRMED 행 — SO당 1행) id — 확정 전이면 None. 화면이 override·WARN 상세 증적을 이 id로 연결한다(id만이라 마스킹 비대상)."""
    if row.confirmed_at is None:
        return None
    found = session.execute(
        select(GateEvaluation.id).where(
            GateEvaluation.subject_type == SUBJECT_SALES_ORDER,
            GateEvaluation.subject_id == row.id,
            GateEvaluation.outcome == EVALUATION_CONFIRMED,
        )
    ).scalar_one_or_none()
    return int(found) if found is not None else None


def detail_body(session: Session, row: SalesOrder) -> dict[str, Any]:
    lines = list(
        session.execute(
            select(SalesOrderLine)
            .where(SalesOrderLine.so_id == row.id, SalesOrderLine.deleted_at.is_(None))
            .order_by(SalesOrderLine.line_no)
        )
        .scalars()
        .all()
    )
    statuses = sku_statuses(session, [line.sku_id for line in lines])
    sources = _source_map(session, lines)
    # S3-2 PR-3a(design-D X3) — 선적 잔량 = 라인 수량 − 살아 있는 선적 라인 합(파생, 저장 아님). 화면은 산술하지 않는다.
    shipment_open = {
        line_id: quantity.open
        for line_id, quantity in open_quantity(session, "SO_LINE", [ln.id for ln in lines]).items()
    }
    body = _summary_body(
        row,
        _doc_numbers(session, _QUOTATIONS, {row.qt_id} if row.qt_id else set()).get(row.qt_id or 0),
        _doc_numbers(session, _PROFORMAS, {row.pi_id} if row.pi_id else set()).get(row.pi_id or 0),
    )
    body.update(
        {
            "minor_units": minor_units(row.currency),
            "fx_rate": rate_text(row.fx_rate),
            "fx_rate_date": row.fx_rate_date.isoformat() if row.fx_rate_date else None,
            "fx_rate_age_days": (today_kst() - row.fx_rate_date).days if row.fx_rate_date else None,
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
            "confirm_evaluation_id": _confirm_evaluation_id(session, row),
            "last_line_no": row.last_line_no,
            "is_reference": row.qt_id is not None,
            "lines": [
                {
                    **_line_body(
                        line, sku_status=statuses.get(line.sku_id), source=sources.get(line.id)
                    ),
                    "shipment_open_quantity": shipment_open.get(line.id, line.quantity),
                }
                for line in lines
            ],
        }
    )
    return body


def require_sales_order(session: Session, so_id: int) -> SalesOrder:
    row = session.execute(
        select(SalesOrder).where(SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"sales_order_id": so_id})
    return row


# ── 생성 착지 (참조 생성 오케스트레이터·인테이크 확정이 부른다) ──────────────────────────


def _check_delivery_date(value: date | None, doc_date: date, *, field: str) -> date | None:
    """요청납기는 증빙일 이전일 수 없다(미래는 자유). None은 그대로."""
    if value is not None and value < doc_date:
        raise invalid(field, "요청납기는 수주 증빙일보다 앞설 수 없습니다.")
    return value


def _check_po_date(value: date | None) -> date | None:
    if value is not None and value > today_kst():
        raise invalid("buyer_po_date", "바이어 PO 일자는 오늘(KST)보다 미래일 수 없습니다.")
    return value


def _new_line(
    header: SalesOrder,
    item: NewSoLine,
    amount: int,
    line_no: int,
    actor_id: int,
) -> SalesOrderLine:
    snap = item.snapshot
    return SalesOrderLine(
        so_id=header.id,
        currency=header.currency,
        line_no=line_no,
        sku_id=snap.sku_id,
        sku_code=snap.sku_code,
        sku_name_ko=snap.sku_name_ko,
        sku_name_en=snap.sku_name_en,
        sku_kind=snap.sku_kind,
        quantity=item.quantity,
        requested_delivery_date=item.requested_delivery_date,
        qt_line_id=item.qt_line_id,
        pi_line_id=item.pi_line_id,
        buyer_item_code=snap.buyer_item_code,
        unit_price_amount=snap.unit_price_amount,
        list_price_amount=snap.list_price_amount,
        line_amount=amount,
        price_basis=snap.price_basis,
        is_free=snap.is_free,
        price_reason=snap.price_reason,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )


def _check_copy_source(session: Session, source_id: int, buyer_partner_id: int) -> None:
    """복제 원본 SO 자격 — **같은 바이어·죽은 상태(취소)·살아 있는 복제본 없음**(X-08·X-13 — 살아 있는 전표 복제=중복 생성 차단).

    원본 행을 `FOR UPDATE`로 잠가(잠금 순서 (5), 원천 QT·PI 뒤) 같은 원본의 동시 복제를 직렬화한다.
    """
    source = session.execute(
        select(SalesOrder)
        .where(SalesOrder.id == source_id, SalesOrder.deleted_at.is_(None))
        .with_for_update()
    ).scalar_one_or_none()
    if (
        source is None
        or source.buyer_partner_id != buyer_partner_id
        or source.status not in DEAD_STATUSES
        or has_live_copy(session, source_id)
    ):
        raise AppError(
            ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE, log_context={"source_id": source_id}
        )


def lock_copy_source(session: Session, source_id: int | None) -> None:
    """복제 원본 SO 행을 `FOR UPDATE`로 **미리** 잠근다 — 참조 생성 오케스트레이터가 원천 라인(8)을 잠그기 *전에* 부른다(잠금 순서 SO(5)→라인(8)).

    자격 검사는 하지 않는다(`create_received_sales_order`의 `_check_copy_source`가 같은 행을 다시 잠그고 검사한다 — 같은 트랜잭션이라 무해).
    """
    if source_id is None:
        return
    session.execute(
        select(SalesOrder.id)
        .where(SalesOrder.id == source_id, SalesOrder.deleted_at.is_(None))
        .with_for_update()
    ).first()


def has_live_copy(session: Session, source_id: int) -> bool:
    """원본에서 복제된 **살아 있는** SO가 이미 있는가(취소된 복제본은 세지 않는다 — X-08)."""
    return bool(
        session.execute(
            select(func.count())
            .select_from(SalesOrder)
            .where(
                SalesOrder.copied_from_id == source_id,
                SalesOrder.deleted_at.is_(None),
                SalesOrder.status.notin_(DEAD_STATUSES),
            )
        ).scalar_one()
    )


def create_received_sales_order(
    session: Session, *, actor: AuthenticatedUser, draft: SalesOrderDraft
) -> SalesOrder:
    """SO 1건 INSERT(RECEIVED) — **SO 생성의 단일 착지**. 호출자의 트랜잭션에 합류한다(자체 커밋 없음).

    순서: 바이어 유형(FOR KEY SHARE) → 통화·시장·증빙일·담당자 → PO번호 정규화·**중복 선검사**(409) → 복제 원본 → 라인 검증(중복 SKU·
    수량·요청납기·건수) → **채번(마지막)** → INSERT+탄생 이력+이벤트 → 라인 INSERT. 선검사를 빠져나간 동시 요청은 DB 부분 유니크가
    잡고 `guarded_flush`가 같은 409로 번역한다. 승격 코드는 **RECEIVED로만** 만든다 — `confirm`을 부르지 않는다(B9 우회 차단표 #3).
    """
    # `**draft.payment_columns`·`**draft.incoterm_columns` 전개는 임의 키(status·confirmed_at·total_amount 등)를 못 싣는다 —
    # 허용 키 집합 밖이면 프로그래밍 오류로 즉시 거부한다(상태·번호·합계 통로 우회 차단, test_doc_status_channel이 이 허용 항목의 근거로 대사).
    if (
        set(draft.payment_columns) - PAYMENT_COLUMN_KEYS
        or set(draft.incoterm_columns) - INCOTERM_COLUMN_KEYS
    ):
        raise ValueError("SalesOrderDraft의 조건 열 dict에 허용되지 않은 키가 있습니다.")
    partner = partners.require_partner_of_any_type(
        session,
        draft.buyer_partner_id,
        ("BUYER",),
        field="buyer_partner_id",
        type_label="바이어",
        lock=True,
    )
    currency = require_known_currency(draft.currency)
    dest = str(draft.dest_market_code).strip().upper()
    markets.require_active_market_code(session, dest, field="dest_market_code")
    doc_date = draft.doc_date or today_kst()
    check_doc_date(doc_date)
    assignee_id = draft.assignee_id or actor.id
    if draft.assignee_id is not None:
        require_active_user(session, assignee_id)
    buyer_name = blank_to_none(draft.buyer_name) or partner.name_en or partner.name_ko
    po_no, po_key = po_columns(draft.buyer_po_no)
    if po_key is not None:
        require_po_free(session, partner.id, po_key)
    _check_po_date(draft.buyer_po_date)
    if draft.copied_from_id is not None:
        _check_copy_source(session, draft.copied_from_id, partner.id)

    editing.require_line_capacity(0, len(draft.lines))
    seen: set[tuple[int, bool]] = set()
    for index, item in enumerate(draft.lines):
        validate_quantity(item.quantity, field=f"lines[{index}].quantity")
        _check_delivery_date(
            item.requested_delivery_date, doc_date, field=f"lines[{index}].requested_delivery_date"
        )
        key = (item.snapshot.sku_id, item.snapshot.is_free)
        if key in seen:
            raise AppError(
                ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE,
                detail={f"lines[{index}].sku_id": item.snapshot.sku_code},
            )
        seen.add(key)
    amounts = [line_amount(item.quantity, item.snapshot.unit_price_amount) for item in draft.lines]
    total = editing.compute_total(amounts)

    fx_rate, fx_rate_date = draft.fx_rate, draft.fx_rate_date
    if fx_rate is None and currency == "KRW":
        fx_rate, fx_rate_date = Decimal(1), doc_date

    number = issue_document_number(session, KIND)  # 잠금 순서 (9) — 항상 마지막
    row = SalesOrder(
        doc_number=number,
        doc_date=doc_date,
        currency=currency,
        total_amount=total,
        last_line_no=len(draft.lines),
        buyer_partner_id=partner.id,
        buyer_name=buyer_name,
        dest_market_code=dest,
        qt_id=draft.qt_id,
        pi_id=draft.pi_id,
        buyer_po_no=po_no,
        buyer_po_no_key=po_key,
        buyer_po_date=draft.buyer_po_date,
        fx_rate=fx_rate,
        fx_rate_date=fx_rate_date,
        assignee_id=assignee_id,
        internal_note=blank_to_none(draft.internal_note),
        copied_from_id=draft.copied_from_id,
        created_by_id=actor.id,
        updated_by_id=actor.id,
        **draft.payment_columns,
        **draft.incoterm_columns,
    )
    with guarded_flush(session, buyer_partner_id=partner.id, po_key=po_key):
        record_birth(
            session, row, actor_user_id=actor.id
        )  # status 대입·INSERT·탄생 이력·created 이벤트
        for line_no, (item, amount) in enumerate(zip(draft.lines, amounts, strict=True), start=1):
            session.add(_new_line(row, item, amount, line_no, actor.id))
        session.flush()
    return row


# ── 조회 ───────────────────────────────────────────────────────────────────


def get_sales_order(so_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(uow.session, require_sales_order(uow.session, so_id))


def _list_filters(
    *,
    status: str | None,
    qt_id: int | None,
    pi_id: int | None,
    buyer_partner_id: int | None,
    assignee_id: int | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conditions: list[Any] = [SalesOrder.deleted_at.is_(None)]
    if status:
        conditions.append(SalesOrder.status == status)
    if qt_id:
        conditions.append(SalesOrder.qt_id == qt_id)
    if pi_id:
        conditions.append(SalesOrder.pi_id == pi_id)
    if buyer_partner_id:
        conditions.append(SalesOrder.buyer_partner_id == buyer_partner_id)
    if assignee_id:
        conditions.append(SalesOrder.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                SalesOrder.doc_number.icontains(needle, autoescape=True),
                SalesOrder.buyer_name.icontains(needle, autoescape=True),
                SalesOrder.buyer_po_no.icontains(needle, autoescape=True),
            )
        )
    if date_from:
        conditions.append(SalesOrder.doc_date >= date_from)
    if date_to:
        conditions.append(SalesOrder.doc_date <= date_to)
    return conditions


def list_sales_orders(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
    qt_id: int | None = None,
    pi_id: int | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    conditions = _list_filters(
        status=status,
        qt_id=qt_id,
        pi_id=pi_id,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(SalesOrder).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(SalesOrder)
                .where(*conditions)
                .order_by(SalesOrder.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        qt_numbers = _doc_numbers(session, _QUOTATIONS, {r.qt_id for r in rows if r.qt_id})
        pi_numbers = _doc_numbers(session, _PROFORMAS, {r.pi_id for r in rows if r.pi_id})
        return [
            _summary_body(row, qt_numbers.get(row.qt_id or 0), pi_numbers.get(row.pi_id or 0))
            for row in rows
        ], total


EXPORT_HEADER: tuple[str, ...] = (
    "수주번호",
    "증빙일",
    "상태",
    "바이어",
    "바이어PO",
    "PO일자",
    "견적번호",
    "PI번호",
    "목적지",
    "통화",
    "합계",
    "결제유형",
    "선수금(%)",
    "Incoterms",
    "담당자",
    "생성일",
)


def export_rows(
    *,
    status: str | None = None,
    qt_id: int | None = None,
    pi_id: int | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[tuple[Any, ...]]:
    """CSV 행(헤더 단위). 상한 초과는 422 — 조건을 좁히게 한다(전 목록 내보내기 §12.2). 원가·마진 열은 없다(판가 서류 값)."""
    conditions = _list_filters(
        status=status,
        qt_id=qt_id,
        pi_id=pi_id,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(SalesOrder).where(*conditions)
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({total:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        rows = session.execute(
            select(SalesOrder, User.display_name)
            .outerjoin(User, User.id == SalesOrder.assignee_id)
            .where(*conditions)
            .order_by(SalesOrder.id)
        ).all()
        qt_numbers = _doc_numbers(session, _QUOTATIONS, {r.qt_id for r, _ in rows if r.qt_id})
        pi_numbers = _doc_numbers(session, _PROFORMAS, {r.pi_id for r, _ in rows if r.pi_id})
        return [
            (
                r.doc_number,
                r.doc_date.isoformat(),
                r.status,
                r.buyer_name,
                r.buyer_po_no or "",
                r.buyer_po_date.isoformat() if r.buyer_po_date else "",
                qt_numbers.get(r.qt_id or 0, ""),
                pi_numbers.get(r.pi_id or 0, ""),
                r.dest_market_code,
                r.currency,
                money_text(r.total_amount, r.currency),
                r.payment_type or "",
                advance_pct_text(r.advance_pct_bp) or "",
                f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
                name or "",
                created_date_kst(r.created_at),
            )
            for r, name in rows
        ]


def list_status_log(*, so_id: int, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_sales_order(session, so_id)
        total = session.execute(
            select(func.count())
            .select_from(SalesOrderStatusLog)
            .where(SalesOrderStatusLog.sales_order_id == so_id)
        ).scalar_one()
        rows = session.execute(
            select(SalesOrderStatusLog, User.display_name)
            .outerjoin(User, User.id == SalesOrderStatusLog.actor_user_id)
            .where(SalesOrderStatusLog.sales_order_id == so_id)
            .order_by(SalesOrderStatusLog.id.desc())
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
                "approval_id": log.approval_id,
            }
            for log, name in rows
        ], total


# ── 편집 ───────────────────────────────────────────────────────────────────

_FREE_FIELDS = ("internal_note", "assignee_id")

#: PATCH가 받는 CONTENT 요청 필드(FREE 제외) — 스키마 필드 집합과 같은지 테스트가 대사한다.
CONTENT_REQUEST_FIELDS: frozenset[str] = frozenset(
    {
        "doc_date",
        "dest_market_code",
        "fx_rate",
        "fx_rate_date",
        "payment_terms",
        "incoterm",
        "buyer_name",
        "buyer_po_no",
        "buyer_po_date",
    }
)


def _terms_of(row: SalesOrder) -> dict[str, Any]:
    return {
        "payment_type": row.payment_type,
        "advance_pct_bp": row.advance_pct_bp,
        "balance_anchor": row.balance_anchor,
        "balance_days": row.balance_days,
    }


def _source_floor_date(session: Session, row: SalesOrder) -> date | None:
    """참조 수주의 증빙일 하한 — 원천 QT·PI 증빙일 중 늦은 쪽(후속 `doc_date >= 원천 doc_date`, A10)."""
    dates: list[date] = []
    for tbl, source_id in ((_QUOTATIONS, row.qt_id), (_PROFORMAS, row.pi_id)):
        if source_id is None:
            continue
        found = session.execute(
            select(tbl.c.doc_date).where(tbl.c.id == source_id)
        ).scalar_one_or_none()
        if found is not None:
            dates.append(found)
    return sorted(dates)[-1] if dates else None


def _header_columns(session: Session, values: dict[str, Any], row: SalesOrder) -> dict[str, Any]:
    """요청 필드(부분) → 검증된 헤더 컬럼 값 — **보낸 필드만** 바뀐다. 얽힌 필드(환율·결제조건)는 통째로 다시 검증한다."""
    cols: dict[str, Any] = {}
    for name in _NOT_NULLABLE & values.keys():
        if values[name] is None:
            raise invalid(name, "이 항목은 비울 수 없습니다.")

    doc_date = values.get("doc_date", row.doc_date)
    if "doc_date" in values:
        check_doc_date(doc_date)
        floor = _source_floor_date(session, row)
        if floor is not None and doc_date < floor:
            raise invalid("doc_date", "증빙일은 원천 문서의 증빙일보다 앞설 수 없습니다.")
        earliest = session.execute(
            select(func.min(SalesOrderLine.requested_delivery_date)).where(
                SalesOrderLine.so_id == row.id, SalesOrderLine.deleted_at.is_(None)
            )
        ).scalar_one()
        if earliest is not None and earliest < doc_date:
            raise invalid(
                "doc_date",
                "라인의 요청납기가 새 증빙일보다 앞섭니다. 요청납기를 먼저 고치거나 더 이른 증빙일을 입력해 주세요.",
            )
        cols["doc_date"] = doc_date

    if "buyer_name" in values:
        new_name = blank_to_none(values["buyer_name"])
        if new_name is None:
            raise invalid("buyer_name", "바이어 표기를 비울 수 없습니다.")
        cols["buyer_name"] = new_name

    if "dest_market_code" in values:
        if row.qt_id is not None:
            raise invalid(
                "dest_market_code",
                "견적·PI에서 만든 수주는 목적지 시장을 바꿀 수 없습니다. 원천과 어긋나면 새 견적으로 작성해 주세요.",
            )
        code = str(values["dest_market_code"]).strip().upper()
        markets.require_active_market_code(session, code, field="dest_market_code")
        cols["dest_market_code"] = code

    if {"doc_date", "fx_rate", "fx_rate_date"} & values.keys():
        rate_raw = values.get("fx_rate", rate_text(row.fx_rate))
        rate_date = values.get("fx_rate_date", row.fx_rate_date)
        rate, rate_on = resolve_fx(row.currency, rate_raw, rate_date, doc_date)
        cols["fx_rate"], cols["fx_rate_date"] = rate, rate_on

    if "payment_terms" in values:
        terms = build_payment_terms(values["payment_terms"])
        if terms is None or terms.columns() != _terms_of(row):
            require_lc_enabled(session, terms)  # 새로 입력하는 L/C만 — 동일값은 검사하지 않는다
        cols.update(terms.columns() if terms else EMPTY_TERMS_COLUMNS)
    if "incoterm" in values:
        incoterm = build_incoterm(values["incoterm"])
        cols.update(incoterm.columns() if incoterm else EMPTY_INCOTERM_COLUMNS)

    if "buyer_po_no" in values:
        po_no, po_key = po_columns(values["buyer_po_no"])
        if po_key is not None and po_key != row.buyer_po_no_key:
            require_po_free(session, row.buyer_partner_id, po_key, exclude_id=row.id)
        cols["buyer_po_no"], cols["buyer_po_no_key"] = po_no, po_key
    if "buyer_po_date" in values:
        cols["buyer_po_date"] = _check_po_date(values["buyer_po_date"])

    if "internal_note" in values:
        cols["internal_note"] = blank_to_none(values["internal_note"])
    if "assignee_id" in values and values["assignee_id"] is not None:
        require_active_user(session, values["assignee_id"])
        cols["assignee_id"] = values["assignee_id"]
    return cols


def _content_changes(row: SalesOrder, cols: dict[str, Any]) -> list[str]:
    """검증된 컬럼 값 중 현재값과 다른 CONTENT/ORIGIN 열 이름."""
    content = columns_of("sales_orders", ColumnClass.CONTENT, ColumnClass.ORIGIN)
    return sorted(
        name for name, value in cols.items() if name in content and getattr(row, name) != value
    )


#: 승인 결속 digest(`digest.gate_input_digest`)의 입력 중 SO 헤더에서 편집할 수 있는 열 — 거래처·통화는 ORIGIN이라 편집 경로가 없다.
#: 라인은 SKU·수량·단가·무상 표지가 입력이다(라인 추가·제외는 항상 입력 변경). 여기 없는 열(메모·담당자·PO번호·납기 등)을 고쳐도 승인은 유지된다.
GATE_INPUT_HEADER_COLUMNS = frozenset({"fx_rate", "fx_rate_date"})


def void_open_approval_on_input_change(
    session: Session, *, so_id: int, actor_id: int, reason_code: VoidReasonCode
) -> int:
    """**편집·취소 경로의 즉시 청소 훅**(eager) — 이 SO의 열린(요청됨·승인됨) 여신 초과 승인을 같은 트랜잭션에서 무효화한다. 없으면 0.

    호출자는 SO 행을 이미 `FOR UPDATE`로 잡았다(전역 잠금 순서 (5) → approvals (7)). 승인 코어의 지연 검증(결정·소비 시점 digest·통화·상한 재검증)이 훅이 빠진 경로의 백스톱이다 —
    eager(결재함 위생·재요청 유도)+lazy(안전망) 둘 다 둔다. 승인을 **부여하지 않고 좁히기만** 한다.
    """
    return approvals_service.void_for_target(
        session,
        approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value,
        target_id=so_id,
        actor_user_id=actor_id,
        reason_code=reason_code,
    )


def update_sales_order(
    *, actor: AuthenticatedUser, so_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """헤더 편집 — 접수(RECEIVED)에서만 CONTENT를 고칠 수 있다. 동결 후 CONTENT가 달라지면 409 FROZEN(필드명만).

    동결(확정 이후)·보류 중 전표에서는 CONTENT 필드를 **어떤 값으로든** 바꿀 수 없으므로, 값 형식 검증이 먼저 실패해도 응답은 검증
    오류가 아니라 FROZEN이다(바꿀 수 없는 것을 검증해 주지 않는다).
    """
    values = {k: v for k, v in payload.items() if k != "version"}
    content_keys = sorted(CONTENT_REQUEST_FIELDS & values.keys())
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, SalesOrder, so_id, expected_version=payload["version"])
        editable = row.status in EDITABLE_STATES[KIND]
        try:
            cols = _header_columns(session, values, row)
        except AppError as exc:
            # 동결·보류·취소 SO에서는 값 검증 오류 **와 중복 PO(점유 문서 정보)** 보다 FROZEN 409가 먼저다(점유 문서 노출 방지)
            if (
                not editable
                and content_keys
                and exc.code
                in (
                    ErrorCode.VALIDATION_INVALID_FIELD,
                    ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO,
                )
            ):
                editing.assert_editable(KIND, row.status, fields=content_keys)
            raise
        changed = _content_changes(row, cols)
        if changed:
            editing.assert_editable(KIND, row.status, fields=changed)
        if GATE_INPUT_HEADER_COLUMNS & set(changed):
            # 환율 변경은 여신 환산·노출을 바꾼다 — 열린 승인을 같은 트랜잭션에서 무효화한다(승인 후 불변).
            void_open_approval_on_input_change(
                session, so_id=row.id, actor_id=actor.id, reason_code=VoidReasonCode.TARGET_CHANGED
            )
        # ★ `begin_nested()`는 진입 시 세션을 먼저 flush한다 — 변경을 **SAVEPOINT 안에서** 대입해야 유니크 경합 위반이 번역 경로로 들어온다
        #   (밖에서 대입하면 진입 flush의 IntegrityError가 세션을 깨뜨려 PendingRollbackError 500이 된다).
        with guarded_flush(
            session,
            buyer_partner_id=row.buyer_partner_id,
            po_key=cols.get("buyer_po_no_key", row.buyer_po_no_key),
        ):
            for name, value in cols.items():
                if getattr(row, name) != value:
                    setattr(row, name, value)
            row.updated_by_id = actor.id
            session.flush()
        return detail_body(session, row)


def update_meta(*, actor: AuthenticatedUser, so_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """FREE 열(내부 메모·담당자)만 — 동결 후에도 허용된다(인계·오기 정정). **실제 변경이 없으면 version·updated_by를 건드리지 않는다**."""
    values = {k: v for k, v in payload.items() if k in _FREE_FIELDS}
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, SalesOrder, so_id, expected_version=payload["version"])
        if "assignee_id" in values and values["assignee_id"] is None:
            raise invalid("assignee_id", "담당자를 비울 수 없습니다.")
        changed = False
        if "assignee_id" in values:
            require_active_user(session, values["assignee_id"])
            if row.assignee_id != values["assignee_id"]:
                row.assignee_id = values["assignee_id"]
                changed = True
        if "internal_note" in values:
            note = blank_to_none(values["internal_note"])
            if row.internal_note != note:
                row.internal_note = note
                changed = True
        if changed:
            row.updated_by_id = actor.id
            session.flush()
        return detail_body(session, row)


# ── 라인 3함수 — assert_editable → 헤더 FOR UPDATE → version 대조 → 헤더 version 상승 → 합계 재계산 ──


def _require_line(session: Session, header: SalesOrder, line_id: int) -> SalesOrderLine:
    """다른 전표의 라인 id를 섞으면 404 — IDOR 방지(라인 단독 라우트 없음, 소속 단언)."""
    line = session.execute(
        select(SalesOrderLine).where(
            SalesOrderLine.id == line_id,
            SalesOrderLine.so_id == header.id,
            SalesOrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise NotFoundError(log_context={"sales_order_id": header.id, "line_id": line_id})
    return line


def _duplicate_exists(
    session: Session, so_id: int, sku_id: int, is_free: bool, *, exclude_line_id: int | None = None
) -> bool:
    query = (
        select(func.count())
        .select_from(SalesOrderLine)
        .where(
            SalesOrderLine.so_id == so_id,
            SalesOrderLine.sku_id == sku_id,
            SalesOrderLine.is_free.is_(is_free),
            SalesOrderLine.deleted_at.is_(None),
        )
    )
    if exclude_line_id is not None:
        query = query.where(SalesOrderLine.id != exclude_line_id)
    return bool(session.execute(query).scalar_one())


def _finish_line_change(
    session: Session,
    actor: AuthenticatedUser,
    header: SalesOrder,
    line: SalesOrderLine | None,
    *,
    last_line_no: int | None = None,
) -> dict[str, Any]:
    """라인 변경 마무리 — 합계 재계산 → 헤더 version 상승(단일 UPDATE) → 갱신된 version·합계를 응답에.

    ★ 헤더를 dirty로 만드는 대입은 **라인 flush 뒤**에 몰아서 한다(합계 SUM 쿼리의 autoflush가 헤더 UPDATE를 먼저 내보내면
      version이 두 번 오른다 — 라인 변경 1건 = 헤더 version +1이 응답 계약이다).
    """
    session.flush()
    editing.recompute_total(session, KIND, header, SalesOrderLine)
    if last_line_no is not None:
        header.last_line_no = (
            last_line_no  # 결번 허용·재사용 금지 — 카운터는 헤더 잠금 하에서만 오른다
        )
    editing.bump_header_version(header)
    header.updated_by_id = actor.id
    session.flush()
    body: dict[str, Any] = {
        "line": None,
        "header_version": header.version,
        "total_amount": header.total_amount,
        "total_text": money_text(header.total_amount, header.currency),
    }
    if line is not None:
        statuses = sku_statuses(session, [line.sku_id])
        body["line"] = _line_body(
            line,
            sku_status=statuses.get(line.sku_id),
            source=_source_map(session, [line]).get(line.id),
        )
    return body


def _source_line_kind(header: SalesOrder) -> tuple[str, DocKind, int] | None:
    """참조 수주의 원천 라인 종류·전표 종류·원천 헤더 id — 직접 수주는 None. PI 경유는 PI 라인, QT 직접은 QT 라인."""
    if header.pi_id is not None:
        return "PI_LINE", DocKind.PROFORMA_INVOICE, header.pi_id
    if header.qt_id is not None:
        return "QT_LINE", DocKind.QUOTATION, header.qt_id
    return None


def _require_source_capacity(
    session: Session, header: SalesOrder, source_line_id: int, delta: int
) -> None:
    """원천 라인을 `FOR UPDATE`로 잠그고 잔량을 다시 계산해 이 SO가 `delta`만큼 더 가져갈 수 있는지 본다(초과 409).

    잠금 순서 SO(5) → 라인(8). 잔량은 살아 있는 후속 라인 SUM 파생이라 이 SO의 현재 수량도 이미 소비로 세어져 있다.
    """
    spec = _source_line_kind(header)
    assert spec is not None
    line_kind, source_kind, source_header_id = spec
    locked = lock_source_lines(session, source_kind, source_header_id, [source_line_id])
    if not locked:
        raise invalid("sku_id", "원천 라인을 찾을 수 없습니다. 화면을 새로 고쳐 주세요.")
    quantities: dict[int, OpenQuantity] = open_quantity(
        session,
        line_kind,  # type: ignore[arg-type]
        locked,
    )
    require_within_open(quantities, {source_line_id: delta})


_SRC_COLUMNS = (
    ("id", Integer),
    ("sku_id", Integer),
    ("sku_code", String),
    ("sku_name_ko", String),
    ("sku_name_en", String),
    ("sku_kind", String),
    ("buyer_item_code", String),
    ("quantity", Integer),
    ("unit_price_amount", BigInteger),
    ("list_price_amount", BigInteger),
    ("price_basis", String),
    ("is_free", Boolean),
    ("price_reason", String),
    ("deleted_at", DateTime),
)


def _source_table(name: str, fk_name: str) -> Any:
    """원천 라인 표(이름 기반 Core — L1은 다른 L1의 모델을 임포트하지 못한다). 값 복사에 필요한 열만 선언한다."""
    return table(
        name,
        *(column(c, t) for c, t in _SRC_COLUMNS),
        column(fk_name, Integer),
    )


_SRC_PI_LINES = _source_table("proforma_invoice_lines", "pi_id")
_SRC_QT_LINES = _source_table("quotation_lines", "qt_id")


def _find_source_line_for_sku(
    session: Session, header: SalesOrder, sku_id: int, is_free: bool
) -> Any | None:
    """참조 수주에 라인을 (다시) 추가할 때 값을 복사할 원천 라인 — 같은 (SKU, 유무상)의 살아 있는 원천 라인."""
    spec = _source_line_kind(header)
    assert spec is not None
    line_kind, _source_kind, source_header_id = spec
    tbl, fk = (_SRC_PI_LINES, "pi_id") if line_kind == "PI_LINE" else (_SRC_QT_LINES, "qt_id")
    return session.execute(
        select(*[tbl.c[c] for c, _ in _SRC_COLUMNS[:-1]]).where(
            tbl.c[fk] == source_header_id,
            tbl.c.sku_id == sku_id,
            tbl.c.is_free.is_(is_free),
            tbl.c.deleted_at.is_(None),
        )
    ).first()


def add_line(*, actor: AuthenticatedUser, so_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """라인 추가 — 직접 수주는 마스터 값을 1회 스냅샷하고, 참조 수주는 **원천에 있는 SKU만** 원천 라인 값을 복사해 (다시) 추가한다.

    자동 병합 없음: 같은 SKU 유상·무상 각 1줄까지. 참조 수주 재추가는 원천 잔량 안에서만(잠금 후 재계산, 초과 409).
    """
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, SalesOrder, so_id, expected_version=payload["version"])
        editing.assert_editable(KIND, header.status)
        editing.require_line_capacity(
            editing.count_live_lines(session, KIND, header.id, SalesOrderLine)
        )
        # 라인 추가는 항상 게이트 입력(노출)을 바꾼다 — 열린 승인 무효화(검증이 뒤에서 실패하면 트랜잭션 전체가 롤백된다).
        void_open_approval_on_input_change(
            session, so_id=header.id, actor_id=actor.id, reason_code=VoidReasonCode.TARGET_CHANGED
        )
        raw = {k: v for k, v in payload.items() if k != "version"}
        sku = require_sellable_sku(session, raw["sku_id"], KIND)  # 단종은 저장 허용(확정 시 재검사)
        is_free = bool(raw.get("is_free", False))
        if _duplicate_exists(session, header.id, sku.id, is_free):
            raise AppError(ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={"sku_id": sku.sku_code})
        delivery = _check_delivery_date(
            raw.get("requested_delivery_date"), header.doc_date, field="requested_delivery_date"
        )
        quantity = validate_quantity(raw["quantity"])
        qt_line_id = pi_line_id = None
        if header.qt_id is None:
            snap = snapshot_line_from_master(
                session,
                sku=sku,
                buyer_partner_id=header.buyer_partner_id,
                currency=header.currency,
                doc_date=header.doc_date,
                unit_price=raw.get("unit_price"),
                is_free=is_free,
                price_reason=raw.get("price_reason"),
                buyer_item_code=raw.get("buyer_item_code"),
            )
        else:
            forbidden = [k for k in ("unit_price", "price_reason", "buyer_item_code") if k in raw]
            if forbidden or is_free:
                raise invalid(
                    forbidden[0] if forbidden else "is_free",
                    "견적·PI에서 만든 수주에 다시 추가하는 품목은 원천 값이 복사됩니다. 추가한 뒤 라인을 수정해 주세요.",
                )
            source = _find_source_line_for_sku(session, header, sku.id, is_free)
            if source is None:
                raise invalid(
                    "sku_id",
                    "원천 문서에 없는 품목은 추가할 수 없습니다. 품목을 더하려면 새 견적 개정본을 작성해 주세요.",
                )
            _require_source_capacity(session, header, int(source.id), quantity)
            snap = snapshot_line_from_source(source)
            if header.pi_id is not None:
                pi_line_id = int(source.id)
            else:
                qt_line_id = int(source.id)
        amount = line_amount(quantity, snap.unit_price_amount)
        line_no = header.last_line_no + 1  # 결번 허용·재사용 금지(카운터 대입은 finish에서)
        line = _new_line(
            header,
            NewSoLine(snap, quantity, delivery, qt_line_id=qt_line_id, pi_line_id=pi_line_id),
            amount,
            line_no,
            actor.id,
        )
        session.add(line)
        return _finish_line_change(session, actor, header, line, last_line_no=line_no)


def update_line(
    *, actor: AuthenticatedUser, so_id: int, line_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """라인 제자리 수정(안정 id) — 수량·단가·무상·사유·바이어 품번·요청납기. 참조 수주의 수량 증가는 원천 잔량 안에서만(초과 409)."""
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, SalesOrder, so_id, expected_version=payload["version"])
        editing.assert_editable(KIND, header.status)
        line = _require_line(session, header, line_id)
        raw = {k: v for k, v in payload.items() if k != "version"}
        new_quantity = line.quantity
        if "quantity" in raw:
            if raw["quantity"] is None:
                raise invalid("quantity", "수량을 비울 수 없습니다.")
            new_quantity = validate_quantity(raw["quantity"])
        repriced: tuple[int, str, bool, str | None] | None = None
        if {"unit_price", "is_free", "price_reason"} & raw.keys():
            repriced = reprice(
                line,
                header.currency,
                unit_price=raw.get("unit_price"),
                is_free=raw.get("is_free"),
                price_reason=raw.get("price_reason"),
            )
            if repriced[2] != line.is_free and _duplicate_exists(
                session, header.id, line.sku_id, repriced[2], exclude_line_id=line.id
            ):
                raise AppError(
                    ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={"sku_id": line.sku_code}
                )
        # 게이트 입력(수량·단가·무상 표지)이 실제로 바뀔 때만 열린 승인을 무효화한다(납기·품번·사유 수정은 승인을 건드리지 않는다).
        inputs_change = new_quantity != line.quantity or (
            repriced is not None
            and (repriced[0], repriced[2]) != (line.unit_price_amount, line.is_free)
        )
        if inputs_change:
            void_open_approval_on_input_change(
                session,
                so_id=header.id,
                actor_id=actor.id,
                reason_code=VoidReasonCode.TARGET_CHANGED,
            )
        # 원천 라인 잠금((8) — 참조 수주의 수량 증가는 원천 잔량 안에서)은 승인 무효화((7))보다 **뒤**다 — 전역 LOCK_ORDER(approvals → lines).
        source_line = line.pi_line_id or line.qt_line_id
        if source_line is not None and new_quantity > line.quantity:
            _require_source_capacity(session, header, source_line, new_quantity - line.quantity)
        line.quantity = new_quantity
        if repriced is not None:
            unit, basis, free, reason = repriced
            line.unit_price_amount, line.price_basis = unit, basis
            line.is_free, line.price_reason = free, reason
        if "buyer_item_code" in raw:
            line.buyer_item_code = resolve_buyer_item_code(
                session,
                header.buyer_partner_id,
                line.sku_id,
                raw["buyer_item_code"],
                field="buyer_item_code",
            )
        if "requested_delivery_date" in raw:
            line.requested_delivery_date = _check_delivery_date(
                raw["requested_delivery_date"], header.doc_date, field="requested_delivery_date"
            )
        line.line_amount = line_amount(line.quantity, line.unit_price_amount)
        line.updated_by_id = actor.id
        line.updated_at = utcnow()
        return _finish_line_change(session, actor, header, line)


def remove_line(
    *, actor: AuthenticatedUser, so_id: int, line_id: int, version: int
) -> dict[str, Any]:
    """라인 제외(soft delete) — 라인 번호는 재사용하지 않고, 원천 소비량에서 즉시 빠진다(파생 SUM)."""
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, SalesOrder, so_id, expected_version=version)
        editing.assert_editable(KIND, header.status)
        line = _require_line(session, header, line_id)
        void_open_approval_on_input_change(
            session, so_id=header.id, actor_id=actor.id, reason_code=VoidReasonCode.TARGET_CHANGED
        )
        line.deleted_at = utcnow()
        line.updated_by_id = actor.id
        return _finish_line_change(session, actor, header, None)
