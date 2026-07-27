"""K. 보안·품질 — 계정 관리의 마지막 방어선 (DESIGN.md §2 권한·통제 / ADR-0013 부기).

여기서 막는 것은 **되돌릴 방법이 시스템 안에 없어지는 상태**다. 관리자가 0명이
되면 복구에 DB 직접 수정이 필요하고, 그건 감사 추적 밖에서 일어난다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.identity.service import SESSION_COOKIE_NAME
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
USERS = "/api/v1/users"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login_as(client: TestClient, email: str, password: str = DEFAULT_PASSWORD) -> Response:
    return client.post(LOGIN, json={"email": email, "password": password})


def _error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def _audit_actions() -> list[str]:
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT action FROM audit_log")).scalars())


# ── 마지막 관리자 보호 ─────────────────────────────────────────────────────


def test_last_admin_cannot_lose_the_admin_role(client: TestClient) -> None:
    """마지막 관리자의 ADMIN 역할은 회수할 수 없다"""
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "admin@example.com")

    response = client.delete(f"{USERS}/{admin_id}/roles/ADMIN")

    assert response.status_code == 409
    assert _error_code(response) == "IDENTITY.ADMIN.LAST_ONE"
    assert client.get(f"{USERS}/{admin_id}").json()["roles"] == ["ADMIN"]


def test_last_admin_cannot_be_deactivated(client: TestClient) -> None:
    """마지막 관리자의 계정은 비활성화할 수 없다"""
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    response = client.patch(f"{USERS}/{admin_id}/active", json={"is_active": False})

    assert response.status_code == 409
    assert _error_code(response) == "IDENTITY.ADMIN.LAST_ONE"
    assert client.get(f"{USERS}/{admin_id}").json()["is_active"] is True


def test_blocked_attempt_is_audited(client: TestClient) -> None:
    """막힌 시도도 기록된다 (누가 마지막 관리자를 지우려 했는지 남아야 한다)"""
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    client.delete(f"{USERS}/{admin_id}/roles/ADMIN")

    assert "identity.admin.last_one_protected" in _audit_actions()


def test_admin_role_can_be_revoked_when_another_admin_exists(client: TestClient) -> None:
    """관리자가 둘 이상이면 회수할 수 있다 (보호가 과보호가 되지 않는다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    second_id = create_user("admin2@example.com", roles=(RoleCode.ADMIN,))
    _login_as(client, "admin@example.com")

    response = client.delete(f"{USERS}/{second_id}/roles/ADMIN")

    assert response.status_code == 200
    assert response.json()["roles"] == []


def test_inactive_admin_does_not_count_as_a_remaining_admin(client: TestClient) -> None:
    """비활성 관리자는 "남은 관리자"로 세지 않는다

    비활성 계정은 로그인 자체가 막혀 있어 아무것도 관리할 수 없다. 그걸 한 명으로
    세면 "관리자가 둘"이라고 판단하고 마지막 활성 관리자를 지우게 된다.
    """
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    create_user("sleeping@example.com", roles=(RoleCode.ADMIN,), is_active=False)
    _login_as(client, "admin@example.com")

    response = client.delete(f"{USERS}/{admin_id}/roles/ADMIN")

    assert response.status_code == 409


# ── 계정 비활성 ────────────────────────────────────────────────────────────


def test_deactivating_a_user_blocks_login_and_kills_live_sessions(client: TestClient) -> None:
    """비활성화하면 새 로그인도 막히고 이미 열린 세션도 끊긴다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as leaver:
        _login_as(leaver, "leaver@example.com")
        assert leaver.get(ME).status_code == 200

        _login_as(client, "admin@example.com")
        assert (
            client.patch(f"{USERS}/{target_id}/active", json={"is_active": False}).status_code
            == 200
        )

        assert leaver.get(ME).status_code == 401, "비활성 처리했는데 기존 세션이 살아 있다"

    assert _login_as(TestClient(app), "leaver@example.com").status_code == 403


def test_reactivating_a_user_restores_login(client: TestClient) -> None:
    """다시 활성화하면 로그인이 복구된다 (비활성은 삭제가 아니다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("returner@example.com", is_active=False)
    _login_as(client, "admin@example.com")

    client.patch(f"{USERS}/{target_id}/active", json={"is_active": True})

    with TestClient(app) as returner:
        assert _login_as(returner, "returner@example.com").status_code == 200


def test_account_state_changes_are_audited(client: TestClient) -> None:
    """활성·비활성 전환이 audit_log에 남는다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    target_id = create_user("target@example.com")
    _login_as(client, "admin@example.com")

    client.patch(f"{USERS}/{target_id}/active", json={"is_active": False})
    client.patch(f"{USERS}/{target_id}/active", json={"is_active": True})

    actions = _audit_actions()
    assert "identity.account.deactivated" in actions
    assert "identity.account.activated" in actions


# ── 잠금 해제 경로 (ADR-0013 부기) ─────────────────────────────────────────


def test_admin_can_unlock_a_locked_account(client: TestClient) -> None:
    """관리자는 잠긴 계정을 즉시 풀 수 있다 (15분 자동 해제를 기다리지 않는다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    locked_id = create_user("locked@example.com")

    with TestClient(app) as victim:
        for _ in range(5):
            _login_as(victim, "locked@example.com", password="틀린-비밀번호")
        assert _login_as(victim, "locked@example.com").status_code == 423

    _login_as(client, "admin@example.com")
    assert client.post(f"{USERS}/{locked_id}/unlock").status_code == 200

    with TestClient(app) as victim:
        assert _login_as(victim, "locked@example.com").status_code == 200


def test_unlock_resets_the_failure_counter(client: TestClient) -> None:
    """해제는 실패 카운터도 0으로 되돌린다 (남아 있으면 한 번만 틀려도 다시 잠긴다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    locked_id = create_user("locked@example.com")

    with TestClient(app) as victim:
        for _ in range(5):
            _login_as(victim, "locked@example.com", password="틀린-비밀번호")

    _login_as(client, "admin@example.com")
    client.post(f"{USERS}/{locked_id}/unlock")

    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT failed_login_count FROM users WHERE id = :id"), {"id": locked_id}
        ).scalar_one()
    assert count == 0
    assert "identity.account.unlocked" in _audit_actions()


def test_lock_expires_on_its_own_after_the_lockout_window(client: TestClient) -> None:
    """관리자 개입이 없어도 잠금 기간이 지나면 스스로 풀린다"""
    create_user("locked@example.com")
    for _ in range(5):
        _login_as(client, "locked@example.com", password="틀린-비밀번호")
    assert _login_as(client, "locked@example.com").status_code == 423

    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET locked_until = now() - interval '1 second'"))

    assert _login_as(client, "locked@example.com").status_code == 200


# ── 비관리자 차단 (역할 API 전건) ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "/roles", {"role": "TRADE"}),
        ("DELETE", "/roles/TRADE", None),
        ("PATCH", "/active", {"is_active": False}),
        ("POST", "/unlock", None),
    ],
)
def test_non_admin_cannot_touch_account_administration(
    client: TestClient, method: str, suffix: str, body: dict[str, object] | None
) -> None:
    """비관리자는 역할 부여·회수·활성전환·잠금해제 어느 것도 못 한다"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    target_id = create_user("target@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    response = client.request(method, f"{USERS}/{target_id}{suffix}", json=body)

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.FORBIDDEN"


def test_admin_cannot_be_impersonated_with_a_stale_cookie(client: TestClient) -> None:
    """비활성화된 관리자의 쿠키로는 관리 기능을 쓸 수 없다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    demoted_id = create_user("admin2@example.com", roles=(RoleCode.ADMIN,))

    with TestClient(app) as demoted:
        _login_as(demoted, "admin2@example.com")
        stale_cookie = demoted.cookies.get(SESSION_COOKIE_NAME)

    _login_as(client, "admin@example.com")
    client.patch(f"{USERS}/{demoted_id}/active", json={"is_active": False})

    with TestClient(app) as stale:
        stale.cookies.set(SESSION_COOKIE_NAME, stale_cookie)
        assert stale.get(USERS).status_code == 401
