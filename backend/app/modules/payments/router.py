"""입금 원장 조회 엔드포인트 (S3-1 PR-10a / design-E E7).

`GET /proforma-invoices/{pi_id}/payments` — 전 역할 열람(금액은 원가·마진이 아니라 마스킹 비대상), 페이지 기본 50, 순입금·선수금 청구액·PI 상태 요약 동봉.

**쓰기(입금 기록·역기록)는 이 라우터에 없다** — 두 동작은 PI 잠금·멱등 선점·상태 수렴을 한 트랜잭션에서 조합하는 오케스트레이션이라 trade_chain
(`payment_flow`·`router.pi_router`/`payments_router`)에 있다(입금 모듈은 trade_chain을 임포트하지 못한다 — 방향 반전, 설계 §2.8).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import CurrentUser
from app.core.db.uow import unit_of_work
from app.core.pagination import PageParams
from app.modules.payments import service
from app.modules.payments.schemas import PaymentOut, PaymentPage, PaymentSummaryOut
from app.modules.proforma_invoices.service import require_proforma_invoice

router = APIRouter(prefix="/proforma-invoices", tags=["payments"])


@router.get("/{pi_id}/payments", summary="PI 입금 원장 (페이지·순입금·선수금 청구액·PI 상태 요약)")
def list_pi_payments(
    pi_id: Annotated[int, Path(ge=1)],
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
) -> PaymentPage:
    with unit_of_work() as uow:
        session = uow.session
        pi = require_proforma_invoice(session, pi_id)
        items, total = service.list_payment_rows(
            session, pi_id, offset=params.offset, limit=params.limit
        )
        summary = service.summary_body(pi, service.net_received_for_pi(session, pi_id))
    return PaymentPage(
        items=[PaymentOut.model_validate(item) for item in items],
        total=total,
        page=params.page,
        size=params.size,
        summary=PaymentSummaryOut.model_validate(summary),
    )
