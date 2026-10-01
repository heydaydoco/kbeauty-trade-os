"""H. 결재선 매핑 — strict 초과 경계·다중 구간·같은 통화·fail-closed·요청 시점 동결 (S3-1 PR-9a / ADR-0061 / design-C C3).

경계·방향 같은 한 줄 규칙(`<` vs `<=`, ORDER BY 방향, 통화 조건)이 결재 역할을 조용히 바꾼다 — 그래서 경계 3점·다중 구간·다통화를 전부 단정한다.
"""

from __future__ import annotations

import pytest

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.approvals import lines
from app.modules.identity.models import RoleCode
from tests.factories.approvals import (
    TYPE,
    add_line,
    approval_row,
    count,
    credit_so,
    make_user,
    request,
)

pytestmark = pytest.mark.group_h


def _resolve(amount: int, currency: str = "USD") -> tuple[int, str] | None:
    with unit_of_work() as uow:
        row = lines.resolve_line(uow.session, approval_type=TYPE, amount=amount, currency=currency)
        return None if row is None else (row.threshold_amount, row.approver_role)


def test_the_boundary_is_strictly_greater_than_the_threshold() -> None:
    """임계 100,000: 금액 100,000은 해당 없음(초과가 아니다), 100,001은 해당 — strict >"""
    add_line(100_000, role="TRADE")
    assert _resolve(99_999) is None
    assert _resolve(100_000) is None
    assert _resolve(100_001) == (100_000, "TRADE")


def test_threshold_zero_covers_every_positive_amount() -> None:
    """임계 0 = 양(+)의 금액 전부 — 1도 해당된다(0은 해당 없음)"""
    add_line(0, role="TRADE")
    assert _resolve(0) is None
    assert _resolve(1) == (0, "TRADE")


def test_tiered_lines_pick_the_highest_threshold_that_is_exceeded() -> None:
    """다중 구간(0→TRADE, 500,000→ADMIN): 500,000은 TRADE, 500,001부터 ADMIN — 가장 큰 초과 임계가 이긴다(정렬 방향 고정)"""
    add_line(0, role="TRADE")
    add_line(500_000, role="ADMIN")
    add_line(2_000_000, role="CERT")
    assert _resolve(1) == (0, "TRADE")
    assert _resolve(500_000) == (0, "TRADE")
    assert _resolve(500_001) == (500_000, "ADMIN")
    assert _resolve(2_000_000) == (500_000, "ADMIN")
    assert _resolve(2_000_001) == (2_000_000, "CERT")


def test_only_the_same_currency_counts_with_no_conversion() -> None:
    """통화가 다른 결재선은 무시한다(환산 없음) — USD 요청에 KRW 행만 있으면 해당 없음, 통화별로 독립된 구간"""
    add_line(0, currency="KRW", role="ADMIN")
    assert _resolve(10_000_000, "USD") is None
    add_line(0, currency="USD", role="TRADE")
    assert _resolve(1, "USD") == (0, "TRADE")
    assert _resolve(1, "KRW") == (0, "ADMIN")
    assert _resolve(1, "EUR") is None


def test_deleted_lines_are_ignored_and_the_same_threshold_can_be_re_registered() -> None:
    """soft delete된 행은 선택에서 빠지고, 삭제 뒤 같은 임계를 신규로 등록할 수 있다(부분 유니크)"""
    line_id = add_line(0, role="ADMIN")
    assert _resolve(5) == (0, "ADMIN")
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE approval_lines SET deleted_at = now() WHERE id = :i"), {"i": line_id}
        )
    assert _resolve(5) is None
    add_line(0, role="TRADE")
    assert _resolve(5) == (0, "TRADE")


def test_request_without_a_matching_line_is_refused_for_every_gap() -> None:
    """요청 생성은 해당 구간이 없으면 422(LINE.NOT_CONFIGURED) — 행 0개·최소 임계 이하·통화 없음 모두 같다. 승인 행은 남지 않는다"""
    so = credit_so()  # 초과분 100,000 USD
    make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    for setup in (
        None,
        ("KRW", 0),
        ("USD", 100_000),
    ):  # 행 없음 / 통화 불일치 / 초과분이 최소 임계와 같아 해당 없음
        if setup is not None:
            add_line(setup[1], currency=setup[0], role="TRADE")
        with pytest.raises(AppError) as caught:
            request(so["id"], requester)
        assert caught.value.code == ErrorCode.APPROVALS_LINE_NOT_CONFIGURED
        assert caught.value.status_code == 422
        assert count("approvals") == 0


def test_a_line_change_does_not_touch_an_approval_already_requested() -> None:
    """요청 시점에 동결된 required_role·approval_line_id는 결재선을 고치거나 지워도 그대로다 — 새 요청만 새 규칙을 따른다"""
    so = credit_so()
    line_id = add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    make_user(RoleCode.ADMIN)
    first = request(so["id"], make_user(RoleCode.TRADE)).approval
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE approval_lines SET approver_role = 'ADMIN', deleted_at = now() WHERE id = :i"
            ),
            {"i": line_id},
        )
    frozen = approval_row(first.id)
    assert frozen["required_role"] == "TRADE" and frozen["approval_line_id"] == line_id
    add_line(0, role="ADMIN")
    from tests.factories.approvals import line_set, so_set

    line_set(so["id"], quantity=6, line_amount=240_000)
    so_set(so["id"], total_amount=240_000)
    second = request(so["id"], make_user(RoleCode.TRADE)).approval
    assert approval_row(second.id)["required_role"] == "ADMIN"
    assert approval_row(first.id)["status"] == "VOIDED"  # 스냅샷이 바뀌어 이전 요청은 무효
