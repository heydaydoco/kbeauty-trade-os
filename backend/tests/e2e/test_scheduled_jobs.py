"""K. 보안·품질 — 배치 레지스트리 화면 계약 (§15 관리 화면 노출 / 요청 14).

★ 화면에서 가능한 것은 목록 열람과 켜기·끄기뿐이다. 스케줄 문자열 편집을 열지
  않는 이유: 문법이 폐쇄 2형이라 잘못 적으면 그 잡이 조용히 안 돌고, 그 침묵은
  다음 도과 때까지 드러나지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
JOBS = "/api/v1/scheduled-jobs"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("jobs-view-admin@example.com", RoleCode.ADMIN)


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("jobs-view-cert@example.com", RoleCode.CERT)


def test_the_registry_is_visible_to_admins(admin: TestClient) -> None:
    scheduler.register_jobs()
    listed = admin.get(JOBS)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 2
    codes = {item["code"] for item in body["items"]}
    assert codes == {"certification-sweep", "outbox-dispatch"}
    # 등록만 되고 안 도는 잡을 화면이 구분해 보여 준다.
    assert all(item["is_mapped"] for item in body["items"])


def test_only_admins_see_the_registry(cert: TestClient) -> None:
    assert cert.get(JOBS).status_code == 403


def test_an_admin_can_disable_a_job(admin: TestClient) -> None:
    scheduler.register_jobs()
    job = admin.get(JOBS).json()["items"][0]

    toggled = admin.patch(
        f"{JOBS}/{job['id']}", json={"version": job["version"], "is_enabled": False}
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["is_enabled"] is False


def test_a_non_admin_cannot_toggle(admin: TestClient, cert: TestClient) -> None:
    scheduler.register_jobs()
    job = admin.get(JOBS).json()["items"][0]
    denied = cert.patch(
        f"{JOBS}/{job['id']}", json={"version": job["version"], "is_enabled": False}
    )
    assert denied.status_code == 403, denied.text


def test_a_stale_version_is_refused(admin: TestClient) -> None:
    scheduler.register_jobs()
    job = admin.get(JOBS).json()["items"][0]
    admin.patch(f"{JOBS}/{job['id']}", json={"version": job["version"], "is_enabled": False})
    stale = admin.patch(f"{JOBS}/{job['id']}", json={"version": job["version"], "is_enabled": True})
    assert stale.status_code == 409, stale.text


def test_the_schedule_string_cannot_be_edited_from_the_screen(admin: TestClient) -> None:
    """스케줄은 코드(레지스트리)와 함께 움직인다 — 화면에서 바꾸는 통로가 없다"""
    scheduler.register_jobs()
    job = admin.get(JOBS).json()["items"][0]
    refused = admin.patch(
        f"{JOBS}/{job['id']}",
        json={"version": job["version"], "is_enabled": True, "schedule": "interval@99"},
    )
    assert refused.status_code == 422, refused.text
