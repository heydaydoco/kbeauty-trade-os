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
    status: str = "ACTIVE",
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
            status=status,
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

    # 시장은 트랜잭션 밖에서 먼저 보장한다(S2-1 FK 승격 — create_product의
    # 브랜드 선생성과 같은 이유: unit_of_work 중첩 금지).
    create_market(country_code)
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


# ── 준비도 매트릭스용 (S2-3 PR-3) — 요건 세트·인스턴스·세트 SKU ────────────────


def create_requirement_template(
    market: str = "US",
    name: str = "MoCRA 제품 리스팅",
    applies_to: str = "SKU",
    *,
    status: str = "CONFIRMED",
    lead_days: int | None = 90,
    profile_id: int | None = None,
) -> int:
    """요건 템플릿을 만들고(선택: 품목군 세트에 연결) id를 돌려준다.

    ★ 근거 2필드를 채워 두어 CONFIRMED 게이트(DB CHECK)를 통과한다 — 게이트 자체는
      requirement_templates 테스트의 몫이고, 여기는 확정된 요건이 필요한 테스트의 준비다.
    """
    from datetime import date

    from app.modules.requirements.models import RequirementTemplate

    market_id = create_market(market)
    with unit_of_work() as uow:
        row = RequirementTemplate(
            market_id=market_id,
            name=name,
            applies_to=applies_to,
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 9, 1),
            renewal_lead_days=lead_days,
            status=status,
        )
        uow.session.add(row)
        uow.session.flush()
        template_id = row.id
    if profile_id is not None:
        link_profile_template(profile_id, template_id)
    return template_id


def link_profile_template(profile_id: int, template_id: int, *, deleted: bool = False) -> None:
    """품목군의 요건 세트에 템플릿을 잇는다(deleted=True면 해제된 연결)."""
    from app.core.time import utcnow
    from app.modules.requirements.models import ItemProfileRequirementTemplate

    with unit_of_work() as uow:
        link = ItemProfileRequirementTemplate(
            item_profile_id=profile_id, requirement_template_id=template_id
        )
        if deleted:
            link.deleted_at = utcnow()
        uow.session.add(link)


def create_sku_with_axes(
    sku_code: str,
    *,
    profile_id: int | None = None,
    product_profile_id: int | None = None,
    manufacturer_id: int | None = None,
    name_ko: str = "테스트 SKU",
    status: str = "ACTIVE",
) -> tuple[int, int]:
    """(sku_id, product_id) — 품목군은 SKU·제품에 **따로** 붙는다(자동 적용 축 검증의 전제)."""
    product_id = create_product(f"P-{sku_code}"[:40])
    sku_id = create_sku(sku_code, name_ko=name_ko, product_id=product_id, status=status)
    with unit_of_work() as uow:
        sku = uow.session.get(Sku, sku_id)
        product = uow.session.get(Product, product_id)
        assert sku is not None and product is not None
        sku.item_profile_id = profile_id
        sku.manufacturer_partner_id = manufacturer_id
        product.item_profile_id = product_profile_id
    return sku_id, product_id


def create_set_sku(
    sku_code: str,
    components: list[int],
    *,
    profile_id: int | None = None,
    manufacturer_id: int | None = None,
) -> int:
    """세트 SKU(구성품 수량 1)를 만들고 id를 돌려준다 (§4.2 — SET은 처방을 갖지 않는다)."""
    from app.modules.catalog.models import SetComponent

    with unit_of_work() as uow:
        sku = Sku(
            sku_code=sku_code,
            name_ko="테스트 세트",
            kind="SET",
            item_profile_id=profile_id,
            manufacturer_partner_id=manufacturer_id,
        )
        uow.session.add(sku)
        uow.session.flush()
        for component in components:
            uow.session.add(SetComponent(set_sku_id=sku.id, component_sku_id=component, quantity=1))
        return sku.id


def create_certification_instance(
    template_id: int,
    target_type: str,
    target_id: int | None,
    *,
    status: str,
    expires_on: object | None = None,
    lead_days: int | None = 90,
    assignee_id: int | None = None,
    valid_from: object | None = None,
) -> int:
    """인증 인스턴스 행을 직접 만든다 — 상태·만료일을 자유롭게 심는 **픽스처 한정** 경로.

    ★ 서비스 경로(create_certification)는 항상 미착수로 시작하고 상태 대입을 막는다
      (층2). 매트릭스처럼 "이 상태에서 무슨 색인가"를 보는 테스트는 상태를 심어야
      하므로 여기서 직접 만든다 — 전이 규칙 자체는 상태머신 테스트의 몫이다.
    """
    from datetime import date

    from app.modules.certifications.models import Certification

    for label, value in (("expires_on", expires_on), ("valid_from", valid_from)):
        # 문자열 등을 조용히 무기한(NULL)으로 바꾸지 않는다 — 준비 실수가 기능 결함처럼 보인다.
        if value is not None and not isinstance(value, date):
            raise TypeError(f"{label}은 date여야 합니다: {value!r}")
    with unit_of_work() as uow:
        row = Certification(
            template_id=template_id,
            target_type=target_type,
            target_id=target_id,
            status=status,
            template_name="스냅샷",
            requirement_type="REGISTRATION",
            renewal_lead_days=lead_days,
            expires_on=expires_on if isinstance(expires_on, date) else None,
            valid_from=valid_from if isinstance(valid_from, date) else None,
            assignee_id=assignee_id,
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def create_link_document(
    owner_id: int,
    *,
    valid_until: object | None,
    owner_type: str = "SKU",
    document_type: str = "CFS",
    tag: str = "doc",
) -> int:
    """LINK형 문서 행을 직접 만든다(유효기간 지정) — 등록 경로의 계약은 documents 테스트의 몫."""
    from datetime import date

    from app.modules.documents.models import Document, DocumentType

    if valid_until is not None and not isinstance(valid_until, date):
        raise TypeError(f"valid_until은 date여야 합니다: {valid_until!r}")
    with unit_of_work() as uow:
        type_id = uow.session.execute(
            select(DocumentType.id).where(
                DocumentType.code == document_type, DocumentType.deleted_at.is_(None)
            )
        ).scalar_one()
        row = Document(
            owner_type=owner_type,
            owner_id=owner_id,
            document_type_id=type_id,
            storage_kind="LINK",
            url=f"https://example.com/{tag}-{owner_id}.pdf",
            issued_on=date(2025, 1, 1),
            valid_until=valid_until if isinstance(valid_until, date) else None,
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id
