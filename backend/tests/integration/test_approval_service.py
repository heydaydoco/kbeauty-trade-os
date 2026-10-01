"""A·H. 승인 코어 서비스 — 요청·결정·소비·무효의 본체 (S3-1 PR-9a / ADR-0060·0061 / design-C C2~C4·C6·X1).

실제 DB·실제 잠금으로 서비스 함수를 직접 부른다(HTTP는 test_approvals_api). 핵심 시나리오:
  · 요청 — 결재선 해석(fail-closed)·멱등 재요청·스냅샷 변경 시 이전 승인 VOID·자격자 공집합 거부
  · 결정 — 승인·반려·회수 18조합(상태×동사)·사유 필수·version·SoD(ADMIN 포함 자기 승인 불가)·실패도 커밋(거부 audit)
  · 승인 후 불변 — digest 결속(라인만 바뀌어도·version이 안 올라도 무효)·메모는 무효 아님·상한 초과·승인 불필요
  · 1회 소비 — 소비 후 재소비·타 대상 재사용 불가·BLOCKED는 커밋 후 raise 규약(VOID·감사 보존)
"""

from __future__ import annotations

import pytest

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.modules.approvals import service
from app.modules.approvals.machine import ConsumeOutcome, DecisionVerb, VoidReasonCode
from app.modules.identity.models import RoleCode
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

pytestmark = pytest.mark.group_h


def _code(exc: pytest.ExceptionInfo[AppError]) -> str:
    return str(exc.value.code)


def test_a_credit_excess_request_creates_a_requested_approval_with_a_frozen_line() -> None:
    """초과 SO의 요청이 REQUESTED 승인을 만든다 — 초과분·결재 역할·digest가 요청 시점에 동결된다"""
    so = credit_so()  # 한도 100,000 · 총액 200,000 → 초과분 100,000
    line_id = add_line(0, role="TRADE")
    requester = make_user(RoleCode.TRADE)
    make_user(RoleCode.TRADE)  # 결재 자격자

    result = request(so["id"], requester)

    assert result.created is True
    row = approval_row(result.approval.id)
    assert row["status"] == "REQUESTED"
    assert row["basis_amount"] == 100_000 and row["basis_currency"] == "USD"
    assert row["required_role"] == "TRADE" and row["approval_line_id"] == line_id
    assert len(row["snapshot_digest"]) == 64
    assert row["requested_by_id"] == requester.id
    assert row["target_label"] == so["doc_number"]
    assert [(e["from_status"], e["to_status"]) for e in events_of(row["id"])] == [
        (None, "REQUESTED")
    ]
    assert row["snapshot"]["excess_amount"] == 100_000  # 표시용 상세(판정에 쓰지 않는다)


def test_no_line_means_no_request_fail_closed() -> None:
    """결재선이 없으면 요청을 만들 수 없다(422) — 승인 행 0"""
    so = credit_so()
    make_user(RoleCode.TRADE)
    with pytest.raises(AppError) as caught:
        request(so["id"], make_user(RoleCode.TRADE))
    assert _code(caught) == ErrorCode.APPROVALS_LINE_NOT_CONFIGURED
    assert count("approvals") == 0


def test_a_request_with_nothing_to_approve_is_rejected() -> None:
    """초과가 없는 SO는 승인 요청이 불필요하다(422) — 불필요한 승인 금지"""
    so = credit_so(limit=1_000_000)  # 한도 이내
    add_line(0)
    with pytest.raises(AppError) as caught:
        request(so["id"], make_user(RoleCode.TRADE))
    assert _code(caught) == ErrorCode.VALIDATION_INVALID_FIELD
    assert count("approvals") == 0


def test_a_viewer_cannot_request() -> None:
    """VIEWER 단독 행위자는 승인을 요청할 수 없다(403)"""
    so = credit_so()
    add_line(0)
    with pytest.raises(AppError) as caught:
        request(so["id"], make_user(RoleCode.VIEWER))
    assert caught.value.status_code == 403


def test_the_same_snapshot_returns_the_existing_approval_without_a_second_row() -> None:
    """같은 스냅샷 재요청은 기존 승인을 그대로 돌려준다(created=False) — 이벤트·알림 재발생 없음"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    first = request(so["id"], requester)
    alerts_before = count("alerts")
    second = request(so["id"], requester)
    assert second.created is False and second.approval.id == first.approval.id
    assert count("approvals") == 1 and len(events_of(first.approval.id)) == 1
    assert count("alerts") == alerts_before


def test_a_changed_snapshot_voids_the_old_request_and_opens_a_new_one() -> None:
    """스냅샷이 바뀐 뒤 재요청은 이전 승인을 VOID(TARGET_CHANGED)하고 새 행을 만든다 — 이력 보존·재기안은 신규 행"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    first = request(so["id"], requester)
    line_set(so["id"], quantity=6, line_amount=6 * 40_000)  # 라인 수량 변경(헤더 version 무변)
    so_set(so["id"], total_amount=240_000)
    second = request(so["id"], requester)
    assert second.created is True and second.approval.id != first.approval.id
    old = approval_row(first.approval.id)
    assert old["status"] == "VOIDED"
    assert events_of(first.approval.id)[-1]["reason_code"] == "TARGET_CHANGED"
    assert approval_row(second.approval.id)["basis_amount"] == 140_000
    assert count("approvals") == 2


def test_nobody_eligible_blocks_the_request_and_rolls_back() -> None:
    """자격자가 공집합(유일 ADMIN이 기안자·역할 보유자 없음)이면 요청이 422이고 승인 행이 남지 않는다"""
    so = credit_so()
    add_line(0, role="CERT")  # CERT 보유자 없음
    admin = make_user(RoleCode.ADMIN)  # 유일한 ADMIN = 기안자
    # TRADE 기안자는 CERT 결재 대상의 자격자가 아니다(ADMIN 폴백 대상은 기안자 본인뿐이라 공집합)
    with pytest.raises(AppError) as caught:
        request(so["id"], admin)
    assert _code(caught) == ErrorCode.APPROVALS_APPROVAL_NO_ELIGIBLE_APPROVER
    assert count("approvals") == 0 and count("approval_events") == 0


def test_a_second_approver_approves_and_the_decision_is_recorded() -> None:
    """다른 사용자의 승인이 APPROVED가 되고 결정자·시각·이벤트가 남는다"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    row = approval_row(approval_id)
    assert row["status"] == "APPROVED"
    assert row["decided_by_id"] == approver.id and row["decided_at"] is not None
    assert row["decided_on_behalf_of_id"] is None
    assert [e["to_status"] for e in events_of(approval_id)] == ["REQUESTED", "APPROVED"]


# ── 직무분리(SoD) — ADMIN 포함 자기 승인 불가 ────────────────────────────────────


def test_nobody_can_approve_their_own_request_including_admin() -> None:
    """기안자는 어떤 역할이어도(ADMIN 포함) 자기 기안을 승인·반려할 수 없다(403 SELF_APPROVAL) — 상태 불변·거부 audit"""
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    for requester in (make_user(RoleCode.TRADE), make_user(RoleCode.ADMIN)):
        # 서로 다른 SO로 각각 시험한다 — 대상당 활성 승인은 1건
        target = credit_so()
        approval = request(target["id"], requester).approval
        for verb in (DecisionVerb.APPROVE, DecisionVerb.REJECT):
            with pytest.raises(AppError) as caught:
                decide(approval.id, requester, verb, reason="자기 결재")
            assert _code(caught) == ErrorCode.APPROVALS_DECISION_SELF_APPROVAL
            assert caught.value.status_code == 403
        assert approval_row(approval.id)["status"] == "REQUESTED"
        assert len(events_of(approval.id)) == 1
    denied = audit_actions("approvals.decision.denied")
    assert len(denied) == 4 and {d["detail"]["blocked"] for d in denied} == {"SELF_APPROVAL"}


def test_an_admin_requester_can_be_approved_by_another_admin() -> None:
    """ADMIN이 기안한 건을 **다른** ADMIN이 승인하면 성공한다(양방향 자기검사 — 막는 것은 자기 승인뿐)"""
    so = credit_so()
    add_line(0, role="ADMIN")
    requester = make_user(RoleCode.ADMIN)
    other = make_user(RoleCode.ADMIN)
    approval = request(so["id"], requester).approval
    body = decide(approval.id, other, "APPROVE")
    assert body["status"] == "APPROVED"


@pytest.mark.parametrize(
    "roles",
    [(RoleCode.LOGISTICS,), (RoleCode.CERT,), (RoleCode.VIEWER,), ()],
    ids=["LOGISTICS", "CERT", "VIEWER", "역할없음"],
)
def test_a_user_without_the_required_role_cannot_decide(roles: tuple[RoleCode, ...]) -> None:
    """결재 역할(TRADE)이 없는 사용자는 결정할 수 없다(403 NOT_APPROVER) — 상태 불변·거부 audit"""
    so = credit_so()
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    outsider = make_user(*roles)
    with pytest.raises(AppError) as caught:
        decide(approval.id, outsider, "APPROVE")
    assert _code(caught) == ErrorCode.APPROVALS_DECISION_NOT_APPROVER
    assert approval_row(approval.id)["status"] == "REQUESTED"
    assert audit_actions("approvals.decision.denied")[-1]["detail"]["blocked"] == "NOT_APPROVER"


def test_an_admin_is_eligible_for_any_role_but_a_peer_role_is_not() -> None:
    """활성 ADMIN은 모든 구간의 자격자이고(상시 통과), LOGISTICS 같은 동료 역할은 TRADE 결재 자격이 없다"""
    so = credit_so()
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    assert decide(approval.id, make_user(RoleCode.ADMIN), "APPROVE")["status"] == "APPROVED"


# ── 상태 기계 — 6상태 × 3동사 = 18조합 ─────────────────────────────────────────


def _in_state(state: str) -> tuple[int, object, object]:
    """승인을 지정 상태까지 끌고 간다 → (승인 id, 기안자, 결재자). 새 SO·새 사용자로 격리한다."""
    so = credit_so()
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    requester, approver = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    if state == "REQUESTED":
        return approval.id, requester, approver
    if state == "APPROVED":
        decide(approval.id, approver, "APPROVE")
    elif state == "REJECTED":
        decide(approval.id, approver, "REJECT", reason="사유")
    elif state == "WITHDRAWN":
        decide(approval.id, requester, "WITHDRAW", reason="사유")
    elif state == "CONSUMED":
        decide(approval.id, approver, "APPROVE")
        with unit_of_work() as uow:
            result = service.consume_approval(
                uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
            )
        assert result.outcome is ConsumeOutcome.CONSUMED
    elif state == "VOIDED":
        with unit_of_work() as uow:
            service.void_for_target(
                uow.session,
                approval_type=TYPE,
                target_id=so["id"],
                actor_user_id=approver.id,
                reason_code=VoidReasonCode.TARGET_CANCELLED,
            )
    return approval.id, requester, approver


_ALLOWED_HUMAN = {
    ("REQUESTED", "APPROVE"),
    ("REQUESTED", "REJECT"),
    ("REQUESTED", "WITHDRAW"),
    ("APPROVED", "WITHDRAW"),
}


@pytest.mark.parametrize(
    "state", ["REQUESTED", "APPROVED", "REJECTED", "WITHDRAWN", "CONSUMED", "VOIDED"]
)
@pytest.mark.parametrize("verb", ["APPROVE", "REJECT", "WITHDRAW"])
def test_the_human_transition_table_is_enforced_for_all_18_combinations(
    state: str, verb: str
) -> None:
    """6상태×3동사 18조합 — 허용 4(요청됨×3·승인됨×회수)는 성공, 나머지 14는 409(NOT_ALLOWED)이고 상태 불변"""
    approval_id, requester, approver = _in_state(state)
    actor = requester if verb == "WITHDRAW" else approver
    if (state, verb) in _ALLOWED_HUMAN:
        body = decide(approval_id, actor, verb, reason="사유")
        assert (
            body["status"]
            == {"APPROVE": "APPROVED", "REJECT": "REJECTED", "WITHDRAW": "WITHDRAWN"}[verb]
        )
    else:
        with pytest.raises(AppError) as caught:
            decide(approval_id, actor, verb, reason="사유")
        assert _code(caught) == ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED
        assert caught.value.status_code == 409
        assert approval_row(approval_id)["status"] == state


def test_reject_and_withdraw_require_a_reason() -> None:
    """반려·회수는 공백 제거 후 1자 이상의 사유가 필수다(422) — 승인에는 필요 없다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    requester, approver = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    for verb, actor in (("REJECT", approver), ("WITHDRAW", requester)):
        for reason in (None, "", "   "):
            with pytest.raises(AppError) as caught:
                decide(approval.id, actor, verb, reason=reason)
            assert _code(caught) == ErrorCode.APPROVALS_TRANSITION_REASON_REQUIRED
    assert approval_row(approval.id)["status"] == "REQUESTED"
    assert decide(approval.id, approver, "APPROVE", reason=None)["status"] == "APPROVED"


def test_withdraw_is_for_the_requester_or_admin_only() -> None:
    """회수는 기안자 본인 또는 ADMIN만 — 다른 TRADE는 403, ADMIN은 사유가 있으면 성공"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    with pytest.raises(AppError) as caught:
        decide(approval.id, make_user(RoleCode.TRADE), "WITHDRAW", reason="남의 건")
    assert caught.value.status_code == 403
    body = decide(approval.id, make_user(RoleCode.ADMIN), "WITHDRAW", reason="관리자 회수")
    assert body["status"] == "WITHDRAWN"
    assert events_of(approval.id)[-1]["reason"] == "관리자 회수"


def test_withdrawing_an_approved_request_blocks_the_later_consumption() -> None:
    """승인 후 회수(T7)한 건은 소비되지 않는다 — 확정 시도는 REQUIRED(BLOCKED)"""
    so = credit_so()
    approval_id, requester, _ = approved_for(so)
    decide(approval_id, requester, "WITHDRAW", reason="잘못 내린 승인 철회")
    with unit_of_work() as uow:
        result = service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=requester.id
        )
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED


def test_a_stale_version_is_a_conflict() -> None:
    """낡은 화면(version 불일치)의 결정은 409 VERSION_CONFLICT다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    with pytest.raises(VersionConflictError):
        decide(approval.id, make_user(RoleCode.TRADE), "APPROVE", version=99)


def test_a_rejected_request_can_be_requested_again_as_a_new_row() -> None:
    """반려된 뒤 재요청은 신규 승인 행이다 — 이력 2건이 병존하고 종결 행은 되살아나지 않는다"""
    so = credit_so()
    add_line(0)
    approver = make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    first = request(so["id"], requester).approval
    decide(first.id, approver, "REJECT", reason="보완 필요")
    second = request(so["id"], requester).approval
    assert second.id != first.id
    assert approval_row(first.id)["status"] == "REJECTED"
    assert approval_row(second.id)["status"] == "REQUESTED"
    assert count("approvals") == 2


# ── 승인 후 불변 — digest 결속 ─────────────────────────────────────────────────


def _consume(so_id: int, actor_id: int) -> service.ConsumeResult:
    """확정 통로가 하는 방식 — UoW 안에서 소비하고, BLOCKED면 UoW를 **정상 종료(커밋)한 뒤** 호출자가 raise한다."""
    with unit_of_work() as uow:
        return service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so_id, actor_user_id=actor_id
        )


def test_consumption_succeeds_once_and_the_approval_is_spent() -> None:
    """승인 → 소비 성공(CONSUMED) → 같은 승인으로 두 번째 소비는 REQUIRED — 1회 소비"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    first = _consume(so["id"], approver.id)
    assert first.outcome is ConsumeOutcome.CONSUMED and first.approval_id == approval_id
    row = approval_row(approval_id)
    assert row["status"] == "CONSUMED" and row["consumed_by_id"] == approver.id
    second = _consume(so["id"], approver.id)
    assert second.outcome is ConsumeOutcome.BLOCKED
    assert second.error is not None and second.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED


def test_the_consumed_approval_cannot_be_reused_for_another_order() -> None:
    """소비된 승인은 target_id가 고정이라 다른 SO의 확정에 쓰이지 않는다 — 다른 SO는 REQUIRED"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    other = credit_so()
    result = _consume(other["id"], approver.id)
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED
    assert (
        approval_row(approval_id)["status"] == "APPROVED"
    )  # 다른 SO의 시도가 이 승인을 건드리지 않는다


def test_consuming_without_an_approval_is_blocked_and_leaves_an_audit_trail() -> None:
    """승인이 없는 확정 시도는 BLOCKED(REQUIRED)이고 우회 시도 audit가 남는다 — ADMIN 행위자 포함"""
    so = credit_so()
    admin = make_user(RoleCode.ADMIN)
    result = _consume(so["id"], admin.id)
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED
    rows = audit_actions("approvals.approval.bypass_blocked")
    assert len(rows) == 1 and rows[0]["actor_user_id"] == admin.id
    assert rows[0]["detail"]["target_id"] == so["id"]


def test_a_pending_request_does_not_open_the_gate() -> None:
    """요청만 있고 아직 결재 전(REQUESTED)이면 소비는 BLOCKED — 요청은 승인이 아니다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    request(so["id"], make_user(RoleCode.TRADE))
    result = _consume(so["id"], make_user(RoleCode.ADMIN).id)
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.detail == {"pending_request": True}


def test_a_line_change_after_approval_voids_it_even_if_the_header_version_did_not_move() -> None:
    """승인 후 라인(수량·단가)이 **version을 올리지 않은 채** 바뀌어도 digest 불일치로 소비가 거부된다 — 지연 검증 백스톱"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    line_set(
        so["id"], unit_price_amount=39_000, line_amount=5 * 39_000
    )  # raw UPDATE — 서비스 훅·version 우회
    result = _consume(so["id"], approver.id)
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.code == ErrorCode.APPROVALS_APPROVAL_STALE
    assert approval_row(approval_id)["status"] == "VOIDED"
    assert events_of(approval_id)[-1]["reason_code"] == "TARGET_CHANGED"


def test_a_memo_change_does_not_void_the_approval() -> None:
    """메모·담당자·version 같은 무관 변경은 승인을 무효화하지 않는다 — 소비 성공(불필요한 재승인 압력 없음)"""
    so = credit_so()
    _, _, approver = approved_for(so)
    so_set(so["id"], internal_note="메모만 수정", version=7)
    result = _consume(so["id"], approver.id)
    assert result.outcome is ConsumeOutcome.CONSUMED


def test_exposure_growth_beyond_the_approved_cap_voids_the_approval() -> None:
    """승인 후 초과분이 승인 상한을 넘으면(CAP_EXCEEDED) 무효 — 재요청은 새 금액으로 결재선을 다시 해석한다"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    # 같은 digest를 유지하면서 한도만 낮춰 초과분을 키운다(digest는 거래처 한도를 보지 않는다)
    from tests.factories.approvals import set_credit_limit

    set_credit_limit(so["buyer_partner_id"], 50_000)  # 초과분 150,000 > 승인 상한 100,000
    result = _consume(so["id"], approver.id)
    assert result.outcome is ConsumeOutcome.BLOCKED
    assert result.error is not None and result.error.code == ErrorCode.APPROVALS_APPROVAL_STALE
    assert approval_row(approval_id)["status"] == "VOIDED"
    assert events_of(approval_id)[-1]["reason_code"] == "CAP_EXCEEDED"


def test_exposure_shrink_to_nothing_voids_the_pending_approval_and_needs_none() -> None:
    """노출이 줄어 초과분이 0이 되면 미소비 승인은 VOID(NOT_REQUIRED)되고 소비는 NOT_REQUIRED(확정 진행 가능)다"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    from tests.factories.approvals import set_credit_limit

    set_credit_limit(so["buyer_partner_id"], 1_000_000)
    result = _consume(so["id"], approver.id)
    assert result.outcome is ConsumeOutcome.NOT_REQUIRED
    assert approval_row(approval_id)["status"] == "VOIDED"
    assert events_of(approval_id)[-1]["reason_code"] == "NOT_REQUIRED"
    assert count("approvals", "status IN ('REQUESTED','APPROVED')") == 0


def test_a_cancelled_order_voids_its_approval_through_the_hook() -> None:
    """SO 취소가 `void_for_target`을 부르면 활성 승인이 결재함에서 사라진다(요청됨·승인됨 모두)"""
    for approve in (False, True):
        so = credit_so()
        add_line(0) if count("approval_lines") == 0 else None
        requester, approver = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
        approval = request(so["id"], requester).approval
        if approve:
            decide(approval.id, approver, "APPROVE")
        with unit_of_work() as uow:
            n = service.void_for_target(
                uow.session,
                approval_type=TYPE,
                target_id=so["id"],
                actor_user_id=requester.id,
                reason_code=VoidReasonCode.TARGET_CANCELLED,
            )
        assert n == 1
        assert approval_row(approval.id)["status"] == "VOIDED"
        with unit_of_work() as uow:  # 활성 승인이 없으면 무동작
            assert (
                service.void_for_target(
                    uow.session,
                    approval_type=TYPE,
                    target_id=so["id"],
                    actor_user_id=requester.id,
                    reason_code=VoidReasonCode.TARGET_CANCELLED,
                )
                == 0
            )


def test_a_blocked_consumption_commits_the_void_before_the_caller_raises() -> None:
    """BLOCKED는 예외가 아니라 결과값이다 — 호출자가 UoW를 커밋한 **뒤** raise하면 VOID·감사가 살아남는다(중첩 UoW 계약)"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    line_set(so["id"], quantity=6, line_amount=6 * 40_000)
    with pytest.raises(AppError) as caught:
        with unit_of_work() as uow:
            result = service.consume_approval(
                uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
            )
        # UoW가 정상 종료(커밋)된 뒤에 던진다 — 확정 서비스의 규약
        assert result.error is not None
        raise result.error
    assert caught.value.code == ErrorCode.APPROVALS_APPROVAL_STALE
    assert approval_row(approval_id)["status"] == "VOIDED"  # 롤백되지 않았다


def test_raising_inside_the_unit_of_work_would_roll_the_void_back() -> None:
    """대조 — UoW **안에서** raise하면 VOID가 롤백된다(그래서 소비는 예외를 던지지 않고 결과를 돌려준다)"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    line_set(so["id"], quantity=6, line_amount=6 * 40_000)
    with pytest.raises(AppError), unit_of_work() as uow:
        result = service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
        )
        assert result.error is not None
        raise result.error
    assert (
        approval_row(approval_id)["status"] == "APPROVED"
    )  # 롤백됨 — 같은 시도를 반복하면 매번 같은 결과가 아니다


def test_an_approve_decision_on_a_changed_target_voids_and_returns_stale() -> None:
    """결정(APPROVE) 시점에도 digest를 재검증한다 — 요청 뒤 바뀐 대상은 승인되지 않고 VOID(TARGET_CHANGED)+409 STALE이 커밋된다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    line_set(so["id"], quantity=6, line_amount=6 * 40_000)
    with pytest.raises(AppError) as caught:
        decide(approval.id, make_user(RoleCode.TRADE), "APPROVE")
    assert _code(caught) == ErrorCode.APPROVALS_APPROVAL_STALE
    assert approval_row(approval.id)["status"] == "VOIDED"  # 실패도 커밋
    assert events_of(approval.id)[-1]["reason_code"] == "TARGET_CHANGED"


def test_an_unevaluable_target_never_passes_as_approved_or_consumed() -> None:
    """평가 불능(통화 비교 불가)은 요청·결정·소비 어디서도 통과로 취급되지 않는다 — 승인 상한을 정할 수 없다(fail-closed)"""
    from tests.factories.approvals import set_credit_limit

    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    # 한도 통화를 EUR로 바꾸면 USD 전표·환율 없음 → 비교 불가
    set_credit_limit(so["buyer_partner_id"], 100_000, "EUR")
    with pytest.raises(AppError) as caught:
        request(so["id"], make_user(RoleCode.TRADE))
    assert _code(caught) == ErrorCode.VALIDATION_INVALID_FIELD
    with pytest.raises(AppError):
        _consume(so["id"], make_user(RoleCode.ADMIN).id)
    assert count("approvals") == 0


def test_outbox_events_carry_only_ids_and_states_never_amounts_or_digests() -> None:
    """상태 전이마다 아웃박스 이벤트가 발행되고(요청·승인·소비) 페이로드는 id·유형·대상·from/to·행위자뿐이다 — 금액·digest·사유가 외부 채널로 나가지 않는다"""
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    so = credit_so()
    approval_id, requester, approver = approved_for(so)
    with unit_of_work() as uow:
        service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
        )
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT event_type, payload FROM events WHERE event_type LIKE 'approvals.approval.%' ORDER BY id"
            )
        ).all()
    assert [r[0] for r in rows] == [
        "approvals.approval.requested",
        "approvals.approval.approved",
        "approvals.approval.consumed",
    ]
    for _, payload in rows:
        assert set(payload) == {
            "approval_id",
            "approval_type",
            "target_type",
            "target_id",
            "from_status",
            "to_status",
            "actor_user_id",
        }
        assert payload["approval_id"] == approval_id
    assert requester.id and "digest" not in str(rows) and "100000" not in str(rows)


# ── 알림 — 요청·결과 (C7) ───────────────────────────────────────────────────────


def _alert_rows(like: str) -> list[dict]:
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text("SELECT * FROM alerts WHERE dedup_key LIKE :p ORDER BY id"), {"p": like}
            ).mappings()
        ]


def test_a_request_notifies_the_role_holders_even_with_no_alert_rules() -> None:
    """알림 규칙이 0행이어도 요청이 결재 역할 보유자(기안자 제외)에게 알림을 만든다 — ADMIN·다른 역할·VIEWER·기안자는 받지 않는다(전사 폭포 금지)"""
    so = credit_so()
    add_line(0, role="TRADE")
    holder, other_holder = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    admin, logistics, viewer = (
        make_user(RoleCode.ADMIN),
        make_user(RoleCode.LOGISTICS),
        make_user(RoleCode.VIEWER),
    )
    requester = make_user(RoleCode.TRADE)
    assert count("alert_rules") == 0
    approval = request(so["id"], requester).approval
    rows = _alert_rows(f"approval:{approval.id}:requested:%")
    recipients = {r["recipient_user_id"] for r in rows}
    assert {holder.id, other_holder.id} <= recipients
    assert not recipients & {requester.id, admin.id, logistics.id, viewer.id}
    assert all(r["entity_type"] == "approvals" and r["entity_id"] == approval.id for r in rows)
    assert all(r["severity"] == "WARN" and "@" not in r["title"] for r in rows)
    assert not any("100000" in (r["body"] or "") for r in rows)  # 금액을 싣지 않는다


def test_a_request_with_no_role_holder_falls_back_to_the_admins_with_a_note() -> None:
    """결재 역할 보유자가 0명이면 활성 ADMIN 전원에게(기안자 제외) 전달되고 본문에 안내 문구가 붙는다"""
    from app.modules.approvals import alerts

    so = credit_so()
    add_line(0, role="CERT")
    admin = make_user(RoleCode.ADMIN)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    rows = _alert_rows(f"approval:{approval.id}:requested:%")
    assert [r["recipient_user_id"] for r in rows] == [admin.id]
    assert alerts.ROLE_FALLBACK_NOTE in rows[0]["body"]


def test_a_valid_delegate_is_a_recipient_and_a_failed_request_leaves_no_alert() -> None:
    """유효한 수임자도 요청 알림을 받는다 · 요청이 거부되면(자격자 공집합) 알림도 롤백되어 0건이다(승인 행이 있으면 알림도 있다)"""
    from datetime import timedelta

    from app.core.time import today_kst
    from tests.factories.approvals import add_delegation

    so = credit_so()
    add_line(0, role="TRADE")
    delegator = make_user(RoleCode.TRADE)
    delegate = make_user(RoleCode.LOGISTICS)
    add_delegation(delegator, delegate, today_kst(), today_kst() + timedelta(days=3))
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    recipients = {
        r["recipient_user_id"] for r in _alert_rows(f"approval:{approval.id}:requested:%")
    }
    assert delegate.id in recipients and delegator.id in recipients

    other = credit_so()
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.begin() as connection:  # 자격자를 모두 없앤다(TRADE 전원 비활성)
        connection.execute(text("UPDATE users SET is_active = false"))
    admin_requester = make_user(RoleCode.ADMIN)  # 유일한 활성 사용자 = 기안자 ADMIN
    before = count("alerts")
    with pytest.raises(AppError) as caught:
        request(other["id"], admin_requester)
    assert caught.value.code == ErrorCode.APPROVALS_APPROVAL_NO_ELIGIBLE_APPROVER
    assert count("alerts") == before


def test_results_are_notified_to_the_requester_unless_inactive_or_self_made() -> None:
    """결과 통지 — 승인·반려는 기안자에게 1건, 기안자 본인이 한 회수는 알리지 않고 ADMIN이 한 회수는 알린다, 기안자가 비활성이면 생략한다"""
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    add_line(0, role="TRADE")
    approver = make_user(RoleCode.TRADE)

    def fresh() -> tuple[int, object]:
        so = credit_so()
        requester = make_user(RoleCode.TRADE)
        return int(request(so["id"], requester).approval.id), requester

    a1, r1 = fresh()
    decide(a1, approver, "APPROVE")
    a2, r2 = fresh()
    decide(a2, approver, "REJECT", reason="보완")
    a3, r3 = fresh()
    decide(a3, r3, "WITHDRAW", reason="내가 회수")  # 자기 회수 — 알리지 않는다
    a4, r4 = fresh()
    decide(a4, make_user(RoleCode.ADMIN), "WITHDRAW", reason="관리자 회수")
    a5, r5 = fresh()
    with owner_engine.begin() as connection:
        connection.execute(text("UPDATE users SET is_active = false WHERE id = :i"), {"i": r5.id})
    decide(a5, approver, "APPROVE")  # 기안자 비활성 — 생략

    def got(approval_id: int, user: object, outcome: str) -> int:
        return len(
            [
                r
                for r in _alert_rows(f"approval:{approval_id}:{outcome}:%")
                if r["recipient_user_id"] == user.id  # type: ignore[attr-defined]
            ]
        )

    assert got(a1, r1, "approved") == 1
    assert got(a2, r2, "rejected") == 1
    assert got(a3, r3, "withdrawn") == 0
    assert got(a4, r4, "withdrawn") == 1
    assert got(a5, r5, "approved") == 0


def test_the_transition_function_rejects_all_29_unallowed_pairs() -> None:
    """상태 대입 통로(`_record_transition`)가 직접 불려도 미허용 29쌍은 전부 거부한다(409 NOT_ALLOWED·행 불변) — HUMAN/SYSTEM 통로 검사에 기대지 않는다"""
    from app.modules.approvals.machine import ALLOWED, ApprovalStatus
    from app.modules.approvals.models import Approval

    rejected = 0
    for state in ApprovalStatus:
        so = credit_so()
        if count("approval_lines") == 0:
            add_line(0)
        make_user(RoleCode.TRADE)
        requester = make_user(RoleCode.TRADE)
        approval_id = int(request(so["id"], requester).approval.id)
        approver = make_user(RoleCode.TRADE)
        if state is ApprovalStatus.APPROVED:
            decide(approval_id, approver, "APPROVE")
        elif state is ApprovalStatus.REJECTED:
            decide(approval_id, approver, "REJECT", reason="사유")
        elif state is ApprovalStatus.WITHDRAWN:
            decide(approval_id, requester, "WITHDRAW", reason="사유")
        elif state is ApprovalStatus.CONSUMED:
            decide(approval_id, approver, "APPROVE")
            with unit_of_work() as uow:
                service.consume_approval(
                    uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
                )
        elif state is ApprovalStatus.VOIDED:
            with unit_of_work() as uow:
                service.void_for_target(
                    uow.session,
                    approval_type=TYPE,
                    target_id=so["id"],
                    actor_user_id=approver.id,
                    reason_code=VoidReasonCode.TARGET_CHANGED,
                )
        for target in ApprovalStatus:
            if (state, target) in ALLOWED:
                continue
            kwargs: dict = (
                {"reason": "사유"}
                if target in (ApprovalStatus.REJECTED, ApprovalStatus.WITHDRAWN)
                else {}
            )
            if target is ApprovalStatus.VOIDED:
                kwargs = {"reason_code": VoidReasonCode.TARGET_CHANGED}
            with pytest.raises(AppError) as caught, unit_of_work() as uow:
                row = uow.session.get(Approval, approval_id)
                assert row is not None
                service._record_transition(
                    uow.session, row, to=target, actor_user_id=approver.id, **kwargs
                )
            assert caught.value.code == ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED, (state, target)
            rejected += 1
        assert approval_row(approval_id)["status"] == state.value
    assert rejected == 29


def test_approving_when_nothing_needs_approval_any_more_voids_with_not_required() -> None:
    """요청 뒤 노출이 줄어 초과분이 0이 되면 결정(APPROVE)은 승인되지 않고 VOID(NOT_REQUIRED)+409 STALE이 커밋된다"""
    so = credit_so()
    add_line(0)
    make_user(RoleCode.TRADE)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    set_credit_limit(so["buyer_partner_id"], 1_000_000)
    with pytest.raises(AppError) as caught:
        decide(approval.id, make_user(RoleCode.TRADE), "APPROVE")
    assert caught.value.code == ErrorCode.APPROVALS_APPROVAL_STALE
    assert approval_row(approval.id)["status"] == "VOIDED"
    assert events_of(approval.id)[-1]["reason_code"] == "NOT_REQUIRED"
