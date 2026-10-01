"""PI 입금 상태 수렴 — `converge_payment_status` 단일 진입점 (S3-1 ADR-0051 / design-B B1 / design-integrated X-17·X-21).

PI 상태 {ISSUED, PARTIALLY_PAID, PAID}는 **누적 순입금액의 순수 함수**다 — `derive_pi_status(received_total, due)`가 대상 상태를
정하고(0→ISSUED, 0<x<due→PARTIALLY_PAID, x≥due>0→PAID) 이 함수만이 그 결과를 PI에 적용한다(6방향 자동 전이).
`due`(선수금 청구액)는 컬럼이 아니라 `split_advance(PI.total, advance_pct_bp).advance`이고 호출자가 계산해 인자로 넘긴다(X-17 — 파생값
이중 저장 금지). `due == 0`(선수금 T/T 아님)은 수렴 대상이 아니라 no-op이다.

■ **production 호출처는 입금 오케스트레이터 `trade_chain/payment_flow.py` 한 곳뿐이다**(PR-10a — 입금 기록·역기록이 같은 트랜잭션에서 부른다. 호출자 집합은 test_pi_contract가 핀).
  테스트도 직접 호출로 상태 기계의 자동 6방향을 시험한다. 호출자가 사슬 잠금(QT→PI)을 이미 잡았어도 되고(같은 트랜잭션의 재잠금은 무해) 아니어도 이 함수가 잡는다.
■ 취소·만료된 PI에는 수렴할 수 없다(엣지 없음) — 409 `PAYMENT.PI_NOT_OPEN`으로 **fail-closed** 거부한다(새 PI를 발행해야 한다).
■ 입금 역기록으로 ISSUED에 **되돌아왔는데** 유효기간이 지났고 살아 있는 후속 SO가 없으면 같은 트랜잭션에서 EXPIRED로 추가 수렴한다
  (이력 2행) — 일부입금·입금완료 PI는 유효기간이 SO 생성을 막지 않지만(선수금이 갇히는 사고 방지) 입금이 사라지면 다시 미입금 발행이다.
■ 입금 모듈(`payments`)은 trade_chain을 임포트하지 못한다(§2.8) — 그래서 호출 방향은 trade_chain→payments(오케스트레이터가 원장 함수와 이 함수를 조합)로 반전했다(PROGRESS 9a·10a 자율 확정).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.trade_chain.chain_ops import converge_parent, lock_chain
from app.modules.trade_docs.chain import has_live_children
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.transition import record_transition

KIND = DocKind.PROFORMA_INVOICE
PAYMENT_STATES = ("ISSUED", "PARTIALLY_PAID", "PAID")


def derive_pi_status(received_total_amount: int, due_amount: int) -> str:
    """누적 순입금 → PI 입금 상태(순수 함수). 음수 누적·음수 due는 호출 계약 위반이다(ValueError)."""
    if received_total_amount < 0 or due_amount < 0:
        raise ValueError("입금 누적액·청구액은 0 이상이어야 합니다.")
    if received_total_amount == 0:
        return "ISSUED"
    if due_amount > 0 and received_total_amount >= due_amount:
        return "PAID"
    return "PARTIALLY_PAID"


def converge_payment_status(
    session: Session,
    pi_id: int,
    *,
    received_total_amount: int,
    due_amount: int,
    actor_user_id: int | None,
    today: date | None = None,
) -> str | None:
    """PI를 누적 순입금에 맞는 입금 상태로 수렴시킨다 — 바꿨으면 도달 상태(마지막 상태), 아니면 None."""
    today = today or today_kst()
    pi: ProformaInvoice = lock_chain(session, KIND, pi_id)[KIND]
    if pi.status not in PAYMENT_STATES:
        raise AppError(
            ErrorCode.TRADE_DOCS_PAYMENT_PI_NOT_OPEN,
            detail={"status": pi.status},
            log_context={"proforma_invoice_id": pi_id},
        )
    if due_amount == 0:  # 선수금 T/T가 아니라 수렴 대상이 아니다 — 상태를 만지지 않는다
        return None
    target = derive_pi_status(received_total_amount, due_amount)
    if target == pi.status:
        return None
    record_transition(
        session,
        pi,
        target,
        actor_user_id=actor_user_id,
        reason=None,
        automatic=True,
    )
    final = target
    if (
        target == "ISSUED"
        and is_lapsed(KIND, pi.status, pi.valid_until, today)
        and not has_live_children(session, KIND, pi.id)
    ):
        record_transition(
            session,
            pi,
            "EXPIRED",
            actor_user_id=actor_user_id,
            reason=f"자동: 유효기간 경과 (valid_until={pi.valid_until}, 기준일={today})",
            automatic=True,
        )
        converge_parent(session, KIND, pi, actor_user_id=actor_user_id, today=today)
        final = "EXPIRED"
    return final
