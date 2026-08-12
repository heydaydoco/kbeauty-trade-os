"""K. 보안·품질 — 알림 규칙 권한·알림함 소유권·목록 계약 (§18.1·§18.4 / 요청 10).

★ 알림함은 개인 수신함이다 — 남의 알림을 확인 처리하면 그 사람 대신 "봤다"고
  기록되는 셈이라, ADR-07의 재발송 금지 기준이 남의 손에 넘어간다.
★ 규칙 관리가 관리자 전용인 이유(판정 요청 10): 수신자·채널 결정은 조직 관리
  계열이고, 기일 계열의 관리자 폴백 게이트가 이 규칙에 걸린다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing
from app.modules.worklist.models import Alert
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
ALERTS = "/api/v1/alerts"
RULES = "/api/v1/alert-rules"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("rules-admin@example.com", RoleCode.ADMIN)


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("rules-cert@example.com", RoleCode.CERT)


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "code": "CERT-EXPIRING",
        "name_ko": "인증 만료 임박",
        "event_type": "certifications.certification.status_changed",
        "severity": "WARN",
    }
    body.update(overrides)
    return body


def _seed_alert_for(user_id: int, *, key: str = "seed:tasks:1:D-30") -> int:
    with unit_of_work() as uow:
        created = notifications.notify(
            uow.session,
            subject_key=key,
            title="기일 임박",
            assignee_id=user_id,
            routing=Routing.DEADLINE,
        )
        assert len(created) == 1
        return created[0]


# ── 규칙 CRUD 권한 (요청 10) ─────────────────────────────────────────────────


def test_an_admin_can_manage_rules(admin: TestClient) -> None:
    created = admin.post(RULES, json=_payload(), headers={"Idempotency-Key": "rule-1"})
    assert created.status_code == 201, created.text
    rule = created.json()

    listed = admin.get(RULES)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert {"items", "total", "page", "size"} <= set(listed.json())  # Page 봉투(§18.4)

    edited = admin.patch(
        f"{RULES}/{rule['id']}", json={"version": rule["version"], "is_enabled": False}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["is_enabled"] is False

    removed = admin.delete(f"{RULES}/{rule['id']}", params={"version": edited.json()["version"]})
    assert removed.status_code == 204, removed.text
    assert admin.get(RULES).json()["total"] == 0


def test_a_non_admin_cannot_touch_rules(admin: TestClient, cert: TestClient) -> None:
    created = admin.post(RULES, json=_payload(), headers={"Idempotency-Key": "rule-perm"})
    rule = created.json()

    assert (
        cert.post(RULES, json=_payload(code="X"), headers={"Idempotency-Key": "x"}).status_code
        == 403
    )
    assert cert.get(RULES).status_code == 403
    assert (
        cert.patch(f"{RULES}/{rule['id']}", json={"version": 1, "is_enabled": False}).status_code
        == 403
    )
    assert cert.delete(f"{RULES}/{rule['id']}", params={"version": 1}).status_code == 403


def test_a_stale_version_is_refused(admin: TestClient) -> None:
    """§17.2 낙관 잠금 — 규칙도 사람이 화면에서 고치는 마스터다"""
    rule = admin.post(RULES, json=_payload(), headers={"Idempotency-Key": "rule-ver"}).json()
    admin.patch(f"{RULES}/{rule['id']}", json={"version": rule["version"], "name_ko": "변경"})
    stale = admin.patch(
        f"{RULES}/{rule['id']}", json={"version": rule["version"], "name_ko": "다시"}
    )
    assert stale.status_code == 409, stale.text


def test_unknown_fields_are_refused(admin: TestClient) -> None:
    """모르는 필드는 조용한 무시가 아니라 422 (신규 모듈이 처음부터 forbid)"""
    refused = admin.post(
        RULES, json=_payload(recipient_everyone=True), headers={"Idempotency-Key": "rule-forbid"}
    )
    assert refused.status_code == 422, refused.text


def test_the_same_key_replays_the_first_result(admin: TestClient) -> None:
    first = admin.post(RULES, json=_payload(), headers={"Idempotency-Key": "rule-idem"})
    second = admin.post(RULES, json=_payload(), headers={"Idempotency-Key": "rule-idem"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert admin.get(RULES).json()["total"] == 1


# ── 알림함 소유권 (§18.1) ────────────────────────────────────────────────────


def test_an_alert_belongs_to_its_recipient(admin: TestClient, cert: TestClient) -> None:
    """남의 알림은 목록에도 없고 확인 처리도 403 — 관리자도 예외가 아니다"""
    with unit_of_work() as uow:
        recipient = uow.session.execute(
            select(Alert.recipient_user_id).limit(1)
        ).scalar_one_or_none()
    assert recipient is None  # 준비 상태 확인

    me = cert.get("/api/v1/auth/me").json()["id"]
    alert_id = _seed_alert_for(me)

    assert cert.get(ALERTS).json()["total"] == 1
    assert admin.get(ALERTS).json()["total"] == 0
    denied = admin.post(f"{ALERTS}/{alert_id}/ack")
    assert denied.status_code == 403, denied.text


def test_acking_a_missing_alert_is_404(cert: TestClient) -> None:
    assert cert.post(f"{ALERTS}/999999/ack").status_code == 404


def test_the_alert_list_is_paginated(cert: TestClient) -> None:
    """§18.4 — 목록은 Page 봉투이고 상한을 넘기면 422"""
    me = cert.get("/api/v1/auth/me").json()["id"]
    for index in range(3):
        _seed_alert_for(me, key=f"seed:tasks:{index}:D-30")

    page = cert.get(ALERTS, params={"page": 1, "size": 2})
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert cert.get(ALERTS, params={"size": 500}).status_code == 422


def test_unacknowledged_filter_narrows_the_list(cert: TestClient) -> None:
    me = cert.get("/api/v1/auth/me").json()["id"]
    first = _seed_alert_for(me, key="seed:tasks:a:D-30")
    _seed_alert_for(me, key="seed:tasks:b:D-30")
    cert.post(f"{ALERTS}/{first}/ack")

    filtered = cert.get(ALERTS, params={"unacknowledged_only": True})
    assert filtered.json()["total"] == 1
