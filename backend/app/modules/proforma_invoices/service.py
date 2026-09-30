"""PI 서비스 — 생성 착지·조회·FREE 열 편집·상태이력·CSV (S3-1 ADR-0052·0053 / design-A A4·A8).

L1: 이 모듈은 **전이를 하지 않는다**(입금 수렴·만료·취소는 trade_chain L2). PI는 초안이 없어 **라인 편집·헤더 CONTENT
편집 API가 아예 없다**(생성=발행=동결) — 여기서 하는 일은 (1) 참조 생성 오케스트레이터가 부르는 `insert_issued`(채번·INSERT·
탄생 이력·이벤트, 한 트랜잭션), (2) 조회, (3) 동결 후에도 고칠 수 있는 FREE 열(내부 메모·담당자) 편집이다.

■ 모든 쓰기의 순서 — 헤더 `FOR UPDATE` → `version` 대조(409) → 변경. 채번은 **생성 트랜잭션의 마지막 단계**(잠금 순서 (9)).
■ 감사: 전표 생성·전이·편집은 audit_log에 이중 기록하지 않는다(상태이력+아웃박스가 정본 — X-24).
■ 원천 QT의 문서번호는 표시용이라 테이블 이름 기반 Core 쿼리로 읽는다(L1은 다른 L1의 모델을 임포트하지 못한다 — 계층 DAG).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Integer, String, column, func, or_, select, table
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import NotFoundError
from app.core.money import minor_units
from app.core.time import today_kst
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser
from app.modules.proforma_invoices.models import (
    BANK_SNAPSHOT_COLUMNS,
    ProformaInvoice,
    ProformaInvoiceLine,
)
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.doc_number import issue_document_number
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.models import ProformaInvoiceStatusLog
from app.modules.trade_docs.payment_terms import split_advance
from app.modules.trade_docs.snapshot import LineSnapshot, line_amount
from app.modules.trade_docs.transition import record_birth
from app.modules.trade_docs.validation import invalid, require_active_user
from app.modules.trade_docs.views import (
    created_date_kst,
    incoterm_body,
    money_text,
    payment_terms_body,
    rate_text,
)

KIND = DocKind.PROFORMA_INVOICE
EXPORT_MAX_ROWS = 50_000

_QUOTATIONS = table("quotations", column("id", Integer), column("doc_number", String))


@dataclass(frozen=True, slots=True)
class NewPiLine:
    """생성 트랜잭션이 넣을 PI 라인 한 줄 — 원천 QT 라인 id·값 스냅샷(복사)·수량."""

    qt_line_id: int
    snapshot: LineSnapshot
    quantity: int


# ── 뷰 ─────────────────────────────────────────────────────────────────────


def qt_doc_numbers(session: Session, qt_ids: list[int]) -> dict[int, str]:
    if not qt_ids:
        return {}
    return {
        int(r[0]): str(r[1])
        for r in session.execute(
            select(_QUOTATIONS.c.id, _QUOTATIONS.c.doc_number).where(
                _QUOTATIONS.c.id.in_(set(qt_ids))
            )
        ).all()
    }


def _line_body(line: ProformaInvoiceLine) -> dict[str, Any]:
    cur = line.currency
    return {
        "id": line.id,
        "line_no": line.line_no,
        "qt_line_id": line.qt_line_id,
        "sku_id": line.sku_id,
        "sku_code": line.sku_code,
        "sku_name_ko": line.sku_name_ko,
        "sku_name_en": line.sku_name_en,
        "sku_kind": line.sku_kind,
        "quantity": line.quantity,
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
    }


def _summary_body(row: ProformaInvoice, qt_number: str | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "doc_number": row.doc_number,
        "doc_date": row.doc_date.isoformat(),
        "status": row.status,
        "qt_id": row.qt_id,
        "qt_doc_number": qt_number,
        "buyer_partner_id": row.buyer_partner_id,
        "buyer_name": row.buyer_name,
        "dest_market_code": row.dest_market_code,
        "currency": row.currency,
        "total_amount": row.total_amount,
        "total_text": money_text(row.total_amount, row.currency),
        "valid_until": row.valid_until.isoformat(),
        "is_lapsed": is_lapsed(KIND, row.status, row.valid_until, today_kst()),
        "assignee_id": row.assignee_id,
        "copied_from_id": row.copied_from_id,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }


def advance_body(
    total_amount: int, currency: str, payment_type: str | None, advance_pct_bp: int | None
) -> dict[str, Any] | None:
    """선수금 청구액(파생) — 선수금 T/T가 아니면 None. 저장하지 않는다(X-17)."""
    if payment_type != "TT_ADVANCE" or advance_pct_bp is None:
        return None
    advance, balance = split_advance(total_amount, advance_pct_bp)
    return {
        "advance_amount": advance,
        "advance_text": money_text(advance, currency),
        "balance_amount": balance,
        "balance_text": money_text(balance, currency),
    }


def bank_body(source: Any, account_id: int) -> dict[str, Any]:
    """은행 스냅샷 응답 — `source`는 PI 행(열 이름 bank_*)이다."""
    return {
        "account_id": account_id,
        "beneficiary_name": source.bank_beneficiary_name,
        "beneficiary_address": source.bank_beneficiary_address,
        "bank_name": source.bank_name,
        "bank_address": source.bank_address,
        "account_no": source.bank_account_no,
        "swift_code": source.bank_swift_code,
    }


def detail_body(session: Session, row: ProformaInvoice) -> dict[str, Any]:
    lines = (
        session.execute(
            select(ProformaInvoiceLine)
            .where(ProformaInvoiceLine.pi_id == row.id, ProformaInvoiceLine.deleted_at.is_(None))
            .order_by(ProformaInvoiceLine.line_no)
        )
        .scalars()
        .all()
    )
    body = _summary_body(row, qt_doc_numbers(session, [row.qt_id]).get(row.qt_id))
    body.update(
        {
            "minor_units": minor_units(row.currency),
            "fx_rate": rate_text(row.fx_rate),
            "fx_rate_date": row.fx_rate_date.isoformat() if row.fx_rate_date else None,
            "fx_rate_age_days": (today_kst() - row.fx_rate_date).days if row.fx_rate_date else None,
            "payment_terms": payment_terms_body(row),
            "incoterm": incoterm_body(row),
            "buyer_address": row.buyer_address,
            "internal_note": row.internal_note,
            "frozen_at": row.frozen_at.isoformat(),
            "last_line_no": row.last_line_no,
            "bank": bank_body(row, row.bank_account_id),
            "advance": advance_body(
                row.total_amount, row.currency, row.payment_type, row.advance_pct_bp
            ),
            "lines": [_line_body(line) for line in lines],
        }
    )
    return body


def require_proforma_invoice(session: Session, pi_id: int) -> ProformaInvoice:
    row = session.execute(
        select(ProformaInvoice).where(
            ProformaInvoice.id == pi_id, ProformaInvoice.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"proforma_invoice_id": pi_id})
    return row


# ── 생성 착지 (참조 생성 오케스트레이터 trade_chain.reference가 부른다) ─────────────────


def insert_issued(
    session: Session, *, actor_id: int, header: dict[str, Any], lines: list[NewPiLine]
) -> ProformaInvoice:
    """PI 1건 INSERT — 채번은 **마지막 단계**(모든 검증·라인 구성 뒤, INSERT 직전). 생성 = 발행 = 동결.

    헤더 합계는 라인 금액 합을 미리 구해 생성 시점에 넣는다(QT `insert_draft`와 같은 규칙). 합계 0(전 라인 무상)은
    선수금 청구서가 될 수 없어 422다.
    """
    editing.require_line_capacity(0, len(lines))
    amounts = [line_amount(item.quantity, item.snapshot.unit_price_amount) for item in lines]
    total = editing.compute_total(amounts)
    if total <= 0:
        raise invalid("lines", "합계가 0인 청구서는 발행할 수 없습니다. 유상 라인을 포함해 주세요.")
    number = issue_document_number(session, KIND)  # 잠금 순서 (9) — 항상 마지막
    row = ProformaInvoice(
        doc_number=number,
        total_amount=total,
        last_line_no=len(lines),
        created_by_id=actor_id,
        updated_by_id=actor_id,
        **header,
    )
    record_birth(
        session, row, actor_user_id=actor_id
    )  # status 대입·INSERT·탄생 이력·created 이벤트
    for line_no, (item, amount) in enumerate(zip(lines, amounts, strict=True), start=1):
        snap = item.snapshot
        session.add(
            ProformaInvoiceLine(
                pi_id=row.id,
                qt_id=row.qt_id,
                qt_line_id=item.qt_line_id,
                currency=row.currency,
                line_no=line_no,
                sku_id=snap.sku_id,
                sku_code=snap.sku_code,
                sku_name_ko=snap.sku_name_ko,
                sku_name_en=snap.sku_name_en,
                sku_kind=snap.sku_kind,
                quantity=item.quantity,
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
        )
    session.flush()
    return row


def bank_snapshot_columns(account: Any) -> dict[str, Any]:
    """은행 계좌 마스터 → PI 은행 스냅샷 6열(값 복사 — 이후 마스터 변경은 무관). 복사 지점은 이 함수 1곳이다."""
    return {
        column_name: getattr(account, source)
        for column_name, source in BANK_SNAPSHOT_COLUMNS.items()
    }


def has_live_copy(session: Session, source_id: int) -> bool:
    """원본에서 복제된 **살아 있는** PI가 이미 있는가(취소·만료된 복제본은 세지 않는다 — X-08)."""
    from app.modules.trade_docs.machine import DEAD_STATUSES

    return bool(
        session.execute(
            select(func.count())
            .select_from(ProformaInvoice)
            .where(
                ProformaInvoice.copied_from_id == source_id,
                ProformaInvoice.deleted_at.is_(None),
                ProformaInvoice.status.notin_(DEAD_STATUSES),
            )
        ).scalar_one()
    )


# ── 조회 ───────────────────────────────────────────────────────────────────


def get_proforma_invoice(pi_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(uow.session, require_proforma_invoice(uow.session, pi_id))


def _list_filters(
    *,
    status: str | None,
    qt_id: int | None,
    buyer_partner_id: int | None,
    assignee_id: int | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conditions: list[Any] = [ProformaInvoice.deleted_at.is_(None)]
    if status:
        conditions.append(ProformaInvoice.status == status)
    if qt_id:
        conditions.append(ProformaInvoice.qt_id == qt_id)
    if buyer_partner_id:
        conditions.append(ProformaInvoice.buyer_partner_id == buyer_partner_id)
    if assignee_id:
        conditions.append(ProformaInvoice.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                ProformaInvoice.doc_number.icontains(needle, autoescape=True),
                ProformaInvoice.buyer_name.icontains(needle, autoescape=True),
            )
        )
    if date_from:
        conditions.append(ProformaInvoice.doc_date >= date_from)
    if date_to:
        conditions.append(ProformaInvoice.doc_date <= date_to)
    return conditions


def list_proforma_invoices(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
    qt_id: int | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    conditions = _list_filters(
        status=status,
        qt_id=qt_id,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(ProformaInvoice).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(ProformaInvoice)
                .where(*conditions)
                .order_by(ProformaInvoice.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        numbers = qt_doc_numbers(session, [row.qt_id for row in rows])
        return [_summary_body(row, numbers.get(row.qt_id)) for row in rows], total


EXPORT_HEADER: tuple[str, ...] = (
    "PI번호",
    "견적번호",
    "증빙일",
    "상태",
    "바이어",
    "목적지",
    "통화",
    "합계",
    "선수금",
    "잔금",
    "유효기간",
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
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[tuple[Any, ...]]:
    """CSV 행(헤더 단위). 상한 초과는 422 — 조건을 좁히게 한다(전 목록 내보내기 §12.2). 은행 계좌번호는 싣지 않는다."""
    conditions = _list_filters(
        status=status,
        qt_id=qt_id,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(ProformaInvoice).where(*conditions)
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({total:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        rows = session.execute(
            select(ProformaInvoice, User.display_name)
            .outerjoin(User, User.id == ProformaInvoice.assignee_id)
            .where(*conditions)
            .order_by(ProformaInvoice.id)
        ).all()
        numbers = qt_doc_numbers(session, [r.qt_id for r, _ in rows])
        out: list[tuple[Any, ...]] = []
        for r, name in rows:
            advance = advance_body(r.total_amount, r.currency, r.payment_type, r.advance_pct_bp)
            out.append(
                (
                    r.doc_number,
                    numbers.get(r.qt_id, ""),
                    r.doc_date.isoformat(),
                    r.status,
                    r.buyer_name,
                    r.dest_market_code,
                    r.currency,
                    money_text(r.total_amount, r.currency),
                    advance["advance_text"] if advance else "",
                    advance["balance_text"] if advance else "",
                    r.valid_until.isoformat(),
                    r.payment_type or "",
                    payment_terms_body(r)["advance_pct"] or "",
                    f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
                    name or "",
                    created_date_kst(r.created_at),
                )
            )
        return out


def list_status_log(*, pi_id: int, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_proforma_invoice(session, pi_id)
        total = session.execute(
            select(func.count())
            .select_from(ProformaInvoiceStatusLog)
            .where(ProformaInvoiceStatusLog.proforma_invoice_id == pi_id)
        ).scalar_one()
        rows = session.execute(
            select(ProformaInvoiceStatusLog, User.display_name)
            .outerjoin(User, User.id == ProformaInvoiceStatusLog.actor_user_id)
            .where(ProformaInvoiceStatusLog.proforma_invoice_id == pi_id)
            .order_by(ProformaInvoiceStatusLog.id.desc())
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


# ── 편집(FREE 열만) ─────────────────────────────────────────────────────────

_FREE_FIELDS = ("internal_note", "assignee_id")


def update_meta(*, actor: AuthenticatedUser, pi_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """FREE 열(내부 메모·담당자)만 — 동결 후에도 허용된다(인계·오기 정정). 그 밖의 열은 요청 스키마에 없다."""
    values = {k: v for k, v in payload.items() if k in _FREE_FIELDS}
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, ProformaInvoice, pi_id, expected_version=payload["version"])
        if "assignee_id" in values:
            if values["assignee_id"] is None:
                raise invalid("assignee_id", "담당자를 비울 수 없습니다.")
            require_active_user(session, values["assignee_id"])
            row.assignee_id = values["assignee_id"]
        if "internal_note" in values:
            note = values["internal_note"]
            row.internal_note = (str(note).strip() or None) if note is not None else None
        row.updated_by_id = actor.id
        session.flush()
        return detail_body(session, row)
