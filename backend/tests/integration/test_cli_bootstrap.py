"""K. 보안·품질 — 첫 관리자 부트스트랩 (app/cli.py).

이 경로가 없으면 시스템에 들어갈 방법이 없다(로그인은 계정을, 계정 생성은
관리자를 요구한다). 들어갈 수 없는 시스템은 완성된 시스템이 아니다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.cli import MIN_PASSWORD_LENGTH, create_admin, main
from app.core.db.session import engine
from app.modules.identity.models import RoleCode

pytestmark = pytest.mark.group_k

PASSWORD = "bootstrap-password-1234"


def _roles_of(user_id: int) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                    "WHERE ur.user_id = :id AND ur.deleted_at IS NULL"
                ),
                {"id": user_id},
            ).scalars()
        )


def test_creates_an_admin_account() -> None:
    """첫 관리자가 만들어지고 ADMIN 역할을 갖는다"""
    user_id = create_admin("boss@example.com", PASSWORD, "영준이")
    assert _roles_of(user_id) == {RoleCode.ADMIN.value}


def test_email_is_normalized() -> None:
    """대문자로 넘겨도 소문자로 저장된다 (DB CHECK와 충돌하지 않는다)"""
    user_id = create_admin("Boss@Example.COM", PASSWORD, "영준이")
    with engine.connect() as connection:
        email = connection.execute(
            text("SELECT email FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
    assert email == "boss@example.com"


def test_running_twice_does_not_duplicate() -> None:
    """다시 실행해도 계정·역할이 늘어나지 않는다 (재실행이 안전해야 한다)"""
    first = create_admin("boss@example.com", PASSWORD, "영준이")
    second = create_admin("boss@example.com", PASSWORD, "영준이")

    assert first == second
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM user_roles WHERE deleted_at IS NULL")
            ).scalar_one()
            == 1
        )


def test_bootstrap_is_audited() -> None:
    """부트스트랩 계정도 audit_log에 남는다

    "이 관리자는 언제 어디서 생겼나"에 답할 수 없는 계정을 만들지 않는다.
    """
    create_admin("boss@example.com", PASSWORD, "영준이")

    with engine.connect() as connection:
        actions = set(connection.execute(text("SELECT action FROM audit_log")).scalars())
    assert "identity.account.bootstrapped" in actions
    assert "identity.role.granted" in actions


def test_short_password_is_refused() -> None:
    """짧은 비밀번호로는 관리자를 만들 수 없다 (전체 권한 계정이다)"""
    with pytest.raises(SystemExit):
        main(["create-admin", "--email", "boss@example.com", "--password", "short"])

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 0


def test_cli_entrypoint_creates_the_account() -> None:
    """명령줄 진입점이 실제로 동작한다 (영준이가 칠 명령 그대로)"""
    exit_code = main(
        [
            "create-admin",
            "--email",
            "boss@example.com",
            "--display-name",
            "영준이",
            "--password",
            "a" * MIN_PASSWORD_LENGTH,
        ]
    )

    assert exit_code == 0
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
