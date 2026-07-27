"""K. 보안·품질 — 로그인·세션 (DESIGN.md §18.1 / ADR-0013 / §20 K "로그인 5회 잠금").

전부 HTTP 표면에서 검사한다. 서비스 함수를 직접 부르면 쿠키 속성·상태 코드·
에러 봉투처럼 사용자가 실제로 만나는 계약이 검증되지 않는다.
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
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"

EMAIL = "junebee@example.com"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, *, email: str = EMAIL, password: str = DEFAULT_PASSWORD) -> Response:
    return client.post(LOGIN, json={"email": email, "password": password})


def _error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def test_login_succeeds_and_returns_roles(client: TestClient) -> None:
    """올바른 자격 증명으로 로그인하면 세션 쿠키와 내 역할이 온다"""
    create_user(EMAIL, roles=(RoleCode.TRADE, RoleCode.CERT))

    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["roles"] == ["CERT", "TRADE"]
    assert client.cookies.get(SESSION_COOKIE_NAME)


def test_session_cookie_is_httponly_and_lax(client: TestClient) -> None:
    """세션 쿠키는 HttpOnly + SameSite=Lax다 (ADR-0013)

    dev/test는 http라 Secure를 켜지 않는다 — 켜면 쿠키가 아예 붙지 않아
    로컬 개발이 통째로 막힌다. 운영 전환은 settings.is_prod가 한다.
    """
    create_user(EMAIL)
    response = _login(client)
    raw = response.headers["set-cookie"].lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw
    assert "secure" not in raw


def test_me_requires_authentication(client: TestClient) -> None:
    """로그인하지 않고 내 정보를 부르면 401이고 한국어 안내가 온다"""
    response = client.get(ME)
    assert response.status_code == 401
    assert _error_code(response) == "COMMON.AUTH.UNAUTHENTICATED"
    assert "로그인" in response.json()["error"]["message"]


def test_wrong_password_and_unknown_email_are_indistinguishable(client: TestClient) -> None:
    """없는 이메일과 틀린 비밀번호가 같은 응답을 준다 (계정 열거 차단 — §18.1)"""
    create_user(EMAIL)

    wrong_password = _login(client, password="완전히-틀린-비밀번호")
    unknown_email = _login(client, email="nobody@example.com", password=DEFAULT_PASSWORD)

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert _error_code(wrong_password) == _error_code(unknown_email)
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]


def test_five_consecutive_failures_lock_the_account(client: TestClient) -> None:
    """비밀번호를 5회 연속 틀리면 계정이 잠긴다 (§18.1)"""
    create_user(EMAIL)

    for attempt in range(4):
        response = _login(client, password="틀린-비밀번호")
        assert response.status_code == 401, f"{attempt + 1}번째 시도에서 이미 잠겼다"

    fifth = _login(client, password="틀린-비밀번호")
    assert fifth.status_code == 423
    assert _error_code(fifth) == "COMMON.AUTH.ACCOUNT_LOCKED"


def test_locked_account_rejects_even_the_correct_password(client: TestClient) -> None:
    """잠긴 뒤에는 올바른 비밀번호로도 들어갈 수 없다 (잠금이 실효를 가진다)"""
    create_user(EMAIL)
    for _ in range(5):
        _login(client, password="틀린-비밀번호")

    response = _login(client)  # 이번엔 진짜 비밀번호

    assert response.status_code == 423
    assert _error_code(response) == "COMMON.AUTH.ACCOUNT_LOCKED"


def test_failure_counter_survives_the_rejected_request(client: TestClient) -> None:
    """실패 기록은 요청이 예외로 끝나도 남는다 (롤백되면 5회 잠금이 영원히 작동 안 한다)"""
    create_user(EMAIL)
    _login(client, password="틀린-비밀번호")

    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT failed_login_count FROM users WHERE email = :e"), {"e": EMAIL}
        ).scalar_one()
    assert count == 1


def test_successful_login_resets_the_failure_counter(client: TestClient) -> None:
    """성공하면 실패 카운터가 0으로 돌아간다"""
    create_user(EMAIL)
    for _ in range(3):
        _login(client, password="틀린-비밀번호")

    assert _login(client).status_code == 200

    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT failed_login_count FROM users WHERE email = :e"), {"e": EMAIL}
        ).scalar_one()
    assert count == 0


def test_login_rotates_the_session_id(client: TestClient) -> None:
    """로그인 성공은 언제나 새 세션을 발급하고 기존 세션을 폐기한다

    세션 고정 공격 차단(ADR-0013) — 공격자가 미리 심어 둔 세션ID가 로그인으로
    인증된 세션으로 승격되면 안 된다.
    """
    create_user(EMAIL)
    _login(client)
    first_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert first_token

    _login(client)  # 같은 쿠키를 들고 다시 로그인
    second_token = client.cookies.get(SESSION_COOKIE_NAME)

    assert second_token != first_token, "로그인이 기존 세션ID를 그대로 재사용했다"

    with TestClient(app) as stale:
        stale.cookies.set(SESSION_COOKIE_NAME, first_token)
        assert stale.get(ME).status_code == 401, "폐기됐어야 할 이전 세션이 아직 살아 있다"


def test_expired_session_is_rejected(client: TestClient) -> None:
    """만료된 세션은 401이다 (§18.1 세션 만료)"""
    create_user(EMAIL)
    _login(client)
    assert client.get(ME).status_code == 200

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE user_sessions SET expires_at = now() - interval '1 second'")
        )

    assert client.get(ME).status_code == 401


def test_logout_invalidates_the_session(client: TestClient) -> None:
    """로그아웃하면 그 쿠키로는 더 이상 아무것도 못 한다"""
    create_user(EMAIL)
    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)

    assert client.post(LOGOUT).status_code == 204

    with TestClient(app) as stale:
        stale.cookies.set(SESSION_COOKIE_NAME, token)
        assert stale.get(ME).status_code == 401


def test_inactive_account_cannot_log_in(client: TestClient) -> None:
    """비활성 계정은 자격 증명이 맞아도 들어올 수 없다 (§2 계정 비활성 절차)"""
    create_user(EMAIL, is_active=False)

    response = _login(client)

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.ACCOUNT_INACTIVE"


def test_email_is_case_insensitive_on_login(client: TestClient) -> None:
    """대문자로 입력해도 같은 계정으로 로그인된다"""
    create_user(EMAIL)
    assert _login(client, email="JuneBee@Example.COM").status_code == 200


def test_login_attempts_are_audited(client: TestClient) -> None:
    """성공·실패 로그인이 모두 audit_log에 남는다 (§18.1 "로그인은 audit 필수")"""
    create_user(EMAIL)
    _login(client, password="틀린-비밀번호")
    _login(client)

    with engine.connect() as connection:
        actions = list(
            connection.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars()
        )
    assert "auth.login.failed" in actions
    assert "auth.login.succeeded" in actions


def test_audit_row_carries_the_request_id(client: TestClient) -> None:
    """감사 기록에 요청 추적 번호가 붙는다 (사용자 신고 ↔ 로그 연결 — §18.3)"""
    create_user(EMAIL)
    response = _login(client)
    request_id = response.headers["X-Request-ID"]

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT request_id FROM audit_log WHERE action = 'auth.login.succeeded'")
        ).scalar_one()
    assert stored == request_id
