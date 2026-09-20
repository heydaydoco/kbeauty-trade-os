"""B·C — 문서 soft delete × 인증 태스크 서류 링크 (S2-3 판정 요청 19 (나)).

★ 세 가지를 실 HTTP로 본다: ① 링크 중 삭제 → 409 `DOCUMENTS.DOCUMENT.LINKED_TO_TASK`
  ② 링크 해제(null) 후 삭제 → 204 ③ 스캔 활성 한정 — 삭제된 문서는 기일 스캔
  후보 밖(링크가 있었다면 애초에 삭제가 안 되므로 "링크×삭제 잔존 조합"은 0).
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
from app.modules.certifications.models import CertificationTask
from app.modules.deadlines import service as deadlines
from app.modules.identity.models import RoleCode
from app.modules.requirements.models import RequirementTemplate
from tests.support.factories import DEFAULT_PASSWORD, create_market, create_sku, create_user

pytestmark = pytest.mark.group_b

LOGIN = "/api/v1/auth/login"
CERTIFICATIONS = "/api/v1/certifications"
DOCUMENTS = "/api/v1/documents"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("doclink-cert@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("doclink-trade@example.com", RoleCode.TRADE)


@pytest.fixture
def linked(cert: TestClient) -> dict[str, Any]:
    """인증 1건 + 태스크 1건 + SKU 소유 LINK 문서(유효기간 있음) 연결 상태."""
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
    sku_id = create_sku()
    created = cert.post(
        CERTIFICATIONS,
        json={"template_id": template_id, "target_type": "SKU", "target_id": sku_id},
        headers={"Idempotency-Key": "doclink-cert"},
    )
    assert created.status_code == 201, created.text
    certification_id = created.json()["id"]
    task = cert.post(
        f"{CERTIFICATIONS}/{certification_id}/tasks",
        json={"seq": 1, "item_name": "CFS 준비", "is_required": True},
        headers={"Idempotency-Key": "doclink-task"},
    )
    assert task.status_code == 201, task.text
    document = cert.post(
        f"{DOCUMENTS}/links",
        json={
            "owner_type": "SKU",
            "owner_id": sku_id,
            "document_type": "CFS",
            "url": "https://example.com/cfs.pdf",
            "issued_on": "2026-01-01",
            "valid_until": "2026-12-31",
        },
        headers={"Idempotency-Key": "doclink-doc"},
    )
    assert document.status_code == 201, document.text
    linked = cert.patch(
        f"{CERTIFICATIONS}/{certification_id}/tasks/{task.json()['id']}",
        json={"version": task.json()["version"], "document_id": document.json()["id"]},
    )
    assert linked.status_code == 200, linked.text
    return {
        "certification_id": certification_id,
        "task": linked.json(),
        "document_id": document.json()["id"],
    }


def test_deleting_a_linked_document_is_refused(cert: TestClient, linked: dict[str, Any]) -> None:
    """링크 중 삭제 → 409 LINKED_TO_TASK, 문서·링크는 그대로(요청 19 (나))"""
    refused = cert.delete(f"{DOCUMENTS}/{linked['document_id']}")
    assert refused.status_code == 409, refused.text
    body = refused.json()["error"]
    assert body["code"] == "DOCUMENTS.DOCUMENT.LINKED_TO_TASK"
    assert body["detail"]["task_ids"] == [linked["task"]["id"]]
    assert cert.get(f"{DOCUMENTS}/{linked['document_id']}").status_code == 200
    with unit_of_work() as uow:
        still = uow.session.execute(
            select(CertificationTask.document_id).where(
                CertificationTask.id == linked["task"]["id"]
            )
        ).scalar_one()
    assert still == linked["document_id"]


def test_trader_cannot_bypass_the_guard(trader: TestClient, linked: dict[str, Any]) -> None:
    """무역 역할(문서 삭제 권한 보유)도 링크된 문서는 못 지운다 — 교차 지점 봉쇄"""
    refused = trader.delete(f"{DOCUMENTS}/{linked['document_id']}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "DOCUMENTS.DOCUMENT.LINKED_TO_TASK"


def test_unlink_then_delete_succeeds(cert: TestClient, linked: dict[str, Any]) -> None:
    """선해제(null) 후 삭제 → 204, 이후 스캔 후보 밖(활성 한정)"""
    unlinked = cert.patch(
        f"{CERTIFICATIONS}/{linked['certification_id']}/tasks/{linked['task']['id']}",
        json={"version": linked["task"]["version"], "document_id": None},
    )
    assert unlinked.status_code == 200, unlinked.text
    deleted = cert.delete(f"{DOCUMENTS}/{linked['document_id']}")
    assert deleted.status_code == 204, deleted.text
    assert cert.get(f"{DOCUMENTS}/{linked['document_id']}").status_code == 404
    assert deadlines.scan_deadlines(base_date=date(2026, 7, 4))["documents"] == 0


def test_a_removed_task_releases_the_document(cert: TestClient, linked: dict[str, Any]) -> None:
    """태스크 자체를 제거(soft delete)해도 링크가 풀린 것과 같다 — 활성 링크만 센다"""
    removed = cert.delete(
        f"{CERTIFICATIONS}/{linked['certification_id']}/tasks/{linked['task']['id']}"
    )
    assert removed.status_code == 204, removed.text
    assert cert.delete(f"{DOCUMENTS}/{linked['document_id']}").status_code == 204


def test_linked_document_stays_in_the_deadline_scan(
    cert: TestClient, linked: dict[str, Any]
) -> None:
    """링크된 활성 문서는 스캔 대상 — 삭제 차단이 곧 유효기간 알림의 보존이다"""
    counts = deadlines.scan_deadlines(base_date=date(2026, 7, 4))  # D-180 지남
    assert counts["documents"] == 1
