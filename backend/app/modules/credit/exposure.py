"""미결 SO 노출 — 술어·잔액식의 **단일 정의** (S3-1 ADR-0064 / design-E E1 1·2).

■ 미결 SO 술어(`open_orders_stmt` 한 곳): `buyer_partner_id = :p AND confirmed_at IS NOT NULL AND deleted_at IS NULL AND status NOT IN
  ('COMPLETED','CANCELLED') AND id <> :self`. 뜻: "한 번이라도 게이트를 통과해 확정된 적이 있고 종결(완료·취소)되지 않은 SO".
  포함 = CONFIRMED·PARTIALLY_ALLOCATED·ALLOCATED·IN_SHIPMENT + **확정 이력이 있는 ON_HOLD**. 제외 = RECEIVED(접수·게이트 전), 접수 단계 ON_HOLD
  (`confirmed_at IS NULL`), COMPLETED(선적 완료 — 채권 영역, S3-3 provider), CANCELLED, soft delete, 자기 자신.
  **음수 집합(제외 열거) 형태**라 후속 세션이 새 상태를 추가해도 조용히 0으로 빠지지 않고 기본 산입된다.
■ `open_order_amount`가 잔액식의 **유일한 정의**다 — S3-1은 `total_amount` 그대로(선적 0). S3-2는 선적분 차감을 여기에 넣되, 선적분 차감은
  S3-3 채권 provider가 `reflected=True`로 등록되는 릴리스와 **같은 PR에서만**(그 사이 노출 공백 방지 — E11 인계 계약).
"""

from __future__ import annotations

from sqlalchemy import Select, select

from app.modules.sales_orders.models import SalesOrder

#: 종결 상태 — 노출에서 빠진다(완료는 채권 영역, 취소는 약정 소멸).
CLOSED_STATUSES: tuple[str, ...] = ("COMPLETED", "CANCELLED")


def open_orders_stmt(
    partner_id: int, *, exclude_sales_order_id: int | None = None
) -> Select[tuple[SalesOrder]]:
    """한 거래처의 미결 SO 선택문 — 노출 산정의 **유일한 술어**."""
    stmt = select(SalesOrder).where(
        SalesOrder.buyer_partner_id == partner_id,
        SalesOrder.confirmed_at.is_not(None),
        SalesOrder.deleted_at.is_(None),
        SalesOrder.status.not_in(CLOSED_STATUSES),
    )
    if exclude_sales_order_id is not None:
        stmt = stmt.where(SalesOrder.id != exclude_sales_order_id)
    return stmt


def open_order_amount(order: SalesOrder) -> int:
    """미결 SO 1건의 노출 금액(전표 통화 최소단위) — S3-1은 선적 0이라 총액 그대로."""
    return int(order.total_amount)
