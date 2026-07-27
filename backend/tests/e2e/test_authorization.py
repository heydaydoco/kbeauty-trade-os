"""K. 보안·품질 — 역할·소유권 인가 (DESIGN.md §18.1 / S0-2 DoD "타 사용자 리소스 403").

§20 K의 "타 사용자 전표 URL 403"을 S0-2 시점에 존재하는 자원(users)으로 옮겨
검증한다. 전표는 Phase 3에 생기지만, **막는 기제**는 여기서 서고 이후 화면은
같은 의존성을 재사용한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text

from app.core.db.session import engine
from app.core.pagination import DEFAULT_PAGE_SIZE
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
USERS = "/api/v1/users"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login_as(client: TestClient, email: str) -> Response:
    response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text
    return response


def _error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


# ── 소유권 (IDOR) ──────────────────────────────────────────────────────────


def test_user_can_read_own_record(client: TestClient) -> None:
    """자기 자신은 조회할 수 있다"""
    my_id = create_user("me@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "me@example.com")

    response = client.get(f"{USERS}/{my_id}")

    assert response.status_code == 200
    assert response.json()["id"] == my_id


def test_reading_another_users_record_is_forbidden(client: TestClient) -> None:
    """남의 id를 URL에 넣어 부르면 403이다 (§18.1 IDOR 차단)"""
    create_user("me@example.com", roles=(RoleCode.TRADE,))
    other_id = create_user("other@example.com", roles=(RoleCode.LOGISTICS,))
    _login_as(client, "me@example.com")

    response = client.get(f"{USERS}/{other_id}")

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.FORBIDDEN"


def test_forbidden_response_does_not_leak_the_other_user(client: TestClient) -> None:
    """403 응답에 남의 정보가 섞여 나오지 않는다"""
    create_user("me@example.com", roles=(RoleCode.TRADE,))
    other_id = create_user("secret@example.com", display_name="비밀 이름")
    _login_as(client, "me@example.com")

    body = client.get(f"{USERS}/{other_id}").text

    assert "secret@example.com" not in body
    assert "비밀 이름" not in body


def test_admin_can_read_any_user(client: TestClient) -> None:
    """관리자는 남의 기록도 볼 수 있다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    other_id = create_user("other@example.com")
    _login_as(client, "admin@example.com")

    assert client.get(f"{USERS}/{other_id}").status_code == 200


# ── 역할 기반 인가 ─────────────────────────────────────────────────────────


def test_non_admin_cannot_list_users(client: TestClient) -> None:
    """관리자가 아니면 사용자 목록을 볼 수 없다"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    response = client.get(USERS)

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.FORBIDDEN"


def test_viewer_cannot_grant_roles(client: TestClient) -> None:
    """조회 역할은 권한을 부여할 수 없다"""
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    target_id = create_user("target@example.com")
    _login_as(client, "viewer@example.com")

    response = client.post(f"{USERS}/{target_id}/roles", json={"role": "ADMIN"})

    assert response.status_code == 403


def test_unauthenticated_request_is_401_not_403(client: TestClient) -> None:
    """로그인하지 않은 요청은 403이 아니라 401이다 (화면이 로그인으로 보내야 한다)"""
    response = client.get(USERS)
    assert response.status_code == 401


# ── 페이지네이션 (§18.4) ───────────────────────────────────────────────────


def test_user_list_is_paginated_with_default_50(client: TestClient) -> None:
    """목록은 Page 봉투로 오고 기본 크기는 50이다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    body = client.get(USERS).json()

    assert set(body) == {"items", "total", "page", "size"}
    assert body["size"] == DEFAULT_PAGE_SIZE
    assert body["page"] == 1


def test_page_size_over_the_cap_is_rejected(client: TestClient) -> None:
    """상한을 넘는 size는 422로 거부된다 (전건 조회 우회 차단)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    assert client.get(USERS, params={"size": 10_000}).status_code == 422


def test_pagination_slices_the_result(client: TestClient) -> None:
    """page·size가 실제로 결과를 자른다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    for index in range(4):
        create_user(f"user{index}@example.com")
    _login_as(client, "admin@example.com")

    first = client.get(USERS, params={"size": 2, "page": 1}).json()
    second = client.get(USERS, params={"size": 2, "page": 2}).json()

    assert first["total"] == second["total"] == 5
    assert len(first["items"]) == len(second["items"]) == 2
    assert {item["id"] for item in first["items"]} & {
        item["id"] for item in second["items"]
    } == set()


# ── 권한 변경 audit (§18.1 "권한변경은 audit 필수") ─────────────────────────


def test_granting_a_role_is_audited(client: TestClient) -> None:
    """역할 부여가 audit_log에 남고, 부여한 사람이 기록된다"""
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("target@example.com")
    _login_as(client, "admin@example.com")

    response = client.post(f"{USERS}/{target_id}/roles", json={"role": "TRADE"})

    assert response.status_code == 200
    assert response.json()["roles"] == ["TRADE"]

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT actor_user_id, entity_id, detail->>'role' AS role "
                "FROM audit_log WHERE action = 'identity.role.granted'"
            )
        ).one()
    assert row.actor_user_id == admin_id
    assert row.entity_id == target_id
    assert row.role == "TRADE"


def test_granting_the_same_role_twice_changes_nothing(client: TestClient) -> None:
    """이미 가진 역할을 다시 부여해도 에러가 아니고 중복 행도 생기지 않는다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("target@example.com")
    _login_as(client, "admin@example.com")

    client.post(f"{USERS}/{target_id}/roles", json={"role": "TRADE"})
    second = client.post(f"{USERS}/{target_id}/roles", json={"role": "TRADE"})

    assert second.status_code == 200
    assert second.json()["roles"] == ["TRADE"]

    with engine.connect() as connection:
        active = connection.execute(
            text("SELECT count(*) FROM user_roles WHERE user_id = :id AND deleted_at IS NULL"),
            {"id": target_id},
        ).scalar_one()
    assert active == 1


def test_revoking_a_role_is_audited_and_takes_effect(client: TestClient) -> None:
    """역할 회수가 기록되고, 회수된 역할은 즉시 사라진다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("target@example.com", roles=(RoleCode.TRADE, RoleCode.CERT))
    _login_as(client, "admin@example.com")

    response = client.delete(f"{USERS}/{target_id}/roles/TRADE")

    assert response.status_code == 200
    assert response.json()["roles"] == ["CERT"]

    with engine.connect() as connection:
        actions = list(connection.execute(text("SELECT action FROM audit_log")).scalars())
    assert "identity.role.revoked" in actions


def test_revoked_role_no_longer_authorizes(client: TestClient) -> None:
    """회수된 역할로는 더 이상 통과하지 못한다 (권한 판정이 회수를 실제로 본다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    create_user("boss@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    with TestClient(app) as boss:
        _login_as(boss, "boss@example.com")
        # admin 계정의 ADMIN 역할을 회수한다.
        admin_id = client.get("/api/v1/auth/me").json()["id"]
        assert boss.delete(f"{USERS}/{admin_id}/roles/ADMIN").status_code == 200

    assert client.get(USERS).status_code == 403


def test_unknown_role_value_is_rejected(client: TestClient) -> None:
    """5종에 없는 역할 문자열은 422로 거부된다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("target@example.com")
    _login_as(client, "admin@example.com")

    response = client.post(f"{USERS}/{target_id}/roles", json={"role": "SUPERUSER"})

    assert response.status_code == 422
