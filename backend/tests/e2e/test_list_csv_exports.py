"""B. 목록 CSV 3종 — 라벨·성분규칙·BOM (§12.2 / §20 B "한글·BOM CSV").

S1-2 관찰 항목 "라벨·BOM·규칙 목록 CSV는 §12.2 엑셀 코어로"의 종결분이다.
이 파일이 고정하는 것: UTF-8 BOM·CRLF·한글 무손실, 그리고 **BOM CSV에 원가가
없다**는 것(웹 세션 승인 문면 "BOM=원가 열 미포함" — 역할 무관 같은 파일).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_ingredient,
    create_ingredient_rule,
    create_material,
    create_product,
    create_sku,
    create_user,
)

pytestmark = pytest.mark.group_b

LOGIN = "/api/v1/auth/login"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer@example.com", RoleCode.VIEWER)


def test_labels_csv_exports_all_skus_with_bom_and_crlf(trader: TestClient) -> None:
    """라벨 CSV — BOM·CRLF·한글, 파일 열은 없다(documents의 것)"""
    sku_id = create_sku("SKU-L1", name_ko="수분 세럼 30ml")
    created = trader.post(
        f"/api/v1/skus/{sku_id}/labels",
        json={
            "country_code": "US",
            "label_version": 1,
            "language": "en",
            "approval_status": "APPROVED",
            "inci_local_verified": True,
            "origin_mark_verified": False,
        },
        headers={"Idempotency-Key": "lbl1"},
    )
    assert created.status_code == 201, created.text

    response = trader.get("/api/v1/labels/export.csv")
    assert response.status_code == 200, response.text
    text = response.text
    assert text.startswith("﻿")
    assert "\r\n" in text
    header = text.removeprefix("﻿").splitlines()[0]
    assert header == "ID,SKU코드,국가,언어,판번,승인상태,컷인일,INCI현지검증,원산지표기검증"
    assert "SKU-L1" in text
    assert "APPROVED" in text


def test_ingredient_rules_csv_carries_the_evidence_trail(trader: TestClient) -> None:
    """규칙 CSV — INCI명으로 읽히고 근거링크·최종확인일이 실린다 (ADR-03)"""
    ingredient_id = create_ingredient("Hydroquinone", name_ko="하이드로퀴논")
    create_ingredient_rule(ingredient_id, country_code="US", rule_type="PROHIBITED")

    response = trader.get("/api/v1/ingredient-rules/export.csv")
    assert response.status_code == 200, response.text
    text = response.text
    assert text.startswith("﻿")
    header = text.removeprefix("﻿").splitlines()[0]
    assert header == "ID,INCI명,국가,규칙유형,최대함량(%),근거링크,최종확인일"
    assert "HYDROQUINONE" in text.upper()
    assert "PROHIBITED" in text


def test_bom_csv_has_no_cost_for_anyone(trader: TestClient, viewer: TestClient) -> None:
    """★ BOM CSV에 원가가 없다 — 열도, 값도, 역할 분기도 없다 (승인 문면·ADR-0024)"""
    product_id = create_product("PRD-B1", name_ko="수분 세럼")
    material_id = create_material("MAT-B1", name_ko="정제수")
    created = trader.post(
        f"/api/v1/products/{product_id}/boms",
        json={
            "material_id": material_id,
            "quantity": "70.5",
            "quantity_unit": "g",
            "unit_cost": "12.34",
            "currency": "USD",
            "origin_status": "DOMESTIC",
        },
        headers={"Idempotency-Key": "bom1"},
    )
    assert created.status_code == 201, created.text

    trader_text = trader.get("/api/v1/product-boms/export.csv").text
    header = trader_text.removeprefix("﻿").splitlines()[0]
    assert header == "ID,제품코드,자재코드,자재명(국문),소요량,단위,원산지상태"
    assert "PRD-B1" in trader_text
    assert "70.5" in trader_text
    # 원가는 어떤 표기로도 없다 — 사람 표기(12.34)도 최소단위(1234)도.
    assert "12.34" not in trader_text
    assert "1234" not in trader_text
    assert "USD" not in trader_text

    # 조회 역할도 같은 파일을 받는다 — 파일에 원가가 없으니 분기가 필요 없다.
    viewer_text = viewer.get("/api/v1/product-boms/export.csv").text
    assert viewer_text == trader_text
