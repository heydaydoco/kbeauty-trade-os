"""참조 생성 — 견적(QT) → 선수금 청구서(PI) (S3-1 ADR-0052 / design-A A4 / design-B B3·B7 / design-integrated §2.9).

DoD ① "참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음)"의 1단이다. 요청 스키마가 원천에 있는 값을 다시 받지 않고
서버가 원천 라인에서 **값을 복사**한다(마스터 재조회 금지 — `snapshot_line_from_source`). PI는 초안이 없어 생성 = 발행 = 동결이므로
편집 요구는 **비저장 미리보기**(`preview_proforma_invoice` — 채번·이벤트·멱등 키 소비 0)로 충족한다.

■ 검증 순서(생성·미리보기 공통, `_plan`): 원천 QT 존재 → 바이어 유형(fail-closed) → 낙관 잠금(409) → **원천 자격**(상태 ∈
  {ISSUED, CONVERTED} 아니면 409 PARENT_NOT_USABLE → **유효기간 `valid_until`을 직접 검사**해 경과면 422 VALIDITY_EXPIRED —
  만료 스윕이 아직 안 돌았어도 막는다) → 날짜(증빙일·유효기간) → 결제조건·Incoterms 재정의 → 은행 계좌(활성·통화 일치) →
  원천 라인 잠금(id 오름차순)·잔량 재계산·요청 수량 ≤ 잔량(409 EXCEEDS_OPEN) → SKU 재검사 → 라인 값 복사·합계.
■ 잠금 순서: 멱등 claim(0) → 바이어 `FOR KEY SHARE`(2) → QT `FOR UPDATE`(3) → (복제 원본 PI `FOR UPDATE`(4)) → 원천 라인
  `FOR UPDATE` id순(8) → 채번(9, 마지막). 같은 QT의 동시 생성은 QT 행 잠금이 직렬화하므로 잔량 초과 2건이 함께 성공할 수 없다.
■ 원천 QT는 **수정하지 않는다**(잠금+읽기) — QT version 불변. PI 생성은 QT를 CONVERTED로 만들지 않는다(수주전환=SO 확정, X-18).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import minor_units
from app.core.time import today_kst
from app.modules.bank_accounts.models import BankAccount
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners import service as partners
from app.modules.proforma_invoices import service as pis
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.quotations.models import Quotation, QuotationLine
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.editing import compute_total, require_line_capacity
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.incoterms import build_incoterm
from app.modules.trade_docs.lines import unusable_sku_reasons
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.payment_terms import build_payment_terms, require_lc_enabled
from app.modules.trade_docs.quantities import (
    lock_source_lines,
    open_quantity,
    require_within_open,
)
from app.modules.trade_docs.snapshot import (
    line_amount,
    snapshot_line_from_source,
    validate_quantity,
)
from app.modules.trade_docs.validation import (
    check_doc_date,
    check_valid_until,
    invalid,
    require_active_user,
)
from app.modules.trade_docs.views import incoterm_body, money_text, payment_terms_body, rate_text

PI_CREATE_ENDPOINT = "POST /api/v1/quotations/{id}/proforma-invoices"

#: PI를 만들 수 있는 원천 QT 상태 — 초안·취소·만료는 불가(초안은 동결 전이라 참조할 수 없다).
SOURCE_STATUSES = ("ISSUED", "CONVERTED")


@dataclass(slots=True)
class _Plan:
    """검증을 통과한 생성 계획 — 헤더 컬럼·라인 목록·미리보기 표시용 값."""

    qt: Quotation
    header: dict[str, Any]
    lines: list[pis.NewPiLine]
    open_before: dict[int, int]
    total: int


def _require_source_quotation(
    session: Session, qt_id: int, version: int, *, lock: bool
) -> Quotation:
    """원천 QT 확보 — 무잠금으로 바이어를 얻어 KEY SHARE로 잠근 뒤 QT를 잠그고 바이어가 그대로인지 재확인한다.

    미리보기(`lock=False`)는 잠그지 않지만 같은 검증(바이어 유형·version 대조)을 한다 — 미리보기 성공이 생성 성공의 예고가 되도록.
    """
    peek = session.execute(
        select(Quotation.buyer_partner_id).where(
            Quotation.id == qt_id, Quotation.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if peek is None:
        raise NotFoundError(log_context={"quotation_id": qt_id})
    partners.require_partner_of_any_type(
        session, peek, ("BUYER",), field="buyer_partner_id", type_label="바이어", lock=lock
    )
    if lock:
        qt = lock_document(session, Quotation, qt_id, expected_version=version)
    else:
        found = session.execute(
            select(Quotation).where(Quotation.id == qt_id, Quotation.deleted_at.is_(None))
        ).scalar_one_or_none()
        if found is None:
            raise NotFoundError(log_context={"quotation_id": qt_id})
        qt = found
        if qt.version != version:
            raise VersionConflictError(log_context={"quotation_id": qt_id})
    if qt.buyer_partner_id != peek:  # 잠금 사이에 바이어가 바뀌었다 — 재시도 안내(409)
        raise VersionConflictError(log_context={"quotation_id": qt_id})
    return qt


def _require_usable_source(qt: Quotation, today: date) -> None:
    """원천 자격 — 상태(초안·취소·만료 불가)와 유효기간(**직접 검사** — 스윕 실행 여부와 무관)."""
    if qt.status not in SOURCE_STATUSES:
        raise AppError(
            ErrorCode.TRADE_DOCS_PARENT_NOT_USABLE,
            detail={"status": qt.status},
            log_context={"quotation_id": qt.id},
        )
    if is_lapsed(DocKind.QUOTATION, qt.status, qt.valid_until, today):
        raise AppError(ErrorCode.TRADE_DOCS_VALIDITY_EXPIRED, log_context={"quotation_id": qt.id})


def _bank_account(session: Session, account_id: int, currency: str, *, lock: bool) -> BankAccount:
    """활성 계좌이고 **통화가 PI 통화와 같을 때만** 허용 — 잘못된 통화 입금 안내를 막는다.

    생성 경로(`lock=True`)는 계좌 행을 `FOR SHARE`로 잡는다 — 은행 정정·비활성(UPDATE)과 직렬화돼 PI가 **커밋된 계좌 값 하나**를
    통째로 복사한다(FOR KEY SHARE는 비키 열 UPDATE·soft delete와 충돌하지 않아 이 보증이 없다 — 검토 지시 6의 KEY SHARE를 SHARE로
    강화). 잠금 순서: 계좌는 QT 뒤·라인 앞의 말단 마스터이고 계좌 UPDATE 트랜잭션은 다른 전표 행을 잠그지 않아 교착 고리가 없다.
    미리보기는 잠그지 않는다.
    """
    query = select(BankAccount).where(
        BankAccount.id == account_id, BankAccount.deleted_at.is_(None)
    )
    account = session.execute(
        query.with_for_update(read=True) if lock else query
    ).scalar_one_or_none()
    if account is None:
        raise invalid(
            "bank_account_id",
            "사용할 수 없는 은행 계좌입니다. 계좌를 다시 선택하거나 관리자에게 등록을 요청해 주세요.",
        )
    if account.currency != currency:
        raise invalid(
            "bank_account_id",
            f"{currency} 계좌가 아닙니다. 통화가 같은 계좌를 선택하거나 관리자에게 {currency} 계좌 등록을 요청해 주세요.",
        )
    return account


def _check_copy_source(session: Session, source_id: int, qt: Quotation, *, lock: bool) -> None:
    """복제 원본 PI 자격 — 같은 QT·같은 거래처·**취소·만료 상태**·살아 있는 복제본 없음(X-08 — 중복 생성 차단).

    원본 행을 `FOR UPDATE`로 잠가(잠금 순서 (4), QT 뒤) 같은 원본의 동시 복제를 직렬화한다.
    """
    query = select(ProformaInvoice).where(
        ProformaInvoice.id == source_id, ProformaInvoice.deleted_at.is_(None)
    )
    source = session.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if (
        source is None
        or source.qt_id != qt.id
        or source.buyer_partner_id != qt.buyer_partner_id
        or source.status not in DEAD_STATUSES
        or pis.has_live_copy(session, source_id)
    ):
        raise AppError(
            ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE, log_context={"source_id": source_id}
        )


def _select_lines(
    session: Session, qt: Quotation, requested: list[dict[str, Any]] | None, *, lock: bool
) -> tuple[list[QuotationLine], dict[int, int], dict[int, int]]:
    """원천 라인 확정 → (라인 행들, 라인별 가져올 수량, 라인별 잔량[잠금 후 재계산]).

    `requested`가 None이면 잔량이 남은 라인 전부를 잔량 전부로. 라인을 id 오름차순으로 잠근 뒤 잔량을 **다시** 계산한다
    (확인→기록 창 방어). 다른 QT의 라인 id를 섞으면 422(존재 여부를 알려 주지 않는다 — IDOR 방지).
    """
    wanted_ids = None if requested is None else [int(item["source_line_id"]) for item in requested]
    if wanted_ids is not None and len(set(wanted_ids)) != len(wanted_ids):
        raise invalid("lines", "같은 원천 라인을 두 번 지정할 수 없습니다.")
    if lock:
        line_ids = lock_source_lines(session, DocKind.QUOTATION, qt.id, wanted_ids)
    else:
        line_ids = list(
            session.execute(
                select(QuotationLine.id)
                .where(
                    QuotationLine.qt_id == qt.id,
                    QuotationLine.deleted_at.is_(None),
                    *([QuotationLine.id.in_(wanted_ids)] if wanted_ids is not None else []),
                )
                .order_by(QuotationLine.id)
            ).scalars()
        )
    if wanted_ids is not None:
        missing = [i for i in wanted_ids if i not in set(line_ids)]
        if missing:
            index = wanted_ids.index(missing[0])
            raise invalid(
                f"lines[{index}].source_line_id",
                "이 견적의 라인이 아닙니다. 라인을 다시 선택해 주세요.",
            )
    quantities = open_quantity(session, "QT_LINE", line_ids)
    if requested is None:
        take = {i: quantities[i].open for i in line_ids if quantities[i].open > 0}
        if not take:
            require_within_open(
                quantities, dict.fromkeys(line_ids or [0], 1)
            )  # 전량 소진 — 409 + 잔량 0 안내
    else:
        take = {}
        for index, item in enumerate(requested):
            take[int(item["source_line_id"])] = validate_quantity(
                item["quantity"], field=f"lines[{index}].quantity"
            )
        require_within_open(quantities, take)
    rows = {
        row.id: row
        for row in session.execute(
            select(QuotationLine).where(QuotationLine.id.in_(sorted(take)))
        ).scalars()
    }
    ordered = [rows[i] for i in sorted(take, key=lambda i: rows[i].line_no)]
    return ordered, take, {i: quantities[i].open for i in take}


def _plan(
    session: Session,
    actor: AuthenticatedUser,
    qt_id: int,
    payload: dict[str, Any],
    *,
    lock: bool,
) -> _Plan:
    today = today_kst()
    qt = _require_source_quotation(session, qt_id, payload["version"], lock=lock)
    _require_usable_source(qt, today)

    doc_date: date = payload.get("doc_date") or today
    check_doc_date(doc_date)
    if doc_date < qt.doc_date:
        raise invalid("doc_date", "증빙일은 원천 견적의 증빙일보다 앞설 수 없습니다.")
    valid_until: date = payload["valid_until"]
    check_valid_until(valid_until, doc_date)
    if valid_until < today:
        raise invalid("valid_until", "유효기간이 이미 지났습니다. 오늘 이후 날짜로 바꿔 주세요.")

    overrides = payload.get("overrides") or {}
    terms_cols: dict[str, Any] = {
        "payment_type": qt.payment_type,
        "advance_pct_bp": qt.advance_pct_bp,
        "balance_anchor": qt.balance_anchor,
        "balance_days": qt.balance_days,
    }
    if overrides.get("payment_terms") is not None:
        terms = build_payment_terms(overrides["payment_terms"])
        assert terms is not None
        if terms.columns() != terms_cols:
            require_lc_enabled(
                session, terms
            )  # 새로 입력하는 L/C만 — 원천에서 상속된 값은 검사하지 않는다
        terms_cols = terms.columns()
    incoterm_cols: dict[str, Any] = {
        "incoterm_code": qt.incoterm_code,
        "incoterm_place": qt.incoterm_place,
        "incoterm_year": qt.incoterm_year,
    }
    if overrides.get("incoterm") is not None:
        incoterm = build_incoterm(overrides["incoterm"])
        assert incoterm is not None
        incoterm_cols = incoterm.columns()
    assignee_id = overrides.get("assignee_id") or actor.id
    if overrides.get("assignee_id") is not None:
        require_active_user(session, assignee_id)
    note = overrides.get("internal_note")
    note = (str(note).strip() or None) if note is not None else None

    account = _bank_account(session, payload["bank_account_id"], qt.currency, lock=lock)

    copied_from = payload.get("copied_from_id")
    if copied_from is not None:
        _check_copy_source(session, copied_from, qt, lock=lock)

    source_lines, take, open_before = _select_lines(session, qt, payload.get("lines"), lock=lock)
    require_line_capacity(0, len(source_lines))
    unusable = unusable_sku_reasons(session, [line.sku_id for line in source_lines])
    if unusable:
        # 인덱스는 **요청 lines 순서** 기준(lines를 생략했으면 원천 라인 번호 순) — 화면이 지목한 줄을 정확히 짚게 한다.
        requested = payload.get("lines")
        position = (
            {int(item["source_line_id"]): i for i, item in enumerate(requested)}
            if requested is not None
            else {line.id: i for i, line in enumerate(source_lines)}
        )
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_SKU_DISCONTINUED,
            detail={
                f"lines[{position[line.id]}].source_line_id": unusable[line.sku_id]
                for line in sorted(source_lines, key=lambda ln: position[ln.id])
                if line.sku_id in unusable
            },
        )
    new_lines = [
        pis.NewPiLine(line.id, snapshot_line_from_source(line), take[line.id])
        for line in source_lines
    ]
    total = compute_total(
        [line_amount(item.quantity, item.snapshot.unit_price_amount) for item in new_lines]
    )
    if total <= 0:
        raise invalid("lines", "합계가 0인 청구서는 발행할 수 없습니다. 유상 라인을 포함해 주세요.")

    header: dict[str, Any] = {
        "doc_date": doc_date,
        "currency": qt.currency,
        "buyer_partner_id": qt.buyer_partner_id,
        "buyer_name": qt.buyer_name,
        "buyer_address": qt.buyer_address,
        "dest_market_code": qt.dest_market_code,
        "fx_rate": qt.fx_rate,
        "fx_rate_date": qt.fx_rate_date,
        **terms_cols,
        **incoterm_cols,
        "valid_until": valid_until,
        "internal_note": note,
        "assignee_id": assignee_id,
        "qt_id": qt.id,
        "bank_account_id": account.id,
        **pis.bank_snapshot_columns(account),
    }
    if copied_from is not None:
        header["copied_from_id"] = copied_from
    return _Plan(qt, header, new_lines, open_before, total)


def create_proforma_invoice(
    *, actor: AuthenticatedUser, idempotency_key: str, qt_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """QT → PI 참조 생성(=발행·동결) — 한 트랜잭션. 같은 키 재수신은 최초 결과를 그대로 돌려준다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PI_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={"qt_id": qt_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        plan = _plan(session, actor, qt_id, payload, lock=True)
        row = pis.insert_issued(session, actor_id=actor.id, header=plan.header, lines=plan.lines)
        body = pis.detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def preview_proforma_invoice(
    *, actor: AuthenticatedUser, qt_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """비저장 미리보기 — 채번·상태이력·이벤트·멱등 키·라인 잠금 소비가 **전혀 없다**(읽기 전용 트랜잭션).

    생성과 같은 검증을 같은 순서로 하므로 미리보기가 통과하면 같은 본문의 생성이 (경합이 없는 한) 성공한다.
    """
    with unit_of_work() as uow:
        session = uow.session
        plan = _plan(session, actor, qt_id, payload, lock=False)
        header = plan.header
        cur = header["currency"]
        lines = [
            {
                "line_no": number,
                "qt_line_id": item.qt_line_id,
                "sku_id": item.snapshot.sku_id,
                "sku_code": item.snapshot.sku_code,
                "sku_name_ko": item.snapshot.sku_name_ko,
                "sku_name_en": item.snapshot.sku_name_en,
                "sku_kind": item.snapshot.sku_kind,
                "quantity": item.quantity,
                "open_quantity_before": plan.open_before[item.qt_line_id],
                "buyer_item_code": item.snapshot.buyer_item_code,
                "unit_price_amount": item.snapshot.unit_price_amount,
                "unit_price_text": money_text(item.snapshot.unit_price_amount, cur),
                "line_amount": line_amount(item.quantity, item.snapshot.unit_price_amount),
                "line_amount_text": money_text(
                    line_amount(item.quantity, item.snapshot.unit_price_amount), cur
                ),
                "price_basis": item.snapshot.price_basis,
                "is_free": item.snapshot.is_free,
                "price_reason": item.snapshot.price_reason,
            }
            for number, item in enumerate(plan.lines, start=1)
        ]
        holder = _Holder(header)
        return {
            "qt_id": plan.qt.id,
            "qt_doc_number": plan.qt.doc_number,
            "source_version": plan.qt.version,
            "doc_date": header["doc_date"].isoformat(),
            "valid_until": header["valid_until"].isoformat(),
            "currency": cur,
            "minor_units": minor_units(cur),
            "buyer_partner_id": header["buyer_partner_id"],
            "buyer_name": header["buyer_name"],
            "buyer_address": header["buyer_address"],
            "dest_market_code": header["dest_market_code"],
            "fx_rate": rate_text(header["fx_rate"]),
            "fx_rate_date": header["fx_rate_date"].isoformat() if header["fx_rate_date"] else None,
            "payment_terms": payment_terms_body(holder),
            "incoterm": incoterm_body(holder),
            "internal_note": header["internal_note"],
            "assignee_id": header["assignee_id"],
            "bank": pis.bank_body(holder, header["bank_account_id"]),
            "total_amount": plan.total,
            "total_text": money_text(plan.total, cur),
            "advance": pis.advance_body(
                plan.total, cur, header["payment_type"], header["advance_pct_bp"]
            ),
            "lines": lines,
        }


class _Holder:
    """헤더 dict를 속성 접근으로 읽게 하는 얇은 어댑터 — 저장 전 값을 PI 행과 같은 응답 조립 함수에 태운다."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.__dict__.update(values)
