"""H. 여신 초과 → 승인 게이트 — **승인 우회 차단(ADMIN 포함)·승인 후 불변·1회 소비** (S3-1 PR-12a / DoD ③ / design-E E4·E5, design-C C4·C6 / ADR-0060·0070).

한도 3,000·SO 총액 5,000(단가 1,000×5) → 초과분 2,000. 확정은 **미해소 시도에서 승인을 만들지도 소비하지도 않는다**: 승인 요청은 사람의 명시 동작(`POST …/approval-requests`)이고,
승인 결정은 다른 사람(요청자≠결정자, ADMIN 포함), 확정은 승인을 **1회 소비**하며 소비 시점에 digest·상한을 다시 본다. 실 HTTP·실 DB.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.approvals import service as approvals_service
from app.modules.approvals.service import ApprovalRef
from app.modules.identity.models import RoleCode
from tests.factories.approvals import (
    actor_of,
    approval_row,
    audit_actions,
    count,
    decide,
    events_of,
    make_user,
    set_credit_limit,
)
from tests.factories.confirm import (
    approvals_of,
    code_of,
    confirm,
    ensure_approval_line,
    error_of,
    evaluations,
    ready_so,
    ready_so_for,
    request_credit_approval,
    scalar,
    so_row,
    so_version,
    status_log,
)
from tests.factories.gates import line_ids, set_line, set_terms
from tests.factories.trade import idem, logged_in, user_id_of

pytestmark = pytest.mark.group_h

TRADE = RoleCode.TRADE
ADMIN = RoleCode.ADMIN
SO = "/api/v1/sales-orders"
LIMIT = 3_000  # 총액 5,000 → 초과분 2,000


def _over(**kwargs: Any) -> dict[str, Any]:
    ensure_approval_line()
    return ready_so(limit=LIMIT, **kwargs)


def _blocked_credit(response: Any) -> dict[str, Any]:
    assert response.status_code == 409, response.text
    assert code_of(response) == "TRADE_CHAIN.CONFIRM.GATE_BLOCKED", response.text
    detail = error_of(response)["detail"]
    credit = [g for g in detail["blocked_gates"] if g["gate_code"] == "CREDIT"]
    assert len(credit) == 1, detail
    assert credit[0]["resolution"] == "APPROVAL" and credit[0]["reason_code"] == "LIMIT_EXCEEDED"
    return dict(detail)


def _assert_not_confirmed(so_id: int) -> None:
    row = so_row(so_id)
    assert row["status"] == "RECEIVED" and row["confirmed_at"] is None
    assert row["credit_verdict"] is None and row["credit_approval_id"] is None
    assert evaluations(so_id, "CONFIRMED") == []


def _request(client: Any, so_id: int) -> dict[str, Any]:
    response = request_credit_approval(client, so_id)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _approved(so: dict[str, Any], client: Any, approver: Any = None) -> int:
    """요청(client 사용자) → 다른 사람의 승인까지 마친 승인 id."""
    approval_id = int(_request(client, so["id"])["id"])
    decide(approval_id, approver or make_user(TRADE), "APPROVE")
    return approval_id


# ══ 우회 차단 — 승인 없는 확정 ══════════════════════════════════════════════════════


@pytest.mark.parametrize("role", [TRADE, ADMIN], ids=["무역", "관리자"])
def test_an_over_limit_order_cannot_be_confirmed_without_approval_even_by_an_admin(
    role: RoleCode,
) -> None:
    """한도 초과 SO의 확정 = 409 GATE_BLOCKED(CREDIT·APPROVAL 해소·LIMIT_EXCEEDED, needs_approval=true) — **ADMIN도 동일**(예외 없음). SO·증적 무변 ·
    **승인 행 0·승인 이벤트 0**(확정 시도만으로 요청이 생기지 않는다) · 우회 시도 audit 1건 · BLOCKED 증거 1행(커밋)"""
    so = _over()
    with logged_in(role) as client:
        response = confirm(client, so["id"])
    detail = _blocked_credit(response)
    assert detail["needs_approval"] is True and detail["pending_approval_id"] is None
    _assert_not_confirmed(so["id"])
    assert approvals_of(so["id"]) == [] and count("approval_events") == 0
    (bypass,) = audit_actions("approvals.approval.bypass_blocked")
    assert (
        bypass["detail"]["target_id"] == so["id"] and bypass["detail"]["pending_request"] is False
    )
    assert len(evaluations(so["id"], "BLOCKED")) == 1


def test_the_blocked_response_carries_the_credit_numbers_for_the_request_button() -> None:
    """차단 응답이 화면용 여신 수치(한도·노출·초과분·미수 미반영)를 싣는다 — 무역·관리자에게만(이 라우트가 그 역할 한정)"""
    so = _over()
    with logged_in(TRADE) as client:
        detail = _blocked_credit(confirm(client, so["id"]))
    credit = next(g for g in detail["blocked_gates"] if g["gate_code"] == "CREDIT")
    basis = credit["basis"]
    assert basis["limit_amount"] == LIMIT and basis["excess_amount"] == 2_000
    assert basis["receivables_reflected"] is False and basis["advisory"] is False


# ══ 승인 요청 API ═══════════════════════════════════════════════════════════════════


def test_an_explicit_request_creates_one_approval_and_a_repeat_returns_the_same_one() -> None:
    """명시 요청 = 승인 1건(REQUESTED·기안자·초과분 상한 2,000 USD·digest 결속) 201 · 같은 입력의 재요청은 기존 승인 200(`created=false`) — 이벤트·알림이 늘지 않는다"""
    so = _over()
    with logged_in(TRADE) as client:
        first = request_credit_approval(client, so["id"])
        events_after_first = count("approval_events")
        again = request_credit_approval(client, so["id"])
    assert first.status_code == 201 and first.json()["created"] is True
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["id"] == first.json()["id"]
    row = approval_row(first.json()["id"])
    assert row["status"] == "REQUESTED" and row["basis_amount"] == 2_000
    assert row["basis_currency"] == "USD" and len(row["snapshot_digest"]) == 64
    assert count("approval_events") == events_after_first == 1


def test_a_request_is_refused_when_nothing_needs_approval() -> None:
    """초과분 ≤ 0(한도 이내)이면 422 — 불필요한 승인을 만들지 않는다"""
    ensure_approval_line()
    so = ready_so(limit=100_000)
    with logged_in(TRADE) as client:
        response = request_credit_approval(client, so["id"])
    assert response.status_code == 422
    assert approvals_of(so["id"]) == []


def test_a_request_is_refused_when_the_credit_cannot_be_evaluated() -> None:
    """평가 불능(한도 통화≠전표 통화·환산 불가)이면 422 — 승인으로 우회할 수 없다(승인 상한을 정할 수 없다)"""
    ensure_approval_line()
    so = ready_so(limit=3_000, limit_currency="EUR")
    with logged_in(TRADE) as client:
        response = request_credit_approval(client, so["id"])
    assert response.status_code == 422
    assert approvals_of(so["id"]) == []


def test_a_request_without_a_configured_approval_line_fails_closed() -> None:
    """결재선이 없으면 요청 단계에서 422 `APPROVALS.LINE.NOT_CONFIGURED`(정상 fail-closed) — 승인 행은 생기지 않는다"""
    so = ready_so(limit=LIMIT)
    assert count("approval_lines") == 0
    with logged_in(TRADE) as client:
        response = request_credit_approval(client, so["id"])
    assert response.status_code == 422 and code_of(response) == "APPROVALS.LINE.NOT_CONFIGURED"
    assert approvals_of(so["id"]) == []


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT])
def test_roles_without_trade_authority_cannot_request_an_approval(role: RoleCode) -> None:
    so = _over()
    with logged_in(role) as client:
        response = request_credit_approval(client, so["id"])
    assert response.status_code == 403
    assert approvals_of(so["id"]) == []


def test_a_request_with_a_stale_version_or_on_a_non_received_order_is_a_409() -> None:
    """화면이 본 version과 다르면 409 · 접수가 아닌(보류) SO는 409 TRANSITION.NOT_ALLOWED"""
    so = _over()
    with logged_in(TRADE) as client:
        stale = request_credit_approval(client, so["id"], version=so_version(so["id"]) + 3)
        assert stale.status_code == 409 and code_of(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
        moved = client.post(
            f"{SO}/{so['id']}/transitions",
            json={"to": "ON_HOLD", "version": so_version(so["id"]), "reason": "보류"},
            headers=idem(),
        )
        assert moved.status_code == 200
        held = request_credit_approval(client, so["id"])
    assert held.status_code == 409 and code_of(held) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert approvals_of(so["id"]) == []


def test_the_request_body_has_no_extra_fields() -> None:
    """승인 요청 본문은 extra=forbid — 승인 상태·금액·결정자 같은 필드를 실을 수 없다(422)"""
    so = _over()
    with logged_in(TRADE) as client:
        for field in ("status", "basis_amount", "approver_id", "force"):
            r = client.post(
                f"{SO}/{so['id']}/approval-requests",
                json={"version": so_version(so["id"]), field: 1},
                headers=idem(),
            )
            assert r.status_code == 422, field
    assert approvals_of(so["id"]) == []


# ══ 승인 → 확정 → 1회 소비 ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("confirmer", [TRADE, ADMIN], ids=["무역이확정", "관리자가확정"])
def test_an_approved_over_limit_order_confirms_and_consumes_the_approval_once(
    confirmer: RoleCode,
) -> None:
    """무역 요청 → **다른 사람**의 승인 → 확정 200 — credit_verdict=APPROVED·credit_approval_id=그 승인 · 승인 CONSUMED(1회·소비자 기록) ·
    상태이력 확정 행의 approval_id · 증거 results.approval_id · 승인 이벤트 requested→approved→consumed"""
    so = _over()
    with logged_in(TRADE) as requester:
        approval_id = _approved(so, requester)
    with logged_in(confirmer) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    body = response.json()["sales_order"]
    assert body["credit_verdict"] == "APPROVED" and body["credit_approval_id"] == approval_id
    row = approval_row(approval_id)
    assert row["status"] == "CONSUMED" and row["consumed_at"] is not None
    assert [e["to_status"] for e in events_of(approval_id)] == ["REQUESTED", "APPROVED", "CONSUMED"]
    log = status_log(so["id"])
    assert log[-1]["to_status"] == "CONFIRMED" and log[-1]["approval_id"] == approval_id
    (evidence,) = evaluations(so["id"], "CONFIRMED")
    assert evidence["results"]["approval_id"] == approval_id
    assert evidence["results"]["credit_verdict"] == "APPROVED"
    credit = [g for g in response.json()["gates"] if g["gate_code"] == "CREDIT"]
    assert credit and credit[0]["settlement"] == "APPROVED"


def test_a_consumed_approval_cannot_confirm_another_order_of_the_same_buyer() -> None:
    """승인은 1회·1SO — 소비된 뒤 같은 거래처의 다른 초과 SO는 그 승인으로 확정되지 않는다(409·REQUIRED 안내) · DB도 같은 승인을 두 SO에 달 수 없다"""
    first = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(first, client)
        assert confirm(client, first["id"]).status_code == 200
        # 같은 거래처의 두 번째 초과 SO — 한도는 이미 확정 SO(5,000)로 초과 상태
        second = ready_so_for(first["buyer"], price=1000, quantity=5)
        response = confirm(client, second["id"])
    _blocked_credit(response)
    _assert_not_confirmed(second["id"])
    assert approval_row(approval_id)["status"] == "CONSUMED"
    assert approvals_of(second["id"]) == []


def test_a_rejected_withdrawn_or_voided_approval_never_confirms() -> None:
    """반려·회수·무효(VOID) 승인은 소비되지 않는다 — 확정은 GATE_BLOCKED이고 `pending_approval_id`는 없다(활성 승인이 아니므로)"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = int(_request(client, so["id"])["id"])
        decide(approval_id, make_user(TRADE), "REJECT", reason="한도 초과 불가")
        detail = _blocked_credit(confirm(client, so["id"]))
        assert detail["pending_approval_id"] is None
        assert approval_row(approval_id)["status"] == "REJECTED"
        # 재기안(신규 행) → 승인 → 회수
        second = int(_request(client, so["id"])["id"])
        assert second != approval_id
        approver = make_user(TRADE)
        decide(second, approver, "APPROVE")
        decide(
            second, make_user(ADMIN), "WITHDRAW", reason="재검토"
        )  # 회수는 기안자 본인 또는 관리자
        _blocked_credit(confirm(client, so["id"]))
        assert approval_row(second)["status"] == "WITHDRAWN"
    _assert_not_confirmed(so["id"])


def test_a_pending_request_does_not_confirm_and_is_pointed_to_by_the_blocked_response() -> None:
    """결재 대기(REQUESTED) 중에는 확정되지 않는다 — 응답이 `pending_approval_id`·상태를 안내하고 우회 시도 audit의 pending_request=true"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = int(_request(client, so["id"])["id"])
        detail = _blocked_credit(confirm(client, so["id"]))
    assert detail["pending_approval_id"] == approval_id
    assert detail["pending_approval_status"] == "REQUESTED"
    assert (
        audit_actions("approvals.approval.bypass_blocked")[-1]["detail"]["pending_request"] is True
    )
    assert approval_row(approval_id)["status"] == "REQUESTED"


@pytest.mark.parametrize("requester_role", [TRADE, ADMIN], ids=["무역기안", "관리자기안"])
def test_the_requester_cannot_approve_their_own_request_and_so_the_order_stays_blocked(
    requester_role: RoleCode,
) -> None:
    """자기 승인 금지(직무분리 — ADMIN 포함) — 기안자 본인의 승인 결정은 403 SELF_APPROVAL, 따라서 확정은 막힌 채 남는다"""
    from app.core.errors.exceptions import AppError

    so = _over()
    email = f"self-{requester_role.value.lower()}@example.com"
    with logged_in(requester_role, email=email) as client:
        approval_id = int(_request(client, so["id"])["id"])
        me = actor_of(user_id_of(email), requester_role)
        with pytest.raises(AppError) as caught:
            decide(approval_id, me, "APPROVE")
        assert caught.value.code == "APPROVALS.DECISION.SELF_APPROVAL"
        _blocked_credit(confirm(client, so["id"]))
    assert approval_row(approval_id)["status"] == "REQUESTED"
    _assert_not_confirmed(so["id"])


def test_an_admin_can_approve_someone_elses_request_but_not_confirm_without_it() -> None:
    """관리자는 **남의** 요청을 승인할 수 있고(결재 자격), 그 승인으로 확정된다 — 승인 없는 관리자 확정은 위에서 막힌다(양방향)"""
    so = _over()
    with logged_in(TRADE) as requester:
        approval_id = int(_request(requester, so["id"])["id"])
    admin = make_user(ADMIN)
    decide(approval_id, admin, "APPROVE")
    with logged_in(ADMIN) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert approval_row(approval_id)["decided_by_id"] == admin.id


# ══ 승인 후 불변 — 편집 훅(eager)·digest(lazy) ═══════════════════════════════════════


def test_editing_the_order_after_approval_voids_the_approval_and_requires_a_new_request() -> None:
    """승인 후 SO 라인 수량 변경(서비스 경로) → 열린 승인이 **같은 트랜잭션에서** VOIDED(TARGET_CHANGED) — 확정은 막히고 재요청이 필요하다(새 승인 행)"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        (line,) = line_ids(so["id"])
        edit = client.patch(
            f"{SO}/{so['id']}/lines/{line}",
            json={"version": so_version(so["id"]), "quantity": 6},
        )
        assert edit.status_code == 200, edit.text
        row = approval_row(approval_id)
        assert row["status"] == "VOIDED"
        assert events_of(approval_id)[-1]["reason_code"] == "TARGET_CHANGED"
        blocked = _blocked_credit(confirm(client, so["id"]))
        assert blocked["pending_approval_id"] is None
        again = request_credit_approval(client, so["id"])
        assert again.status_code == 201 and again.json()["id"] != approval_id
        assert again.json()["basis_amount"] == 3_000  # 총액 6,000 − 한도 3,000
    _assert_not_confirmed(so["id"])


@pytest.mark.parametrize(
    "edit",
    ["add_line", "remove_line", "update_price", "fx_rate"],
    ids=["라인추가", "라인제외", "단가변경", "환율변경"],
)
def test_every_input_changing_edit_voids_the_open_approval(edit: str) -> None:
    """게이트 입력(라인 추가·제외·단가·환율)을 바꾸는 편집은 모두 열린 승인을 무효화한다(void 훅이 4경로에 배선됐음 — 훅 제거 변이의 표적)"""
    so = _over()
    from tests.factories.trade import create_priced_sku

    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        version = so_version(so["id"])
        (line,) = line_ids(so["id"])
        if edit == "add_line":
            extra = create_priced_sku(amount=1000)
            r = client.post(
                f"{SO}/{so['id']}/lines", json={"version": version, "sku_id": extra, "quantity": 1}
            )
        elif edit == "remove_line":
            r = client.delete(f"{SO}/{so['id']}/lines/{line}", params={"version": version})
        elif edit == "update_price":
            r = client.patch(
                f"{SO}/{so['id']}/lines/{line}",
                json={"version": version, "unit_price": "11.00"},
            )
        else:
            r = client.patch(f"{SO}/{so['id']}", json={"version": version, "fx_rate": "1400"})
        assert r.status_code == 200 or r.status_code == 201, r.text
    row = approval_row(approval_id)
    assert row["status"] == "VOIDED", edit
    assert events_of(approval_id)[-1]["reason_code"] == "TARGET_CHANGED"


def test_an_edit_that_does_not_touch_the_gate_inputs_keeps_the_approval() -> None:
    """메모·담당자·바이어 PO번호·요청납기처럼 게이트 입력이 아닌 편집은 승인을 무효화하지 않는다(무관 변경으로 재승인이 생겨 우회 압력이 되지 않게) — 이어서 확정된다"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        (line,) = line_ids(so["id"])
        meta = client.patch(
            f"{SO}/{so['id']}/meta",
            json={"version": so_version(so["id"]), "internal_note": "메모 수정"},
        )
        assert meta.status_code == 200
        header = client.patch(
            f"{SO}/{so['id']}",
            json={"version": so_version(so["id"]), "buyer_po_no": "PO-NEW-1"},
        )
        assert header.status_code == 200, header.text
        line_edit = client.patch(
            f"{SO}/{so['id']}/lines/{line}",
            json={"version": so_version(so["id"]), "requested_delivery_date": "2026-12-31"},
        )
        assert line_edit.status_code == 200, line_edit.text
        assert approval_row(approval_id)["status"] == "APPROVED"
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert approval_row(approval_id)["status"] == "CONSUMED"


def test_cancelling_the_order_voids_the_open_approval_but_not_a_consumed_one() -> None:
    """SO 취소는 열린 승인을 TARGET_CANCELLED로 무효화한다 · 이미 소비된 승인(확정 SO의 취소)은 CONSUMED 종결 그대로"""
    open_so = _over()
    with logged_in(TRADE) as client:
        open_id = int(_request(client, open_so["id"])["id"])
        cancel = client.post(
            f"{SO}/{open_so['id']}/transitions",
            json={"to": "CANCELLED", "version": so_version(open_so["id"]), "reason": "고객 취소"},
            headers=idem(),
        )
        assert cancel.status_code == 200, cancel.text
        assert approval_row(open_id)["status"] == "VOIDED"
        assert events_of(open_id)[-1]["reason_code"] == "TARGET_CANCELLED"

        done = _over()
        consumed_id = _approved(done, client)
        assert confirm(client, done["id"]).status_code == 200
        cancel2 = client.post(
            f"{SO}/{done['id']}/transitions",
            json={"to": "CANCELLED", "version": so_version(done["id"]), "reason": "고객 취소"},
            headers=idem(),
        )
        assert cancel2.status_code == 200, cancel2.text
    assert approval_row(consumed_id)["status"] == "CONSUMED"


def test_a_raw_price_change_that_skips_the_edit_hook_is_still_caught_by_the_digest() -> None:
    """**version을 올리지 않는 직접 UPDATE**(훅·낙관 잠금 우회)로 단가를 바꿔도 소비 불가 — digest 불일치라 확정은 GATE_BLOCKED(승인 없음으로 취급)이고 SO는 그대로다(지연 검증 백스톱)"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        # 총액은 5,000 그대로(초과분·상한 불변) — **digest만** 달라지는 변조라 digest 대조 제거가 곧바로 드러난다
        set_line(so["id"], 1, unit_price=1250, quantity=4, list_price=1250)
        response = confirm(client, so["id"])
    _blocked_credit(response)
    _assert_not_confirmed(so["id"])
    assert (
        approval_row(approval_id)["status"] == "APPROVED"
    )  # 조회·차단 경로는 승인을 건드리지 않는다


def test_a_stale_approval_that_clearance_still_sees_is_voided_at_consumption_and_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정산(clearance)이 아직 승인을 본다고 가정(경합 모사)해도 **소비 시점 재검증**이 막는다 — 409 STALE · 승인은 VOIDED(TARGET_CHANGED)로 **커밋돼 남고**(실패도 커밋) · SO·증적 무변 · BLOCKED 증거 1행"""
    from app.modules.trade_chain import gate_flow

    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        set_line(so["id"], 1, unit_price=1250, quantity=4, list_price=1250)  # 총액 5,000 불변·digest만 변함
        fake = ApprovalRef(
            id=approval_id,
            approval_type="SO_CREDIT_EXCEEDED",
            target_id=so["id"],
            basis_amount=2_000,
            basis_currency="USD",
            required_role="TRADE",
        )
        monkeypatch.setattr(
            gate_flow.approvals_service, "peek_active_approved", lambda *a, **k: fake
        )
        response = confirm(client, so["id"])
    assert response.status_code == 409 and code_of(response) == "APPROVALS.APPROVAL.STALE"
    assert approval_row(approval_id)["status"] == "VOIDED"
    assert events_of(approval_id)[-1]["reason_code"] == "TARGET_CHANGED"
    _assert_not_confirmed(so["id"])
    assert len(evaluations(so["id"], "BLOCKED")) == 1


def test_exposure_growth_beyond_the_approved_cap_makes_the_approval_stale() -> None:
    """승인 후 같은 거래처의 다른 SO가 먼저 확정돼 노출이 늘면(초과분 > 승인 상한) 이 승인으론 확정되지 않는다 — 재요청하면 기존 승인은 VOID(TARGET_CHANGED)되고 새 승인 행이 생긴다"""
    so = _over()  # 한도 3,000 · 총액 5,000 → 승인 상한 2,000
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        other = ready_so_for(so["buyer"], price=1000, quantity=2)  # 2,000 — 단독으로는 한도 이내
        assert (
            confirm(client, other["id"]).status_code == 200
        )  # 노출 +2,000 → 이제 A의 초과분 4,000
        _blocked_credit(confirm(client, so["id"]))
        assert (
            approval_row(approval_id)["status"] == "APPROVED"
        )  # 차단 경로는 승인을 건드리지 않는다
        again = request_credit_approval(client, so["id"])
    assert again.status_code == 201 and again.json()["basis_amount"] == 4_000
    assert approval_row(approval_id)["status"] == "VOIDED"
    _assert_not_confirmed(so["id"])


def test_raising_the_limit_after_approval_confirms_without_consuming_and_voids_the_approval() -> (
    None
):
    """승인 후 한도 상향으로 범위 내가 되면 승인을 소비하지 않고 정상 확정(WITHIN_LIMIT) — 남은 승인은 VOID(NOT_REQUIRED)로 정리된다"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        set_credit_limit(so["buyer"], 100_000, "USD")
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    body = response.json()["sales_order"]
    assert body["credit_verdict"] == "WITHIN_LIMIT" and body["credit_approval_id"] is None
    assert approval_row(approval_id)["status"] == "VOIDED"
    assert events_of(approval_id)[-1]["reason_code"] == "NOT_REQUIRED"
    assert status_log(so["id"])[-1]["approval_id"] is None


# ══ 게이트 순서 — 하드 실패가 소비보다 먼저 ═══════════════════════════════════════════


def test_another_unresolved_gate_blocks_before_the_approval_is_consumed() -> None:
    """승인이 있어도 다른 게이트(PI 입금 BLOCK)가 미해소면 확정되지 않고 **승인은 소비되지 않는다**(APPROVED 그대로) — 소비는 모든 게이트가 해소된 뒤의 마지막 부작용이다"""
    so = _over()
    set_terms(so["id"], "TT_ADVANCE")  # PI 없는 선수금 → PI_DEPOSIT BLOCK(정책 미설정=BLOCK)
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        response = confirm(client, so["id"])
        assert (
            response.status_code == 409 and code_of(response) == "TRADE_CHAIN.CONFIRM.GATE_BLOCKED"
        )
        gates = {g["gate_code"] for g in error_of(response)["detail"]["blocked_gates"]}
        assert gates == {"PI_DEPOSIT"}  # 여신은 승인으로 해소된 상태
    assert approval_row(approval_id)["status"] == "APPROVED"
    _assert_not_confirmed(so["id"])


def test_an_unevaluable_credit_cannot_be_rescued_by_an_existing_approval() -> None:
    """평가 불능(통화 환산 불가)이면 승인이 있어도 통과 불가 — 승인 경로가 없는 UNKNOWN은 그대로 막힌다(과거 승인이 남아 있어도)"""
    so = _over()
    with logged_in(TRADE) as client:
        approval_id = _approved(so, client)
        set_credit_limit(so["buyer"], 3_000, "EUR")  # 이후 한도 통화가 바뀌어 환산 불가
        response = confirm(client, so["id"])
        assert response.status_code == 409
        blocked = {g["gate_code"]: g for g in error_of(response)["detail"]["blocked_gates"]}
        assert blocked["CREDIT"]["resolution"] == "NONE"
    _assert_not_confirmed(so["id"])
    assert approval_row(approval_id)["status"] in ("APPROVED", "VOIDED")


# ══ 소비 접점 — 승인 코어 직접 시도 ═══════════════════════════════════════════════════


def test_the_approval_core_alone_cannot_confirm_an_order() -> None:
    """승인을 받아도 SO를 확정하는 것은 확정 통로뿐이다 — 승인 결정(APPROVED)만으로 SO 상태·증적은 바뀌지 않는다"""
    so = _over()
    with logged_in(TRADE) as client:
        _approved(so, client)
    _assert_not_confirmed(so["id"])
    assert (
        scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == 1
    )


def test_consume_requires_a_real_actor_and_the_service_rejects_a_viewer_confirmer() -> None:
    """확정 서비스는 역할 없는 행위자를 잠금 이전에 거부한다(라우트 게이트와 이중) — VIEWER 서비스 직접 호출도 403"""
    from app.core.errors.exceptions import ForbiddenError
    from tests.factories.confirm import confirm_service

    so = _over()
    viewer = make_user(RoleCode.VIEWER)
    with pytest.raises(ForbiddenError):
        confirm_service(viewer, so["id"])
    assert approvals_service  # 모듈 임포트 유지(소비 접점 확인용)
    _assert_not_confirmed(so["id"])
