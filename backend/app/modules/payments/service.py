"""입금 원장 서비스 — 순수 원장 함수 (S3-1 PR-10a / design-E E7 / ADR-0068).

■ **이 모듈은 trade_chain을 임포트하지 않는다**(설계 §2.8 — 입금 모듈은 L2 오케스트레이터의 하위). 그래서 여기 함수는 **잠금·멱등·PI 상태 수렴을
  하지 않는다**: PI 행 잠금(`lock_chain`)·Idempotency-Key 선점·`converge_payment_status` 호출은 **오케스트레이터
  `trade_chain/payment_flow.py`가 한 트랜잭션에서 조합**한다(방향 반전 — PROGRESS 9a "자율 확정"). 여기 함수는 호출자가 PI를 이미 잠갔다는 전제로
  검증 → INSERT → audit → outbox만 한다(같은 트랜잭션·같은 세션).
■ `net_received_for_pi`가 **순입금의 유일한 정의**다(`COALESCE(SUM(received_amount), 0)` — 부호 있는 합). PI 상태·다른 집계로 대체하지 않는다.
■ 선수금 청구액 `due`는 저장하지 않는다 — `advance_due_amount(pi)` = `split_advance(total, bp).advance`(X-17; 선수금 T/T가 아니면 0).
■ 검증 순서(E7): ① PI 열림(ISSUED·PARTIALLY_PAID·PAID) → ② 결제유형 TT_ADVANCE → ③ 통화 일치 → ④ 금액 파싱(자릿수·양수) → ⑤ 증빙일 ≤ 오늘(KST)
  → ⑥ 순입금+금액 ≤ due. 먼저 걸린 것이 응답이다(검사 순서가 곧 오류 우선순위).
■ 로그·audit·outbox에는 **참조 텍스트(reference)·사유(reason)를 싣지 않는다** — id·금액·통화·증빙일뿐이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.money import AmountFormatError, parse_minor_amount
from app.core.time import today_kst
from app.modules.audit import service as audit
from app.modules.outbox import service as outbox
from app.modules.payments.models import (
    KIND_RECEIPT,
    KIND_REVERSAL,
    MAX_MINOR_AMOUNT,
    Payment,
)
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.trade_docs.constants import PaymentType
from app.modules.trade_docs.machine import PI_PAYMENT_STATES
from app.modules.trade_docs.payment_terms import split_advance
from app.modules.trade_docs.validation import invalid
from app.modules.trade_docs.views import money_text

#: 입금을 받을 수 있는 PI 상태 — 취소·만료 PI는 입금을 받지 않는다(새 PI 발행).
OPEN_PI_STATES = PI_PAYMENT_STATES

ADVANCE_PAYMENT_TYPE = PaymentType.TT_ADVANCE.value


def advance_due_amount(pi: ProformaInvoice) -> int:
    """PI의 선수금 청구액(최소단위) — 저장하지 않는 파생값. 선수금 T/T가 아니면 0."""
    if pi.payment_type != ADVANCE_PAYMENT_TYPE or pi.advance_pct_bp is None:
        return 0
    return split_advance(pi.total_amount, pi.advance_pct_bp)[0]


def net_received_for_pi(session: Session, pi_id: int) -> int:
    """PI의 순입금(최소단위) — 입금 합 − 역기록(부호 있는 합). **유일한 정의**다."""
    total = session.execute(
        select(func.coalesce(func.sum(Payment.received_amount), 0)).where(Payment.pi_id == pi_id)
    ).scalar_one()
    return int(total)


def require_open_pi(pi: ProformaInvoice) -> None:
    if pi.status not in OPEN_PI_STATES:
        raise AppError(
            ErrorCode.TRADE_DOCS_PAYMENT_PI_NOT_OPEN,
            detail={"status": pi.status},
            log_context={"proforma_invoice_id": pi.id},
        )


def _constraint_of(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


#: payments의 DB 제약·유니크 인덱스 → 업무 오류 번역표. **모델·마이그레이션의 제약 집합 ⊆ 이 표**임을 테스트가 pg_constraint·pg_indexes로 대사한다
#: (신규 제약이 번역 없이 500으로 새지 않게). 서비스 선검증이 1차이고 DB 위반은 최후 방어선이다 — CHECK·FK 위반은 선검증이 놓친 입력/상태 결함이라
#: 422(입력) 또는 409(상태)로 일관되게 번역한다.
CONSTRAINT_ERRORS: dict[str, ErrorCode] = {
    "uq_payments_reverses_payment_id": ErrorCode.PAYMENTS_PAYMENT_ALREADY_REVERSED,
    "fk_payments_reverses_same_pi_currency": ErrorCode.PAYMENTS_PAYMENT_NOT_REVERSIBLE,
    "ck_payments_kind_sign": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_kind_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_currency_upper": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_reference_not_blank": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_reference_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_reason_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_payments_received_amount_range": ErrorCode.VALIDATION_INVALID_FIELD,
    # 존재하지 않는 PI·거래처·사용자 FK — 호출 계약 위반(잠금 뒤 조회 통과 후라 정상 경로에서는 발생 불가)
    "fk_payments_pi_id_proforma_invoices": ErrorCode.RESOURCE_NOT_FOUND,
    "fk_payments_partner_id_partners": ErrorCode.RESOURCE_NOT_FOUND,
    "fk_payments_recorded_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    # 자동 생성 키(IDENTITY)라 위반 불가 — 번역표 완결성을 위해 등재
    "pk_payments": ErrorCode.INTERNAL_UNEXPECTED,
    "uq_payments_id_pi_id_received_currency": ErrorCode.INTERNAL_UNEXPECTED,
}


def translate_integrity_error(exc: IntegrityError) -> AppError | None:
    """DB 제약 위반 → 업무 오류(번역표에 있고 내부 오류가 아닌 것만). 없으면 None — 호출자는 원 예외를 그대로 전파한다(fail-visible)."""
    code = CONSTRAINT_ERRORS.get(_constraint_of(exc) or "")
    if code is None or code is ErrorCode.INTERNAL_UNEXPECTED:
        return None
    return AppError(code)


def _flush(session: Session) -> None:
    """INSERT를 확정(flush)하고 DB 제약 위반을 번역표대로 업무 오류로 바꾼다 — 번역 불가는 조용히 삼키지 않고 그대로 전파(500)."""
    try:
        session.flush()
    except IntegrityError as exc:
        translated = translate_integrity_error(exc)
        if translated is not None:
            raise translated from exc
        raise


def append_receipt(
    session: Session,
    *,
    actor_user_id: int,
    pi: ProformaInvoice,
    received_amount_text: str,
    received_currency: str,
    received_on: date,
    reference: str,
    today: date | None = None,
) -> tuple[Payment, int]:
    """입금 1건을 원장에 더한다(호출자가 PI를 이미 잠갔다). (행, 입금 후 순입금)을 돌려준다."""
    # ① PI 열림 ② 선수금 T/T
    require_open_pi(pi)
    if pi.payment_type != ADVANCE_PAYMENT_TYPE:
        raise AppError(
            ErrorCode.PAYMENTS_PAYMENT_PI_NOT_ADVANCE,
            detail={"payment_type": pi.payment_type},
            log_context={"proforma_invoice_id": pi.id},
        )
    # ③ 통화 — 환산하지 않는다(요청이 통화를 명시해 오입력을 잡는다)
    if received_currency != pi.currency:
        raise AppError(
            ErrorCode.PAYMENTS_PAYMENT_CURRENCY_MISMATCH,
            detail={"pi_currency": pi.currency, "received_currency": received_currency},
            log_context={"proforma_invoice_id": pi.id},
        )
    # ④ 금액 — 자릿수 초과는 반올림 없이 거부, 양수
    try:
        amount = parse_minor_amount(received_amount_text, pi.currency, field="received_amount")
    except AmountFormatError as exc:
        raise invalid("received_amount", exc.reason) from exc
    if amount <= 0:
        raise invalid("received_amount", "입금액은 0보다 커야 합니다.")
    if amount > MAX_MINOR_AMOUNT:
        raise invalid("received_amount", "금액이 너무 큽니다.")
    # ⑤ 증빙일 — 미래 불가(KST), 소급 허용
    if received_on > (today or today_kst()):
        raise invalid("received_on", "입금일은 오늘(KST)보다 미래일 수 없습니다.")
    # ⑥ 순입금 + 금액 ≤ 선수금 청구액
    due = advance_due_amount(pi)
    net_before = net_received_for_pi(session, pi.id)
    if net_before + amount > due:
        raise AppError(
            ErrorCode.PAYMENTS_PAYMENT_EXCEEDS_DUE,
            detail={
                "due_amount": money_text(due, pi.currency),
                "net_received_amount": money_text(net_before, pi.currency),
                "remaining_amount": money_text(max(due - net_before, 0), pi.currency),
            },
            log_context={"proforma_invoice_id": pi.id},
        )

    row = Payment(
        partner_id=pi.buyer_partner_id,
        pi_id=pi.id,
        kind=KIND_RECEIPT,
        received_amount=amount,
        received_currency=pi.currency,
        received_on=received_on,
        reference=reference.strip(),
        recorded_by_id=actor_user_id,
    )
    session.add(row)
    _flush(session)
    net_after = net_before + amount
    audit.record(
        session,
        action="payments.receipt.recorded",
        actor_user_id=actor_user_id,
        entity_type="payments",
        entity_id=row.id,
        detail={
            "pi_id": pi.id,
            "amount": amount,
            "currency": pi.currency,
            "received_on": received_on.isoformat(),
            "net_after": net_after,
        },
    )
    outbox.publish(
        session,
        event_type="payments.payment.recorded",
        aggregate_type="payments",
        aggregate_id=row.id,
        payload={"payment_id": row.id, "pi_id": pi.id, "kind": KIND_RECEIPT},
    )
    return row, net_after


def append_reversal(
    session: Session,
    *,
    actor_user_id: int,
    pi: ProformaInvoice,
    original: Payment,
    reason: str,
) -> tuple[Payment, int]:
    """원 입금의 역기록(−전액)을 원장에 더한다(호출자가 PI를 이미 잠갔다). (행, 역기록 후 순입금)을 돌려준다."""
    require_open_pi(pi)
    if (
        original.pi_id != pi.id
    ):  # 호출 계약 위반(오케스트레이터가 원 입금의 PI를 잠근다) — fail-closed
        raise ValueError("역기록 대상 입금이 잠근 PI에 속하지 않습니다.")
    if original.kind != KIND_RECEIPT:
        raise AppError(
            ErrorCode.PAYMENTS_PAYMENT_NOT_REVERSIBLE,
            log_context={"payment_id": original.id},
        )
    already = session.execute(
        select(exists().where(Payment.reverses_payment_id == original.id))
    ).scalar_one()
    if already:
        raise AppError(
            ErrorCode.PAYMENTS_PAYMENT_ALREADY_REVERSED,
            log_context={"payment_id": original.id},
        )
    net_before = net_received_for_pi(session, pi.id)
    row = Payment(
        partner_id=original.partner_id,
        pi_id=original.pi_id,
        kind=KIND_REVERSAL,
        received_amount=-original.received_amount,
        received_currency=original.received_currency,
        received_on=today_kst(),
        reference=f"역기록 #{original.id}",
        reverses_payment_id=original.id,
        reason=reason.strip(),
        recorded_by_id=actor_user_id,
    )
    session.add(row)
    _flush(session)
    net_after = net_before - original.received_amount
    audit.record(
        session,
        action="payments.receipt.reversed",
        actor_user_id=actor_user_id,
        entity_type="payments",
        entity_id=row.id,
        detail={
            "pi_id": pi.id,
            "original_payment_id": original.id,
            "amount": row.received_amount,
            "currency": row.received_currency,
            "net_after": net_after,
        },
    )
    outbox.publish(
        session,
        event_type="payments.payment.reversed",
        aggregate_type="payments",
        aggregate_id=row.id,
        payload={
            "payment_id": row.id,
            "pi_id": pi.id,
            "original_payment_id": original.id,
            "kind": KIND_REVERSAL,
        },
    )
    return row, net_after


def get_payment(session: Session, payment_id: int) -> Payment:
    row = session.get(Payment, payment_id)
    if row is None:
        raise NotFoundError(detail={"resource": "payment"})
    return row


# ── 응답 본문 ──────────────────────────────────────────────────────────────────


def summary_body(pi: ProformaInvoice, net_received: int) -> dict[str, Any]:
    """PI 입금 요약 — 순입금·선수금 청구액·잔여·PI 상태(금액은 최소단위 정수+표기 문자열)."""
    due = advance_due_amount(pi)
    remaining = max(due - net_received, 0)
    return {
        "pi_id": pi.id,
        "pi_status": pi.status,
        "currency": pi.currency,
        "payment_type": pi.payment_type,
        "due_amount": due,
        "due_text": money_text(due, pi.currency),
        "net_received_amount": net_received,
        "net_received_text": money_text(net_received, pi.currency),
        "remaining_amount": remaining,
        "remaining_text": money_text(remaining, pi.currency),
    }


def payment_body(row: Payment, reversed_by_payment_id: int | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "pi_id": row.pi_id,
        "partner_id": row.partner_id,
        "kind": row.kind,
        "received_amount": row.received_amount,
        "received_amount_text": money_text(row.received_amount, row.received_currency),
        "received_currency": row.received_currency,
        "received_on": row.received_on.isoformat(),
        "reference": row.reference,
        "reverses_payment_id": row.reverses_payment_id,
        "reversed_by_payment_id": reversed_by_payment_id,
        "reason": row.reason,
        "recorded_by_id": row.recorded_by_id,
        "created_at": row.created_at.isoformat(),
    }


def list_payment_rows(
    session: Session, pi_id: int, *, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """PI의 원장 행(입금 id 오름차순 — 시간순). 입금 행에는 그것을 되돌린 역기록 id를 함께 싣는다."""
    total = session.execute(
        select(func.count()).select_from(Payment).where(Payment.pi_id == pi_id)
    ).scalar_one()
    rows = session.execute(
        select(Payment)
        .where(Payment.pi_id == pi_id)
        .order_by(Payment.id)
        .offset(offset)
        .limit(limit)
    ).scalars()
    page = list(rows)
    reversed_by: dict[int | None, int] = dict(
        session.execute(
            select(Payment.reverses_payment_id, Payment.id).where(
                Payment.reverses_payment_id.in_([r.id for r in page])
            )
        )
        .tuples()
        .all()
    )
    return [payment_body(r, reversed_by.get(r.id)) for r in page], int(total)
