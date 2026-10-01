"""A·G·H. 승인·결재선 HTTP 계약 — 권한·멱등·낙관 잠금·결재함·IDOR·서버 계산 필드 (S3-1 PR-9a / design-C C3·C7·C8).

요청 생성은 HTTP에 없다(`POST /approvals`는 라우트가 존재하지 않는다 — 소비 전표 엔드포인트가 PR-12에서 서비스를 부른다). 그래서 승인은 서비스로
만들고(요청자 = 합성 사용자) 결정·조회는 **실 HTTP**로 한다 — ADMIN 토큰으로 자기 기안을 결정해도 서비스 SoD가 막는지(라우트 게이트와 층이 다르다)가 핵심이다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.models import RoleCode
from tests.factories.approvals import (
    add_line,
    audit_actions,
    client_for,
    count,
    credit_so,
    events_of,
    make_user,
    request,
)
from tests.factories.trade import idem, logged_in, unique

pytestmark = pytest.mark.group_a

LINES = "/api/v1/approval-lines"
APPROVALS = "/api/v1/approvals"


def _line_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "approval_type": "SO_CREDIT_EXCEEDED",
        "threshold": "0",
        "currency": "USD",
        "approver_role": "TRADE",
    }
    body.update(overrides)
    return body


def _pending() -> tuple[int, Any, Any]:
    """(승인 id, 기안자, 결재 자격 TRADE) — 임계 0 결재선·REQUESTED 승인 1건."""
    so = credit_so()
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    requester = make_user(RoleCode.TRADE)
    approver = make_user(RoleCode.TRADE)
    return int(request(so["id"], requester).approval.id), requester, approver


# ── 결재선 ───────────────────────────────────────────────────────────────────


def test_admin_manages_approval_lines_with_audit_and_optimistic_lock() -> None:
    """ADMIN이 등록·수정(역할·메모만)·삭제 — 각각 audit가 남고 version 불일치는 409"""
    with logged_in(RoleCode.ADMIN) as admin:
        created = admin.post(
            LINES, json=_line_body(threshold="1000.50", note="기본"), headers=idem()
        )
        assert created.status_code == 201, created.text
        line = created.json()
        assert line["threshold_amount"] == 100_050 and line["threshold_currency"] == "USD"
        assert line["threshold_text"] == "1000.50" and line["version"] == 1

        patched = admin.patch(
            f"{LINES}/{line['id']}", json={"version": 1, "approver_role": "ADMIN", "note": None}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["approver_role"] == "ADMIN" and patched.json()["note"] is None
        assert patched.json()["version"] == 2
        stale = admin.patch(f"{LINES}/{line['id']}", json={"version": 1, "approver_role": "CERT"})
        assert stale.status_code == 409

        deleted = admin.delete(f"{LINES}/{line['id']}", params={"version": 2})
        assert deleted.status_code == 204
        assert admin.get(LINES).json()["total"] == 0
    assert len(audit_actions("approvals.line.created")) == 1
    assert len(audit_actions("approvals.line.updated")) == 1
    assert len(audit_actions("approvals.line.deleted")) == 1
    assert audit_actions("approvals.line.created")[0]["detail"]["threshold_amount"] == 100_050


def test_the_threshold_type_and_currency_cannot_be_patched() -> None:
    """PATCH는 역할·메모만 — 유형·통화·임계 필드는 스키마가 거부한다(422). 변경은 삭제+신규"""
    with logged_in(RoleCode.ADMIN) as admin:
        line = admin.post(LINES, json=_line_body(), headers=idem()).json()
        for field, value in (("threshold", "5"), ("currency", "KRW"), ("approval_type", "X")):
            response = admin.patch(f"{LINES}/{line['id']}", json={"version": 1, field: value})
            assert response.status_code == 422, field


def test_duplicate_threshold_is_a_409_with_the_dedicated_code() -> None:
    """같은 (유형·통화·임계) 두 번째 등록은 409 APPROVALS.LINE.DUPLICATE — 500이 아니다. 삭제 후 재등록은 신규로 성공"""
    with logged_in(RoleCode.ADMIN) as admin:
        first = admin.post(LINES, json=_line_body(threshold="100"), headers=idem())
        assert first.status_code == 201
        second = admin.post(
            LINES, json=_line_body(threshold="100", approver_role="ADMIN"), headers=idem()
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "APPROVALS.LINE.DUPLICATE"
        admin.delete(f"{LINES}/{first.json()['id']}", params={"version": 1})
        again = admin.post(LINES, json=_line_body(threshold="100"), headers=idem())
        assert again.status_code == 201


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"threshold": "-1"}, "음수"),
        ({"threshold": "12.345"}, "USD 소수 3자리(반올림 금지)"),
        ({"currency": "ZZZ"}, "미등록 통화"),
        ({"approver_role": "VIEWER"}, "조회 역할은 결재할 수 없다"),
        ({"approval_type": "NEW_PARTNER"}, "소비 세션이 없는 유형은 만들지 않는다"),
        ({"threshold": "abc"}, "숫자 아님"),
        ({"status": "APPROVED"}, "알 수 없는 필드(extra forbid)"),
    ],
)
def test_invalid_line_requests_are_422(overrides: dict[str, Any], why: str) -> None:
    """결재선 요청 검증 — 형식·통화·역할·유형·여분 필드는 전부 422(저장 0)"""
    with logged_in(RoleCode.ADMIN) as admin:
        response = admin.post(LINES, json=_line_body(**overrides), headers=idem())
        assert response.status_code == 422, why
    assert count("approval_lines") == 0


def test_line_creation_replays_the_first_result_for_the_same_key() -> None:
    """같은 Idempotency-Key 재전송은 최초 결과를 돌려주고 행·audit를 늘리지 않는다"""
    with logged_in(RoleCode.ADMIN) as admin:
        headers = idem()
        first = admin.post(LINES, json=_line_body(), headers=headers)
        again = admin.post(LINES, json=_line_body(), headers=headers)
        assert first.status_code == again.status_code == 201
        assert first.json()["id"] == again.json()["id"]
    assert count("approval_lines") == 1 and len(audit_actions("approvals.line.created")) == 1


@pytest.mark.parametrize(
    "role", [RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER]
)
def test_only_admin_writes_lines_and_viewer_cannot_even_read(role: RoleCode) -> None:
    """결재선 쓰기는 ADMIN 전용(그 밖 403) — 조회는 비조회 4역할, VIEWER는 조회도 403"""
    with logged_in(role) as client:
        assert client.post(LINES, json=_line_body(), headers=idem()).status_code == 403
        assert client.patch(f"{LINES}/1", json={"version": 1}).status_code == 403
        assert client.delete(f"{LINES}/1", params={"version": 1}).status_code == 403
        expected = 403 if role is RoleCode.VIEWER else 200
        assert client.get(LINES).status_code == expected
        assert client.get(f"{LINES}/coverage").status_code == expected
    assert count("approval_lines") == 0


def test_coverage_says_unconfigured_means_no_confirmation() -> None:
    """등록 0행이면 coverage가 '여신 초과 수주는 확정할 수 없습니다'를 안내하고, 임계 0이 없는 통화는 별도 경고한다"""
    with logged_in(RoleCode.TRADE) as client:
        empty = client.get(f"{LINES}/coverage").json()
        assert empty["configured"] is False
        assert any("확정할 수 없습니다" in m for m in empty["messages"])
    add_line(5_000_000, currency="KRW")
    with logged_in(RoleCode.TRADE) as client:
        partial = client.get(f"{LINES}/coverage").json()
        assert partial["configured"] is True
        assert any("KRW" in m and "임계 0" in m for m in partial["messages"])


def test_line_list_is_paginated_with_the_default_of_50() -> None:
    """결재선 목록은 페이지 봉투(기본 50)다"""
    for i in range(3):
        add_line(i * 1000)
    with logged_in(RoleCode.CERT) as client:
        body = client.get(LINES).json()
        assert body["size"] == 50 and body["total"] == 3 and len(body["items"]) == 3
        assert [i["threshold_amount"] for i in body["items"]] == [0, 1000, 2000]
        assert len(client.get(LINES, params={"size": 2}).json()["items"]) == 2


# ── 승인 — 결정·결재함·IDOR·서버 계산 필드 ───────────────────────────────────────


def test_there_is_no_http_route_to_create_an_approval() -> None:
    """승인 요청 생성 라우트는 없다(위조 표면 제거) — POST /approvals는 405, 어떤 필드로도 행이 생기지 않는다"""
    with logged_in(RoleCode.ADMIN) as admin:
        response = admin.post(
            APPROVALS,
            json={"approval_type": "SO_CREDIT_EXCEEDED", "target_id": 1, "basis_amount": 1},
            headers=idem(),
        )
        assert response.status_code == 405
    assert count("approvals") == 0


def test_an_eligible_approver_decides_over_http_and_the_requester_is_notified() -> None:
    """결재 자격자가 HTTP로 승인 → 200 ApprovalView(APPROVED) — 기안자에게 결과 알림 1건"""
    approval_id, requester, approver = _pending()
    with client_for(approver) as client:
        view = client.get(f"{APPROVALS}/{approval_id}").json()
        assert view["can_decide"] is True and view["can_withdraw"] is False
        response = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "APPROVE", "version": view["version"]},
            headers=idem(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "APPROVED" and body["can_decide"] is False
        assert body["decided_by_name"] is not None
    assert (
        count(
            "alerts",
            "recipient_user_id = :u AND dedup_key LIKE 'approval:%:approved:%'",
            u=requester.id,
        )
        == 1
    )


def test_an_admin_token_cannot_approve_their_own_request_over_http() -> None:
    """ADMIN 토큰으로 자기 기안을 결정하면 라우트 게이트는 통과하지만 서비스가 403 SELF_APPROVAL로 막는다 — can_decide=false"""
    so = credit_so()
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    admin_user = make_user(RoleCode.ADMIN)
    approval_id = int(request(so["id"], admin_user).approval.id)
    with client_for(admin_user) as admin:
        view = admin.get(f"{APPROVALS}/{approval_id}").json()
        assert view["can_decide"] is False and view["decide_blocked_reason"] == "SELF"
        assert view["can_withdraw"] is True  # 자기 기안은 회수할 수 있다
        for verb in ("APPROVE", "REJECT"):
            response = admin.post(
                f"{APPROVALS}/{approval_id}/decisions",
                json={"verb": verb, "reason": "자기 결재", "version": view["version"]},
                headers=idem(),
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "APPROVALS.DECISION.SELF_APPROVAL"
    assert len(events_of(approval_id)) == 1  # 상태 불변
    assert len(audit_actions("approvals.decision.denied")) == 2  # 실패도 커밋 — 막힌 시도가 남는다


def test_decision_replay_returns_the_first_result_without_a_second_event() -> None:
    """같은 Idempotency-Key 재전송은 최초 응답을 그대로 돌려주고 이력·알림을 늘리지 않는다. 같은 키·다른 본문은 409"""
    approval_id, requester, approver = _pending()
    with client_for(approver) as client:
        headers = idem()
        payload = {"verb": "APPROVE", "version": 1}
        first = client.post(f"{APPROVALS}/{approval_id}/decisions", json=payload, headers=headers)
        again = client.post(f"{APPROVALS}/{approval_id}/decisions", json=payload, headers=headers)
        assert first.status_code == again.status_code == 200
        assert first.json() == again.json()
        changed = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "REJECT", "reason": "다른 내용", "version": 1},
            headers=headers,
        )
        assert changed.status_code == 409
    assert len(events_of(approval_id)) == 2
    assert count("alerts", "recipient_user_id = :u", u=requester.id) == 1


def test_a_different_key_for_the_same_decision_is_a_transition_conflict() -> None:
    """다른 키로 같은 결정을 다시 보내면 409 TRANSITION.NOT_ALLOWED(이미 승인됨) — 이중 결정 없음"""
    approval_id, _, approver = _pending()
    with client_for(approver) as client:
        ok = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "APPROVE", "version": 1},
            headers=idem(),
        )
        assert ok.status_code == 200
        dup = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "APPROVE", "version": 2},
            headers=idem(),
        )
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "APPROVALS.TRANSITION.NOT_ALLOWED"


def test_the_decision_requires_an_idempotency_key_and_a_known_verb() -> None:
    """Idempotency-Key 필수(없으면 400), 알 수 없는 동사·여분 필드는 422"""
    approval_id, _, approver = _pending()
    with client_for(approver) as client:
        url = f"{APPROVALS}/{approval_id}/decisions"
        assert client.post(url, json={"verb": "APPROVE", "version": 1}).status_code == 400
        assert (
            client.post(url, json={"verb": "AUTO", "version": 1}, headers=idem()).status_code == 422
        )
        assert (
            client.post(
                url, json={"verb": "APPROVE", "version": 1, "force": True}, headers=idem()
            ).status_code
            == 422
        )
        assert (
            client.post(
                url, json={"verb": "APPROVE", "version": 1, "status": "APPROVED"}, headers=idem()
            ).status_code
            == 422
        )


def test_the_inbox_shows_only_what_the_user_can_decide() -> None:
    """결재함: 자격자에게는 보이고, 기안자(ADMIN 포함)·다른 역할·VIEWER에게는 안 보인다 — 배지 건수와 일치"""
    approval_id, requester, approver = _pending()
    other_role = make_user(RoleCode.LOGISTICS)
    admin_user = make_user(RoleCode.ADMIN)
    admin_requester = make_user(RoleCode.ADMIN)
    so2 = credit_so()
    admin_request = int(request(so2["id"], admin_requester).approval.id)

    def inbox(user: Any) -> set[int]:
        with client_for(user) as client:
            ids = {
                i["id"] for i in client.get(APPROVALS, params={"scope": "inbox"}).json()["items"]
            }
            assert client.get(f"{APPROVALS}/inbox-count").json()["count"] == len(ids)
            return ids

    assert inbox(approver) == {approval_id, admin_request}  # TRADE 결재 자격자 — 두 건 다 결정 가능
    assert inbox(requester) == {admin_request}  # 자기 기안(approval_id)은 빠진다
    assert inbox(other_role) == set()  # LOGISTICS는 TRADE 결재 자격이 없다
    assert inbox(admin_user) == {approval_id, admin_request}  # 활성 ADMIN은 모든 구간의 자격자
    assert inbox(admin_requester) == {approval_id}  # ADMIN 기안자의 결재함에 자기 기안은 없다
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get(APPROVALS).status_code == 403


def test_mine_and_all_scopes() -> None:
    """mine은 본인 기안만, all은 ADMIN 전용(그 밖 403)"""
    approval_id, requester, approver = _pending()
    with client_for(requester) as client:
        mine = client.get(APPROVALS, params={"scope": "mine"}).json()
        assert [i["id"] for i in mine["items"]] == [approval_id]
        assert client.get(APPROVALS, params={"scope": "all"}).status_code == 403
    with client_for(approver) as client:
        assert client.get(APPROVALS, params={"scope": "mine"}).json()["total"] == 0
    with logged_in(RoleCode.ADMIN) as admin:
        assert admin.get(APPROVALS, params={"scope": "all"}).json()["total"] == 1
        assert (
            admin.get(APPROVALS, params={"scope": "all", "status": "APPROVED"}).json()["total"] == 0
        )


def test_detail_and_events_are_visible_only_to_related_people() -> None:
    """상세·이력: 기안자·결재 자격자·ADMIN만 본다 — 무관한 사용자(다른 역할)는 403(IDOR), 없는 id는 404"""
    approval_id, requester, approver = _pending()
    outsider = make_user(RoleCode.CERT)
    for user, expected in ((requester, 200), (approver, 200), (outsider, 403)):
        with client_for(user) as client:
            assert client.get(f"{APPROVALS}/{approval_id}").status_code == expected
            assert client.get(f"{APPROVALS}/{approval_id}/events").status_code == expected
    with logged_in(RoleCode.ADMIN) as admin:
        assert admin.get(f"{APPROVALS}/{approval_id}").status_code == 200
        assert admin.get(f"{APPROVALS}/999999").status_code == 404
        events = admin.get(f"{APPROVALS}/{approval_id}/events").json()
        assert events["total"] == 1 and events["items"][0]["to_status"] == "REQUESTED"


def test_a_user_cannot_decide_an_approval_they_are_not_eligible_for() -> None:
    """무관한 역할(CERT)은 결정 라우트를 통과해도 서비스가 404로 막는다(존재 여부·상태 비노출)"""
    approval_id, _, _ = _pending()
    outsider = make_user(RoleCode.CERT)
    with client_for(outsider) as client:
        response = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "APPROVE", "version": 1},
            headers=idem(),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "COMMON.RESOURCE.NOT_FOUND"
        assert response.json()["error"]["detail"] == {}
        missing = client.post(
            f"{APPROVALS}/999999/decisions", json={"verb": "APPROVE", "version": 1}, headers=idem()
        )
        assert missing.status_code == 404 and missing.json()["error"] == {
            **response.json()["error"],
            "request_id": missing.json()["error"]["request_id"],
        }  # 없는 id와 구별되지 않는다


def test_reject_needs_a_reason_over_http_and_withdraw_by_the_requester() -> None:
    """반려는 사유 필수(422 REASON_REQUIRED) · 기안자 본인 회수 성공 · 상태 배지용 status_reason이 서버 문구로 나간다"""
    approval_id, requester, approver = _pending()
    with client_for(approver) as client:
        url = f"{APPROVALS}/{approval_id}/decisions"
        missing = client.post(url, json={"verb": "REJECT", "version": 1}, headers=idem())
        assert missing.status_code == 422
        assert missing.json()["error"]["code"] == "APPROVALS.TRANSITION.REASON_REQUIRED"
    with client_for(requester) as client:
        withdrawn = client.post(
            f"{APPROVALS}/{approval_id}/decisions",
            json={"verb": "WITHDRAW", "reason": "금액 정정 예정", "version": 1},
            headers=idem(),
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "WITHDRAWN"
        assert withdrawn.json()["status_reason"] == "금액 정정 예정"


def test_response_never_carries_cost_or_secret_keys() -> None:
    """승인 응답·스냅샷·이력에 원가·마진·비밀 계열 키가 없다(여신 노출은 판매금액 합이라 마스킹 비대상이지만 원가는 구조적으로 부재)"""
    approval_id, _, approver = _pending()
    from app.core.logging.redaction import is_sensitive_key

    def keys(node: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(key)
                found |= keys(value)
        elif isinstance(node, list):
            for item in node:
                found |= keys(item)
        return found

    with client_for(approver) as client:
        for url in (f"{APPROVALS}/{approval_id}", f"{APPROVALS}/{approval_id}/events", APPROVALS):
            assert not [k for k in keys(client.get(url).json()) if is_sensitive_key(k)]


def test_list_envelope_and_page_size_limits() -> None:
    """승인 목록은 페이지 봉투(기본 50·최대 200)다 — 200 초과는 422"""
    _pending()
    with logged_in(RoleCode.ADMIN) as admin:
        body = admin.get(APPROVALS, params={"scope": "all"}).json()
        assert set(body) == {"items", "total", "page", "size"} and body["size"] == 50
        assert admin.get(APPROVALS, params={"scope": "all", "size": 201}).status_code == 422
        assert admin.get(APPROVALS, params={"scope": "bogus"}).status_code == 422


def test_unique_headers_are_not_shared_between_requests() -> None:
    """(자기검사) idem()은 요청마다 다른 키를 만든다 — 위 재전송 시나리오가 키 충돌로 오염되지 않는다"""
    assert idem() != idem()
    assert unique("x") != unique("x")
    assert TestClient is not None


def test_list_queries_do_not_grow_with_the_number_of_rows() -> None:
    """목록(결재함·전체)의 SQL 문 수는 행 수와 무관하다 — 행 1개일 때와 8개일 때 같다(N+1 없음, §22 렌즈 7)"""
    from tests.support.sqlcount import count_statements

    approver = make_user(RoleCode.TRADE)
    _pending()

    def measure() -> tuple[int, int]:
        with client_for(approver) as client, logged_in(RoleCode.ADMIN) as admin:
            inbox = count_statements(lambda: client.get(APPROVALS, params={"scope": "inbox"}))
            everything = count_statements(lambda: admin.get(APPROVALS, params={"scope": "all"}))
            return inbox, everything

    one = measure()
    assert min(one) > 0  # 빈 측정 가드
    for _ in range(7):
        _pending()
    many = measure()
    assert many == one, (one, many)
    with client_for(approver) as client:
        assert client.get(APPROVALS, params={"scope": "inbox"}).json()["total"] == 8
