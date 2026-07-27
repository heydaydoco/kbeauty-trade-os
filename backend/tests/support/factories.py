"""테스트용 데이터 생성기.

테스트마다 손으로 사용자를 만들면 필수 컬럼이 하나 늘 때 전 테스트가 깨진다.
생성 지점을 여기 하나로 모은다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.passwords import hash_password

DEFAULT_PASSWORD = "kbos-test-password-1234"


def create_user(
    email: str,
    *,
    password: str = DEFAULT_PASSWORD,
    display_name: str = "테스트 사용자",
    roles: tuple[RoleCode, ...] = (),
    is_active: bool = True,
) -> int:
    """사용자를 만들고 id를 돌려준다. 역할까지 한 트랜잭션에 부여한다."""
    with unit_of_work() as uow:
        session = uow.session
        user = User(
            email=email.strip().lower(),
            password_hash=hash_password(password),
            display_name=display_name,
            is_active=is_active,
        )
        session.add(user)
        session.flush()

        for code in roles:
            role_id = session.execute(
                select(Role.id).where(Role.code == code.value, Role.deleted_at.is_(None))
            ).scalar_one()
            session.add(UserRole(user_id=user.id, role_id=role_id))

        return user.id
