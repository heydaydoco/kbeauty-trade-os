"""A·F·K. S3-1 PR-3 마스터 보강 — 거래처 영문명·영문주소, SKU MOQ (ADR-0056).

전표 스냅샷의 원천이 되는 값이 등록·조회·CSV 왕복에서 손실되지 않고, 요청 스키마가 모르는
필드(예: status·credit 우회 필드)를 조용히 무시하지 않고 거부한다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_product, create_user

pytestmark = pytest.mark.group_a


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade-ms@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login",
            json={"email": "trade-ms@example.com", "password": DEFAULT_PASSWORD},
        ).is_success
        yield client


def _partner_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "partner_code": "PTN-EN1",
        "name_ko": "영문 거래처",
        "type_codes": ["BUYER"],
        "name_en": "Sample Buyer Co., Ltd.",
        "address_en": "1 Example Street, Los Angeles, CA, USA",
    }
    body.update(over)
    return body


def _sku_body(product_id: int, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sku_code": "SKU-MOQ1",
        "name_ko": "MOQ 세럼",
        "kind": "SINGLE",
        "product_id": product_id,
        "moq": 500,
    }
    body.update(over)
    return body


def test_partner_english_name_and_address_are_stored_and_returned(trader: TestClient) -> None:
    """영문 상호·주소가 등록·상세 응답에 그대로 나온다(전표 스냅샷의 원천)"""
    created = trader.post(
        "/api/v1/partners", json=_partner_body(), headers={"Idempotency-Key": "ms1"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["name_en"] == "Sample Buyer Co., Ltd."
    detail = trader.get(f"/api/v1/partners/{created.json()['id']}").json()
    assert detail["address_en"].startswith("1 Example Street")


def test_partner_request_rejects_unknown_fields(trader: TestClient) -> None:
    """모르는 필드는 조용히 무시되지 않고 422다(extra=forbid — 여신·상태 우회 필드 차단)"""
    response = trader.post(
        "/api/v1/partners",
        json=_partner_body(is_admin_approved=True),
        headers={"Idempotency-Key": "ms2"},
    )
    assert response.status_code == 422, response.text


def test_sku_request_rejects_unknown_fields(trader: TestClient) -> None:
    """SKU 등록도 모르는 필드를 거부한다"""
    product_id = create_product("PRD-MS")
    response = trader.post(
        "/api/v1/skus", json=_sku_body(product_id, list_price=1), headers={"Idempotency-Key": "ms3"}
    )
    assert response.status_code == 422, response.text


def test_sku_moq_round_trips_and_zero_is_rejected(trader: TestClient) -> None:
    """MOQ는 등록·조회에 그대로 나오고 0 이하는 거부된다(DB CHECK가 마지막 층)"""
    product_id = create_product("PRD-MS2")
    ok = trader.post("/api/v1/skus", json=_sku_body(product_id), headers={"Idempotency-Key": "ms4"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["moq"] == 500
    zero = trader.post(
        "/api/v1/skus",
        json=_sku_body(product_id, sku_code="SKU-MOQ0", moq=0),
        headers={"Idempotency-Key": "ms5"},
    )
    assert zero.status_code == 422, zero.text

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE skus SET moq = 0 WHERE sku_code = 'SKU-MOQ1'"))


def test_new_columns_survive_the_csv_round_trip(trader: TestClient) -> None:
    """거래처 영문명·영문주소, SKU MOQ가 내려받아 그대로 올리면 변화 0으로 왕복된다"""
    product_id = create_product("PRD-MS3")
    assert trader.post(
        "/api/v1/partners", json=_partner_body(), headers={"Idempotency-Key": "ms6"}
    ).is_success
    assert trader.post(
        "/api/v1/skus", json=_sku_body(product_id), headers={"Idempotency-Key": "ms7"}
    ).is_success

    partner_csv = trader.get("/api/v1/partners/export.csv").content
    text_ = partner_csv.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text_)))
    assert rows[0][-2:] == ["영문명", "영문주소"]
    assert rows[1][-2] == "Sample Buyer Co., Ltd."
    staged = trader.post(
        "/api/v1/imports/partners/staging",
        files={"file": ("p.csv", partner_csv, "text/csv")},
        headers={"Idempotency-Key": "ms8"},
    )
    assert staged.status_code == 201, staged.text
    assert staged.json()["unchanged_rows"] == 1 and staged.json()["changed_rows"] == 0

    sku_csv = trader.get("/api/v1/skus/roundtrip.csv").content
    sku_rows = list(csv.reader(io.StringIO(sku_csv.decode("utf-8-sig"))))
    assert sku_rows[0][-1] == "MOQ" and sku_rows[1][-1] == "500"
