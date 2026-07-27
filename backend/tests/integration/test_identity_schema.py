"""K. 보안·품질 — 신원 스키마의 DB 강제 (DESIGN.md §2 권한·통제 / §17.4 / §18.1).

여기서 검사하는 것은 전부 "앱이 깜빡해도 DB가 막는가"다. 애플리케이션 검증만
있는 규칙은 새 코드 경로가 하나 생길 때마다 조용히 뚫린다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.identity.models import ROLE_SEED, Role, RoleCode, User

pytestmark = pytest.mark.group_k


def _new_user(email: str, **overrides: object) -> User:
    defaults: dict[str, object] = {
        "email": email,
        "password_hash": "argon2-placeholder",
        "display_name": "테스트 사용자",
    }
    defaults.update(overrides)
    return User(**defaults)


def test_role_seed_matches_role_code_enum() -> None:
    """DB의 역할 5종이 코드의 RoleCode와 정확히 일치한다

    인가 판단은 코드(RoleCode)가, FK 무결성은 DB(roles)가 맡는다. 둘이 어긋나면
    "권한이 있는데 없다고 나오는" 종류의 조용한 사고가 된다.
    """
    with engine.connect() as connection:
        in_db = set(connection.execute(text("SELECT code FROM roles")).scalars())
    assert in_db == {role.value for role in RoleCode}
    assert in_db == {code.value for code, _, _ in ROLE_SEED}


def test_role_table_rejects_unknown_code() -> None:
    """roles에 5종 밖의 코드를 넣으면 CHECK가 거부한다"""
    with pytest.raises(IntegrityError), unit_of_work() as uow:
        uow.session.add(Role(code="SUPERUSER", name_ko="침입자"))


def test_email_must_be_stored_lowercase() -> None:
    """대문자가 섞인 이메일은 DB가 거부한다 (대소문자만 다른 중복 계정 차단)"""
    with pytest.raises(IntegrityError), unit_of_work() as uow:
        uow.session.add(_new_user("Junebee@Example.com"))


def test_duplicate_active_email_is_rejected() -> None:
    """살아 있는 계정 중 같은 이메일은 둘일 수 없다"""
    with unit_of_work() as uow:
        uow.session.add(_new_user("dup@example.com"))
    with pytest.raises(IntegrityError), unit_of_work() as uow:
        uow.session.add(_new_user("dup@example.com"))


def test_failed_login_count_cannot_go_negative() -> None:
    """실패 카운터는 음수가 될 수 없다 (리셋 로직 버그를 DB가 잡는다)"""
    with pytest.raises(IntegrityError), unit_of_work() as uow:
        uow.session.add(_new_user("neg@example.com", failed_login_count=-1))


@pytest.mark.group_j
def test_soft_deleted_email_can_be_reused_as_a_new_row() -> None:
    """삭제된 계정의 이메일은 재사용할 수 있고, 그것은 부활이 아니라 신규 행이다

    §17.4 "멱등 UNIQUE는 부분 인덱스 — 삭제 행이 재유입을 영구 차단하지 않되,
    재유입은 부활이 아니라 신규". 전 유니크였다면 퇴사자 이메일이 영원히 막힌다.
    """
    with unit_of_work() as uow:
        first = _new_user("rehire@example.com")
        uow.session.add(first)
        uow.session.flush()
        first_id = first.id
        first.deleted_at = utcnow()

    with unit_of_work() as uow:
        second = _new_user("rehire@example.com")
        uow.session.add(second)
        uow.session.flush()
        second_id = second.id

    assert second_id != first_id, "재유입이 기존 행을 되살렸다 — 신규 행이어야 한다"

    with unit_of_work() as uow:
        rows = uow.session.execute(select(User).where(User.email == "rehire@example.com")).scalars()
        assert len({row.id for row in rows}) == 2
