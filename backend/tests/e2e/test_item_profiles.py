"""K. 보안·품질 — 품목군 프로파일 (DESIGN.md §4.8 / ADR-0021).

★ 지금 있는 것은 **분류를 만들고 고르는 것**뿐이다. §4.8의 요건·서류·마일스톤
  세트는 대상 테이블이 생기는 세션이 붙인다(요건 S2-1 / 서류 S1-3 / 마일스톤
  S3-2). 이 파일은 "분류가 제품·SKU에 실제로 붙는가"까지만 본다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.pagination import DEFAULT_PAGE_SIZE
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_product, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
PROFILES = "/api/v1/item-profiles"
PRODUCTS = "/api/v1/products"
BRANDS = "/api/v1/brands"
SKUS = "/api/v1/skus"


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "trade@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def _create(client: TestClient, code: str = "SKINCARE", *, key: str = "p1") -> Response:
    return client.post(
        PROFILES,
        json={"code": code, "name_ko": "스킨케어", "description": "기초 화장품"},
        headers={"Idempotency-Key": key},
    )


# ── 분류 자체 ──────────────────────────────────────────────────────────────


def test_create_then_list(trader: TestClient) -> None:
    """품목군을 만들고 목록에서 다시 본다"""
    created = _create(trader)
    assert created.status_code == 201, created.text

    listed = trader.get(PROFILES).json()
    assert listed["total"] == 1
    assert listed["items"][0]["code"] == "SKINCARE"
    assert listed["items"][0]["name_ko"] == "스킨케어"


def test_duplicate_code_is_rejected(trader: TestClient) -> None:
    """같은 코드는 두 번 등록되지 않는다 (§17.4 부분 유니크)"""
    _create(trader, key="a")
    response = _create(trader, key="b")

    assert response.status_code == 422
    assert "품목군 코드" in response.json()["error"]["detail"]["code"]


def test_double_click_creates_one(trader: TestClient) -> None:
    """더블클릭해도 1건이다 (§17.4 / GC-A3)"""
    first = _create(trader, key="same")
    second = _create(trader, key="same")

    assert first.json() == second.json()
    assert trader.get(PROFILES).json()["total"] == 1


def test_viewer_cannot_create_but_can_read(trader: TestClient) -> None:
    """조회 역할은 품목군을 만들 수 없고 볼 수는 있다 (§18.1)"""
    _create(trader)
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))

    with TestClient(app) as client:
        client.post(LOGIN, json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD})

        assert _create(client, "COLOR", key="v1").status_code == 403
        assert client.get(PROFILES).json()["total"] == 1


def test_list_is_paginated_with_default_50(trader: TestClient) -> None:
    """목록 기본 크기는 50이다 (§18.4)"""
    body = trader.get(PROFILES).json()

    assert body["size"] == DEFAULT_PAGE_SIZE
    assert set(body) == {"items", "total", "page", "size"}


def test_unknown_profile_returns_404(trader: TestClient) -> None:
    """없는 품목군 상세는 404다"""
    assert trader.get(f"{PROFILES}/999999").status_code == 404


# ── 제품·SKU에 붙는가 (§4.8) ───────────────────────────────────────────────


def _brand_id(client: TestClient) -> int:
    response = client.post(
        BRANDS,
        json={"brand_code": "BRD-001", "name_ko": "테스트 브랜드"},
        headers={"Idempotency-Key": "b1"},
    )
    return int(response.json()["id"])


def test_a_product_can_carry_a_profile(trader: TestClient) -> None:
    """제품에 품목군을 붙일 수 있다 (요건 세트는 처방 단위 — §4.8)"""
    profile_id = _create(trader).json()["id"]
    body: dict[str, Any] = {
        "brand_id": _brand_id(trader),
        "product_code": "PRD-001",
        "name_ko": "수분 세럼 처방",
        "item_profile_id": profile_id,
    }

    created = trader.post(PRODUCTS, json=body, headers={"Idempotency-Key": "pr1"})

    assert created.status_code == 201, created.text
    assert created.json()["item_profile_id"] == profile_id
    assert created.json()["item_profile_name_ko"] == "스킨케어"
    # 목록에서도 함께 보인다(조인 1회 — §18.4 N+1 금지).
    assert trader.get(PRODUCTS).json()["items"][0]["item_profile_name_ko"] == "스킨케어"


def test_a_sku_can_carry_a_profile(trader: TestClient) -> None:
    """SKU에도 붙일 수 있다 (서류·마일스톤 세트는 SKU·선적 단위 — §4.8)"""
    profile_id = _create(trader).json()["id"]

    created = trader.post(
        SKUS,
        json={
            "sku_code": "SER-001",
            "name_ko": "수분 세럼",
            "product_id": create_product(),
            "item_profile_id": profile_id,
        },
        headers={"Idempotency-Key": "s1"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["item_profile_name_ko"] == "스킨케어"


def test_a_profile_is_optional(trader: TestClient) -> None:
    """품목군 없이도 제품·SKU가 존재한다 (분류는 선택이다)"""
    created = trader.post(
        SKUS,
        json={"sku_code": "SER-001", "name_ko": "수분 세럼", "product_id": create_product()},
        headers={"Idempotency-Key": "s1"},
    )

    assert created.status_code == 201
    assert created.json()["item_profile_id"] is None


def test_an_unknown_profile_is_rejected(trader: TestClient) -> None:
    """존재하지 않는 품목군을 지정하면 거절된다"""
    response = trader.post(
        SKUS,
        json={
            "sku_code": "SER-001",
            "name_ko": "수분 세럼",
            "product_id": create_product(),
            "item_profile_id": 999_999,
        },
        headers={"Idempotency-Key": "s1"},
    )

    assert response.status_code == 422
    assert "품목군" in response.json()["error"]["detail"]["item_profile_id"]
