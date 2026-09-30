"""견적 서비스 — CRUD·라인 3함수·조회 (S3-1 ADR-0052·0053 / design-A A4~A13 / design-B B2·B8).

L1: 이 모듈은 **전이를 하지 않는다**(발행·취소·개정은 trade_chain L2 — 전이 통로는 transition.py 하나). 여기서 하는
일은 초안(DRAFT) 작성·편집과 라인 편집, 그리고 조회다.

■ 모든 쓰기의 순서 — 헤더 행 `FOR UPDATE` → `version` 대조(409) → 편집 가능 상태 검사(동결이면 409 FROZEN) → 변경 →
  합계 재계산 → 헤더 version 상승(라인만 바뀌어도). 채번은 **생성 트랜잭션의 마지막 단계**(잠금 순서 (9)).
■ 생성만 `Idempotency-Key`(더블클릭=전표 1건), 편집·라인 변경은 version 낙관 잠금만(§17.4 멱등 대상=확정·생성).
■ 감사: 전표 생성·전이·편집은 audit_log에 이중 기록하지 않는다(상태이력+아웃박스가 정본, version·updated_by로 충분 — X-24).
■ 마스터(SKU·판가·품번)를 읽는 곳은 `trade_docs.snapshot`·`trade_docs.lines`뿐이다(값 복사 단일 통로).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.money import minor_units
from app.core.time import today_kst, utcnow
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets import service as markets
from app.modules.partners import service as partners
from app.modules.quotations.models import Quotation, QuotationLine
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.doc_number import issue_document_number
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.fx import require_known_currency, resolve_fx
from app.modules.trade_docs.incoterms import EMPTY_INCOTERM_COLUMNS, build_incoterm
from app.modules.trade_docs.lines import require_sellable_sku
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import DEAD_STATUSES, EDITABLE_STATES
from app.modules.trade_docs.models import QuotationStatusLog
from app.modules.trade_docs.payment_terms import (
    EMPTY_TERMS_COLUMNS,
    advance_pct_text,
    build_payment_terms,
    require_lc_enabled,
)
from app.modules.trade_docs.policy import ColumnClass, columns_of
from app.modules.trade_docs.snapshot import (
    LineSnapshot,
    line_amount,
    reprice,
    resolve_buyer_item_code,
    snapshot_line_from_master,
    validate_quantity,
)
from app.modules.trade_docs.transition import record_birth
from app.modules.trade_docs.validation import (
    blank_to_none,
    check_doc_date,
    check_valid_until,
    invalid,
    require_active_user,
)
from app.modules.trade_docs.views import created_date_kst, money_text, rate_text

KIND = DocKind.QUOTATION
QUOTATION_CREATE_ENDPOINT = "POST /api/v1/quotations"
EXPORT_MAX_ROWS = 50_000

#: 헤더 CONTENT 열 중 요청 필드 이름 → 컬럼 그룹. FREE(내부 메모·담당자)는 동결 후에도 바뀐다.
_NOT_NULLABLE = frozenset(
    {"buyer_partner_id", "dest_market_code", "currency", "doc_date", "buyer_name", "assignee_id"}
)


# ── 공통 검증 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewLine:
    """생성 트랜잭션이 넣을 라인 한 줄 — 스냅샷(값 복사)과 수량."""

    snapshot: LineSnapshot
    quantity: int


def _header_columns(
    session: Session,
    values: dict[str, Any],
    *,
    current: Quotation | None,
) -> dict[str, Any]:
    """요청 필드(부분 가능) → 검증된 헤더 컬럼 값.

    생성(current=None)은 필수 필드가 다 있어야 하고, 편집(current 있음)은 **보낸 필드만** 바뀐다. 환율·결제조건·
    Incoterms처럼 서로 얽힌 필드는 통째로 다시 검증한다(부분 조합이 CHECK를 깨지 않도록).
    """
    cols: dict[str, Any] = {}

    for field in _NOT_NULLABLE & values.keys():
        if values[field] is None:
            raise invalid(field, "이 항목은 비울 수 없습니다.")

    doc_date = values.get("doc_date", current.doc_date if current else today_kst())
    if "doc_date" in values or current is None:
        check_doc_date(doc_date)
        cols["doc_date"] = doc_date

    currency = require_known_currency(values.get("currency", current.currency if current else ""))
    if "currency" in values or current is None:
        cols["currency"] = currency

    if "buyer_partner_id" in values or current is None:
        partner = partners.require_partner_of_any_type(
            session,
            values["buyer_partner_id"],
            ("BUYER",),
            field="buyer_partner_id",
            type_label="바이어",
            lock=True,  # FOR KEY SHARE — 유형 해제(임포트 FOR UPDATE)와 직렬화, 여신 잠금(NO KEY UPDATE)과는 비충돌
        )
        cols["buyer_partner_id"] = partner.id
        changed_buyer = current is None or partner.id != current.buyer_partner_id
        # 바이어 표기 스냅샷 기본값(거래처 변경 시 재복사) — 사용자가 같은 요청에 직접 준 값이 우선한다.
        if "buyer_name" not in values and changed_buyer:
            cols["buyer_name"] = partner.name_en or partner.name_ko
        if "buyer_address" not in values and changed_buyer:
            cols["buyer_address"] = blank_to_none(partner.address_en)

    if "buyer_name" in values:
        name = blank_to_none(values["buyer_name"])
        if name is None:
            raise invalid("buyer_name", "바이어 표기를 비울 수 없습니다.")
        cols["buyer_name"] = name
    if "buyer_address" in values:
        cols["buyer_address"] = blank_to_none(values["buyer_address"])

    if "dest_market_code" in values or current is None:
        code = str(values["dest_market_code"]).strip().upper()
        markets.require_active_market_code(session, code, field="dest_market_code")
        cols["dest_market_code"] = code

    fx_keys = {"currency", "doc_date", "fx_rate", "fx_rate_date"}
    if current is None or fx_keys & values.keys():
        currency_changed = current is not None and currency != current.currency
        # 통화가 바뀌면 옛 환율은 의미가 없다(다른 통화의 1단위=x KRW) — 새로 입력받는다(KRW는 서버가 1을 채운다).
        carry = current is not None and not currency_changed
        rate_raw = values.get("fx_rate", rate_text(current.fx_rate) if carry and current else None)
        rate_date = values.get("fx_rate_date", current.fx_rate_date if carry and current else None)
        rate, rate_on = resolve_fx(currency, rate_raw, rate_date, doc_date)
        cols["fx_rate"], cols["fx_rate_date"] = rate, rate_on

    if "payment_terms" in values:
        raw = values["payment_terms"]
        terms = build_payment_terms(raw)
        if current is None or terms is None or terms.columns() != _terms_of(current):
            require_lc_enabled(
                session, terms
            )  # 새로 입력하는 L/C만 — 상속·동일값은 검사하지 않는다
        cols.update(terms.columns() if terms else EMPTY_TERMS_COLUMNS)
    if "incoterm" in values:
        incoterm = build_incoterm(values["incoterm"])
        cols.update(incoterm.columns() if incoterm else EMPTY_INCOTERM_COLUMNS)

    if "valid_until" in values or "doc_date" in values:
        valid_until = values.get("valid_until", current.valid_until if current else None)
        check_valid_until(valid_until, doc_date)
        if "valid_until" in values or current is None:
            cols["valid_until"] = valid_until

    if "internal_note" in values:
        cols["internal_note"] = blank_to_none(values["internal_note"])
    if "assignee_id" in values and values["assignee_id"] is not None:
        require_active_user(session, values["assignee_id"])
        cols["assignee_id"] = values["assignee_id"]
    return cols


def _terms_of(row: Quotation) -> dict[str, Any]:
    return {
        "payment_type": row.payment_type,
        "advance_pct_bp": row.advance_pct_bp,
        "balance_anchor": row.balance_anchor,
        "balance_days": row.balance_days,
    }


def _content_changes(row: Quotation, cols: dict[str, Any]) -> list[str]:
    """검증된 컬럼 값 중 현재값과 다른 CONTENT/ORIGIN 열 이름."""
    content = columns_of("quotations", ColumnClass.CONTENT, ColumnClass.ORIGIN)
    return sorted(
        name for name, value in cols.items() if name in content and getattr(row, name) != value
    )


# ── 뷰 ─────────────────────────────────────────────────────────────────────


def _line_body(line: QuotationLine) -> dict[str, Any]:
    cur = line.currency
    return {
        "id": line.id,
        "line_no": line.line_no,
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


def _summary_body(row: Quotation) -> dict[str, Any]:
    return {
        "id": row.id,
        "doc_number": row.doc_number,
        "doc_date": row.doc_date.isoformat(),
        "status": row.status,
        "buyer_partner_id": row.buyer_partner_id,
        "buyer_name": row.buyer_name,
        "dest_market_code": row.dest_market_code,
        "currency": row.currency,
        "total_amount": row.total_amount,
        "total_text": money_text(row.total_amount, row.currency),
        "valid_until": row.valid_until.isoformat() if row.valid_until else None,
        "is_lapsed": is_lapsed(KIND, row.status, row.valid_until, today_kst()),
        "assignee_id": row.assignee_id,
        "copied_from_id": row.copied_from_id,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }


def detail_body(session: Session, row: Quotation) -> dict[str, Any]:
    lines = (
        session.execute(
            select(QuotationLine)
            .where(QuotationLine.qt_id == row.id, QuotationLine.deleted_at.is_(None))
            .order_by(QuotationLine.line_no)
        )
        .scalars()
        .all()
    )
    body = _summary_body(row)
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
            "buyer_address": row.buyer_address,
            "internal_note": row.internal_note,
            "frozen_at": row.frozen_at.isoformat() if row.frozen_at else None,
            "last_line_no": row.last_line_no,
            "lines": [_line_body(line) for line in lines],
        }
    )
    return body


def require_quotation(session: Session, qt_id: int) -> Quotation:
    row = session.execute(
        select(Quotation).where(Quotation.id == qt_id, Quotation.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"quotation_id": qt_id})
    return row


# ── 생성 ───────────────────────────────────────────────────────────────────


def build_new_lines(
    session: Session,
    *,
    kind_header: dict[str, Any],
    lines_in: list[dict[str, Any]],
) -> list[NewLine]:
    """라인 입력 → 스냅샷. 같은 (SKU, 유무상) 중복은 409, 단종은 422."""
    out: list[NewLine] = []
    seen: set[tuple[int, bool]] = set()
    for index, raw in enumerate(lines_in):
        prefix = f"lines[{index}]."
        sku = require_sellable_sku(session, raw["sku_id"], KIND, field=f"{prefix}sku_id")
        key = (sku.id, bool(raw.get("is_free", False)))
        if key in seen:
            raise AppError(
                ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={f"{prefix}sku_id": sku.sku_code}
            )
        seen.add(key)
        snapshot = snapshot_line_from_master(
            session,
            sku=sku,
            buyer_partner_id=kind_header["buyer_partner_id"],
            currency=kind_header["currency"],
            doc_date=kind_header["doc_date"],
            unit_price=raw.get("unit_price"),
            is_free=bool(raw.get("is_free", False)),
            price_reason=raw.get("price_reason"),
            buyer_item_code=raw.get("buyer_item_code"),
            field_prefix=prefix,
        )
        out.append(NewLine(snapshot, validate_quantity(raw["quantity"], field=f"{prefix}quantity")))
    return out


def insert_draft(
    session: Session, *, actor_id: int, header: dict[str, Any], lines: list[NewLine]
) -> Quotation:
    """초안 QT 1건 INSERT — 채번은 **마지막 단계**(모든 검증·라인 구성 뒤, INSERT 직전).

    개정(trade_chain)과 생성이 같은 함수를 쓴다. 헤더 합계는 라인 금액 합을 미리 구해 생성 시점에 넣는다.
    """
    editing.require_line_capacity(0, len(lines))
    amounts = [line_amount(item.quantity, item.snapshot.unit_price_amount) for item in lines]
    total = editing.compute_total(amounts)
    number = issue_document_number(session, KIND)  # 잠금 순서 (9) — 항상 마지막
    row = Quotation(
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
        session.add(_new_line(row, item.snapshot, item.quantity, amount, line_no, actor_id))
    session.flush()
    return row


def _new_line(
    header: Quotation, snap: LineSnapshot, quantity: int, amount: int, line_no: int, actor_id: int
) -> QuotationLine:
    return QuotationLine(
        qt_id=header.id,
        currency=header.currency,
        line_no=line_no,
        sku_id=snap.sku_id,
        sku_code=snap.sku_code,
        sku_name_ko=snap.sku_name_ko,
        sku_name_en=snap.sku_name_en,
        sku_kind=snap.sku_kind,
        quantity=quantity,
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


def has_live_copy(session: Session, source_id: int) -> bool:
    """원본에서 복제된 **살아 있는** 전표가 이미 있는가(취소·만료된 복제본은 세지 않는다 — X-08)."""
    return bool(
        session.execute(
            select(func.count())
            .select_from(Quotation)
            .where(
                Quotation.copied_from_id == source_id,
                Quotation.deleted_at.is_(None),
                Quotation.status.notin_(DEAD_STATUSES),
            )
        ).scalar_one()
    )


def _check_copy_source(session: Session, source_id: int, buyer_partner_id: int) -> None:
    """복제 원본 자격 — 같은 유형(QT)·같은 거래처·**원본 상태 ∈ {CANCELLED, EXPIRED}**(살아 있는 전표 복제=중복 차단).

    원본 행을 `FOR UPDATE`로 잠가 같은 원본을 동시에 복제하는 두 요청을 직렬화한다(뒤 요청은 앞 커밋 후 살아 있는
    복제본을 보고 409 — 부분 유니크 위반이 500으로 새지 않게).
    """
    source = session.execute(
        select(Quotation)
        .where(Quotation.id == source_id, Quotation.deleted_at.is_(None))
        .with_for_update()
    ).scalar_one_or_none()
    if (
        source is None
        or source.buyer_partner_id != buyer_partner_id
        or source.status not in DEAD_STATUSES
        or has_live_copy(session, source_id)
    ):
        raise AppError(
            ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE,
            log_context={"source_id": source_id},
        )


def create_quotation(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """견적 작성(DRAFT) — 검증 → 라인 스냅샷 → 채번(마지막) → INSERT+탄생 이력+이벤트, 한 트랜잭션."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=QUOTATION_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        values = {k: v for k, v in payload.items() if k not in ("lines", "copied_from_id")}
        header = _header_columns(session, values, current=None)
        header.setdefault("assignee_id", actor.id)
        for key in (
            "payment_type",
            "advance_pct_bp",
            "balance_anchor",
            "balance_days",
            "incoterm_code",
            "incoterm_place",
            "incoterm_year",
            "valid_until",
            "internal_note",
            "buyer_address",
        ):
            header.setdefault(key, None)
        copied_from = payload.get("copied_from_id")
        if copied_from is not None:
            _check_copy_source(session, copied_from, header["buyer_partner_id"])
            header["copied_from_id"] = copied_from
        lines = build_new_lines(session, kind_header=header, lines_in=payload.get("lines") or [])
        row = insert_draft(session, actor_id=actor.id, header=header, lines=lines)

        body = detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── 조회 ───────────────────────────────────────────────────────────────────


def get_quotation(qt_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(uow.session, require_quotation(uow.session, qt_id))


def _list_filters(
    *,
    status: str | None,
    buyer_partner_id: int | None,
    assignee_id: int | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conditions: list[Any] = [Quotation.deleted_at.is_(None)]
    if status:
        conditions.append(Quotation.status == status)
    if buyer_partner_id:
        conditions.append(Quotation.buyer_partner_id == buyer_partner_id)
    if assignee_id:
        conditions.append(Quotation.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                Quotation.doc_number.icontains(needle, autoescape=True),
                Quotation.buyer_name.icontains(needle, autoescape=True),
            )
        )
    if date_from:
        conditions.append(Quotation.doc_date >= date_from)
    if date_to:
        conditions.append(Quotation.doc_date <= date_to)
    return conditions


def list_quotations(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    conditions = _list_filters(
        status=status,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Quotation).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(Quotation)
                .where(*conditions)
                .order_by(Quotation.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_summary_body(row) for row in rows], total


def export_rows(
    *,
    status: str | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[tuple[Any, ...]]:
    """CSV 행(헤더 단위). 상한 초과는 422 — 조건을 좁히게 한다(전 목록 내보내기 §12.2)."""
    conditions = _list_filters(
        status=status,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Quotation).where(*conditions)
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({total:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        rows = session.execute(
            select(Quotation, User.display_name)
            .outerjoin(User, User.id == Quotation.assignee_id)
            .where(*conditions)
            .order_by(Quotation.id)
        ).all()
        return [
            (
                r.doc_number,
                r.doc_date.isoformat(),
                r.status,
                r.buyer_name,
                r.dest_market_code,
                r.currency,
                money_text(r.total_amount, r.currency),
                r.valid_until.isoformat() if r.valid_until else "",
                r.payment_type or "",
                advance_pct_text(r.advance_pct_bp) or "",
                f"{r.incoterm_code} {r.incoterm_place}" if r.incoterm_code else "",
                name or "",
                created_date_kst(r.created_at),
            )
            for r, name in rows
        ]


EXPORT_HEADER: tuple[str, ...] = (
    "견적번호",
    "증빙일",
    "상태",
    "바이어",
    "목적지",
    "통화",
    "합계",
    "유효기간",
    "결제유형",
    "선수금(%)",
    "Incoterms",
    "담당자",
    "생성일",
)


def list_status_log(*, qt_id: int, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_quotation(session, qt_id)
        total = session.execute(
            select(func.count())
            .select_from(QuotationStatusLog)
            .where(QuotationStatusLog.quotation_id == qt_id)
        ).scalar_one()
        rows = session.execute(
            select(QuotationStatusLog, User.display_name)
            .outerjoin(User, User.id == QuotationStatusLog.actor_user_id)
            .where(QuotationStatusLog.quotation_id == qt_id)
            .order_by(QuotationStatusLog.id.desc())
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


# ── 편집 ───────────────────────────────────────────────────────────────────

_FREE_FIELDS = ("internal_note", "assignee_id")


#: PATCH가 받는 CONTENT 요청 필드(FREE 제외) — 스키마 필드 집합과 같은지 테스트가 대사한다.
CONTENT_REQUEST_FIELDS: frozenset[str] = frozenset(
    {
        "buyer_partner_id",
        "dest_market_code",
        "currency",
        "doc_date",
        "valid_until",
        "fx_rate",
        "fx_rate_date",
        "payment_terms",
        "incoterm",
        "buyer_name",
        "buyer_address",
    }
)


def _prelock_new_buyer(session: Session, buyer_partner_id: int) -> None:
    """잠금 순서 (3 partners) → (4 quotations): 바꿀 바이어를 QT보다 **먼저** FOR KEY SHARE로 잠근다.

    검증 실패(미존재·유형 불일치)는 여기서 삼킨다 — 같은 검증이 QT 잠금 뒤 `_header_columns`에서 다시 돌아
    동결 상태 우선 규칙(FROZEN이 검증 오류보다 먼저)과 함께 같은 오류를 낸다. 이미 잡은 잠금은 트랜잭션 끝까지 유지된다.
    """
    with contextlib.suppress(AppError):
        partners.require_partner_of_any_type(
            session,
            buyer_partner_id,
            ("BUYER",),
            field="buyer_partner_id",
            type_label="바이어",
            lock=True,
        )


def update_quotation(
    *, actor: AuthenticatedUser, qt_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """헤더 편집 — 초안(DRAFT)에서만 CONTENT를 고칠 수 있다. 동결 후 CONTENT가 달라지면 409 FROZEN(필드명만).

    동결된 전표에서는 CONTENT 필드를 **어떤 값으로든** 바꿀 수 없으므로, 그 값의 형식 검증이 먼저 실패하더라도
    응답은 검증 오류가 아니라 FROZEN이다(바꿀 수 없는 것을 검증해 주지 않는다).
    """
    values = {k: v for k, v in payload.items() if k != "version"}
    content_keys = sorted(CONTENT_REQUEST_FIELDS & values.keys())
    with unit_of_work() as uow:
        session = uow.session
        if values.get("buyer_partner_id") is not None:
            _prelock_new_buyer(session, values["buyer_partner_id"])
        row = lock_document(session, Quotation, qt_id, expected_version=payload["version"])
        editable = row.status in EDITABLE_STATES[KIND]
        try:
            cols = _header_columns(session, values, current=row)
        except AppError as exc:
            if not editable and content_keys and exc.code == ErrorCode.VALIDATION_INVALID_FIELD:
                editing.assert_editable(KIND, row.status, fields=content_keys)
            raise
        changed = _content_changes(row, cols)
        if changed:
            editing.assert_editable(KIND, row.status, fields=changed)
        if "currency" in changed and row.last_line_no > 0:
            # 라인이 한 번이라도 있었으면(제외된 라인 포함) 복합 FK (qt_id, currency)가 통화 변경을 막는다.
            raise invalid(
                "currency",
                "이미 라인을 입력한 견적은 통화를 바꿀 수 없습니다(가격은 통화별입니다). 새 견적을 작성해 주세요.",
            )
        buyer_changed = (
            "buyer_partner_id" in cols and cols["buyer_partner_id"] != row.buyer_partner_id
        )
        for name, value in cols.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
        row.updated_by_id = actor.id
        if buyer_changed:
            _rederive_buyer_item_codes(session, row, actor.id)
        session.flush()
        return detail_body(session, row)


def _rederive_buyer_item_codes(session: Session, header: Quotation, actor_id: int) -> None:
    """바이어가 바뀌면 살아 있는 라인 전건의 바이어 품번을 새 바이어 기준으로 다시 정한다(라인 추가와 같은 규칙).

    새 바이어에 매핑이 없거나(0건) 여러 개(2건 이상, 사용자가 골라야 함)이면 NULL — 옛 바이어 품번이 남지 않는다.
    금액은 바뀌지 않으므로 합계 재계산은 없고, 헤더 version은 헤더 편집 UPDATE 1회로 이미 +1이다(라인은 version 없음).
    """
    lines = session.execute(
        select(QuotationLine).where(
            QuotationLine.qt_id == header.id, QuotationLine.deleted_at.is_(None)
        )
    ).scalars()
    for line in lines:
        code = resolve_buyer_item_code(
            session, header.buyer_partner_id, line.sku_id, None, field="buyer_item_code"
        )
        if code != line.buyer_item_code:
            line.buyer_item_code = code
            line.updated_by_id = actor_id
            line.updated_at = utcnow()


def update_meta(*, actor: AuthenticatedUser, qt_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """FREE 열(내부 메모·담당자)만 — 동결 후에도 허용된다(인계·오기 정정)."""
    values = {k: v for k, v in payload.items() if k in _FREE_FIELDS}
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(session, Quotation, qt_id, expected_version=payload["version"])
        if "assignee_id" in values and values["assignee_id"] is None:
            raise invalid("assignee_id", "담당자를 비울 수 없습니다.")
        cols = _header_columns(session, values, current=row)
        for name, value in cols.items():
            setattr(row, name, value)
        row.updated_by_id = actor.id
        session.flush()
        return detail_body(session, row)


# ── 라인 3함수 — assert_editable → 헤더 FOR UPDATE → version 대조 → 헤더 version 상승 → 합계 재계산 ──


def _require_line(session: Session, header: Quotation, line_id: int) -> QuotationLine:
    """다른 전표의 라인 id를 섞으면 404 — IDOR 방지(라인 단독 라우트 없음, 소속 단언)."""
    line = session.execute(
        select(QuotationLine).where(
            QuotationLine.id == line_id,
            QuotationLine.qt_id == header.id,
            QuotationLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise NotFoundError(log_context={"quotation_id": header.id, "line_id": line_id})
    return line


def _duplicate_exists(
    session: Session, qt_id: int, sku_id: int, is_free: bool, *, exclude_line_id: int | None = None
) -> bool:
    query = (
        select(func.count())
        .select_from(QuotationLine)
        .where(
            QuotationLine.qt_id == qt_id,
            QuotationLine.sku_id == sku_id,
            QuotationLine.is_free.is_(is_free),
            QuotationLine.deleted_at.is_(None),
        )
    )
    if exclude_line_id is not None:
        query = query.where(QuotationLine.id != exclude_line_id)
    return bool(session.execute(query).scalar_one())


def _finish_line_change(
    session: Session,
    actor: AuthenticatedUser,
    header: Quotation,
    line: QuotationLine | None,
    *,
    last_line_no: int | None = None,
) -> dict[str, Any]:
    """라인 변경 마무리 — 합계 재계산 → 헤더 version 상승(단일 UPDATE) → 갱신된 version·합계를 응답에.

    ★ 헤더를 dirty로 만드는 대입은 **라인 flush 뒤**에 몰아서 한다 — 합계 SUM 쿼리의 autoflush가 헤더 UPDATE를
      먼저 내보내면 version이 두 번 오른다(라인 변경 1건 = 헤더 version +1이 응답 계약이다).
    """
    session.flush()  # 라인 변경만 먼저 내보낸다(헤더는 아직 clean)
    editing.recompute_total(session, KIND, header, QuotationLine)
    if last_line_no is not None:
        header.last_line_no = (
            last_line_no  # 결번 허용·재사용 금지 — 카운터는 헤더 잠금 하에서만 오른다
        )
    editing.bump_header_version(header)
    header.updated_by_id = actor.id
    session.flush()
    return {
        "line": _line_body(line) if line is not None else None,
        "header_version": header.version,
        "total_amount": header.total_amount,
        "total_text": money_text(header.total_amount, header.currency),
    }


def add_line(*, actor: AuthenticatedUser, qt_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """라인 추가 — 마스터 값을 1회 스냅샷한다(자동 병합 없음: 같은 SKU 유상·무상 각 1줄까지)."""
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, Quotation, qt_id, expected_version=payload["version"])
        editing.assert_editable(KIND, header.status)
        editing.require_line_capacity(
            editing.count_live_lines(session, KIND, header.id, QuotationLine)
        )
        raw = {k: v for k, v in payload.items() if k != "version"}
        sku = require_sellable_sku(session, raw["sku_id"], KIND)
        if _duplicate_exists(session, header.id, sku.id, bool(raw.get("is_free", False))):
            raise AppError(ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={"sku_id": sku.sku_code})
        snap = snapshot_line_from_master(
            session,
            sku=sku,
            buyer_partner_id=header.buyer_partner_id,
            currency=header.currency,
            doc_date=header.doc_date,
            unit_price=raw.get("unit_price"),
            is_free=bool(raw.get("is_free", False)),
            price_reason=raw.get("price_reason"),
            buyer_item_code=raw.get("buyer_item_code"),
        )
        quantity = validate_quantity(raw["quantity"])
        amount = line_amount(quantity, snap.unit_price_amount)
        line_no = header.last_line_no + 1  # 결번 허용·재사용 금지(카운터 대입은 finish에서)
        line = _new_line(header, snap, quantity, amount, line_no, actor.id)
        session.add(line)
        return _finish_line_change(session, actor, header, line, last_line_no=line_no)


def update_line(
    *, actor: AuthenticatedUser, qt_id: int, line_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """라인 제자리 수정(안정 id) — 수량·단가·무상·사유·바이어 품번. 단가를 바꾸면 기준은 MANUAL이다."""
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, Quotation, qt_id, expected_version=payload["version"])
        editing.assert_editable(KIND, header.status)
        line = _require_line(session, header, line_id)
        raw = {k: v for k, v in payload.items() if k != "version"}
        if "quantity" in raw:
            if raw["quantity"] is None:
                raise invalid("quantity", "수량을 비울 수 없습니다.")
            line.quantity = validate_quantity(raw["quantity"])
        if {"unit_price", "is_free", "price_reason"} & raw.keys():
            unit, basis, free, reason = reprice(
                line,
                header.currency,
                unit_price=raw.get("unit_price"),
                is_free=raw.get("is_free"),
                price_reason=raw.get("price_reason"),
            )
            if free != line.is_free and _duplicate_exists(
                session, header.id, line.sku_id, free, exclude_line_id=line.id
            ):
                raise AppError(
                    ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE, detail={"sku_id": line.sku_code}
                )
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
        line.line_amount = line_amount(line.quantity, line.unit_price_amount)
        line.updated_by_id = actor.id
        line.updated_at = utcnow()
        return _finish_line_change(session, actor, header, line)


def remove_line(
    *, actor: AuthenticatedUser, qt_id: int, line_id: int, version: int
) -> dict[str, Any]:
    """라인 제외(soft delete) — 라인 번호는 재사용하지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        header = lock_document(session, Quotation, qt_id, expected_version=version)
        editing.assert_editable(KIND, header.status)
        line = _require_line(session, header, line_id)
        line.deleted_at = utcnow()
        line.updated_by_id = actor.id
        return _finish_line_change(session, actor, header, None)
