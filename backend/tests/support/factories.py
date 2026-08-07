"""테스트용 데이터 생성기.

테스트마다 손으로 사용자를 만들면 필수 컬럼이 하나 늘 때 전 테스트가 깨진다.
생성 지점을 여기 하나로 모은다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.catalog.models import Brand, Product, Sku
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
    #
    # ★ 브랜드 코드를 제품 코드에서 파생시킨다. 고정값을 쓰면 한 테스트가 제품을
    #   둘 만드는 순간 브랜드 부분 유니크에 걸려, **테스트 준비 코드가 실패하고
    #   그 실패가 마치 기능 결함처럼 보인다**(실제로 그렇게 한 번 헤맸다).
    if brand_id is None:
        brand_id = create_brand(f"B-{product_code}"[:20])
    with unit_of_work() as uow:
        product = Product(
            brand_id=brand_id,
            product_code=product_code,
            name_ko=name_ko,
        )
        uow.session.add(product)
        uow.session.flush()
        return product.id


def create_sku(
    sku_code: str = "SKU-001",
    *,
    name_ko: str = "테스트 SKU",
    product_id: int | None = None,
) -> int:
    """단품 SKU를 만들고 id를 돌려준다 (§4.1 — 단품은 처방 없이 존재할 수 없다)."""
    if product_id is None:
        product_id = create_product(f"P-{sku_code}"[:40])
    with unit_of_work() as uow:
        sku = Sku(
            sku_code=sku_code,
            name_ko=name_ko,
            kind="SINGLE",
            product_id=product_id,
        )
        uow.session.add(sku)
        uow.session.flush()
        return sku.id


def create_ingredient(inci_name: str = "Aqua", *, name_ko: str | None = None) -> int:
    """성분을 만들고 id를 돌려준다 (§4.3).

    ★ INCI명이 자연키다 — 한 테스트가 성분을 둘 만들면 이름을 달리 넘겨야 한다
      (고정 코드값 함정: PROGRESS 주의 인계 ⑥).
    """
    from app.modules.ingredients.models import Ingredient

    display = " ".join(inci_name.split())
    with unit_of_work() as uow:
        ingredient = Ingredient(
            inci_name=display,
            inci_name_normalized=display.upper(),
            name_ko=name_ko,
        )
        uow.session.add(ingredient)
        uow.session.flush()
        return ingredient.id


def create_ingredient_rule(
    ingredient_id: int,
    *,
    country_code: str = "US",
    rule_type: str = "PROHIBITED",
    max_concentration_pct: str | None = None,
) -> int:
    """성분×국가 규칙을 만들고 id를 돌려준다 (§4.3 / ADR-03).

    근거링크·최종확인일은 NOT NULL이라 그럴듯한 기본값을 넣는다 — 규칙 내용이
    아니라 존재가 필요한 테스트가 대부분이다.
    """
    from decimal import Decimal

    from app.core.time import today_kst
    from app.modules.ingredients.models import IngredientRule

    with unit_of_work() as uow:
        rule = IngredientRule(
            ingredient_id=ingredient_id,
            country_code=country_code,
            rule_type=rule_type,
            max_concentration_pct=(
                None if max_concentration_pct is None else Decimal(max_concentration_pct)
            ),
            source_url="https://example.com/regulation",
            last_verified_on=today_kst(),
        )
        uow.session.add(rule)
        uow.session.flush()
        return rule.id


def create_market(code: str = "US", *, name_ko: str | None = None) -> int:
    """시장을 만들고 id를 돌려준다 (§5.1 / S2-1). **이미 있으면 재사용한다.**

    ★ get-or-create인 이유: code는 전역 UNIQUE(코드 FK 대상 — S2-1 판정
      조건 3)인데, 라벨·규칙·HS를 준비하는 픽스처 여럿이 같은 시장(US)을
      전제한다. 재호출이 준비 실패가 되면 그 실패가 기능 결함처럼 보인다
      (고정 코드값 함정 — 주의 인계 ⑥ — 의 시장판 처방).
    """
    from app.modules.markets.models import Market

    normalized = code.strip().upper()
    with unit_of_work() as uow:
        existing = uow.session.execute(
            select(Market.id).where(Market.code == normalized, Market.deleted_at.is_(None))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        market = Market(code=normalized, name_ko=name_ko or normalized)
        uow.session.add(market)
        uow.session.flush()
        return market.id


def create_partner(
    partner_code: str = "PTN-001",
    *,
    name_ko: str = "테스트 거래처",
    types: tuple[str, ...] = ("SUPPLIER",),
) -> int:
    """거래처를 유형 링크까지 한 트랜잭션에 만들고 id를 돌려준다 (§4.6 / ADR-0026).

    ★ 코드는 자연키다 — 한 테스트가 거래처를 둘 만들면 코드를 달리 넘겨야 한다
      (고정 코드값 함정: PROGRESS 주의 인계 ⑥).
    """
    from app.modules.partners.models import Partner, PartnerTypeLink

    with unit_of_work() as uow:
        partner = Partner(partner_code=partner_code, name_ko=name_ko)
        uow.session.add(partner)
        uow.session.flush()
        for code in types:
            uow.session.add(PartnerTypeLink(partner_id=partner.id, type_code=code))
        uow.session.flush()
        return partner.id


def create_item_profile(code: str = "PRF-001", *, name_ko: str = "테스트 품목군") -> int:
    """품목군 프로파일을 만들고 id를 돌려준다 (§4.8 / ADR-0021).

    ★ 코드는 자연키다 — 한 테스트가 품목군을 둘 만들면 코드를 달리 넘겨야 한다
      (고정 코드값 함정: PROGRESS 주의 인계 ⑥).
    """
    from app.modules.catalog.models import ItemProfile

    with unit_of_work() as uow:
        profile = ItemProfile(code=code, name_ko=name_ko)
        uow.session.add(profile)
        uow.session.flush()
        return profile.id


def create_material(
    material_code: str = "MAT-001",
    *,
    name_ko: str = "테스트 자재",
    material_type: str = "RAW_MATERIAL",
    hs6: str | None = None,
) -> int:
    """자재를 만들고 id를 돌려준다 (§4.4).

    ★ 코드는 사람이 정하는 자연키다 — 한 테스트가 자재를 둘 만들면 코드를
      달리 넘겨야 한다(고정 코드값 함정: PROGRESS 주의 인계 ⑥).
    """
    from app.modules.materials.models import Material

    with unit_of_work() as uow:
        material = Material(
            material_code=material_code,
            name_ko=name_ko,
            material_type=material_type,
            hs6=hs6,
        )
        uow.session.add(material)
        uow.session.flush()
        return material.id
