"""H. 운영 — 기일 알림 실 HTTP 대표 2건 (WBS S2-3 DoD / §20 H / S2-3 PR-2 안건 ② (f)).

★ 전수는 서비스 층(tests/integration/test_deadline_scan.py)이고, 여기서는 실
  HTTP로 두 가지만 본다(조건 C 준용):
  ① **동일 기일 2회 스캔 → 담당자 알림함에 중복 0**(DoD "동일 기일 중복 발송 0")
  ② **ack 후 재스캔 → 재발송 0**(§20 H "확인 알림 재발송 0") — ack는 행을
    지우지 않아 같은 키가 계속 점유된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.certifications.models import Certification
from app.modules.deadlines import service as deadlines
from app.modules.identity.models import RoleCode
from app.modules.requirements.models import RequirementTemplate
from tests.support.factories import DEFAULT_PASSWORD, create_market, create_sku, create_user

pytestmark = pytest.mark.group_h

LOGIN = "/api/v1/auth/login"
ALERTS = "/api/v1/alerts"
CERTIFICATIONS = "/api/v1/certifications"
BASE = date(2026, 7, 4)  # GC-C1 오늘 — 만료 2026-09-30 = D-88


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def owner() -> Iterator[TestClient]:
    yield from _client("deadline-owner@example.com", RoleCode.CERT)


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("deadline-admin@example.com", RoleCode.ADMIN)


def _user_id(client: TestClient) -> int:
    body = client.get("/api/v1/auth/me")
    assert body.status_code == 200, body.text
    return int(body.json()["id"])


def _inbox(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(ALERTS)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _approved_certification(client: TestClient, *, assignee_id: int) -> int:
    """담당자가 지정된 승인 인증(만료 2026-09-30) — 등록은 API, 만료일은 픽스처 주입."""
    with unit_of_work() as uow:
        template = RequirementTemplate(
            market_id=create_market("US", name_ko="미국"),
            name="US SKU 등록",
            applies_to="SKU",
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 6, 1),
            status="CONFIRMED",
        )
        uow.session.add(template)
        uow.session.flush()
        template_id = template.id
    created = client.post(
        CERTIFICATIONS,
        json={
            "template_id": template_id,
            "target_type": "SKU",
            "target_id": create_sku(),
            "assignee_id": assignee_id,
        },
        headers={"Idempotency-Key": "deadline-e2e"},
    )
    assert created.status_code == 201, created.text
    certification_id = int(created.json()["id"])
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Certification).where(Certification.id == certification_id)
        ).scalar_one()
        row.status = "APPROVED"
        row.approved_on = date(2026, 1, 1)
        row.valid_from = date(2026, 1, 1)
        row.expires_on = date(2026, 9, 30)
    return certification_id


def test_two_scans_of_the_same_deadline_send_once(owner: TestClient, admin: TestClient) -> None:
    """동일 기일 2회 스캔 → 담당자 알림함 D-180·D-90 각 1건, 관리자 0건(DoD)"""
    certification_id = _approved_certification(owner, assignee_id=_user_id(owner))

    deadlines.scan_deadlines(base_date=BASE)
    deadlines.scan_deadlines(base_date=BASE)

    inbox = _inbox(owner)
    assert len(inbox) == 2
    assert {item["dedup_key"].split(":")[3] for item in inbox} == {
        "D-180@2026-09-30",
        "D-90@2026-09-30",
    }
    assert all(item["entity_type"] == "certifications" for item in inbox)
    assert all(item["entity_id"] == certification_id for item in inbox)
    assert _inbox(admin) == []  # 담당자 지정 건은 담당자에게만 — 전사 폭포 0


def test_acknowledged_deadline_alert_is_not_resent(owner: TestClient, admin: TestClient) -> None:
    """ack 후 재스캔 → 재발송 0 (§20 H) — 미확인 수도 0으로 남는다"""
    _approved_certification(owner, assignee_id=_user_id(owner))
    deadlines.scan_deadlines(base_date=BASE)
    for item in _inbox(owner):
        assert owner.post(f"{ALERTS}/{item['id']}/ack").status_code == 200

    deadlines.scan_deadlines(base_date=BASE)

    inbox = _inbox(owner)
    assert len(inbox) == 2
    assert all(item["acknowledged_at"] is not None for item in inbox)
    assert owner.get(f"{ALERTS}/unread-count").json()["count"] == 0
