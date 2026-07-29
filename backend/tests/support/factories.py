"""테스트용 데이터 생성기.

테스트마다 손으로 사용자를 만들면 필수 컬럼이 하나 늘 때 전 테스트가 깨진다.
생성 지점을 여기 하나로 모은다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.catalog.models import Brand, Product
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


def create_brand(brand_code: str = "BRD-001", *, name_ko: str = "테스트 브랜드") -> int:
    """브랜드를 만들고 id를 돌려준다 (§4.1)."""
    with unit_of_work() as uow:
        brand = Brand(brand_code=brand_code, name_ko=name_ko)
        uow.session.add(brand)
        uow.session.flush()
        return brand.id


def create_product(
    product_code: str = "PRD-001",
    *,
    name_ko: str = "테스트 처방",
    brand_id: int | None = None,
) -> int:
    """제품(처방)을 만들고 id를 돌려준다.

    ★ DB 레벨로 만든다 — API로 만들면 조회 역할처럼 등록 권한이 없는 사용자를
      검증하는 테스트가 준비 단계에서 막힌다.
    """
    # ★ 브랜드는 트랜잭션 **밖에서** 먼저 만든다. unit_of_work를 중첩하면
    #   §17.1의 "업무 동작 하나 = 트랜잭션 하나"가 테스트 준비 코드에서부터 깨진다.
    if brand_id is None:
        brand_id = create_brand()
    with unit_of_work() as uow:
        product = Product(
            brand_id=brand_id,
            product_code=product_code,
            name_ko=name_ko,
        )
        uow.session.add(product)
        uow.session.flush()
        return product.id
