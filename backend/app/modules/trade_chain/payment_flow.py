"""입금 기록·역기록 오케스트레이터 — `payments` 원장 함수와 PI 상태 수렴을 한 트랜잭션에서 조합 (S3-1 PR-10a / design-E E7 / ADR-0068).

■ **방향 반전(PROGRESS 9a 자율 확정)**: 입금 모듈(`payments`)은 trade_chain을 임포트하지 못한다(설계 §2.8). 그래서 PI 잠금(`lock_chain`)·`converge_payment_status`
  호출·Idempotency-Key 선점은 이 파일(trade_chain→payments 정방향)이 맡고, 원장 INSERT·검증·audit·outbox는 `payments.service`의 순수 원장 함수가 한다.
  **`converge_payment_status`의 production 호출처는 이 파일뿐이다**(핀: test_pi_contract).
■ 순서(둘 다): 멱등 선점 → **PI `lock_chain`(단일 진입 — 거래처→QT→PI, 권한 사전 검증은 라우터 `require_roles`로 잠금 이전)** → 원장 함수(검증·INSERT·audit·outbox)
  → `converge_payment_status(순입금, due)`(PI ISSUED/PARTIALLY_PAID/PAID 6방향 자동 수렴, 상태 이력 `automatic`) → (역기록만) 확정 SO 경고 → `complete`.
  어느 단계든 실패하면 전체 롤백(원장 행·PI 상태·audit·outbox 함께)이고 거부는 멱등 키를 소비하지 않는다(성공만 complete).
■ **확정 SO 경고**: 이 PI를 근거로 이미 확정된(취소·삭제 아님) SO가 있고 역기록 뒤 순입금이 선수금 청구액(`required`) 미만이면 응답 `warnings`에
  `CONFIRMED_SO_ADVANCE_UNMET`(SO id 목록)를 싣고 outbox `payments.payment.reversed_after_confirm`(id만)을 발행한다. **역기록은 막지 않고(현금 사실 정정) SO를 자동 취소하지 않는다(4금)** —
  후속 조치(취소·재확인)는 사람이 한다. `required`는 게이트의 정의와 같은 `split_advance(PI.total, bp).advance`다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.payments import service as payments
from app.modules.payments.models import Payment
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_chain.chain_ops import lock_chain
from app.modules.trade_chain.payment_status import converge_payment_status
from app.modules.trade_docs.constants import DocKind

KIND = DocKind.PROFORMA_INVOICE
RECEIPT_ENDPOINT = "POST /api/v1/proforma-invoices/{pi_id}/payments"
REVERSAL_ENDPOINT = "POST /api/v1/payments/{payment_id}/reversal"

WARNING_CONFIRMED_SO_UNMET = "CONFIRMED_SO_ADVANCE_UNMET"


def confirmed_sales_order_ids(session: Any, pi_id: int) -> list[int]:
    """이 PI를 근거로 확정된 살아 있는 SO(확정 이력 있고 취소·삭제 아님 — 보류 포함)의 id."""
    return list(
        session.execute(
            select(SalesOrder.id)
            .where(
                SalesOrder.pi_id == pi_id,
                SalesOrder.confirmed_at.is_not(None),
                SalesOrder.status != "CANCELLED",
                SalesOrder.deleted_at.is_(None),
            )
            .order_by(SalesOrder.id)
        ).scalars()
    )


def _converge(session: Any, pi: ProformaInvoice, net: int, actor_user_id: int) -> None:
    converge_payment_status(
        session,
        pi.id,
        received_total_amount=net,
        due_amount=payments.advance_due_amount(pi),
        actor_user_id=actor_user_id,
    )


def record_receipt(
    *, actor: AuthenticatedUser, idempotency_key: str, pi_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """PI 선수금 입금 기록 — 201. `payload`는 `PaymentReceiptRequest.model_dump(mode="json")`."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=RECEIPT_ENDPOINT,
            key=idempotency_key,
            request_body={"pi_id": pi_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        pi: ProformaInvoice = lock_chain(session, KIND, pi_id)[KIND]
        row, net = payments.append_receipt(
            session,
            actor_user_id=actor.id,
            pi=pi,
            received_amount_text=payload["received_amount"],
            received_currency=payload["received_currency"],
            received_on=date.fromisoformat(payload["received_on"]),
            reference=payload["reference"],
        )
        _converge(session, pi, net, actor.id)

        body = {
            "payment": payments.payment_body(row),
            "summary": payments.summary_body(pi, net),
            "warnings": [],
        }
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def reverse_payment(
    *, actor: AuthenticatedUser, idempotency_key: str, payment_id: int, reason: str
) -> tuple[int, dict[str, Any]]:
    """입금 역기록(−전액 신규 행) — 201. 확정 SO 경고는 응답 `warnings`·outbox로만 알린다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REVERSAL_ENDPOINT,
            key=idempotency_key,
            request_body={"payment_id": payment_id, "reason": reason},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        # 무잠금으로 원 입금 → PI id(입금 행은 불변이라 잠금 전 조회가 안전하다) → PI 단일 진입 잠금
        peek = payments.get_payment(session, payment_id)
        pi: ProformaInvoice = lock_chain(session, KIND, peek.pi_id)[KIND]
        original: Payment = payments.get_payment(session, payment_id)
        row, net = payments.append_reversal(
            session, actor_user_id=actor.id, pi=pi, original=original, reason=reason
        )
        _converge(session, pi, net, actor.id)

        warnings: list[dict[str, Any]] = []
        due = payments.advance_due_amount(pi)
        if net < due:
            so_ids = confirmed_sales_order_ids(session, pi.id)
            if so_ids:
                warnings.append({"code": WARNING_CONFIRMED_SO_UNMET, "sales_order_ids": so_ids})
                outbox.publish(
                    session,
                    event_type="payments.payment.reversed_after_confirm",
                    aggregate_type="payments",
                    aggregate_id=row.id,
                    payload={"payment_id": row.id, "pi_id": pi.id, "sales_order_ids": so_ids},
                )

        body = {
            "payment": payments.payment_body(row),
            "summary": payments.summary_body(pi, net),
            "warnings": warnings,
        }
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body
