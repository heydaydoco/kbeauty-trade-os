"""A·K. `SO_CREDIT_EXCEEDED` TargetSpec·digest 골든 벡터·peek·레지스트리 (S3-1 PR-9a / ADR-0060·0064 / design-C C6, design-E E5, X-07).

digest는 "승인 후 불변"의 결속 토큰이다 — 직렬화 형식이 바뀌면 미소비 승인이 전부 무효가 되므로 **골든 벡터**로 고정한다. 무엇이 입력이고 무엇이 아닌지(라인 내용 O·메모·담당자·version
X)를 데이터로 단정한다. peek는 읽기 전용(어떤 쓰기·VOID도 없음)임을 상태·이력 불변으로 확인한다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.approvals import registry, service
from app.modules.approvals.machine import ApprovalType, TargetType
from app.modules.credit import spec
from app.modules.identity.models import RoleCode
from app.modules.sales_orders.digest import canonical_gate_input, digest_of, gate_input_digest
from tests.factories.approvals import (
    TYPE,
    add_line,
    approval_row,
    approved_for,
    audit_actions,
    count,
    credit_so,
    decide,
    events_of,
    line_set,
    make_user,
    request,
    set_credit_limit,
    so_set,
)

pytestmark = pytest.mark.group_a


# ── digest ───────────────────────────────────────────────────────────────────


def test_digest_golden_vectors_pin_the_serialisation() -> None:
    """골든 벡터 — canonical JSON(키 정렬·공백 없음·정수는 문자열·NFC)과 sha256이 고정값이다. 형식을 바꾸면 이 테스트가 실패한다(ADR 필요)"""
    canonical = canonical_gate_input(
        buyer_partner_id=7,
        currency="USD",
        fx_rate=Decimal("1350.50000000"),
        fx_rate_date=date(2026, 9, 1),
        lines=[(3, 10, 40000, False), (4, 2, 0, True)],
    )
    assert canonical == (
        '{"buyer_partner_id":"7","currency":"USD","fx_rate":"1350.50000000","fx_rate_date":"2026-09-01",'
        '"lines":[["3","10","40000","0"],["4","2","0","1"]]}'
    )
    assert (
        digest_of(canonical) == "57ed43497307efd822aefb748f482d424f55db4b51c6dcf86e19131c646e5f70"
    )
    empty = canonical_gate_input(
        buyer_partner_id=1, currency="한글", fx_rate=None, fx_rate_date=None, lines=[]
    )
    assert (
        empty
        == '{"buyer_partner_id":"1","currency":"한글","fx_rate":null,"fx_rate_date":null,"lines":[]}'
    )
    assert digest_of(empty) == "9a0ddcedf3fc8cd0c92f4c85a3d3f2d0dc9e1c7d68b067b1a8afb476dc118b25"


def test_the_canonical_form_is_independent_of_decimal_spelling_and_normalises_unicode() -> None:
    """환율 표기(NUMERIC(18,8) 고정 자릿수)와 NFC 정규화 — 같은 값은 같은 입력이다(조합형·분해형 한글 동치)"""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "한글")
    a = canonical_gate_input(
        buyer_partner_id=1, currency=decomposed, fx_rate=None, fx_rate_date=None, lines=[]
    )
    b = canonical_gate_input(
        buyer_partner_id=1, currency="한글", fx_rate=None, fx_rate_date=None, lines=[]
    )
    assert a == b


def _digest(so_id: int) -> str:
    with unit_of_work() as uow:
        return gate_input_digest(uow.session, so_id)


def test_the_digest_changes_with_line_content_but_not_with_unrelated_edits() -> None:
    """입력 변경(수량·단가·무상 표지·환율·환율 기준일)은 digest를 바꾸고, 메모·담당자·version·PO번호·납기는 바꾸지 않는다"""
    so = credit_so()
    base = _digest(so["id"])
    assert len(base) == 64 and _digest(so["id"]) == base  # 결정적

    # 무관한 변경 — digest 불변
    so_set(so["id"], internal_note="메모", version=9, buyer_po_no="PO-X", buyer_po_no_key="PO-X")
    assert _digest(so["id"]) == base

    changes = [
        ("수량", lambda: line_set(so["id"], quantity=6, line_amount=240_000)),
        ("단가", lambda: line_set(so["id"], unit_price_amount=39_999, line_amount=239_994)),
        (
            "무상",
            lambda: line_set(
                so["id"], is_free=True, unit_price_amount=0, line_amount=0, price_reason="샘플 무상"
            ),
        ),
        ("환율", lambda: so_set(so["id"], fx_rate=1400, fx_rate_date=date(2026, 9, 1))),
        ("환율기준일", lambda: so_set(so["id"], fx_rate_date=date(2026, 9, 2))),
    ]
    seen = {base}
    for label, change in changes:
        change()
        current = _digest(so["id"])
        assert current not in seen, f"{label} 변경이 digest를 바꾸지 않았다"
        seen.add(current)


def test_a_deleted_line_is_not_an_input() -> None:
    """soft delete된 라인은 입력이 아니다 — 라인을 제외하면 digest가 바뀐다(조용한 변경이 아니다)"""
    so = credit_so()
    base = _digest(so["id"])
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_order_lines SET deleted_at = now() WHERE so_id = :i"),
            {"i": so["id"]},
        )
    assert _digest(so["id"]) != base


# ── spec.snapshot ────────────────────────────────────────────────────────────


def _snapshot(so_id: int, *, lock: bool = False) -> registry.TargetSnapshot | None:
    with unit_of_work() as uow:
        return spec.snapshot(uow.session, so_id, lock=lock)


@pytest.mark.parametrize("lock", [False, True])
def test_snapshot_reports_the_excess_in_the_limit_currency(lock: bool) -> None:
    """초과 SO — amount=초과분(한도 통화)·digest=판정 입력·gate_open=True·detail=평가 스냅샷(표시용)"""
    so = credit_so()
    snap = _snapshot(so["id"], lock=lock)
    assert snap is not None and snap.gate_open is True
    assert snap.amount == 100_000 and snap.currency == "USD"
    assert snap.label == so["doc_number"] and snap.digest == _digest(so["id"])
    assert snap.detail["verdict"] == "EXCEEDED" and snap.detail["excess_amount"] == 100_000


def test_snapshot_is_zero_when_within_limit_or_unmanaged() -> None:
    """한도 이내·여신 관리 안 함은 amount=0(승인 불필요)"""
    within = credit_so(limit=1_000_000)
    snap = _snapshot(within["id"])
    assert snap is not None and snap.amount == 0 and snap.gate_open is True
    set_credit_limit(within["buyer_partner_id"], None, None)
    unmanaged = _snapshot(within["id"])
    assert unmanaged is not None and unmanaged.amount == 0


def test_snapshot_is_closed_for_orders_past_the_gate_and_none_for_missing_ones() -> None:
    """RECEIVED가 아닌 SO(확정·취소·보류)는 gate_open=False(평가하지 않는다), 없는 SO·삭제된 SO는 None"""
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    so = credit_so()
    for status in ("CANCELLED", "ON_HOLD"):
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE sales_orders SET status = :s WHERE id = :i"),
                {"s": status, "i": so["id"]},
            )
        snap = _snapshot(so["id"])
        assert snap is not None and snap.gate_open is False and snap.amount == 0, status
    assert _snapshot(987654) is None
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET deleted_at = now() WHERE id = :i"), {"i": so["id"]}
        )
    assert _snapshot(so["id"]) is None


def test_an_unevaluable_snapshot_raises_instead_of_passing() -> None:
    """평가 불능(통화 비교 불가)은 spec이 AppError를 던진다 — 통과 취급이 없다(승인 상한을 정할 수 없다)"""
    so = credit_so()
    set_credit_limit(so["buyer_partner_id"], 100_000, "EUR")
    for lock in (False, True):
        with pytest.raises(AppError) as caught:
            _snapshot(so["id"], lock=lock)
        assert caught.value.code == ErrorCode.VALIDATION_INVALID_FIELD
        assert "credit" in caught.value.detail


# ── peek — 읽기 전용 ─────────────────────────────────────────────────────────


def _peek(so_id: int) -> service.ApprovalRef | None:
    with unit_of_work() as uow:
        return service.peek_active_approved(uow.session, approval_type=TYPE, target_id=so_id)


def _state(approval_id: int) -> tuple[str, int, int]:
    row = approval_row(approval_id)
    return row["status"], row["version"], len(events_of(approval_id))


def test_peek_returns_only_a_currently_consumable_approval_and_never_writes() -> None:
    """peek: REQUESTED는 None, APPROVED+유효는 참조, 승인 후 대상이 바뀌어 stale이면 None — 어떤 경우에도 상태·이력·버전이 그대로다(VOID하지 않는다)"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    requester, approver = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    before = _state(approval.id)
    assert _peek(so["id"]) is None and _state(approval.id) == before  # 요청만 있다

    decide(approval.id, approver, "APPROVE")
    approved_state = _state(approval.id)
    ref = _peek(so["id"])
    assert ref is not None and ref.id == approval.id and ref.basis_amount == 100_000
    assert _state(approval.id) == approved_state

    line_set(so["id"], quantity=6, line_amount=240_000)  # 승인 후 변경 → stale
    assert _peek(so["id"]) is None
    assert (
        _state(approval.id) == approved_state
    )  # peek는 VOID하지 않는다 — 권위 있는 판정·VOID는 consume이 한다


def test_peek_is_none_without_any_approval() -> None:
    """활성 승인이 없으면 None"""
    so = credit_so()
    assert _peek(so["id"]) is None


# ── 우회 시도 audit ──────────────────────────────────────────────────────────


def test_note_bypass_attempt_records_who_tried_what_with_the_pending_flag() -> None:
    """우회 시도 audit — 행위자·유형·대상·'요청 대기 중 여부'가 남는다(막힌 시도 기록, ADMIN 포함)"""
    so = credit_so()
    admin = make_user(RoleCode.ADMIN)
    with unit_of_work() as uow:
        service.note_bypass_attempt(
            uow.session, actor_user_id=admin.id, approval_type=TYPE, target_id=so["id"]
        )
    [row] = audit_actions("approvals.approval.bypass_blocked")
    assert row["actor_user_id"] == admin.id
    assert row["detail"] == {
        "approval_type": TYPE,
        "target_type": "SALES_ORDER",
        "target_id": so["id"],
        "pending_request": False,
        "stale_approved": False,
    }


# ── 레지스트리 ───────────────────────────────────────────────────────────────


def test_the_spec_is_registered_for_the_only_approval_type_and_only_once() -> None:
    """S3-1의 유일한 승인 유형에 spec이 등록돼 있고(공회전 방지 len≥1), 같은 유형의 2차 등록·알 수 없는 유형·유형-대상 불일치는 거부된다"""
    specs = registry.registered_specs()
    assert set(specs) == {ApprovalType.SO_CREDIT_EXCEEDED.value}
    only = specs[TYPE]
    assert (
        only.target_type == TargetType.SALES_ORDER.value and only.consumer_module == "trade_chain"
    )
    with pytest.raises(ValueError, match="이미 등록"):
        registry.register_target_spec(only)
    with pytest.raises(ValueError, match="알 수 없는"):
        registry.register_target_spec(
            registry.TargetSpec("EXPENSE_OVER_THRESHOLD", "SALES_ORDER", "x", only.snapshot)
        )
    with pytest.raises(ValueError, match="대상 종류"):
        registry.register_target_spec(
            registry.TargetSpec(TYPE, "PURCHASE_ORDER", "x", only.snapshot)
        )


def test_without_a_spec_every_operation_fails_closed() -> None:
    """spec이 등록되지 않은 유형은 요청·소비가 모두 실패한다(RuntimeError) — 대상을 모르는 채 승인을 내주지 않는다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    removed = registry.unregister_for_tests(TYPE)
    try:
        with pytest.raises(RuntimeError):
            request(so["id"], make_user(RoleCode.TRADE))
        with pytest.raises(RuntimeError), unit_of_work() as uow:
            service.consume_approval(
                uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=1
            )
        assert count("approvals") == 0
    finally:
        assert removed is not None
        registry.register_target_spec(removed)


def test_a_consumed_approval_binds_only_the_snapshot_it_was_given() -> None:
    """승인은 요청 시점 스냅샷에 결속된다 — 같은 SO가 소비된 뒤 다시 승인이 필요해지면 새 요청이 필요하다(재사용 없음)"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    with unit_of_work() as uow:
        result = service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
        )
    assert result.approval_id == approval_id
    assert approval_row(approval_id)["status"] == "CONSUMED"
    # 소비된 SO는 확정 상태가 아니라 접수 상태로 남아 있어도(이 시험에선 확정 통로가 없다) 같은 승인은 두 번 쓰이지 않는다
    with unit_of_work() as uow:
        again = service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
        )
    assert again.error is not None and again.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED
