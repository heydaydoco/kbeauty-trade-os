"""C·K. 인증 인스턴스 API — 대표 쌍·권한·게이트 (WBS S2-2 DoD / GC-C9 / 조건 C).

★ 대표 쌍 소수만 실 HTTP로 본다(조건 C — 110쌍 전수는 서비스 층, 실 HTTP
  110회 금지). 이 파일의 몫: DoD "비정상 전이 API 호출 → 4xx"의 실 API 실측
  (GC-C9 양방향 — 골든), 권한 경계(판정 ⑫ — 편집=인증+관리자·열람=전 역할),
  생성 게이트 422, PATCH의 status 구조적 부재(422 — extra=forbid).
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
from app.modules.documents.models import Document, DocumentType
from app.modules.identity.models import RoleCode
from app.modules.requirements.models import RequirementTemplate
from tests.support.factories import DEFAULT_PASSWORD, create_market, create_sku, create_user

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
CERTIFICATIONS = "/api/v1/certifications"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("cert-e2e@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade-e2e@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer-e2e@example.com", RoleCode.VIEWER)


def _template(*, status: str = "CONFIRMED", name: str = "US SKU 등록") -> int:
    with unit_of_work() as uow:
        row = RequirementTemplate(
            market_id=create_market("US", name_ko="미국"),
            name=name,
            applies_to="SKU",
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 8, 1),
            status=status,
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _register(client: TestClient, *, key: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": overrides.pop("template_id", None) or _template(),
        "target_type": "SKU",
        "target_id": overrides.pop("target_id", None) or create_sku(),
    }
    payload.update(overrides)
    response = client.post(CERTIFICATIONS, json=payload, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


# ── GC-C9 — 비정상 전이 거부 (양방향, 골든) ─────────────────────────────────


@pytest.mark.golden
def test_gc_c9_rejects_a_jump_to_approved(cert: TestClient) -> None:
    """GC-C9 거부 방향 — 미착수→승인 직행은 409, 상태 불변·이력 무기록"""
    body = _register(cert, key="gc9-deny")
    response = cert.post(
        f"{CERTIFICATIONS}/{body['id']}/transitions",
        json={"to": "APPROVED", "version": body["version"]},
        headers={"Idempotency-Key": "gc9-deny-go"},
    )
    assert response.status_code == 409, response.text
    envelope = response.json()["error"]
    assert envelope["code"] == "CERTIFICATIONS.TRANSITION.NOT_ALLOWED"
    assert envelope["detail"] == {"from": "NOT_STARTED", "to": "APPROVED"}
    detail = cert.get(f"{CERTIFICATIONS}/{body['id']}").json()
    assert detail["status"] == "NOT_STARTED"  # 상태 불변
    log = cert.get(f"{CERTIFICATIONS}/{body['id']}/status-log").json()
    assert log["total"] == 0  # 이력 무기록


@pytest.mark.golden
def test_gc_c9_allows_the_legal_first_step(cert: TestClient) -> None:
    """GC-C9 성공 방향(자기검사) — 미착수→서류준비는 성공+이력 1행 자동"""
    body = _register(cert, key="gc9-ok")
    response = cert.post(
        f"{CERTIFICATIONS}/{body['id']}/transitions",
        json={"to": "PREPARING", "version": body["version"]},
        headers={"Idempotency-Key": "gc9-ok-go"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PREPARING"
    log = cert.get(f"{CERTIFICATIONS}/{body['id']}/status-log").json()
    assert log["total"] == 1
    entry = log["items"][0]
    assert (entry["from_status"], entry["to_status"]) == ("NOT_STARTED", "PREPARING")
    assert entry["actor_user_id"] is not None  # 사람 전이


# ── 미허용 대표·부속 필수 (DoD "비정상 전이 API 호출 → 4xx") ────────────────


def test_transition_to_renewing_from_start_is_denied(cert: TestClient) -> None:
    body = _register(cert, key="deny-renew")
    response = cert.post(
        f"{CERTIFICATIONS}/{body['id']}/transitions",
        json={"to": "RENEWING", "version": body["version"]},
        headers={"Idempotency-Key": "deny-renew-go"},
    )
    assert response.status_code == 409


def test_submission_without_applied_on_is_rejected(cert: TestClient) -> None:
    """허용 전이라도 부속 필수 결여는 422 — 같은 요청·같은 트랜잭션(§17.1)"""
    body = _register(cert, key="need-field")
    step = cert.post(
        f"{CERTIFICATIONS}/{body['id']}/transitions",
        json={"to": "PREPARING", "version": body["version"]},
        headers={"Idempotency-Key": "need-field-1"},
    )
    assert step.status_code == 200
    response = cert.post(
        f"{CERTIFICATIONS}/{body['id']}/transitions",
        json={"to": "SUBMITTED", "version": step.json()["version"]},
        headers={"Idempotency-Key": "need-field-2"},
    )
    assert response.status_code == 422


def test_patch_cannot_smuggle_status(cert: TestClient) -> None:
    """PATCH에 status를 실으면 422 — 구조적 부재+extra=forbid (#16 선례 전면화)"""
    body = _register(cert, key="smuggle")
    response = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}",
        json={"version": body["version"], "status": "APPROVED"},
    )
    assert response.status_code == 422
    detail = cert.get(f"{CERTIFICATIONS}/{body['id']}").json()
    assert detail["status"] == "NOT_STARTED"


# ── 생성 게이트 대표 (안건 ①) ───────────────────────────────────────────────


def test_create_from_draft_template_is_rejected(cert: TestClient) -> None:
    template_id = _template(status="DRAFT", name="US 초안 템플릿")
    response = cert.post(
        CERTIFICATIONS,
        json={"template_id": template_id, "target_type": "SKU", "target_id": create_sku()},
        headers={"Idempotency-Key": "gate-e2e"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CERTIFICATIONS.TEMPLATE.NOT_CONFIRMED"


# ── 권한 (판정 ⑫ — K) ──────────────────────────────────────────────────────


def test_trader_and_viewer_cannot_edit(
    cert: TestClient, trader: TestClient, viewer: TestClient
) -> None:
    body = _register(cert, key="perm")
    for client in (trader, viewer):
        denied_create = client.post(
            CERTIFICATIONS,
            json={"template_id": body["template_id"], "target_type": "SKU", "target_id": 1},
            headers={"Idempotency-Key": "perm-try"},
        )
        assert denied_create.status_code == 403
        denied_transition = client.post(
            f"{CERTIFICATIONS}/{body['id']}/transitions",
            json={"to": "PREPARING", "version": body["version"]},
            headers={"Idempotency-Key": "perm-try-2"},
        )
        assert denied_transition.status_code == 403


def test_every_role_can_read(cert: TestClient, trader: TestClient, viewer: TestClient) -> None:
    body = _register(cert, key="read")
    for client in (cert, trader, viewer):
        listing = client.get(CERTIFICATIONS)
        assert listing.status_code == 200
        assert listing.json()["total"] >= 1
        detail = client.get(f"{CERTIFICATIONS}/{body['id']}")
        assert detail.status_code == 200
        tasks = client.get(f"{CERTIFICATIONS}/{body['id']}/tasks")
        assert tasks.status_code == 200


# ── 태스크 서류 링크 (S2-2 PR-2 — §5.1 "서류 링크"·§5.6 공용 참조) ──────────


def _insert_link_document(*, owner_type: str = "SKU", owner_id: int = 1) -> int:
    """공용 참조 대상 문서 준비 — 등록 경로의 계약은 test_documents가 고정한다."""
    with unit_of_work() as uow:
        type_id = uow.session.execute(
            select(DocumentType.id).where(
                DocumentType.code == "CFS", DocumentType.deleted_at.is_(None)
            )
        ).scalar_one()
        row = Document(
            owner_type=owner_type,
            owner_id=owner_id,
            document_type_id=type_id,
            storage_kind="LINK",
            url="https://example.com/cfs.pdf",
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _add_task(client: TestClient, certification_id: int, *, key: str) -> dict[str, Any]:
    response = client.post(
        f"{CERTIFICATIONS}/{certification_id}/tasks",
        json={"seq": 1, "item_name": "CFS 준비", "is_required": True},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_task_links_and_unlinks_a_document(cert: TestClient) -> None:
    """서류 연결(양수)→해제(null) 왕복 — 3값 의미론의 두 변경 방향 실측"""
    body = _register(cert, key="doclink")
    task = _add_task(cert, body["id"], key="doclink-task")
    document_id = _insert_link_document(owner_id=body["target_id"])

    linked = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": task["version"], "document_id": document_id},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["document_id"] == document_id

    unlinked = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": linked.json()["version"], "document_id": None},
    )
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["document_id"] is None


def test_task_link_omission_keeps_the_document(cert: TestClient) -> None:
    """document_id 생략=미변경 — 체크 토글이 연결을 지우지 않는다(3값 의미론)"""
    body = _register(cert, key="dockeep")
    task = _add_task(cert, body["id"], key="dockeep-task")
    document_id = _insert_link_document(owner_id=body["target_id"])
    linked = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": task["version"], "document_id": document_id},
    )
    toggled = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": linked.json()["version"], "done": True},
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["done"] is True
    assert toggled.json()["document_id"] == document_id


def test_task_link_to_a_missing_document_is_rejected(cert: TestClient) -> None:
    body = _register(cert, key="docmiss")
    task = _add_task(cert, body["id"], key="docmiss-task")
    refused = cert.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": task["version"], "document_id": 999_999},
    )
    assert refused.status_code == 422, refused.text
    assert "존재하지 않는 문서" in refused.json()["error"]["detail"]["document_id"]


def test_task_link_edit_is_denied_to_non_editors(cert: TestClient, viewer: TestClient) -> None:
    body = _register(cert, key="docperm")
    task = _add_task(cert, body["id"], key="docperm-task")
    document_id = _insert_link_document(owner_id=body["target_id"])
    denied = viewer.patch(
        f"{CERTIFICATIONS}/{body['id']}/tasks/{task['id']}",
        json={"version": task["version"], "document_id": document_id},
    )
    assert denied.status_code == 403, denied.text
