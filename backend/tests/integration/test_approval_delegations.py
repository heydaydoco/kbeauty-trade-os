"""H. 대결(위임) — 기간 유효·이력·범위·중첩·종료·권한 초과·재위임 불가 (S3-1 PR-9a / ADR-0061 / design-C C5, GC-H5).

"대결 기간 유효+이력"이 §20 H의 한 줄이다. 핵심은 **날짜 판정**(KST 달력 날짜 양끝 포함, UTC/KST 갈림), **자격 계산**(위임자·수임자 활성·역할 — 비활성·상실은
즉시 무효·재활성 시 복귀), **범위**(유형+역할 — ADMIN 위임이어도 ADMIN 전권이 아니다), **재위임 불가**, **중첩 금지**, **조기 종료**다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.approvals.authority import decision_authority
from app.modules.approvals.machine import AuthorityKind
from app.modules.approvals.models import Approval
from app.modules.identity.models import RoleCode
from tests.factories.approvals import (
    add_delegation,
    add_line,
    approval_row,
    audit_actions,
    client_for,
    count,
    credit_so,
    decide,
    events_of,
    fixed_today,
    make_user,
    request,
)
from tests.factories.trade import idem, logged_in

pytestmark = pytest.mark.group_h

DELEGATIONS = "/api/v1/delegations"


def _setup() -> tuple[int, Any, Any]:
    """(TRADE 승인 id, 기안자 TRADE, 위임자 TRADE) — 결재선 임계 0=TRADE."""
    so = credit_so()
    add_line(0, role="TRADE")
    delegator = make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    return int(approval.id), requester, delegator


def _deactivate(user_id: int, active: bool = False) -> None:
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET is_active = :a WHERE id = :i"), {"a": active, "i": user_id}
        )


def _revoke_role(user_id: int, code: str) -> None:
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE user_roles SET deleted_at = now() WHERE user_id = :i"
                " AND role_id = (SELECT id FROM roles WHERE code = :c)"
            ),
            {"i": user_id, "c": code},
        )


def _authority(approval_id: int, user: Any) -> AuthorityKind:
    from app.core.db.uow import unit_of_work

    with unit_of_work() as uow:
        approval = uow.session.get(Approval, approval_id)
        assert approval is not None
        return decision_authority(uow.session, actor_id=user.id, approval=approval).kind


# ── 날짜 판정 — 양끝 포함 · UTC/KST 갈림 ────────────────────────────────────────


def test_period_bounds_are_inclusive_on_both_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """시작일·종료일 당일은 유효하고 시작 전날·종료 다음 날은 무효다(KST 달력 날짜 양끝 포함)"""
    approval_id, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)  # TRADE 자격이 본인에게는 없는 수임자
    start, end = date(2026, 10, 10), date(2026, 10, 14)
    add_delegation(delegator, delegate, start, end)
    cases = [
        (start - timedelta(days=1), AuthorityKind.DENIED_NOT_APPROVER),
        (start, AuthorityKind.DELEGATED),
        (date(2026, 10, 12), AuthorityKind.DELEGATED),
        (end, AuthorityKind.DELEGATED),
        (end + timedelta(days=1), AuthorityKind.DENIED_NOT_APPROVER),
    ]
    for today, expected in cases:
        with fixed_today(monkeypatch, today):
            assert _authority(approval_id, delegate) is expected, today


def test_the_judgement_uses_the_korean_date_not_the_utc_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTC 2026-10-14 15:30 = KST 15일 00:30 — 종료일이 14일인 대결은 무효이고 15일이면 유효하다(UTC 날짜로 판정하면 반대가 된다)"""
    approval_id, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    ends_14th = add_delegation(delegator, delegate, date(2026, 10, 1), date(2026, 10, 14))
    from app.core import time as core_time

    monkeypatch.setattr(core_time, "utcnow", lambda: datetime(2026, 10, 14, 15, 30, tzinfo=UTC))
    assert today_kst() == date(2026, 10, 15)  # 자기검사 — 한국은 이미 15일이다
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE delegations SET revoked_at = now(), revoked_by_id = :u WHERE id = :i"),
            {"u": delegator.id, "i": ends_14th},
        )
    add_delegation(delegator, delegate, date(2026, 10, 1), date(2026, 10, 15))
    assert _authority(approval_id, delegate) is AuthorityKind.DELEGATED


# ── 이력 — 사용 기록·위임자 알림 ────────────────────────────────────────────────


def test_a_delegated_decision_records_both_people_and_the_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """수임자가 결정하면 approvals·approval_events 양쪽에 수임자·위임자·대결 id가 같은 값으로 남고, 위임자에게 알림 1건"""
    approval_id, requester, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    delegation_id = add_delegation(
        delegator, delegate, today_kst(), today_kst() + timedelta(days=3)
    )
    body = decide(approval_id, delegate, "APPROVE")
    assert body["status"] == "APPROVED"
    assert body["decided_on_behalf_of_name"] is not None
    row = approval_row(approval_id)
    assert row["decided_by_id"] == delegate.id
    assert row["decided_on_behalf_of_id"] == delegator.id
    assert row["decided_delegation_id"] == delegation_id
    last = events_of(approval_id)[-1]
    assert (last["actor_user_id"], last["on_behalf_of_user_id"], last["delegation_id"]) == (
        delegate.id,
        delegator.id,
        delegation_id,
    )
    assert (
        count(
            "alerts",
            "recipient_user_id = :u AND dedup_key LIKE 'approval:%:delegated:%'",
            u=delegator.id,
        )
        == 1
    )
    assert (
        count(
            "alerts",
            "recipient_user_id = :u AND dedup_key LIKE 'approval:%:approved:%'",
            u=requester.id,
        )
        == 1
    )


def test_no_delegation_means_no_authority_and_an_expired_one_does_not_count() -> None:
    """대결이 없거나 기간이 지났으면 TRADE 자격이 없는 사용자는 결정할 수 없다(403 NOT_APPROVER)"""
    approval_id, _, delegator = _setup()
    outsider = make_user(RoleCode.LOGISTICS)
    with pytest.raises(AppError) as caught:
        decide(approval_id, outsider, "APPROVE")
    assert caught.value.code == ErrorCode.APPROVALS_DECISION_NOT_APPROVER
    add_delegation(
        delegator, outsider, today_kst() - timedelta(days=9), today_kst() - timedelta(days=1)
    )
    add_delegation(
        delegator, outsider, today_kst() + timedelta(days=1), today_kst() + timedelta(days=5)
    )
    with pytest.raises(AppError):
        decide(approval_id, outsider, "APPROVE")  # 지난 대결·미래 대결 모두 지금은 무효
    assert approval_row(approval_id)["status"] == "REQUESTED"


# ── 자격 계산 — 비활성·역할 상실은 즉시 무효, 재활성 시 복귀 ─────────────────────────


def test_inactive_parties_or_a_lost_role_make_the_delegation_inert_immediately() -> None:
    """위임자·수임자 비활성, 위임자의 역할 상실은 즉시 무효 — 재활성·재부여하면 남은 기간 동안 다시 유효하다(수용된 동작)"""
    approval_id, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    add_delegation(delegator, delegate, today_kst(), today_kst() + timedelta(days=5))
    assert _authority(approval_id, delegate) is AuthorityKind.DELEGATED

    _deactivate(delegator.id)
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER
    _deactivate(delegator.id, True)
    assert _authority(approval_id, delegate) is AuthorityKind.DELEGATED  # 재활성 시 복귀

    _deactivate(delegate.id)
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER
    _deactivate(delegate.id, True)

    _revoke_role(delegator.id, "TRADE")  # 위임자가 위임한 역할을 잃었다
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER


def test_a_delegate_without_any_approver_role_is_inert() -> None:
    """수임자가 비조회 역할을 전부 잃으면(VIEWER만 남음) 대결은 무효다"""
    approval_id, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    add_delegation(delegator, delegate, today_kst(), today_kst() + timedelta(days=5))
    _revoke_role(delegate.id, "LOGISTICS")
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER


def test_the_delegation_list_shows_the_computed_state_and_inert_reason() -> None:
    """대결 목록이 계산 상태(ACTIVE·UPCOMING·EXPIRED·REVOKED·INERT)와 INERT 사유 코드를 서버 계산으로 보여 준다"""
    _, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    today = today_kst()
    active = add_delegation(delegator, delegate, today, today + timedelta(days=5))
    add_delegation(
        delegator, delegate, today + timedelta(days=9), today + timedelta(days=12), role="TRADE"
    )
    add_delegation(
        delegator, delegate, today - timedelta(days=9), today - timedelta(days=2), role="TRADE"
    )
    _deactivate(delegate.id)
    with client_for(delegator) as client:
        states = {i["id"]: i for i in client.get(DELEGATIONS).json()["items"]}
        assert (
            states[active]["state"] == "INERT"
            and states[active]["inert_reason"] == "delegate_inactive"
        )
        assert sorted(s["state"] for s in states.values()) == ["EXPIRED", "INERT", "UPCOMING"]
    _deactivate(delegate.id, True)
    with client_for(delegator) as client:
        assert {i["id"]: i["state"] for i in client.get(DELEGATIONS).json()["items"]}[
            active
        ] == "ACTIVE"


# ── 범위·권한 초과·재위임 불가 ───────────────────────────────────────────────────


def test_a_trade_user_cannot_delegate_the_admin_role_but_an_admin_can_delegate_trade() -> None:
    """위임자가 보유하지 않은 역할(TRADE→ADMIN)은 위임할 수 없다(422) — ADMIN이 TRADE를 위임하는 것은 성공"""
    trade, admin, delegate = (
        make_user(RoleCode.TRADE),
        make_user(RoleCode.ADMIN),
        make_user(RoleCode.LOGISTICS),
    )
    today = today_kst()
    body = {
        "delegate_user_id": delegate.id,
        "approval_type": "SO_CREDIT_EXCEEDED",
        "delegated_role": "ADMIN",
        "start_on": today.isoformat(),
        "end_on": (today + timedelta(days=3)).isoformat(),
    }
    with client_for(trade) as client:
        response = client.post(DELEGATIONS, json=body, headers=idem())
        assert response.status_code == 422
        assert "delegated_role" in response.text
    with client_for(admin) as client:
        ok = client.post(DELEGATIONS, json={**body, "delegated_role": "TRADE"}, headers=idem())
        assert ok.status_code == 201, ok.text
        assert ok.json()["state"] == "ACTIVE" and ok.json()["delegator_user_id"] == admin.id
    assert len(audit_actions("approvals.delegation.created")) == 1


def test_an_admin_delegation_covers_only_the_delegated_role_not_admin_power() -> None:
    """ADMIN이 TRADE 대결을 위임해도 수임자는 ADMIN 구간(required_role=ADMIN) 승인은 결정할 수 없다 — 권한 초과 방지"""
    so = credit_so()
    add_line(0, role="ADMIN")
    admin, delegate = make_user(RoleCode.ADMIN), make_user(RoleCode.TRADE)
    make_user(RoleCode.ADMIN)
    approval = request(so["id"], make_user(RoleCode.TRADE)).approval
    add_delegation(admin, delegate, today_kst(), today_kst() + timedelta(days=3), role="TRADE")
    with pytest.raises(AppError) as caught:
        decide(approval.id, delegate, "APPROVE")
    assert caught.value.code == ErrorCode.APPROVALS_DECISION_NOT_APPROVER
    add_delegation(admin, delegate, today_kst(), today_kst() + timedelta(days=3), role="ADMIN")
    assert decide(approval.id, delegate, "APPROVE")["status"] == "APPROVED"


def test_a_delegation_is_scoped_to_its_approval_type_and_role() -> None:
    """대결은 (유형, 역할) 범위다 — 다른 유형·다른 역할 승인에는 효력이 없다(새 승인 유형이 추가돼도 기존 위임이 조용히 확장되지 않는다)"""
    from app.core.db.uow import unit_of_work
    from app.modules.approvals.authority import valid_delegations

    approval_id, requester, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    add_delegation(delegator, delegate, today_kst(), today_kst() + timedelta(days=3))

    def matches(approval_type: str, role: str) -> int:
        with unit_of_work() as uow:
            return len(
                uow.session.execute(
                    valid_delegations(
                        approval_type=approval_type,
                        required_role=role,
                        requested_by_id=requester.id,
                        today=today_kst(),
                    )
                ).all()
            )

    assert matches("SO_CREDIT_EXCEEDED", "TRADE") == 1
    assert matches("EXPENSE_OVER_THRESHOLD", "TRADE") == 0  # 다른 유형
    assert matches("SO_CREDIT_EXCEEDED", "CERT") == 0  # 다른 역할
    assert _authority(approval_id, delegate) is AuthorityKind.DELEGATED


def test_redelegation_is_structurally_impossible() -> None:
    """A→B 위임(B는 LOGISTICS)이어도 B가 그 TRADE 권한을 C에게 넘길 수 없다(B가 TRADE를 보유하지 않으므로 422) — 사슬 불성립"""
    approval_id, _, a = _setup()
    b, c = make_user(RoleCode.LOGISTICS), make_user(RoleCode.CERT)
    add_delegation(a, b, today_kst(), today_kst() + timedelta(days=3))
    body = {
        "delegate_user_id": c.id,
        "approval_type": "SO_CREDIT_EXCEEDED",
        "delegated_role": "TRADE",
        "start_on": today_kst().isoformat(),
        "end_on": (today_kst() + timedelta(days=3)).isoformat(),
    }
    with client_for(b) as client:
        assert client.post(DELEGATIONS, json=body, headers=idem()).status_code == 422
    # DB로 강제로 사슬을 만들어도(B→C) C는 결정할 수 없다 — 위임자 B가 TRADE를 본인 자격으로 보유하지 않기 때문
    add_delegation(b, c, today_kst(), today_kst() + timedelta(days=3))
    assert _authority(approval_id, c) is AuthorityKind.DENIED_NOT_APPROVER
    assert _authority(approval_id, b) is AuthorityKind.DELEGATED


def test_delegate_equal_to_requester_is_blocked_at_decision_time() -> None:
    """수임자=기안자는 등록이 아니라 **결정 시점**에 건별로 막힌다(SELF) — 위임자=기안자인 대결로는 다른 수임자도 그 건을 결정하지 못한다"""
    so = credit_so()
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    delegator = make_user(RoleCode.TRADE)
    requester = make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    # 수임자=기안자
    add_delegation(delegator, requester, today_kst(), today_kst() + timedelta(days=3))
    with pytest.raises(AppError) as caught:
        decide(approval.id, requester, "APPROVE")
    assert caught.value.code == ErrorCode.APPROVALS_DECISION_SELF_APPROVAL
    # 위임자=기안자: 기안자가 위임해 둔 수임자는 이 건을 대결로 결정할 수 없다(LOGISTICS라 본인 자격도 없음)
    other = make_user(RoleCode.LOGISTICS)
    add_delegation(requester, other, today_kst(), today_kst() + timedelta(days=3))
    with pytest.raises(AppError) as caught2:
        decide(approval.id, other, "APPROVE")
    assert caught2.value.code == ErrorCode.APPROVALS_DECISION_NOT_APPROVER
    assert approval_row(approval.id)["status"] == "REQUESTED"


# ── 등록 검증 ────────────────────────────────────────────────────────────────


def _reg_body(delegate_id: int, **overrides: Any) -> dict[str, Any]:
    today = today_kst()
    body: dict[str, Any] = {
        "delegate_user_id": delegate_id,
        "approval_type": "SO_CREDIT_EXCEEDED",
        "delegated_role": "TRADE",
        "start_on": today.isoformat(),
        "end_on": (today + timedelta(days=7)).isoformat(),
    }
    body.update(overrides)
    return body


def test_registration_rejects_retroactive_inverted_self_and_viewer_delegates() -> None:
    """소급(시작일<오늘)·종료<시작·본인 지정·VIEWER 수임자·없는 사용자는 전부 422 — 행 0"""
    delegator = make_user(RoleCode.TRADE)
    delegate = make_user(RoleCode.CERT)
    viewer = make_user(RoleCode.VIEWER)
    today = today_kst()
    cases = [
        _reg_body(delegate.id, start_on=(today - timedelta(days=1)).isoformat()),
        _reg_body(
            delegate.id, start_on=(today + timedelta(days=3)).isoformat(), end_on=today.isoformat()
        ),
        _reg_body(delegator.id),
        _reg_body(viewer.id),
        _reg_body(987654),
    ]
    with client_for(delegator) as client:
        for body in cases:
            assert client.post(DELEGATIONS, json=body, headers=idem()).status_code == 422, body
    assert count("delegations") == 0


def test_viewer_cannot_register_and_a_non_admin_cannot_register_for_someone_else() -> None:
    """VIEWER는 대결 등록 403, 비ADMIN이 타인을 위임자로 지정하면 403 — ADMIN은 부재자 대신 등록할 수 있다"""
    delegator, delegate = make_user(RoleCode.TRADE), make_user(RoleCode.CERT)
    with logged_in(RoleCode.VIEWER) as viewer:
        assert (
            viewer.post(DELEGATIONS, json=_reg_body(delegate.id), headers=idem()).status_code == 403
        )
    other = make_user(RoleCode.TRADE)
    with client_for(other) as client:
        forged = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, delegator_user_id=delegator.id), headers=idem()
        )
        assert forged.status_code == 403
    with logged_in(RoleCode.ADMIN) as admin:
        ok = admin.post(
            DELEGATIONS, json=_reg_body(delegate.id, delegator_user_id=delegator.id), headers=idem()
        )
        assert ok.status_code == 201 and ok.json()["delegator_user_id"] == delegator.id
    assert count("delegations") == 1


def test_overlapping_periods_conflict_but_adjacent_and_other_roles_coexist() -> None:
    """같은 (위임자·유형·역할)의 겹치는 기간은 409 — 인접(종료+1=시작)·종료된 행과의 겹침·다른 역할은 성공"""
    delegator = make_user(RoleCode.ADMIN)  # 모든 역할을 위임할 수 있다
    delegate = make_user(RoleCode.CERT)
    today = today_kst()
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    with client_for(delegator) as client:
        first = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, start_on=d(0), end_on=d(4)), headers=idem()
        )
        assert first.status_code == 201
        overlap = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, start_on=d(4), end_on=d(8)), headers=idem()
        )
        assert overlap.status_code == 409
        assert overlap.json()["error"]["code"] == "APPROVALS.DELEGATION.OVERLAP"
        inside = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, start_on=d(1), end_on=d(2)), headers=idem()
        )
        assert inside.status_code == 409
        adjacent = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, start_on=d(5), end_on=d(9)), headers=idem()
        )
        assert adjacent.status_code == 201
        other_role = client.post(
            DELEGATIONS,
            json=_reg_body(delegate.id, delegated_role="LOGISTICS", start_on=d(0), end_on=d(4)),
            headers=idem(),
        )
        assert other_role.status_code == 201
        revoked = client.post(f"{DELEGATIONS}/{first.json()['id']}/revoke", json={"version": 1})
        assert revoked.status_code == 200
        reuse = client.post(
            DELEGATIONS, json=_reg_body(delegate.id, start_on=d(0), end_on=d(4)), headers=idem()
        )
        assert reuse.status_code == 201  # 종료된 행과의 겹침은 허용


def test_early_revocation_takes_effect_immediately_and_is_authorised() -> None:
    """조기 종료는 위임자 본인·ADMIN만(타인 403) — 즉시 무효가 되고, 이미 종료된 건 재종료는 409, audit가 남는다"""
    approval_id, _, delegator = _setup()
    delegate = make_user(RoleCode.LOGISTICS)
    delegation_id = add_delegation(
        delegator, delegate, today_kst(), today_kst() + timedelta(days=5)
    )
    assert _authority(approval_id, delegate) is AuthorityKind.DELEGATED
    bystander = make_user(RoleCode.TRADE)
    with client_for(bystander) as client:
        assert (
            client.post(f"{DELEGATIONS}/{delegation_id}/revoke", json={"version": 1}).status_code
            == 403
        )
    with client_for(delegator) as client:
        stale = client.post(f"{DELEGATIONS}/{delegation_id}/revoke", json={"version": 9})
        assert stale.status_code == 409
        done = client.post(f"{DELEGATIONS}/{delegation_id}/revoke", json={"version": 1})
        assert done.status_code == 200 and done.json()["state"] == "REVOKED"
        again = client.post(f"{DELEGATIONS}/{delegation_id}/revoke", json={"version": 2})
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "APPROVALS.DELEGATION.NOT_ACTIVE"
    assert _authority(approval_id, delegate) is AuthorityKind.DENIED_NOT_APPROVER
    assert len(audit_actions("approvals.delegation.revoked")) == 1


def test_there_is_no_route_to_edit_a_delegation_period() -> None:
    """기간·당사자·범위를 고치는 라우트가 없다(종료 후 신규 등록) — 대결 경로의 메서드는 GET·POST뿐이고 `/delegations/{id}` 자체가 없다"""
    from app.main import app

    methods: dict[str, set[str]] = {}
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/v1/delegations"):
            methods[path] = set(operations)
    assert methods == {
        "/api/v1/delegations": {"get", "post"},
        "/api/v1/delegations/{delegation_id}/revoke": {"post"},
    }
    delegator, delegate = make_user(RoleCode.TRADE), make_user(RoleCode.CERT)
    delegation_id = add_delegation(
        delegator, delegate, today_kst(), today_kst() + timedelta(days=2)
    )
    with client_for(delegator) as client:
        for method in ("patch", "put", "delete"):
            response = client.request(method.upper(), f"{DELEGATIONS}/{delegation_id}", json={})
            assert response.status_code in (404, 405)


def test_only_admin_lists_all_delegations_and_mine_covers_both_sides() -> None:
    """scope=all은 ADMIN 전용 · scope=mine은 내가 위임했거나 수임한 건 모두"""
    delegator, delegate, bystander = (
        make_user(RoleCode.TRADE),
        make_user(RoleCode.CERT),
        make_user(RoleCode.TRADE),
    )
    add_delegation(delegator, delegate, today_kst(), today_kst() + timedelta(days=2))
    for user, expected in ((delegator, 1), (delegate, 1), (bystander, 0)):
        with client_for(user) as client:
            assert client.get(DELEGATIONS).json()["total"] == expected
            assert client.get(DELEGATIONS, params={"scope": "all"}).status_code == 403
    with logged_in(RoleCode.ADMIN) as admin:
        assert admin.get(DELEGATIONS, params={"scope": "all"}).json()["total"] == 1


# ── 수임자 후보 디렉터리 ─────────────────────────────────────────────────────────


def test_the_candidate_directory_exposes_only_id_and_display_name() -> None:
    """후보 응답의 키 집합이 정확히 {id, display_name}이다 — 이메일·역할·비활성 사유 미노출, 본인·비활성·VIEWER 제외, VIEWER 호출은 403"""
    me = make_user(RoleCode.TRADE, name="나")
    good = make_user(RoleCode.CERT, name="후보-김가나")
    make_user(RoleCode.VIEWER, name="후보-조회")
    make_user(RoleCode.LOGISTICS, active=False, name="후보-비활성")
    with client_for(me) as client:
        body = client.get("/api/v1/approvals/delegation-candidates", params={"q": "후보-"}).json()
        assert [i["id"] for i in body["items"]] == [good.id]
        assert all(set(i) == {"id", "display_name"} for i in body["items"])
        everyone = client.get(
            "/api/v1/approvals/delegation-candidates", params={"size": 200}
        ).json()
        assert me.id not in {i["id"] for i in everyone["items"]}
        assert (
            client.get("/api/v1/approvals/delegation-candidates", params={"q": "%"}).json()["total"]
            == 0
        )
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get("/api/v1/approvals/delegation-candidates").status_code == 403


def test_inert_delegates_are_neither_notified_nor_counted_as_eligible() -> None:
    """수임자가 비활성이거나 비조회 역할이 없으면(대결이 계산상 무효) 요청 알림 수신자·결재 자격자 집합에서 빠진다 — 유효한 수임자는 들어간다"""
    from app.core.db.uow import unit_of_work
    from app.modules.approvals.authority import eligible_user_ids, notification_recipients
    from app.modules.approvals.models import Approval

    approval_id, _, delegator = _setup()
    active_delegate = make_user(RoleCode.LOGISTICS)
    inactive_delegate = make_user(RoleCode.CERT)
    viewer_only_delegate = make_user(RoleCode.VIEWER)
    today = today_kst()
    # 같은 위임자·시작일의 미종료 대결은 하나뿐(DB 유니크)이라 수임자마다 다른 위임자(TRADE)를 쓴다
    add_delegation(delegator, active_delegate, today, today + timedelta(days=3))
    for delegate in (inactive_delegate, viewer_only_delegate):
        add_delegation(make_user(RoleCode.TRADE), delegate, today, today + timedelta(days=3))
    _deactivate(inactive_delegate.id)
    with unit_of_work() as uow:
        approval = uow.session.get(Approval, approval_id)
        assert approval is not None
        eligible = set(eligible_user_ids(uow.session, approval))
        recipients, fallback = notification_recipients(uow.session, approval)
    assert active_delegate.id in eligible and active_delegate.id in recipients
    for inert in (inactive_delegate, viewer_only_delegate):
        assert inert.id not in eligible and inert.id not in recipients
    assert fallback is False
