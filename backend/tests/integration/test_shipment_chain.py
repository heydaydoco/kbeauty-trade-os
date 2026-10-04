"""A. 선적 사슬 — 역순 취소(CHILD_LINKS SO→선적·PO→선적)·SO 취소 검사 순서(R-02)·LIVE 술어 (S3-2 PR-3a / ADR-0074·0075 / design-A A6).

원시 SQL로 선적 행을 만들어 **사슬 판정만** 본다(생성 서비스·수렴은 test_shipments e2e·동시성 시험의 몫). 기대 코드는 GC-A14의 R-02 문면 그대로다:
살아 있는 선적이 있는 SO 취소 = 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE` + `detail.successors=[SH-…]`(IN_SHIPMENT여도 상태 검사보다 먼저),
IN_SHIPMENT SO의 보류 요청 = 409 `TRADE_DOCS.TRANSITION.NOT_ALLOWED`(보류는 역순 취소가 아니다 — 엣지 없음).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.exceptions import AppError
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import lifecycle
from tests.factories.approvals import make_user
from tests.factories.shipments import confirmed_so, raw_shipment, scalar, so_status, so_version
from tests.factories.trade import raw_po, unique

pytestmark = pytest.mark.group_a


def _set(table: str, row_id: int, **values: Any) -> None:
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table} SET {assignments} WHERE id = :i"), {**values, "i": row_id}
        )


def _cancel_so(so_id: int, *, to: str = "CANCELLED", reason: str = "바이어 취소") -> Any:
    actor = make_user(RoleCode.TRADE)
    return lifecycle.transition_sales_order(
        actor=actor,
        idempotency_key=unique("so-cancel"),
        so_id=so_id,
        to=to,
        version=so_version(so_id),
        reason=reason,
    )


def _doc_number(shipment_id: int) -> str:
    return str(scalar("SELECT doc_number FROM shipments WHERE id = :i", i=shipment_id))


@pytest.mark.golden
def test_gc_a14_in_shipment_so_cancel_says_successor_alive_before_the_state_check() -> None:
    """GC-A14 / R-02 — 살아 있는 선적이 있는 IN_SHIPMENT SO 취소 = 409 SUCCESSOR_ALIVE + detail.successors=[SH-…](NOT_ALLOWED 아님)·상태·이력 무변"""
    so = confirmed_so((10,))
    shipment = raw_shipment(so["id"])
    _set("sales_orders", so["id"], status="IN_SHIPMENT")
    logs_before = scalar(
        "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"]
    )
    with pytest.raises(AppError) as caught:
        _cancel_so(so["id"])
    assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert caught.value.status_code == 409
    assert caught.value.detail == {"successors": [_doc_number(shipment)]}
    assert so_status(so["id"]) == "IN_SHIPMENT"
    assert (
        scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == logs_before
    )


def test_in_shipment_so_hold_request_is_not_allowed() -> None:
    """IN_SHIPMENT SO 보류 요청은 엣지가 없어 409 NOT_ALLOWED(보류는 역순 취소가 아니다 — R-02 문면, 화면은 보류 버튼 비노출)"""
    so = confirmed_so((10,))
    raw_shipment(so["id"])
    _set("sales_orders", so["id"], status="IN_SHIPMENT")
    with pytest.raises(AppError) as caught:
        _cancel_so(so["id"], to="ON_HOLD", reason="보류")
    assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert so_status(so["id"]) == "IN_SHIPMENT"


@pytest.mark.parametrize("shipment_status", ["PLANNED", "RELEASE_ORDERED", "CLOSED"])
def test_any_live_shipment_blocks_so_cancel_even_closed(shipment_status: str) -> None:
    """LIVE = 미삭제 + CANCELLED·EXPIRED 아님 — 계획·출고지시·종결(예약 값) 선적 모두 SO 취소를 막는다(이행된 사슬의 선행 취소 차단)"""
    so = confirmed_so((10,))
    raw_shipment(so["id"], status=shipment_status)
    with pytest.raises(AppError) as caught:
        _cancel_so(so["id"])
    assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"


def test_cancelled_or_deleted_shipments_do_not_block_so_cancel() -> None:
    """취소된 선적·soft delete 선적은 후속으로 세지 않는다 — SO 취소가 통과한다(역순 취소의 끝)"""
    so = confirmed_so((10,))
    cancelled = raw_shipment(so["id"], status="CANCELLED")
    deleted = raw_shipment(so["id"])
    _set("shipments", deleted, deleted_at="2026-10-01T00:00:00+00:00")
    assert cancelled and deleted
    _cancel_so(so["id"])
    assert so_status(so["id"]) == "CANCELLED"


def test_po_with_a_live_import_shipment_cannot_be_cancelled() -> None:
    """CHILD_LINKS PO→선적 — 살아 있는 수입선적이 있는 PO 취소 = 409 SUCCESSOR_ALIVE(수입선적 생성 경로는 PR-5a — 사슬 등록은 FK가 생긴 이 PR)"""
    po = raw_po("ISSUED")
    so = confirmed_so((10,))  # 헤더 사본 값의 원천(통화·조건)으로만 쓴다
    shipment = raw_shipment(so["id"], kind="IMPORT", po_id=po)
    actor = make_user(RoleCode.TRADE)
    with pytest.raises(AppError) as caught:
        lifecycle.transition_purchase_order(
            actor=actor,
            idempotency_key=unique("po-cancel"),
            po_id=po,
            to="CANCELLED",
            version=int(scalar("SELECT version FROM purchase_orders WHERE id = :i", i=po)),
            reason="공급 취소",
        )
    assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert caught.value.detail == {"successors": [_doc_number(shipment)]}
