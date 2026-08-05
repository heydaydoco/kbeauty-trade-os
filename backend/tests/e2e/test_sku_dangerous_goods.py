"""K. 보안·품질 — SKU 물류·DG 속성 (DESIGN.md §4.1·§7.7 / ADR-0016 정정·ADR-0003 ⑧).

■ 이 파일이 고정하는 경계

  잠그는 것: ① 세트는 DG 속성을 하나도 갖지 않는다 ② 비DG 행에 UN번호·Class·
  포장등급·LQ가 남아 있으면 안 된다 ③ 중량·박스입수·사용기한·알코올함량의 상식 범위.

  **잠그지 않는 것**: dg_flag=true인데 UN번호가 비어 있어도 등록은 통과한다.
  등록 시점에 DG 정보가 다 모여 있지 않은 것이 실무의 정상 상태이고, 차단해야
  할 지점은 등록이 아니라 선적이다(§7.7 DG 게이트, P4). 교차 정합(에어로졸=true
  인데 dg_flag=false)도 같은 이유로 여기서 막지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_partner,
    create_product,
    create_user,
)

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
SKUS = "/api/v1/skus"

Register = Callable[..., Response]


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "trade@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def register(trader: TestClient) -> Register:
    product_id = create_product()

    def _register(**overrides: Any) -> Response:
        body: dict[str, Any] = {
            "sku_code": "SER-001",
            "name_ko": "수분 세럼",
            "product_id": product_id,
        }
        body.update(overrides)
        if body.get("kind") == "SET":
            body.pop("product_id", None)
        return trader.post(SKUS, json=body, headers={"Idempotency-Key": "k1"})

    return _register


# ── 물류 속성 (§4.1 / ADR-0003 ⑧) ──────────────────────────────────────────


def test_logistics_attributes_round_trip(register: Register) -> None:
    """바코드·중량·박스입수·사용기한·제조사가 그대로 저장·반환된다

    제조사는 S1-3에서 거래처(유형 OEM) FK로 승격됐다(ADR-0020) — 응답의
    manufacturer_name은 파트너명 조인 값이다.
    """
    partner_id = create_partner("PTN-KOLMAR", name_ko="한국콜마", types=("OEM",))
    response = register(
        barcode="8801234567890",
        unit_weight_g="132.500",
        box_qty=24,
        shelf_life_months=36,
        manufacturer_partner_id=partner_id,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["barcode"] == "8801234567890"
    # ★ 소수는 문자열로 오간다 — float으로 바꾸면 §2 ADR-02의 Float 금지를
    #   우회하게 되고, 그 오차는 정산 대사에서 원인을 못 찾는다.
    assert body["unit_weight_g"] == "132.500"
    assert body["box_qty"] == 24
    assert body["shelf_life_months"] == 36
    assert body["manufacturer_partner_id"] == partner_id
    assert body["manufacturer_name"] == "한국콜마"


def test_manufacturer_must_be_an_oem_partner(register: Register) -> None:
    """OEM 유형이 아닌 거래처는 제조사가 될 수 없다 ([M1] 보강(S1-3) ⑤)"""
    partner_id = create_partner("PTN-FWD", name_ko="포워더만", types=("FORWARDER",))
    response = register(manufacturer_partner_id=partner_id)
    assert response.status_code == 422, response.text
    assert "OEM 유형" in response.json()["error"]["detail"]["manufacturer_partner_id"]


def test_unknown_manufacturer_partner_is_rejected(register: Register) -> None:
    response = register(manufacturer_partner_id=999_999)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit_weight_g", "0"),
        ("box_qty", 0),
        ("shelf_life_months", 0),
        ("alcohol_content_pct", "101"),
    ],
)
def test_out_of_range_values_are_rejected(register: Register, field: str, value: Any) -> None:
    """상식 범위를 벗어난 값은 거절된다 (중량·박스입수·사용기한 >0, 알코올 0~100)"""
    assert register(**{field: value}).status_code == 422


def test_omitted_optional_attributes_stay_empty(register: Register) -> None:
    """안 적은 속성은 비어 있다 (0이나 거짓 기본값으로 채우지 않는다)

    ★ 중량을 안 적었는데 0이 들어가면, 나중에 그 0이 "0g으로 확인된 값"과
      구분되지 않는다. 모르는 것은 모르는 채로 남아야 한다.
    """
    body = register().json()

    assert body["unit_weight_g"] is None
    assert body["box_qty"] is None
    assert body["manufacturer_partner_id"] is None
    assert body["manufacturer_name"] is None
    assert body["dg_flag"] is False


# ── DG: 세트 방향 (ADR-0016 부기) ──────────────────────────────────────────


def test_set_sku_cannot_carry_dangerous_goods_info(register: Register) -> None:
    """세트 SKU에는 위험물 정보를 넣을 수 없다 (판정은 구성품 단위 — P4까지 미결)"""
    response = register(sku_code="SET-001", name_ko="기획 세트", kind="SET", dg_flag=True)

    assert response.status_code == 422
    assert "구성품" in response.json()["error"]["detail"]["dg_flag"]


def test_set_sku_cannot_carry_a_flash_point_either(register: Register) -> None:
    """세트는 인화점 같은 근거값도 갖지 않는다 (DG 속성 전부 부재)"""
    response = register(sku_code="SET-001", name_ko="기획 세트", kind="SET", flash_point_c="13.0")

    assert response.status_code == 422
    assert "인화점" in response.json()["error"]["detail"]["dg_flag"]


# ── DG: 비DG 방향 (ADR-0016 정정) ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("un_number", "1993", "UN번호"),
        ("dg_class", "3", "Class"),
        ("packing_group", "III", "포장등급"),
        ("is_limited_quantity", True, "LQ"),
    ],
)
def test_classification_without_the_dg_flag_is_rejected(
    register: Register, field: str, value: Any, label: str
) -> None:
    """위험물이 아닌데 분류값만 적으면 거절된다 (분류는 DG 판정이 있어야 의미가 있다)"""
    response = register(**{field: value})

    assert response.status_code == 422
    assert label in response.json()["error"]["detail"]["dg_flag"]


def test_non_dg_sku_may_record_the_evidence_that_made_it_non_dg(register: Register) -> None:
    """★ 비DG 단품도 인화점·알코올함량·에어로졸·MSDS는 적을 수 있다

    이 셋은 "왜 DG가 아닌지"의 근거 입력값이다. 여기까지 잠그면 비DG 증빙을
    시스템에 남길 자리가 사라지고, 사람은 그 값을 엑셀로 되돌아간다.
    (ADR-0016 정정의 핵심 통과 케이스)
    """
    response = register(
        dg_flag=False,
        flash_point_c="65.0",
        alcohol_content_pct="4.50",
        is_aerosol=True,
        msds_url="https://example.com/msds.pdf",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dg_flag"] is False
    assert body["flash_point_c"] == "65.0"
    assert body["alcohol_content_pct"] == "4.50"
    assert body["is_aerosol"] is True
    assert body["msds_url"] == "https://example.com/msds.pdf"


# ── DG: 정상 등록 ──────────────────────────────────────────────────────────


def test_dangerous_goods_sku_registers_with_full_classification(register: Register) -> None:
    """위험물 SKU는 분류값과 함께 등록된다"""
    response = register(
        name_ko="헤어 스프레이",
        dg_flag=True,
        un_number="1950",
        dg_class="2.1",
        packing_group="II",
        flash_point_c="-20.0",
        alcohol_content_pct="55.00",
        is_aerosol=True,
        is_limited_quantity=True,
        msds_url="https://example.com/msds.pdf",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["un_number"] == "1950"
    assert body["dg_class"] == "2.1"
    assert body["packing_group"] == "II"
    # 인화점은 음수가 실재한다 — 하한 제약을 걸지 않은 이유가 이것이다.
    assert body["flash_point_c"] == "-20.0"
    assert body["is_limited_quantity"] is True


def test_incomplete_dangerous_goods_info_still_registers(register: Register) -> None:
    """★ 위험물인데 분류값이 비어 있어도 등록은 통과한다 (P4 게이트가 막을 일이다)

    등록 시점에 UN번호·MSDS가 다 모여 있는 경우는 드물다. 여기서 막으면
    사람은 SKU를 아예 등록하지 않거나 가짜 UN번호를 넣는다 — 둘 다 더 나쁘다.
    실제 차단은 선적에서 한다(§7.7 DG 게이트, Phase 4).
    """
    response = register(dg_flag=True)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dg_flag"] is True
    assert body["un_number"] is None
