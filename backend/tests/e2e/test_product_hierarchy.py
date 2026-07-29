"""K. 보안·품질 — 브랜드→제품(처방) 등록·목록 (DESIGN.md §4.1 / §18.4 / WBS S1-1).

§4.1의 "인증·원산지 판정은 처방 단위, 재고·판매는 SKU 단위"가 성립하려면
처방이 SKU와 **별도로** 등록·조회되어야 한다. 이 파일이 그 계층을 고정한다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.csv_export import UTF8_BOM
from app.core.pagination import DEFAULT_PAGE_SIZE
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
BRANDS = "/api/v1/brands"
PRODUCTS = "/api/v1/products"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login_as(client: TestClient, email: str) -> None:
    response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture
def trader(client: TestClient) -> TestClient:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")
    return client


def _create_brand(client: TestClient, code: str = "BRD-001", *, key: str = "b1") -> Response:
    return client.post(
        BRANDS,
        json={"brand_code": code, "name_ko": "테스트 브랜드"},
        headers={"Idempotency-Key": key},
    )


def _create_product(
    client: TestClient, brand_id: int, code: str = "PRD-001", *, key: str = "p1", **extra: Any
) -> Response:
    body: dict[str, Any] = {
        "brand_id": brand_id,
        "product_code": code,
        "name_ko": "수분 세럼 처방",
    }
    body.update(extra)
    return client.post(PRODUCTS, json=body, headers={"Idempotency-Key": key})


# ── 관통 ───────────────────────────────────────────────────────────────────


def test_brand_then_product_chain(trader: TestClient) -> None:
    """브랜드 → 제품(처방)이 한 줄로 이어진다 (§22 렌즈 11)"""
    brand = _create_brand(trader)
    assert brand.status_code == 201, brand.text
    brand_id = brand.json()["id"]

    product = _create_product(trader, brand_id)
    assert product.status_code == 201, product.text

    body = product.json()
    assert body["brand_id"] == brand_id
    assert body["brand_name_ko"] == "테스트 브랜드"
    assert body["status"] == "ACTIVE"

    detail = trader.get(f"{PRODUCTS}/{body['id']}").json()
    assert detail["product_code"] == "PRD-001"


# ── 등록 규율 ──────────────────────────────────────────────────────────────


def test_duplicate_brand_code_is_rejected(trader: TestClient) -> None:
    """같은 브랜드 코드는 두 번 등록되지 않는다 (§17.4 부분 유니크)"""
    _create_brand(trader, key="b1")
    response = _create_brand(trader, key="b2")

    assert response.status_code == 422
    assert "브랜드 코드" in response.json()["error"]["detail"]["brand_code"]


def test_duplicate_product_code_is_rejected(trader: TestClient) -> None:
    """같은 제품 코드는 두 번 등록되지 않는다"""
    brand_id = _create_brand(trader).json()["id"]
    _create_product(trader, brand_id, key="p1")
    response = _create_product(trader, brand_id, key="p2")

    assert response.status_code == 422
    assert "제품 코드" in response.json()["error"]["detail"]["product_code"]


def test_product_under_an_unknown_brand_is_rejected(trader: TestClient) -> None:
    """존재하지 않는 브랜드에는 제품을 달 수 없다"""
    response = _create_product(trader, 999_999)

    assert response.status_code == 422
    assert "브랜드" in response.json()["error"]["detail"]["brand_id"]


def test_double_click_creates_one_brand(trader: TestClient) -> None:
    """더블클릭해도 브랜드는 1건이다 (§17.4 / GC-A3)"""
    first = _create_brand(trader, key="same")
    second = _create_brand(trader, key="same")

    assert first.json() == second.json()
    assert trader.get(BRANDS).json()["total"] == 1


def test_double_click_creates_one_product(trader: TestClient) -> None:
    """더블클릭해도 제품은 1건이다 (§17.4 / GC-A3)"""
    brand_id = _create_brand(trader).json()["id"]
    first = _create_product(trader, brand_id, key="same")
    second = _create_product(trader, brand_id, key="same")

    assert first.json() == second.json()
    assert trader.get(PRODUCTS).json()["total"] == 1


def test_unknown_product_status_is_rejected(trader: TestClient) -> None:
    """제품 상태값은 정해진 것만 받는다"""
    brand_id = _create_brand(trader).json()["id"]
    response = _create_product(trader, brand_id, status="SOLD_OUT")
    assert response.status_code == 422


# ── 권한 (§18.1) ───────────────────────────────────────────────────────────


def test_viewer_cannot_create_masters(client: TestClient) -> None:
    """조회 역할은 브랜드·제품을 등록할 수 없다"""
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    _login_as(client, "viewer@example.com")

    assert _create_brand(client).status_code == 403
    assert _create_product(client, 1).status_code == 403


def test_anonymous_cannot_read_masters(client: TestClient) -> None:
    """로그인 없이 브랜드·제품 목록을 볼 수 없다 (§18.1 IDOR 차단)"""
    assert client.get(BRANDS).status_code == 401
    assert client.get(PRODUCTS).status_code == 401


# ── 목록·CSV (§18.4 / §12.2) ───────────────────────────────────────────────


def test_lists_are_paginated_with_default_50(trader: TestClient) -> None:
    """브랜드·제품 목록 기본 크기는 50이다"""
    for path in (BRANDS, PRODUCTS):
        body = trader.get(path).json()
        assert body["size"] == DEFAULT_PAGE_SIZE, path
        assert set(body) == {"items", "total", "page", "size"}, path


def test_product_csv_starts_with_bom_and_carries_korean(trader: TestClient) -> None:
    """제품 CSV는 BOM으로 시작하고 한글이 손실되지 않는다 (§12.2)"""
    brand_id = _create_brand(trader).json()["id"]
    _create_product(trader, brand_id)

    raw = trader.get(f"{PRODUCTS}/export.csv").content.decode("utf-8")
    assert raw.startswith(UTF8_BOM)

    rows = list(csv.reader(io.StringIO(raw.lstrip(UTF8_BOM))))
    assert rows[0] == ["제품코드", "제품명(국문)", "제품명(영문)", "브랜드코드", "상태"]
    assert rows[1][1] == "수분 세럼 처방"


def test_brand_csv_export_is_reachable(trader: TestClient) -> None:
    """브랜드 CSV 경로가 상세 조회에 먹히지 않는다 (라우트 순서 회귀)

    `/brands/{brand_id}`를 먼저 선언하면 "export.csv"를 id로 해석해 422가 난다.
    """
    _create_brand(trader)
    response = trader.get(f"{BRANDS}/export.csv")

    assert response.status_code == 200
    assert response.content.decode("utf-8").startswith(UTF8_BOM)
