"""C·G. 자재 BOM — 원산지 3값과 원가 마스킹 (§4.4·§2 / GC-B1·B2 / ADR-0024).

이 파일이 고정하는 것: 원산지 기본값이 **미상**이라는 것(WBS S1-2 DoD ②),
그리고 BOM 단가가 조회 역할에게는 **필드 자체로 없다**는 것(행을 감추는
sku_prices와 다른 방식 — ADR-0024 대 ADR-0018).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_material, create_product, create_user

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
PRODUCTS = "/api/v1/products"
MATERIALS = "/api/v1/materials"

Add = Callable[..., Response]


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


@pytest.fixture
def cert() -> Iterator[TestClient]:
    """인증 담당 — 원산지·HS는 보되 원가도 볼 수 있는 역할(ADR-0018 목록)."""
    yield from _client("cert@example.com", RoleCode.CERT)


@pytest.fixture
def product_id() -> int:
    return create_product()


@pytest.fixture
def add_line(trader: TestClient, product_id: int) -> Add:
    def _add(*, key: str = "bom1", material_id: int | None = None, **overrides: Any) -> Response:
        body: dict[str, Any] = {
            # ★ 자재 코드를 멱등 키에서 파생시킨다. 고정값을 쓰면 한 테스트가
            #   라인을 둘 만드는 순간 준비 코드가 유니크에 걸리고, 그 실패가
            #   기능 결함처럼 보인다(PROGRESS 주의 인계 ⑥).
            "material_id": (
                material_id if material_id is not None else create_material(f"M-{key}"[:40])
            ),
            "quantity": "12.5",
            "quantity_unit": "g",
        }
        body.update(overrides)
        return trader.post(
            f"{PRODUCTS}/{product_id}/boms", json=body, headers={"Idempotency-Key": key}
        )

    return _add


# ── 원산지 3값 (WBS DoD ② / GC-B2) ─────────────────────────────────────────


def test_origin_status_defaults_to_unknown(add_line: Add) -> None:
    """★ 원산지를 안 적으면 **미상**이다 (§4.4 "미상은 판정 시 역외 간주")

    기본값을 역내나 NULL로 두면 확인하지 않은 자재가 유리한 쪽으로 계산된다.
    판정(S3-4)이 서기 전에 마스터가 먼저 보수적이어야 한다.
    """
    created = add_line()
    assert created.status_code == 201, created.text
    assert created.json()["origin_status"] == "UNKNOWN"


def test_all_three_origin_statuses_are_accepted(add_line: Add) -> None:
    """역내·역외·미상 3값이 전부 저장된다"""
    for index, status_value in enumerate(("DOMESTIC", "FOREIGN", "UNKNOWN")):
        created = add_line(key=f"o{index}", origin_status=status_value)
        assert created.status_code == 201, created.text
        assert created.json()["origin_status"] == status_value


def test_unknown_origin_value_is_rejected(add_line: Add) -> None:
    """정해지지 않은 원산지 값은 거부된다 (§17.5 CHECK + 스키마)"""
    rejected = add_line(origin_status="PROBABLY_KOREA")
    assert rejected.status_code == 422, rejected.text


# ── 원가 마스킹 (§2 / §20 G / ADR-0024) ────────────────────────────────────


@pytest.mark.group_g
def test_a_trader_sees_the_cost_fields(add_line: Add, trader: TestClient, product_id: int) -> None:
    """원가를 볼 수 있는 역할에게는 단가·통화가 그대로 온다"""
    created = add_line(unit_cost="12.34", currency="USD")
    assert created.status_code == 201, created.text
    # 정수 최소단위로 저장된다(USD 12.34 → 1234).
    assert created.json()["unit_cost"] == 1234

    listed = trader.get(f"{PRODUCTS}/{product_id}/boms").json()
    assert listed["items"][0]["unit_cost"] == 1234
    assert listed["items"][0]["currency"] == "USD"


@pytest.mark.group_g
def test_a_viewer_gets_no_cost_fields_at_all(
    add_line: Add, viewer: TestClient, product_id: int
) -> None:
    """★ 조회 역할의 응답에는 원가 **필드가 없다** — null이 아니라 부재다

    null로 주면 "0원"이나 "미입력"으로 읽히고, 그 오독 자체가 원가 추정 단서다.
    """
    assert add_line(unit_cost="12.34", currency="USD").status_code == 201

    listed = viewer.get(f"{PRODUCTS}/{product_id}/boms")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    (line,) = body["items"]
    assert "unit_cost" not in line
    assert "currency" not in line
    # 원가 값이 응답 본문 어디에도 없다(다른 키에 섞여 나가지 않는다).
    assert "1234" not in listed.text


@pytest.mark.group_g
def test_a_viewer_still_sees_the_line_itself(
    add_line: Add, viewer: TestClient, product_id: int
) -> None:
    """★ 행은 그대로 보인다 — 행을 감추는 sku_prices와 다른 점 (ADR-0024)

    소요량·원산지상태·HS6은 인증·원산지 업무에 필요하다. 행을 지우면 "BOM이
    비어 있다"로 읽혀 §6.3의 전수 대조가 조용히 어긋난다.
    """
    assert add_line(unit_cost="12.34", currency="USD", origin_status="FOREIGN").status_code == 201

    body = viewer.get(f"{PRODUCTS}/{product_id}/boms").json()
    assert body["total"] == 1  # 건수도 줄지 않는다
    (line,) = body["items"]
    assert Decimal(line["quantity"]) == Decimal("12.5")
    assert line["quantity_unit"] == "g"
    assert line["origin_status"] == "FOREIGN"
    assert line["material_code"]


@pytest.mark.group_g
def test_a_cert_user_sees_the_cost(add_line: Add, cert: TestClient, product_id: int) -> None:
    """인증 역할은 원가를 본다 — 판정 계산이 단가를 쓰기 때문이다(§6.3)"""
    assert add_line(unit_cost="12.34", currency="USD").status_code == 201

    body = cert.get(f"{PRODUCTS}/{product_id}/boms").json()
    assert body["items"][0]["unit_cost"] == 1234


@pytest.mark.group_k
def test_a_viewer_cannot_add_a_bom_line(viewer: TestClient, product_id: int) -> None:
    """조회 역할은 BOM을 등록할 수 없다 (§18.1)"""
    denied = viewer.post(
        f"{PRODUCTS}/{product_id}/boms",
        json={"material_id": create_material(), "quantity": "1", "quantity_unit": "g"},
        headers={"Idempotency-Key": "v-bom"},
    )
    assert denied.status_code == 403, denied.text


# ── 금액 규율 (§2 ADR-02 / GC-G1) ──────────────────────────────────────────


def test_cost_and_currency_must_come_together(add_line: Add) -> None:
    """단가만 있고 통화가 없으면 거부된다 — 해석할 수 없는 금액은 금액이 아니다"""
    rejected = add_line(unit_cost="12.34")
    assert rejected.status_code == 422, rejected.text
    assert "함께" in rejected.text


def test_cost_may_be_omitted_entirely(add_line: Add) -> None:
    """단가 미상으로도 등록된다 — BOM은 원산지·소요량이 먼저다"""
    created = add_line()
    assert created.status_code == 201, created.text
    assert created.json()["unit_cost"] is None


def test_too_many_decimal_places_are_rejected(add_line: Add) -> None:
    """통화 자릿수를 넘는 단가는 거부된다 (KRW는 소수점 0자리)"""
    rejected = add_line(unit_cost="1234.56", currency="KRW")
    assert rejected.status_code == 422, rejected.text
    assert "소수점" in rejected.text


def test_unknown_currency_is_rejected(add_line: Add) -> None:
    """등록되지 않은 통화는 거부된다 — 자릿수 출처는 서버 하나다"""
    rejected = add_line(unit_cost="10", currency="XYZ")
    assert rejected.status_code == 422, rejected.text


# ── 구조 (§17.4 / §18.4) ───────────────────────────────────────────────────


def test_same_material_twice_in_one_bom_is_rejected(add_line: Add) -> None:
    material_id = create_material("MAT-DUP")
    first = add_line(key="d1", material_id=material_id)
    assert first.status_code == 201, first.text

    duplicate = add_line(key="d2", material_id=material_id)
    assert duplicate.status_code == 422, duplicate.text
    assert "이미 있습니다" in duplicate.text


@pytest.mark.group_j
def test_same_key_replays_the_first_result(
    add_line: Add, trader: TestClient, product_id: int
) -> None:
    """BOM 추가 더블클릭 → 1건 (§17.4)"""
    material_id = create_material("MAT-IDEM")
    first = add_line(key="bom-dup", material_id=material_id)
    replay = add_line(key="bom-dup", material_id=material_id)

    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert trader.get(f"{PRODUCTS}/{product_id}/boms").json()["total"] == 1


@pytest.mark.group_k
def test_bom_of_a_missing_product_is_404(trader: TestClient) -> None:
    missing = trader.get(f"{PRODUCTS}/999999/boms")
    assert missing.status_code == 404, missing.text


@pytest.mark.group_k
def test_materials_csv_export_has_bom_and_korean(trader: TestClient) -> None:
    """자재 CSV — BOM으로 시작하고 한글이 무손실이다. **원가는 없다**"""
    created = trader.post(
        MATERIALS,
        json={"material_code": "MAT-CSV", "name_ko": "정제수", "material_type": "RAW_MATERIAL"},
        headers={"Idempotency-Key": "csv"},
    )
    assert created.status_code == 201, created.text

    exported = trader.get(f"{MATERIALS}/export.csv")
    assert exported.status_code == 200, exported.text  # /{id}보다 앞 선언 확인
    assert exported.text.startswith("﻿")
    assert "정제수" in exported.text
    # 자재 마스터에는 단가 컬럼 자체가 없다 — CSV로도 원가가 나가지 않는다.
    assert "단가" not in exported.text


def test_absurdly_large_cost_is_rejected_with_a_field_message(add_line: Add) -> None:
    """★ 최소단위 환산이 BIGINT를 넘는 단가는 422다 — 500으로 새지 않는다

    DB의 numeric overflow는 IntegrityError가 아니라 DataError라 서비스의
    중복 처리 경로에 걸리지 않는다 — 스키마에서 막는다(리뷰 검출).
    """
    rejected = add_line(unit_cost="99999999999999999999", currency="KRW")
    assert rejected.status_code == 422, rejected.text
