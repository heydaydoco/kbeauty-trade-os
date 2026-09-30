"""참조 생성 2단 — 견적(QT)/선수금 청구서(PI) → 수주(SO) (S3-1 ADR-0052 / design-A A4 / design-B B3 / design-integrated §2.9).

DoD ① "참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음)"의 2단이다. 요청 스키마가 원천에 있는 값을 다시 받지 않고 서버가 원천 라인에서
**값을 복사**한다(마스터 재조회 금지 — `snapshot_line_from_source`). SO는 접수(RECEIVED)가 편집 가능 초안이라 **미리보기가 없다** —
조건·환율·단가 조정은 생성 후 SO 편집 API로 한다. 생성은 `create_received_sales_order`(L1 단일 착지)를 부른다 — 이 모듈은 **확정을 부르지 않는다**
(SO는 RECEIVED로만 태어나고 QT 수주전환(CONVERTED)은 확정 시점 — X-18, PR-12).

■ 검증 순서(QT·PI 공통): 원천 확보(존재 → 바이어 유형[FOR KEY SHARE] → 원천 `FOR UPDATE`+낙관 잠금 409) → **원천 자격**(QT: 상태 ∈ {ISSUED, CONVERTED} +
  `valid_until` 직접 검사 / PI: 상태 ∈ {ISSUED, PARTIALLY_PAID, PAID} + **미입금 발행 상태일 때만** `valid_until` 직접 검사 — 이미 입금한 바이어의 SO 생성을
  유효기간이 막아 선수금이 갇히는 사고 방지) → 날짜 → (PI만) **활성 SO 1건 제한**(409 ALREADY_CONVERTED — DB 부분 유니크가 최종 보증) → 원천 라인 잠금(id 오름차순)·
  잔량 재계산·요청 수량 ≤ 잔량(409 EXCEEDS_OPEN) → SKU 재검사(단종·삭제 SKU가 실려 나가는 것 차단, 422+목록) → 라인 값 복사 → 착지.
■ 잠금 순서: 멱등 claim(0) → 바이어 `FOR KEY SHARE`(2) → 원천 헤더 `FOR UPDATE`(3 또는 3→4) → (복제 원본 SO `FOR UPDATE`(5)) → 원천 라인 `FOR UPDATE` id순(8) → 채번(9, 마지막).
  PI 경로는 **QT 헤더를 PI보다 먼저**(3→4 — `lock_chain`) 잠근다: SO INSERT의 FK 검사((pi_id, qt_id)→PI·qt_id→QT)가 QT 행에 암묵 `FOR KEY SHARE`를 잡아
  PI만 잠그고 들어가면 "PI 잠금 → QT 암묵 잠금" 역순이 되어 PI 취소(QT→PI)와 교차 교착한다(J 테스트 `cross_chain`·`pi_cancel_versus_so_creation`이 실제로 잡은 결함).
■ 원천은 **수정하지 않는다**(잠금+읽기) — 원천 version 불변. 같은 원천의 동시 생성은 원천 헤더 잠금이 직렬화하므로 잔량 초과·PI→SO 2건이 함께 성공할 수 없다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.time import today_kst
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners import service as partners
from app.modules.proforma_invoices.models import ProformaInvoice, ProformaInvoiceLine
from app.modules.quotations.models import Quotation, QuotationLine
from app.modules.sales_orders import service as sos
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_chain.chain_ops import lock_chain
from app.modules.trade_chain.reference import (
    _require_source_quotation,
    _require_usable_source,
    select_source_lines,
)
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.lines import unusable_sku_reasons
from app.modules.trade_docs.snapshot import snapshot_line_from_source
from app.modules.trade_docs.validation import check_doc_date, invalid

SO_FROM_QT_ENDPOINT = "POST /api/v1/quotations/{id}/sales-orders"
SO_FROM_PI_ENDPOINT = "POST /api/v1/proforma-invoices/{id}/sales-orders"

#: SO를 만들 수 있는 원천 PI 상태 — 미입금 발행·일부입금·입금완료. 취소·만료는 불가.
PI_SOURCE_STATUSES = ("ISSUED", "PARTIALLY_PAID", "PAID")


def _require_source_pi(session: Session, pi_id: int, version: int) -> ProformaInvoice:
    """원천 PI 확보 — 무잠금으로 바이어를 얻어 KEY SHARE로 잠근 뒤 **사슬을 위에서부터**(QT→PI, `lock_chain`) 잠그고 바이어가 그대로인지 재확인한다.

    QT를 PI보다 먼저 잠그는 이유는 모듈 독스트링(SO INSERT의 FK 검사가 QT 행에 암묵 KEY SHARE를 잡는다 — 역순이면 PI 취소와 교착).
    """
    peek = session.execute(
        select(ProformaInvoice.buyer_partner_id).where(
            ProformaInvoice.id == pi_id, ProformaInvoice.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if peek is None:
        raise NotFoundError(log_context={"proforma_invoice_id": pi_id})
    partners.require_partner_of_any_type(
        session, peek, ("BUYER",), field="buyer_partner_id", type_label="바이어", lock=True
    )
    pi: ProformaInvoice = lock_chain(
        session, DocKind.PROFORMA_INVOICE, pi_id, expected_version=version
    )[DocKind.PROFORMA_INVOICE]
    if pi.buyer_partner_id != peek:  # 잠금 사이에 바뀌었다 — 재시도 안내(409)
        raise VersionConflictError(log_context={"proforma_invoice_id": pi_id})
    return pi


def _require_usable_pi(pi: ProformaInvoice, today: date) -> None:
    """원천 자격 — 상태와 유효기간(**직접 검사**, 미입금 발행 상태만 — 입금이 붙은 PI는 유효기간 면제)."""
    if pi.status not in PI_SOURCE_STATUSES:
        raise AppError(
            ErrorCode.TRADE_DOCS_PARENT_NOT_USABLE,
            detail={"status": pi.status},
            log_context={"proforma_invoice_id": pi.id},
        )
    if is_lapsed(DocKind.PROFORMA_INVOICE, pi.status, pi.valid_until, today):
        raise AppError(
            ErrorCode.TRADE_DOCS_VALIDITY_EXPIRED, log_context={"proforma_invoice_id": pi.id}
        )


def _require_no_live_order(session: Session, pi_id: int) -> None:
    """PI→SO 활성 1:1 — 이 PI에서 만든 살아 있는(취소 아닌) SO가 있으면 409. PI 행 잠금 하에서 확인한다."""
    found = session.execute(
        select(SalesOrder.doc_number, SalesOrder.status)
        .where(
            SalesOrder.pi_id == pi_id,
            SalesOrder.deleted_at.is_(None),
            SalesOrder.status != "CANCELLED",
        )
        .limit(1)
    ).one_or_none()
    if found is not None:
        raise AppError(
            ErrorCode.TRADE_DOCS_REFERENCE_ALREADY_CONVERTED,
            detail={"doc_number": str(found[0]), "status": str(found[1])},
        )


def _common_checks(source_doc_date: date, payload: dict[str, Any], today: date) -> date:
    doc_date: date = payload.get("doc_date") or today
    check_doc_date(doc_date)
    if doc_date < source_doc_date:
        raise invalid("doc_date", "증빙일은 원천 문서의 증빙일보다 앞설 수 없습니다.")
    return doc_date


def _terms(source: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    payment = {
        "payment_type": source.payment_type,
        "advance_pct_bp": source.advance_pct_bp,
        "balance_anchor": source.balance_anchor,
        "balance_days": source.balance_days,
    }
    incoterm = {
        "incoterm_code": source.incoterm_code,
        "incoterm_place": source.incoterm_place,
        "incoterm_year": source.incoterm_year,
    }
    return payment, incoterm


def _draft_lines(
    session: Session,
    source_lines: list[Any],
    take: dict[int, int],
    requested: list[dict[str, Any]] | None,
    *,
    from_pi: bool,
) -> list[sos.NewSoLine]:
    """SKU 재검사 후 원천 라인 값을 복사한 SO 라인 목록 — 단종·삭제 SKU는 요청 `lines` 순서 기준 인덱스로 422 열거."""
    unusable = unusable_sku_reasons(session, [line.sku_id for line in source_lines])
    if unusable:
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
    delivery = (
        {int(i["source_line_id"]): i.get("requested_delivery_date") for i in requested}
        if requested is not None
        else {}
    )
    return [
        sos.NewSoLine(
            snapshot_line_from_source(line),
            take[line.id],
            delivery.get(line.id),
            qt_line_id=None if from_pi else line.id,
            pi_line_id=line.id if from_pi else None,
        )
        for line in source_lines
    ]


def create_sales_order_from_quotation(
    *, actor: AuthenticatedUser, idempotency_key: str, qt_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """QT → SO 참조 생성(직접 경로 — PI 없는 후불 거래) — 한 트랜잭션. 같은 키 재수신은 최초 결과를 그대로 돌려준다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=SO_FROM_QT_ENDPOINT,
            key=idempotency_key,
            request_body={"qt_id": qt_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        today = today_kst()
        qt: Quotation = _require_source_quotation(session, qt_id, payload["version"], lock=True)
        _require_usable_source(qt, today)
        doc_date = _common_checks(qt.doc_date, payload, today)
        sos.lock_copy_source(
            session, payload.get("copied_from_id")
        )  # 잠금 순서 SO(5) → 원천 라인(8)
        requested = payload.get("lines")
        source_lines, take, _open = select_source_lines(
            session,
            source_kind=DocKind.QUOTATION,
            line_kind="QT_LINE",
            source_id=qt.id,
            line_model=QuotationLine,
            header_fk=QuotationLine.qt_id,
            requested=requested,
            lock=True,
            noun="견적",
        )
        lines = _draft_lines(session, source_lines, take, requested, from_pi=False)
        pay, inc = _terms(qt)
        draft = sos.SalesOrderDraft(
            buyer_partner_id=qt.buyer_partner_id,
            currency=qt.currency,
            dest_market_code=qt.dest_market_code,
            lines=lines,
            doc_date=doc_date,
            buyer_name=qt.buyer_name,
            buyer_po_no=payload.get("buyer_po_no"),
            buyer_po_date=payload.get("buyer_po_date"),
            assignee_id=payload.get("assignee_id"),
            qt_id=qt.id,
            pi_id=None,
            fx_rate=qt.fx_rate,
            fx_rate_date=qt.fx_rate_date,
            payment_columns=pay,
            incoterm_columns=inc,
            internal_note=payload.get("internal_note"),
            copied_from_id=payload.get("copied_from_id"),
        )
        row = sos.create_received_sales_order(session, actor=actor, draft=draft)
        body = sos.detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def create_sales_order_from_proforma_invoice(
    *, actor: AuthenticatedUser, idempotency_key: str, pi_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """PI → SO 참조 생성(PI 경유 — **활성 1:1**) — 한 트랜잭션. 조건·환율·단가는 PI 값을 그대로 복사한다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=SO_FROM_PI_ENDPOINT,
            key=idempotency_key,
            request_body={"pi_id": pi_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        today = today_kst()
        pi = _require_source_pi(session, pi_id, payload["version"])
        _require_usable_pi(pi, today)
        doc_date = _common_checks(pi.doc_date, payload, today)
        _require_no_live_order(session, pi.id)
        sos.lock_copy_source(
            session, payload.get("copied_from_id")
        )  # 잠금 순서 SO(5) → 원천 라인(8)
        requested = payload.get("lines")
        source_lines, take, _open = select_source_lines(
            session,
            source_kind=DocKind.PROFORMA_INVOICE,
            line_kind="PI_LINE",
            source_id=pi.id,
            line_model=ProformaInvoiceLine,
            header_fk=ProformaInvoiceLine.pi_id,
            requested=requested,
            lock=True,
            noun="선수금 청구서",
        )
        lines = _draft_lines(session, source_lines, take, requested, from_pi=True)
        pay, inc = _terms(pi)
        draft = sos.SalesOrderDraft(
            buyer_partner_id=pi.buyer_partner_id,
            currency=pi.currency,
            dest_market_code=pi.dest_market_code,
            lines=lines,
            doc_date=doc_date,
            buyer_name=pi.buyer_name,
            buyer_po_no=payload.get("buyer_po_no"),
            buyer_po_date=payload.get("buyer_po_date"),
            assignee_id=payload.get("assignee_id"),
            qt_id=pi.qt_id,
            pi_id=pi.id,
            fx_rate=pi.fx_rate,
            fx_rate_date=pi.fx_rate_date,
            payment_columns=pay,
            incoterm_columns=inc,
            internal_note=payload.get("internal_note"),
            copied_from_id=payload.get("copied_from_id"),
        )
        row = sos.create_received_sales_order(session, actor=actor, draft=draft)
        body = sos.detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body
