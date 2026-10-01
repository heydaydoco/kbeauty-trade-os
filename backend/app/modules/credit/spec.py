"""`SO_CREDIT_EXCEEDED` TargetSpec — 여신 초과 수주 승인의 대상 계약 (S3-1 ADR-0060·0064 / design-C C6, design-E E5).

승인 코어는 SO를 모른다 — 이 모듈이 "SO 승인의 대상은 이런 모양이다"를 등록한다. `snapshot()`이 알려 주는 것:
  · `digest` = `sales_orders.digest.gate_input_digest`(판정 입력 내용 — version 아님) · `amount` = **여신 통화 기준 초과분**(승인이 허용하는 상한의 원천)
  · `currency` = 한도 통화 · `gate_open` = SO가 아직 확정 전·미취소·미삭제(RECEIVED)인가 · `detail` = `CreditEvaluation.to_snapshot()`(표시용 스칼라).
■ `lock=True`는 **거래처 → SO 순서**(전역 LOCK_ORDER — 확정 통로와 같은 순서라 교차 교착이 없다)로 잠근다: 무잠금으로 SO의 거래처를 읽고(ORIGIN 불변이라 안전),
  `lock_buyer_for_credit`(NO KEY UPDATE) → SO `FOR UPDATE` → 잠금 뒤 재조회.
■ **평가 불능(UNEVALUABLE)이면 spec이 자기 `AppError`를 던진다**(통과 취급 금지 — 금액을 모르면 승인 상한을 정할 수 없다): 승인으로 우회할 수 없다.
■ 한도 없음(NOT_MANAGED)·한도 이내는 `amount=0`(승인 불필요). 확정 이후·취소된 SO는 평가하지 않고 `gate_open=False`다.
■ ★ **소비 접점 스캔 부재 기록**: `consumer_module="trade_chain"`(SO 확정 서비스)이 `consume_approval(`을 호출해야 이 spec이 의미를 갖는다 — 그 호출·요청 엔드포인트·
  `void_for_target` 훅은 PR-12(확정 배선)에 함께 둔다(한 PR 안에서 닫히도록). PR-9a~PR-11 동안 spec은 있으나 소비자가 없다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.approvals.machine import ApprovalType, TargetType
from app.modules.approvals.registry import (
    TargetSnapshot,
    TargetSpec,
    register_target_spec,
    registered_specs,
)
from app.modules.credit.evaluation import (
    CreditVerdict,
    ReasonCode,
    evaluate_credit,
    evaluate_credit_unlocked,
)
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.sales_orders.digest import gate_input_digest
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_docs.locking import lock_document

#: 게이트 대상 상태 — 확정 전(접수)뿐이다. 확정·보류·취소·완료는 더는 승인 대상이 아니다.
GATE_OPEN_STATUS = "RECEIVED"


def _unevaluable(
    order: SalesOrder, reasons: tuple[str, ...], limit_currency: str | None
) -> AppError:
    if ReasonCode.RECEIVABLE_PROVIDER_ERROR.value in reasons:
        message = "미수채권을 조회하지 못해 여신을 평가할 수 없습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요."
    else:
        message = (
            f"한도 통화({limit_currency})와 전표 통화({order.currency})를 비교할 수 없어 여신을 평가할 수 없습니다. "
            "관리자가 거래처 여신 통화를 KRW로 맞춘 뒤 다시 시도해 주세요."
        )
    return AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={"credit": message},
        log_context={"sales_order_id": order.id, "reasons": list(reasons)},
    )


def snapshot(session: Session, so_id: int, *, lock: bool) -> TargetSnapshot | None:
    """SO의 여신 초과 승인 대상 스냅샷 — 없거나 삭제됐으면 None. 평가 불능은 AppError."""
    header = session.execute(
        select(SalesOrder.buyer_partner_id).where(
            SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if header is None:
        return None
    if lock:
        locked = lock_buyer_for_credit(session, header)  # (2) 거래처 — 직렬화 잠금
        try:
            order = lock_document(session, SalesOrder, so_id)  # (5) SO — 잠근 뒤 재조회
        except NotFoundError:
            return None
        if (
            order.buyer_partner_id != locked.partner.id
        ):  # ORIGIN 불변이라 정상 경로로는 불가능한 방어
            raise AppError(ErrorCode.CONCURRENCY_VERSION_CONFLICT)
    else:
        found = session.execute(
            select(SalesOrder)
            .where(SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if found is None:
            return None
        order = found
    digest = gate_input_digest(session, order.id)
    if order.status != GATE_OPEN_STATUS:
        return TargetSnapshot(
            label=order.doc_number,
            digest=digest,
            amount=0,
            currency=order.currency,
            detail={},
            gate_open=False,
        )
    evaluation = (
        evaluate_credit(session, locked, order)
        if lock
        else evaluate_credit_unlocked(session, order)
    )
    if evaluation.verdict is CreditVerdict.UNEVALUABLE:
        raise _unevaluable(order, evaluation.reason_codes, evaluation.limit_currency)
    exceeded = evaluation.verdict is CreditVerdict.EXCEEDED
    return TargetSnapshot(
        label=order.doc_number,
        digest=digest,
        amount=evaluation.excess_amount if exceeded else 0,
        currency=(
            evaluation.limit_currency if exceeded and evaluation.limit_currency else order.currency
        ),
        detail=evaluation.to_snapshot(),
        gate_open=True,
    )


def register_specs() -> None:
    """`SO_CREDIT_EXCEEDED` spec 등록 — 이미 등록돼 있으면 건너뛴다(앱 조립 시점 1회, 테스트 복원용 재호출 허용)."""
    if ApprovalType.SO_CREDIT_EXCEEDED.value in registered_specs():
        return
    register_target_spec(
        TargetSpec(
            approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value,
            target_type=TargetType.SALES_ORDER.value,
            consumer_module="trade_chain",
            snapshot=snapshot,
        )
    )


register_specs()
