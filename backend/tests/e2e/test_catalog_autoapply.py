"""C·J. §4.8 자동 적용 — 신규 제품·SKU 등록 시 인증 인스턴스 자동 생성 (S2-2 PR-2 안건 ⑥).

★ 이 파일이 고정하는 계약:
  ① 등록 시 품목군 요건 세트 중 **CONFIRMED + 적용단위 일치**(제품→PRODUCT,
    SKU→SKU) 템플릿 전건에 미착수 인스턴스+태스크가 생긴다 — DRAFT·타 단위는
    비생성(필터를 빼먹으면 COMPANY CHECK·대상 검증이 등록 자체를 굴러뜨린다).
  ② **같은 트랜잭션**(§17.1) — 자동 적용이 실패하면 등록도 롤백된다.
  ③ 멱등 — 활성 인스턴스 기존재 시 생성 생략(예외가 아니라 사전 확인),
    같은 idempotency key 재요청은 replay라 재실행 자체가 없다.
  ④ 아웃박스 — 자동 생성분 created 이벤트에 automatic=true 표식(판정 ⑩ 대칭).

★ 자동 적용 테스트는 반드시 API 경로(POST /products·/skus)로 등록한다 —
  테스트 팩토리(factories.create_product/create_sku)는 서비스를 우회하는
  DB 직생성이라 자동 적용이 발동하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.certifications.models import Certification, CertificationTask
from app.modules.identity.models import RoleCode
from app.modules.outbox.models import Event
from app.modules.requirements.models import (
    ItemProfileRequirementTemplate,
    RequirementTemplate,
    TemplateChecklistItem,
)
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_brand,
    create_item_profile,
    create_market,
    create_user,
)

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
PRODUCTS = "/api/v1/products"
SKUS = "/api/v1/skus"


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade-autoapply@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "trade-autoapply@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def _template(
    *,
    market_id: int,
    name: str,
    applies_to: str,
    status: str = "CONFIRMED",
    checklist: tuple[str, ...] = (),
) -> int:
    with unit_of_work() as uow:
        row = RequirementTemplate(
            market_id=market_id,
            name=name,
            applies_to=applies_to,
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule" if status == "CONFIRMED" else None,
            last_verified_on=date(2026, 8, 1) if status == "CONFIRMED" else None,
            status=status,
        )
        uow.session.add(row)
        uow.session.flush()
        for seq, item_name in enumerate(checklist, start=1):
            uow.session.add(
                TemplateChecklistItem(
                    template_id=row.id, seq=seq, item_name=item_name, is_required=True
                )
            )
        return row.id


def _link(profile_id: int, template_id: int) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            ItemProfileRequirementTemplate(
                item_profile_id=profile_id, requirement_template_id=template_id
            )
        )


@pytest.fixture
def profile_set() -> dict[str, int]:
    """요건 세트 4종 — CONFIRMED PRODUCT(체크 2)·CONFIRMED SKU·DRAFT PRODUCT·
    CONFIRMED FACILITY. 자동 적용 필터가 이 중 단위 일치 CONFIRMED만 골라야 한다."""
    market_id = create_market("US", name_ko="미국")
    profile_id = create_item_profile("PRF-AA", name_ko="자동적용 품목군")
    ids = {
        "profile": profile_id,
        "product_confirmed": _template(
            market_id=market_id,
            name="US 제품 안전성 평가",
            applies_to="PRODUCT",
            checklist=("안전성 자료", "성분 검토"),
        ),
        "sku_confirmed": _template(market_id=market_id, name="US SKU 등록", applies_to="SKU"),
        "product_draft": _template(
            market_id=market_id, name="US 초안 요건", applies_to="PRODUCT", status="DRAFT"
        ),
        "facility_confirmed": _template(
            market_id=market_id, name="US 시설 등록", applies_to="FACILITY"
        ),
    }
    for key in ("product_confirmed", "sku_confirmed", "product_draft", "facility_confirmed"):
        _link(profile_id, ids[key])
    return ids


def _register_product(
    client: TestClient, profile_id: int | None, *, key: str, code: str = "PRD-AA1"
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "brand_id": create_brand(f"BRD-{key[:8]}"),
        "product_code": code,
        "name_ko": "자동적용 제품",
    }
    if profile_id is not None:
        payload["item_profile_id"] = profile_id
    response = client.post(PRODUCTS, json=payload, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def _instances(target_type: str, target_id: int) -> list[Certification]:
    with unit_of_work() as uow:
        return list(
            uow.session.execute(
                select(Certification).where(
                    Certification.target_type == target_type,
                    Certification.target_id == target_id,
                    Certification.deleted_at.is_(None),
                )
            ).scalars()
        )


def test_product_registration_applies_matching_confirmed_templates(
    trader: TestClient, profile_set: dict[str, int]
) -> None:
    """제품 등록 → CONFIRMED·PRODUCT 단위 1건만 미착수 인스턴스+태스크 2건 (①)"""
    body = _register_product(trader, profile_set["profile"], key="aa-prod")

    rows = _instances("PRODUCT", body["id"])
    assert [row.template_id for row in rows] == [profile_set["product_confirmed"]]
    assert rows[0].status == "NOT_STARTED"
    assert rows[0].template_name == "US 제품 안전성 평가"  # 스냅샷 동결
    with unit_of_work() as uow:
        tasks = list(
            uow.session.execute(
                select(CertificationTask)
                .where(
                    CertificationTask.certification_id == rows[0].id,
                    CertificationTask.deleted_at.is_(None),
                )
                .order_by(CertificationTask.seq)
            ).scalars()
        )
        assert [(task.seq, task.item_name, task.done) for task in tasks] == [
            (1, "안전성 자료", False),
            (2, "성분 검토", False),
        ]
        # ④ — 자동 생성분 created 이벤트에 automatic=true 표식.
        event = uow.session.execute(
            select(Event).where(
                Event.event_type == "certifications.certification.created",
                Event.aggregate_id == rows[0].id,
            )
        ).scalar_one()
        assert event.payload["automatic"] is True


def test_sku_registration_applies_sku_unit_templates(
    trader: TestClient, profile_set: dict[str, int]
) -> None:
    """SKU 등록 → CONFIRMED·SKU 단위 1건만 생성 (제품 단위·시설 단위는 비대상)"""
    product = _register_product(trader, None, key="aa-sku-prod")
    response = trader.post(
        SKUS,
        json={
            "sku_code": "SKU-AA1",
            "name_ko": "자동적용 SKU",
            "kind": "SINGLE",
            "product_id": product["id"],
            "item_profile_id": profile_set["profile"],
        },
        headers={"Idempotency-Key": "aa-sku"},
    )
    assert response.status_code == 201, response.text

    rows = _instances("SKU", response.json()["id"])
    assert [row.template_id for row in rows] == [profile_set["sku_confirmed"]]
    assert rows[0].status == "NOT_STARTED"


def test_registration_without_a_profile_applies_nothing(trader: TestClient) -> None:
    """품목군 없는 등록은 자동 적용 무대상 — 인스턴스 0건"""
    body = _register_product(trader, None, key="aa-nopro")
    assert _instances("PRODUCT", body["id"]) == []


def test_replayed_registration_does_not_duplicate_instances(
    trader: TestClient, profile_set: dict[str, int]
) -> None:
    """같은 키 재요청은 replay — 자동 적용이 재실행되지 않아 인스턴스 1건 유지 (③)"""
    first = _register_product(trader, profile_set["profile"], key="aa-replay")
    again = trader.post(
        PRODUCTS,
        json={
            "brand_id": first["brand_id"],
            "product_code": "PRD-AA1",
            "name_ko": "자동적용 제품",
            "item_profile_id": profile_set["profile"],
        },
        headers={"Idempotency-Key": "aa-replay"},
    )
    assert again.status_code == 201, again.text
    assert len(_instances("PRODUCT", first["id"])) == 1


def test_an_existing_active_instance_is_skipped(profile_set: dict[str, int]) -> None:
    """기존재 시 생성 생략 (③ — 활성 술어 사전 확인, 예외 경로 아님)"""
    from app.modules.certifications import service as certifications_service
    from app.modules.identity.service import AuthenticatedUser

    actor = AuthenticatedUser(
        id=create_user("aa-skip@example.com", roles=(RoleCode.CERT,)),
        email="aa-skip@example.com",
        display_name="skip",
        roles=frozenset({RoleCode.CERT}),
        session_id=1,
    )
    with unit_of_work() as uow:
        first = certifications_service.apply_item_profile_requirements(
            uow.session,
            item_profile_id=profile_set["profile"],
            target_type="PRODUCT",
            target_id=777,
            actor=actor,
        )
        assert len(first) == 1
        created_template_id = uow.session.execute(
            select(Certification.template_id).where(Certification.id == first[0])
        ).scalar_one()
        assert created_template_id == profile_set["product_confirmed"]
        second = certifications_service.apply_item_profile_requirements(
            uow.session,
            item_profile_id=profile_set["profile"],
            target_type="PRODUCT",
            target_id=777,
            actor=actor,
        )
        assert second == []


@pytest.mark.group_j
def test_autoapply_failure_rolls_back_the_registration(
    trader: TestClient, profile_set: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """자동 적용 실패 = 등록 롤백 — "등록과 같은 트랜잭션"의 실측 (② / §17.1)"""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("autoapply boom")

    monkeypatch.setattr("app.modules.certifications.service.apply_item_profile_requirements", _boom)
    brand_id = create_brand("BRD-ROLL")
    with pytest.raises(RuntimeError):
        trader.post(
            PRODUCTS,
            json={
                "brand_id": brand_id,
                "product_code": "PRD-ROLL",
                "name_ko": "롤백 제품",
                "item_profile_id": profile_set["profile"],
            },
            headers={"Idempotency-Key": "aa-roll"},
        )
    with unit_of_work() as uow:
        from app.modules.catalog.models import Product

        leftover = uow.session.execute(
            select(Product).where(Product.product_code == "PRD-ROLL")
        ).scalar_one_or_none()
        assert leftover is None
