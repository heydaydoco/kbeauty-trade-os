"""여신 평가 — 노출 산식·통화·NULL/0·경계 (S3-1 ADR-0064 / design-E E1·E2).

`exposure_after = 미결 SO 합 + 이번 SO 금액 + 미수채권(provider)` — 각 항은 `comparable_amount()`로 **한도 통화**에 맞춘다. 전부 BIGINT 최소단위.

■ **한도 NULL = 여신 관리 안 함 → `NOT_MANAGED`**(통과 — 환산·노출 계산 자체를 건너뛴다: 관리 안 하는 거래처가 통화 불일치로 막히지 않게).
  **한도 0 = 신용 거래 불가**(이번 SO 금액 > 0이면 항상 초과). 초과 판정 = **`exposure_after > limit`(strict)** — 같으면 통과.
  **이번 SO 금액이 0(전 라인 무상)이면 `WITHIN_LIMIT`(`NO_INCREMENT`)** — 노출을 늘리지 않는 전표가 기존 초과 상태 때문에 막히지 않게.
■ **통화(A7 `comparable_amount` 채택)**: 같은 통화=원액, 한도 통화가 KRW이고 전표에 환율이 있으면 `to_krw`(HALF_UP 단일), 그 외는 `None`.
  **하나라도 `None`이면 `UNEVALUABLE`(`CURRENCY_NOT_CONVERTIBLE`)** — 통과가 아니다. 각 미결 SO는 **자기 확정 시점 환율**로 환산한다(확정 후 환율 불변).
  UNEVALUABLE은 결과 값으로는 존재(advisory·게이트 화면이 사유를 보여 줘야 하므로)하지만 확정·승인 요청 경로에서는 승인 없이 확정을 거부한다 —
  금액을 모르면 승인 상한(`basis_amount>0`)을 정할 수 없다(승인으로 우회 불가).
■ **미수 항**은 provider(`providers.py`) — 기본 구현은 `(reflected=False, amount=None)`이고 **0으로 합산하지 않는다**(`exposure_is_partial=true`).
  선수금 입금분은 노출에서 **차감하지 않는다**(차감하려면 입금↔SO 배분이 필요한 S3-3 몫이고, 미차감은 노출 과대 방향이라 fail-safe).
■ **원가·마진·PURCHASE 계열 필드는 존재하지 않는다**(E2 — 노출은 판매금액 합이라 마스킹 비대상. 스키마·스냅샷 재귀 키 스캔이 고정한다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors.exceptions import NotFoundError
from app.core.time import utcnow
from app.modules.credit import providers
from app.modules.credit.exposure import open_order_amount, open_orders_stmt
from app.modules.credit.locking import LockedBuyer
from app.modules.partners.models import Partner
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_docs.fx import comparable_amount


class CreditVerdict(StrEnum):
    NOT_MANAGED = "NOT_MANAGED"  # 한도 없음 = 여신 관리 안 함(통과)
    WITHIN_LIMIT = "WITHIN_LIMIT"
    EXCEEDED = "EXCEEDED"
    UNEVALUABLE = "UNEVALUABLE"  # 통화 비교 불가·provider 오류(통과 아님)


class ReasonCode(StrEnum):
    CURRENCY_NOT_CONVERTIBLE = "CURRENCY_NOT_CONVERTIBLE"
    RECEIVABLE_PROVIDER_ERROR = "RECEIVABLE_PROVIDER_ERROR"
    NO_INCREMENT = "NO_INCREMENT"


#: provider 실패로 취급하는 예외(평가 불능) — 그 밖의 예외는 버그라 전파한다.
PROVIDER_FAILURES: tuple[type[Exception], ...] = (
    SQLAlchemyError,
    ValueError,
    RuntimeError,
    LookupError,
    ArithmeticError,
    OSError,
)

#: 미환산 SO를 응답에 실을 최대 건수(문서번호·통화만).
UNCONVERTED_MAX = 5


def _after_evaluate_hook() -> None:
    """**테스트 이음새** — 평가 직후 호출된다. 프로덕션 기본은 no-op이고 다른 모듈이 대입하지 않는다(동시성 테스트가 패치해 경합을 결정적으로 만든다)."""


@dataclass(frozen=True, slots=True)
class CreditEvaluation:
    partner_id: int
    sales_order_id: int
    verdict: CreditVerdict
    limit_amount: int | None
    limit_currency: str | None
    open_orders_amount: int | None
    this_order_amount: int | None
    #: 미수 항 — 반영 여부(기본 구현은 False)·금액(미반영이면 None, 0이 아니다).
    receivables_reflected: bool
    receivable_amount: int | None
    exposure_after_amount: int | None
    excess_amount: int
    exposure_is_partial: bool
    reason_codes: tuple[str, ...] = ()
    unconverted: tuple[dict[str, str], ...] = ()
    #: 환산에 쓴 증빙 — 전표 환율·기준일(수동 입력 신뢰 한계를 증적에 남긴다).
    fx_doc_currency: str | None = None
    fx_rate: str | None = None
    fx_rate_date: str | None = None
    advisory: bool = False
    evaluated_at: datetime = field(default_factory=utcnow)

    def to_snapshot(self) -> dict[str, Any]:
        """승인 스냅샷 표시용 상세 — 스칼라만·키 ≤ 20(approvals가 검증). 원가·마진 키는 없다."""
        return {
            "partner_id": self.partner_id,
            "sales_order_id": self.sales_order_id,
            "verdict": self.verdict.value,
            "limit_amount": self.limit_amount,
            "limit_currency": self.limit_currency,
            "open_orders_amount": self.open_orders_amount,
            "this_order_amount": self.this_order_amount,
            "receivables_reflected": self.receivables_reflected,
            "exposure_after_amount": self.exposure_after_amount,
            "excess_amount": self.excess_amount,
            "exposure_is_partial": self.exposure_is_partial,
            "reason_codes": ",".join(self.reason_codes),
            "fx_doc_currency": self.fx_doc_currency,
            "fx_rate": self.fx_rate,
            "fx_rate_date": self.fx_rate_date,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


def _base(
    partner: Partner, order: SalesOrder, *, advisory: bool, **overrides: Any
) -> CreditEvaluation:
    values: dict[str, Any] = {
        "partner_id": partner.id,
        "sales_order_id": order.id,
        "limit_amount": partner.credit_limit_amount,
        "limit_currency": partner.credit_limit_currency,
        "open_orders_amount": None,
        "this_order_amount": None,
        "receivables_reflected": False,
        "receivable_amount": None,
        "exposure_after_amount": None,
        "excess_amount": 0,
        "exposure_is_partial": True,
        "advisory": advisory,
        "fx_doc_currency": order.currency,
        "fx_rate": str(order.fx_rate) if order.fx_rate is not None else None,
        "fx_rate_date": order.fx_rate_date.isoformat() if order.fx_rate_date else None,
    }
    values.update(overrides)
    return CreditEvaluation(**values)


def _evaluate(
    session: Session, partner: Partner, order: SalesOrder, *, advisory: bool
) -> CreditEvaluation:
    limit = partner.credit_limit_amount
    currency = partner.credit_limit_currency

    # 한도 NULL — 여신 관리 안 함: 환산·노출 계산을 건너뛴다(통화 불일치로 막히지 않게).
    if limit is None or currency is None:
        return _base(partner, order, advisory=advisory, verdict=CreditVerdict.NOT_MANAGED)

    # 이번 SO가 노출을 늘리지 않는다(전 라인 무상) — 기존 초과 상태 때문에 막히지 않는다.
    if order.total_amount == 0:
        return _base(
            partner,
            order,
            advisory=advisory,
            verdict=CreditVerdict.WITHIN_LIMIT,
            this_order_amount=0,
            reason_codes=(ReasonCode.NO_INCREMENT.value,),
        )

    reasons: list[str] = []
    this_amount = comparable_amount(order.total_amount, order.currency, order.fx_rate, currency)

    open_total = 0
    unconverted: list[dict[str, str]] = []
    unconverted_count = 0
    for other in session.execute(
        open_orders_stmt(partner.id, exclude_sales_order_id=order.id).order_by(SalesOrder.id)
    ).scalars():
        converted = comparable_amount(
            open_order_amount(other), other.currency, other.fx_rate, currency
        )
        if converted is None:
            unconverted_count += 1
            if len(unconverted) < UNCONVERTED_MAX:
                unconverted.append({"doc_number": other.doc_number, "currency": other.currency})
        else:
            open_total += converted

    receivable_amount: int | None = None
    reflected = False
    provider_failed = False
    try:
        # SAVEPOINT 안에서 — provider의 DB 오류가 호출 트랜잭션을 aborted 상태로 만들지 않는다(이후 쿼리·감사 기록이 계속 가능).
        # 예외는 좁게 잡는다: DB·값·조회 오류만 평가 불능, 프로그래밍 오류(TypeError 등)는 그대로 올려 500으로 드러낸다.
        with session.begin_nested():
            term = providers.get_receivable_provider().outstanding(session, partner.id, currency)
        reflected, receivable_amount = term.reflected, term.amount
    except PROVIDER_FAILURES:
        provider_failed = True

    if this_amount is None:
        unconverted.append({"doc_number": order.doc_number, "currency": order.currency})
        unconverted_count += 1
    if unconverted_count:
        reasons.append(ReasonCode.CURRENCY_NOT_CONVERTIBLE.value)
    if provider_failed:
        reasons.append(ReasonCode.RECEIVABLE_PROVIDER_ERROR.value)
    if reasons:  # 통과가 아니다 — 승인 경로도 없다(금액을 모르면 승인 상한을 정할 수 없다)
        return _base(
            partner,
            order,
            advisory=advisory,
            verdict=CreditVerdict.UNEVALUABLE,
            reason_codes=tuple(reasons),
            unconverted=tuple(unconverted[:UNCONVERTED_MAX]),
        )

    assert this_amount is not None
    # 미수 항은 반영됐을 때만 더한다 — 미반영(None)을 0으로 더하지 않는다.
    exposure = (
        open_total + this_amount + (receivable_amount if reflected and receivable_amount else 0)
    )
    exceeded = exposure > limit
    return _base(
        partner,
        order,
        advisory=advisory,
        verdict=CreditVerdict.EXCEEDED if exceeded else CreditVerdict.WITHIN_LIMIT,
        open_orders_amount=open_total,
        this_order_amount=this_amount,
        receivables_reflected=reflected,
        receivable_amount=receivable_amount if reflected else None,
        exposure_after_amount=exposure,
        excess_amount=exposure - limit if exceeded else 0,
        exposure_is_partial=not reflected,
    )


def evaluate_credit(session: Session, locked: LockedBuyer, order: SalesOrder) -> CreditEvaluation:
    """**권위 있는 평가** — 거래처 잠금(`LockedBuyer`)을 쥔 호출자만 부를 수 있다. 확정·승인 요청/결정/소비 경로가 쓴다."""
    if order.buyer_partner_id != locked.partner.id:
        raise ValueError("잠근 거래처와 SO의 거래처가 다릅니다(잠금 순서 위반).")
    result = _evaluate(session, locked.partner, order, advisory=False)
    _after_evaluate_hook()
    return result


def evaluate_credit_unlocked(session: Session, order: SalesOrder) -> CreditEvaluation:
    """**참고용 평가**(`advisory=True`) — 잠금 없이 계산한다. 화면 표시·peek 전용이며 확정 판정에 쓰지 않는다(확정은 잠금 하 재평가)."""
    partner = session.execute(
        select(Partner).where(Partner.id == order.buyer_partner_id)
    ).scalar_one_or_none()
    if partner is None:
        raise NotFoundError(log_context={"partner_id": order.buyer_partner_id})
    return _evaluate(session, partner, order, advisory=True)
