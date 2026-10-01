"""PI 선수금 입금 게이트 평가 — 순수 함수 (S3-1 PR-11a / design-E E6 / design-integrated X-26·X-36 / ADR-0069).

SO 확정 시 "선수금이 들어왔는가"를 **금액**으로 판정한다. 게이트 결과 4값으로의 변환(모드 OFF/WARN/BLOCK → PASS/WARN/BLOCK·UNKNOWN)은 호출 어댑터
(`trade_chain/gate_evaluators.py`)가 한다 — 이 함수는 입력 `(SO 조건, PI, 모드)`의 순수 함수이고 쓰기·잠금이 없다.

■ **조건 = 금액 대조**: `net_received ≥ required`이면 충족(**등호 통과**, 부족 = strict 미만). `required = split_advance(PI.total, PI.advance_pct_bp).advance`(HALF_UP — 청구서 금액과 동일),
  `net_received = net_received_for_pi`(**순입금의 유일한 정의** — 부호 있는 합). **PI 상태값은 충족 여부에 쓰지 않는다**(30% 입금 시 PARTIALLY_PAID도 통과해야 하고, 상태 조작으로 우회하는
  경로를 막는다) — 유일한 상태 참조는 "입금을 받을 수 있는 PI인가"(`OPEN_PI_STATES` 멤버십, `PI_NOT_USABLE` 방어 분기)뿐이다. `required = 0`(bp가 작아 반올림 0)은 충족(`ZERO_REQUIRED`).
■ **참조 SO는 PI 조건 기준**(`so.pi_id IS NOT NULL`): 결제유형·선수금 %·총액·통화는 PI의 것이다. SO 편집은 결제조건 변경을 허용(A4)하므로 확정 직전 `TT_ADVANCE`→`LC`로 바꿔 게이트를 끄는
  우회가 가능한데, PI가 바이어에게 청구한 조건이 정본이다. SO 조건이 PI와 다르면 `terms_diverge=True`. PI 없는 SO는 SO 자신의 조건이다.
■ **활성 = 판정 기준 결제유형이 `TT_ADVANCE`일 때만**(§7.3 — L/C·후불은 모드가 BLOCK이어도 통과, 양방향 테스트). 결제유형이 없으면 **비활성이 아니라 판정 불능**(`PAYMENT_TYPE_UNSET`).
■ 비충족 3종: `SHORT`(순입금<required) / `PI_MISSING`(판정 기준이 SO 자신인데 `TT_ADVANCE` — PI 없는 선수금 SO. 입금 원장은 PI 단위라 판정할 수 없다) / `PI_NOT_USABLE`(참조 PI가 없거나 입금 가능 상태가 아님 — 방어 분기).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.modules.payments.service import OPEN_PI_STATES, net_received_for_pi
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.trade_docs.constants import PaymentType
from app.modules.trade_docs.payment_terms import split_advance

MODES = ("OFF", "WARN", "BLOCK")


class PiAdvanceState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"  # 선수금 T/T가 아니다 — 게이트 비활성
    SKIPPED_OFF = "SKIPPED_OFF"  # 모드 OFF — 확인 생략(스킵 사실을 증적에 남긴다)
    MET = "MET"  # 순입금 ≥ 요구액(요구액 0 포함)
    UNMET = "UNMET"  # 미충족 — 결과 변환은 모드가 정한다
    INDETERMINATE = (
        "INDETERMINATE"  # 판정 불능(결제조건 부재·불완전) — 모드와 무관하게 통과가 아니다
    )


@dataclass(frozen=True, slots=True)
class SoTerms:
    """SO가 가진 결제조건 — PI 없는 SO의 판정 기준이자 PI와의 불일치 표시용."""

    payment_type: str | None
    advance_pct_bp: int | None


@dataclass(frozen=True, slots=True)
class PiAdvanceEvaluation:
    state: PiAdvanceState
    reason: str
    #: 판정 기준 조건의 출처 — 'PI'(참조 SO) 또는 'SO'.
    terms_basis: str
    #: 참조 SO의 조건이 PI와 다르다(PI 기준으로 판정했음을 화면에 안내).
    terms_diverge: bool
    payment_type: str | None
    mode: str
    required_amount: int | None = None
    received_amount: int | None = None
    currency: str | None = None


def evaluate_pi_advance(
    session: Session,
    *,
    so_terms: SoTerms,
    pi: ProformaInvoice | None,
    pi_referenced: bool,
    mode: str,
) -> PiAdvanceEvaluation:
    """선수금 입금 판정 — `pi_referenced`는 SO가 PI를 참조하는지(PI 행을 못 읽었어도 참조 사실은 안다). 모르는 모드는 ValueError(호출자가 UNKNOWN으로 변환)."""
    if mode not in MODES:
        raise ValueError(f"알 수 없는 PI 게이트 모드입니다: {mode!r}")
    using_pi = pi is not None
    basis = "PI" if using_pi else "SO"
    if pi is not None:
        payment_type, bp = pi.payment_type, pi.advance_pct_bp
        diverge = pi_referenced and (
            so_terms.payment_type != pi.payment_type or so_terms.advance_pct_bp != pi.advance_pct_bp
        )
    else:
        payment_type, bp = so_terms.payment_type, so_terms.advance_pct_bp
        diverge = False

    def result(
        state: PiAdvanceState,
        reason: str,
        *,
        required: int | None = None,
        received: int | None = None,
        currency: str | None = None,
    ) -> PiAdvanceEvaluation:
        return PiAdvanceEvaluation(
            state=state,
            reason=reason,
            terms_basis=basis,
            terms_diverge=diverge,
            payment_type=payment_type,
            mode=mode,
            required_amount=required,
            received_amount=received,
            currency=currency,
        )

    if payment_type is None:
        return result(PiAdvanceState.INDETERMINATE, "PAYMENT_TYPE_UNSET")
    if payment_type != PaymentType.TT_ADVANCE.value:
        return result(PiAdvanceState.NOT_APPLICABLE, "INACTIVE")
    if mode == "OFF":
        return result(PiAdvanceState.SKIPPED_OFF, "SKIPPED_OFF")
    if not pi_referenced:
        return result(PiAdvanceState.UNMET, "PI_MISSING")
    if pi is None or pi.status not in OPEN_PI_STATES:
        return result(PiAdvanceState.UNMET, "PI_NOT_USABLE")
    if bp is None:
        return result(PiAdvanceState.INDETERMINATE, "TERMS_INCOMPLETE")

    required = split_advance(pi.total_amount, bp)[0]
    received = net_received_for_pi(session, pi.id)
    if required == 0:
        return result(
            PiAdvanceState.MET,
            "ZERO_REQUIRED",
            required=required,
            received=received,
            currency=pi.currency,
        )
    state = PiAdvanceState.MET if received >= required else PiAdvanceState.UNMET
    reason = "SUFFICIENT" if state is PiAdvanceState.MET else "SHORT"
    return result(state, reason, required=required, received=received, currency=pi.currency)
