"""A. 전표·정합 — 세트 구성 (DESIGN.md §4.2 / ADR-0016 A8 / GC-E1).

§4.2: 세트는 실물 재고이고 set_components가 그 구성을 정의한다. 조립·해체
원장(§8.2 ASSEMBLY/DISASSEMBLY)은 Phase 4이므로 여기서는 **구성의 정의**만 본다.

DB가 못 잠그는 두 규칙("세트에만 구성품을 담는다" / "구성품은 단품이어야
한다")은 다른 행을 봐야 판정되므로 서비스가 검증한다 — 이 파일이 그것을 지킨다.
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

pytestmark = pytest.mark.group_a

LOGIN = "/api/v1/auth/login"
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


def _single(client: TestClient, code: str, name_ko: str, **extra: Any) -> int:
    """단품 SKU를 만들고 id를 돌려준다."""
    body = {
        "sku_code": code,
        "name_ko": name_ko,
        "product_id": create_product(f"PRD-{code}"),
        **extra,
    }
    response = client.post(SKUS, json=body, headers={"Idempotency-Key": f"sku-{code}"})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _set(client: TestClient, code: str = "SET-001", name_ko: str = "기획 세트") -> int:
    response = client.post(
        SKUS,
        json={"sku_code": code, "name_ko": name_ko, "kind": "SET"},
        headers={"Idempotency-Key": f"sku-{code}"},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _add(
    client: TestClient, set_id: int, component_id: int, quantity: int, *, key: str
) -> Response:
    return client.post(
        f"{SKUS}/{set_id}/components",
        json={"component_sku_id": component_id, "quantity": quantity},
        headers={"Idempotency-Key": key},
    )


# ── 구성 정의 (GC-E1의 "본품 2 + 미니 1") ──────────────────────────────────


def test_set_composition_round_trip(trader: TestClient) -> None:
    """세트에 본품 2 + 미니 1을 담고 목록에서 다시 본다 (GC-E1의 구성 부분)"""
    set_id = _set(trader)
    main = _single(trader, "SER-001", "수분 세럼 30ml", shelf_life_months=36)
    mini = _single(trader, "SER-002", "수분 세럼 10ml", shelf_life_months=24)

    assert _add(trader, set_id, main, 2, key="c1").status_code == 201
    assert _add(trader, set_id, mini, 1, key="c2").status_code == 201

    listed = trader.get(f"{SKUS}/{set_id}/components").json()
    assert listed["total"] == 2
    by_code = {item["component_sku_code"]: item for item in listed["items"]}
    assert by_code["SER-001"]["quantity"] == 2
    assert by_code["SER-002"]["quantity"] == 1
    # 구성품별 사용기한이 함께 보인다 — 세트 로트 유통기한(MIN)의 근거를 화면에서
    # 보려면 필요하다(§4.2, 실제 MIN 계산은 로트가 서는 P4).
    assert by_code["SER-001"]["component_shelf_life_months"] == 36
    assert by_code["SER-002"]["component_shelf_life_months"] == 24


def test_double_click_adds_one_component(trader: TestClient) -> None:
    """더블클릭해도 구성품은 1건이다 (§17.4 / GC-A3)"""
    set_id = _set(trader)
    main = _single(trader, "SER-001", "수분 세럼")

    first = _add(trader, set_id, main, 2, key="same")
    second = _add(trader, set_id, main, 2, key="same")

    assert first.json() == second.json()
    assert trader.get(f"{SKUS}/{set_id}/components").json()["total"] == 1


def test_same_component_cannot_be_added_twice(trader: TestClient) -> None:
    """같은 구성품을 두 줄로 넣을 수 없다 (수량은 한 줄에서 관리한다)"""
    set_id = _set(trader)
    main = _single(trader, "SER-001", "수분 세럼")
    _add(trader, set_id, main, 2, key="c1")

    response = _add(trader, set_id, main, 3, key="c2")

    assert response.status_code == 422
    assert "이미 구성에" in response.json()["error"]["detail"]["component_sku_id"]


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantity_is_rejected(trader: TestClient, quantity: int) -> None:
    """수량은 0이나 음수일 수 없다"""
    set_id = _set(trader)
    main = _single(trader, "SER-001", "수분 세럼")

    assert _add(trader, set_id, main, quantity, key="c1").status_code == 422


# ── DB가 못 잠그는 두 규칙 (§4.2 / ADR-0016 A8) ────────────────────────────


def test_a_set_cannot_be_a_component_of_another_set(trader: TestClient) -> None:
    """★ 세트를 다른 세트의 구성품으로 넣을 수 없다 (ADR-0016 A8 — 중첩 세트 금지)

    §4.2는 중첩을 언급하지 않는다. 여는 것은 언제든 쉽고, 데이터가 쌓인 뒤
    잠그는 것은 불가능하므로 최소로 잠근다.
    """
    outer = _set(trader, "SET-001", "바깥 세트")
    inner = _set(trader, "SET-002", "안쪽 세트")

    response = _add(trader, outer, inner, 1, key="c1")

    assert response.status_code == 422
    assert "단품이어야" in response.json()["error"]["detail"]["component_sku_id"]


def test_a_single_sku_cannot_hold_components(trader: TestClient) -> None:
    """단품 SKU에는 구성품을 담을 수 없다 (구성은 세트의 것이다)"""
    holder = _single(trader, "SER-001", "수분 세럼")
    other = _single(trader, "SER-002", "미니 세럼")

    response = _add(trader, holder, other, 1, key="c1")

    assert response.status_code == 422
    assert "세트 SKU뿐" in response.json()["error"]["detail"]["set_sku_id"]


def test_a_set_cannot_contain_itself(trader: TestClient) -> None:
    """세트는 자기 자신을 구성품으로 가질 수 없다"""
    set_id = _set(trader)

    response = _add(trader, set_id, set_id, 1, key="c1")

    assert response.status_code == 422
    assert "자기 자신" in response.json()["error"]["detail"]["component_sku_id"]


def test_unknown_component_returns_404(trader: TestClient) -> None:
    """없는 SKU를 구성품으로 지정하면 404다"""
    set_id = _set(trader)

    assert _add(trader, set_id, 999_999, 1, key="c1").status_code == 404


# ── 권한·페이지네이션 ──────────────────────────────────────────────────────


def test_viewer_cannot_edit_but_can_read(trader: TestClient) -> None:
    """조회 역할은 구성을 바꿀 수 없고 볼 수는 있다 (§18.1)"""
    set_id = _set(trader)
    main = _single(trader, "SER-001", "수분 세럼")
    _add(trader, set_id, main, 2, key="c1")

    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    with TestClient(app) as client:
        client.post(LOGIN, json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD})

        assert _add(client, set_id, main, 5, key="c2").status_code == 403
        assert client.get(f"{SKUS}/{set_id}/components").json()["total"] == 1


def test_list_is_paginated_with_default_50(trader: TestClient) -> None:
    """목록 기본 크기는 50이다 (§18.4)"""
    set_id = _set(trader)

    body = trader.get(f"{SKUS}/{set_id}/components").json()

    assert body["size"] == DEFAULT_PAGE_SIZE
    assert set(body) == {"items", "total", "page", "size"}
