"""견적 전이 오케스트레이션 — 발행·취소·개정 (S3-1 ADR-0051~0053 / design-B B1~B3·B8 / design-A A10).

이 파일이 QT 상태를 바꾸는 **유일한 호출자**다(`record_transition` 경유). 세 동작 모두 사람 1클릭이고
`Idempotency-Key` 필수(더블클릭=이력 1행), 행 `FOR UPDATE` 후 `version` 대조(409)다. 자동 전이(연쇄·스윕)는
`chain_ops`·(PR-6) `expiry` 몫이다.

■ 발행(issue) — DRAFT→ISSUED 동결 액션. 입력 완결성 사전검사(결제조건·Incoterms·환율·바이어 표기·주소·유효기간·라인 ≥ 1)
  → 500이 아니라 422 INCOMPLETE. 바이어 유형은 **발행 시점에 재검증**(fail-closed — 유형 해제 후에도). 동결 시각은
  `record_transition`이 같은 flush에 쓴다.
■ 개정(revisions) — 원본이 ISSUED일 때만 허용되는 **유일한 복제 진입점**(X-08). 원본 값을 복사한 새 초안을 만들고,
  그 초안을 **발행하는 같은 트랜잭션**이 원본을 사람 엣지 ISSUED→CANCELLED(행위자=발행자, 사유 자동 문구)로 취소한다 —
  발행이 실패하면 원본 취소도 함께 롤백된다. 원본에 살아 있는 후속(PI·SO)이 있으면 개정 발행은 409 SUCCESSOR_ALIVE.
■ 취소 — `record_transition`이 후속 생존 검사를 엣지 검사보다 먼저 한다(역순 취소만).
잠금 순서: 멱등 → 바이어(KEY SHARE) → QT(id 오름차순) → 라인 → 채번(마지막).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.core.time import today_kst
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners import service as partners
from app.modules.quotations import service as quotations
from app.modules.quotations.models import Quotation, QuotationLine
from app.modules.trade_docs.chain import live_children_numbers
from app.modules.trade_docs.constants import REVISION_CANCEL_REASON, DocKind
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.snapshot import snapshot_line_from_source
from app.modules.trade_docs.transition import record_transition

KIND = DocKind.QUOTATION
ISSUE_ENDPOINT = "POST /api/v1/quotations/{id}/issue"
TRANSITION_ENDPOINT = "POST /api/v1/quotations/{id}/transitions"
REVISION_ENDPOINT = "POST /api/v1/quotations/{id}/revisions"


def _lock_buyer_then_quotation(session: Any, qt_id: int, version: int) -> Quotation:
    """잠금 순서 (2)→(3): 무잠금 조회로 바이어를 얻어 KEY SHARE로 잠근 뒤 QT를 잠그고 바이어가 그대로인지 재확인."""
    peek = session.execute(
        select(Quotation.buyer_partner_id).where(
            Quotation.id == qt_id, Quotation.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if peek is None:
        return lock_document(session, Quotation, qt_id, expected_version=version)  # NotFound 위임
    partners.require_partner_of_any_type(
        session, peek, ("BUYER",), field="buyer_partner_id", type_label="바이어", lock=True
    )
    row = lock_document(session, Quotation, qt_id, expected_version=version)
    if row.buyer_partner_id != peek:  # 잠금 사이에 바이어가 바뀌었다 — 재시도 안내(409)
        raise VersionConflictError(log_context={"quotation_id": qt_id})
    return row


def _completeness_errors(session: Any, row: Quotation) -> dict[str, str]:
    missing: dict[str, str] = {}
    if row.payment_type is None:
        missing["payment_terms"] = "결제조건을 입력해 주세요."
    if row.incoterm_code is None:
        missing["incoterm"] = "Incoterms를 입력해 주세요."
    if row.fx_rate is None:
        missing["fx_rate"] = "환율을 입력해 주세요."
    if not (row.buyer_name or "").strip():
        missing["buyer_name"] = "바이어 표기를 입력해 주세요."
    if not (row.buyer_address or "").strip():
        missing["buyer_address"] = (
            "바이어 주소를 입력해 주세요(거래처의 영문 주소를 등록하면 자동으로 채워집니다)."
        )
    if row.valid_until is None:
        missing["valid_until"] = "유효기간을 입력해 주세요."
    if quotations_line_count(session, row.id) == 0:
        missing["lines"] = "라인을 1개 이상 추가해 주세요."
    return missing


def quotations_line_count(session: Any, qt_id: int) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(QuotationLine)
            .where(QuotationLine.qt_id == qt_id, QuotationLine.deleted_at.is_(None))
        ).scalar_one()
    )


def issue_quotation(
    *, actor: AuthenticatedUser, idempotency_key: str, qt_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """견적 발행(동결 액션) — 완결성 검사 → (개정본이면 원본 취소) → DRAFT→ISSUED."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=ISSUE_ENDPOINT,
            key=idempotency_key,
            request_body={"qt_id": qt_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        # 개정본이면 원본을 먼저(id 오름차순 — 원본이 항상 더 오래된 행) 잠근다.
        peek_source = session.execute(
            select(Quotation.copied_from_id).where(Quotation.id == qt_id)
        ).scalar_one_or_none()
        source: Quotation | None = None
        if peek_source is not None and peek_source < qt_id:
            source = lock_document(session, Quotation, peek_source)
        row = _lock_buyer_then_quotation(session, qt_id, version)
        if row.copied_from_id != peek_source:
            raise VersionConflictError(log_context={"quotation_id": qt_id})

        if row.status != "DRAFT":
            raise AppError(
                ErrorCode.TRADE_DOCS_TRANSITION_NOT_ALLOWED,
                detail={"from": row.status, "to": "ISSUED"},
            )
        missing = _completeness_errors(session, row)
        if missing:
            raise AppError(ErrorCode.TRADE_DOCS_DOCUMENT_INCOMPLETE, detail=missing)
        assert row.valid_until is not None
        if row.valid_until < today_kst():
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"valid_until": "유효기간이 이미 지났습니다. 오늘 이후 날짜로 바꿔 주세요."},
            )

        if source is not None and source.status == "ISSUED":
            # 개정 발행 = 원본을 사람 엣지(ISSUED→CANCELLED)로 취소 — 후속이 살아 있으면 여기서 409(전체 롤백).
            record_transition(
                session,
                source,
                "CANCELLED",
                actor_user_id=actor.id,
                reason=REVISION_CANCEL_REASON.format(doc_number=row.doc_number),
                automatic=False,
            )
        elif source is not None and source.status == "CONVERTED":
            # 수주전환된 원본은 개정할 수 없다 — 살아 있는 후속이 있으므로 역순 취소 규칙과 같은 409.
            raise AppError(
                ErrorCode.TRADE_DOCS_CANCEL_SUCCESSOR_ALIVE,
                detail={"successors": live_children_numbers(session, KIND, source.id)},
            )

        record_transition(
            session,
            row,
            "ISSUED",
            actor_user_id=actor.id,
            reason=None,
            automatic=False,
            via_freeze_action=True,
        )
        body = quotations.detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def transition_quotation(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    qt_id: int,
    to: str,
    version: int,
    reason: str | None,
) -> tuple[int, dict[str, Any]]:
    """범용 사람 전이(현재는 취소뿐 — 동결 엣지·자동 엣지는 이 통로로 못 넘는다)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=TRANSITION_ENDPOINT,
            key=idempotency_key,
            request_body={"qt_id": qt_id, "to": to, "version": version, "reason": reason},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        row = lock_document(session, Quotation, qt_id, expected_version=version)
        record_transition(session, row, to, actor_user_id=actor.id, reason=reason, automatic=False)
        body = quotations.detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def create_revision(
    *, actor: AuthenticatedUser, idempotency_key: str, qt_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """개정 초안 작성 — 원본(ISSUED) 값을 그대로 복사한 새 초안(DRAFT). 원본은 이 시점엔 건드리지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REVISION_ENDPOINT,
            key=idempotency_key,
            request_body={"qt_id": qt_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        source = _lock_buyer_then_quotation(session, qt_id, version)
        if source.status != "ISSUED":
            raise AppError(
                ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE,
                detail={"status": source.status},
                log_context={"quotation_id": qt_id},
            )
        successors = live_children_numbers(session, KIND, source.id)
        if successors:
            raise AppError(
                ErrorCode.TRADE_DOCS_CANCEL_SUCCESSOR_ALIVE, detail={"successors": successors}
            )
        if quotations.has_live_copy(
            session, source.id
        ):  # 원본당 살아 있는 개정본은 하나(잠금 하에서 확인)
            raise AppError(
                ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE,
                detail={"reason": "이미 진행 중인 개정본이 있습니다."},
            )
        source_lines = (
            session.execute(
                select(QuotationLine)
                .where(QuotationLine.qt_id == source.id, QuotationLine.deleted_at.is_(None))
                .order_by(QuotationLine.line_no)
            )
            .scalars()
            .all()
        )
        header: dict[str, Any] = {
            "doc_date": today_kst(),
            "currency": source.currency,
            "buyer_partner_id": source.buyer_partner_id,
            "buyer_name": source.buyer_name,
            "buyer_address": source.buyer_address,
            "dest_market_code": source.dest_market_code,
            "fx_rate": source.fx_rate,
            "fx_rate_date": source.fx_rate_date,
            "payment_type": source.payment_type,
            "advance_pct_bp": source.advance_pct_bp,
            "balance_anchor": source.balance_anchor,
            "balance_days": source.balance_days,
            "incoterm_code": source.incoterm_code,
            "incoterm_place": source.incoterm_place,
            "incoterm_year": source.incoterm_year,
            "valid_until": None,  # 새 유효기간은 발행 전에 사람이 정한다(서버 기본값 없음)
            "internal_note": source.internal_note,
            "assignee_id": source.assignee_id,
            "copied_from_id": source.id,
        }
        new_lines = [
            quotations.NewLine(snapshot_line_from_source(line), line.quantity)
            for line in source_lines
        ]
        draft = quotations.insert_draft(session, actor_id=actor.id, header=header, lines=new_lines)
        body = quotations.detail_body(session, draft)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body
