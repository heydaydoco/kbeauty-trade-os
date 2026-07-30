"""C. 인증·원산지 — 성분·규칙·전성분의 등록 경로 (DESIGN.md §4.3 / ADR-03 / WBS S1-2).

성분 규칙은 sku_hs_codes와 같은 지위다 — 판정이 아니라 **사람이 확인한 규칙의
기록**이고(§1 비범위), 근거 없이는 못 적는다(ADR-03). 이 파일은 그 경로를
고정한다. 스크리닝(대조 리포트)은 test_ingredient_screening.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.time import today_kst
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_product, create_user

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
INGREDIENTS = "/api/v1/ingredients"
PRODUCTS = "/api/v1/products"

Record = Callable[..., Response]


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
def viewer() -> Iterator[TestClient]:
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def _register(client: TestClient, *, key: str = "ing1", **overrides: Any) -> Response:
    body: dict[str, Any] = {"inci_name": "Niacinamide", "name_ko": "나이아신아마이드"}
    body.update(overrides)
    return client.post(INGREDIENTS, json=body, headers={"Idempotency-Key": key})


@pytest.fixture
def ingredient_id(trader: TestClient) -> int:
    created = _register(trader)
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


@pytest.fixture
def record_rule(trader: TestClient, ingredient_id: int) -> Record:
    def _record(*, key: str = "rule1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            "country_code": "US",
            "rule_type": "PROHIBITED",
            "source_url": "https://www.fda.gov/cosmetics",
            "last_verified_on": today_kst().isoformat(),
        }
        body.update(overrides)
        return trader.post(
            f"{INGREDIENTS}/{ingredient_id}/rules", json=body, headers={"Idempotency-Key": key}
        )

    return _record


# ── 성분 마스터 ─────────────────────────────────────────────────────────────


def test_register_then_list(trader: TestClient) -> None:
    """성분을 등록하고 목록에서 다시 본다 (§22 렌즈 11)"""
    created = _register(trader)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["inci_name"] == "Niacinamide"
    assert body["name_ko"] == "나이아신아마이드"

    listed = trader.get(INGREDIENTS).json()
    assert listed["total"] == 1
    assert listed["items"][0]["inci_name"] == "Niacinamide"


def test_case_and_spacing_variants_are_the_same_ingredient(trader: TestClient) -> None:
    """'Aqua'와 ' AQUA '는 같은 성분이다 — 정규화 유일이 중복을 잡는다"""
    first = _register(trader, key="a1", inci_name="Aqua")
    assert first.status_code == 201, first.text

    duplicate = _register(trader, key="a2", inci_name="  AQUA  ")
    assert duplicate.status_code == 422, duplicate.text
    assert "이미 등록된 성분" in duplicate.text


def test_display_form_is_preserved(trader: TestClient) -> None:
    """표기(대소문자)는 보존된다 — 라벨 인쇄가 이 표기를 쓴다 (§4.3·§4.5)"""
    created = _register(trader, inci_name="Sodium  Hyaluronate")
    assert created.status_code == 201, created.text
    # 공백만 축약하고 대소문자는 그대로다.
    assert created.json()["inci_name"] == "Sodium Hyaluronate"


@pytest.mark.group_j
def test_same_key_replays_the_first_result(trader: TestClient) -> None:
    """같은 멱등 키의 재요청은 최초 결과를 그대로 돌려준다 (§17.4 더블클릭)"""
    first = _register(trader, key="dup")
    replay = _register(trader, key="dup")

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert trader.get(INGREDIENTS).json()["total"] == 1


@pytest.mark.group_k
def test_viewer_cannot_register(viewer: TestClient) -> None:
    """조회 역할은 성분을 등록할 수 없다 (§18.1 — 서버가 강제한다)"""
    denied = _register(viewer)
    assert denied.status_code == 403, denied.text


@pytest.mark.group_k
def test_missing_ingredient_is_404(trader: TestClient) -> None:
    missing = trader.get(f"{INGREDIENTS}/999999")
    assert missing.status_code == 404, missing.text


# ── 성분×국가 규칙 (§4.3 / ADR-03) ─────────────────────────────────────────


def test_record_rule_then_list(trader: TestClient, ingredient_id: int, record_rule: Record) -> None:
    """규칙을 적고 목록에서 다시 본다"""
    created = record_rule()
    assert created.status_code == 201, created.text

    listed = trader.get(f"{INGREDIENTS}/{ingredient_id}/rules").json()
    assert listed["total"] == 1
    assert listed["items"][0]["rule_type"] == "PROHIBITED"


def test_country_code_is_normalized_to_uppercase(record_rule: Record) -> None:
    """'us'로 적어도 'US'로 저장된다 — 유일키가 표기 차이에 뚫리면 안 된다"""
    created = record_rule(country_code="us")
    assert created.status_code == 201, created.text
    assert created.json()["country_code"] == "US"


def test_restricted_requires_a_limit(record_rule: Record) -> None:
    """한도 없는 제한은 거부된다 — 스크리닝이 판정 불능이 된다 (양방향 CHECK ①)"""
    rejected = record_rule(rule_type="RESTRICTED")
    assert rejected.status_code == 422, rejected.text
    assert "농도 한도가 필요" in rejected.text


def test_prohibited_rejects_a_limit(record_rule: Record) -> None:
    """한도 있는 금지는 거부된다 — 어느 쪽이 진실인지 모호해진다 (양방향 CHECK ②)"""
    rejected = record_rule(rule_type="PROHIBITED", max_concentration_pct="2.0")
    assert rejected.status_code == 422, rejected.text
    assert "농도 한도를 넣지 않습니다" in rejected.text


def test_rule_without_evidence_is_rejected(record_rule: Record) -> None:
    """근거링크가 링크가 아니면 거부된다 (ADR-03 — 링크가 아니면 근거가 아니다)"""
    rejected = record_rule(source_url="담당자가 확인함")
    assert rejected.status_code == 422, rejected.text


def test_rule_verified_in_the_future_is_rejected(record_rule: Record) -> None:
    """미래의 최종확인일은 거부된다 (ADR-03 — 확인은 이미 한 일이다)"""
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    rejected = record_rule(last_verified_on=tomorrow)
    assert rejected.status_code == 422, rejected.text


def test_second_rule_for_the_same_country_is_rejected(record_rule: Record) -> None:
    """같은 성분×국가에 규칙은 한 행이다 — 금지·제한 공존 불허 (웹 세션 판정)

    공존을 허용하면 스크리닝이 같은 성분을 "금지"이면서 "제한초과"로도 집계하는
    모순 리포트가 가능해진다.
    """
    first = record_rule(key="r1", rule_type="PROHIBITED")
    assert first.status_code == 201, first.text

    conflicting = record_rule(key="r2", rule_type="RESTRICTED", max_concentration_pct="2.0")
    assert conflicting.status_code == 422, conflicting.text
    assert "공존할 수 없습니다" in conflicting.text


# ── 전성분 (제품×성분 — §4.3 "함량·표시순서") ──────────────────────────────


def _add(
    client: TestClient, product_id: int, *, key: str, ingredient_id: int, **overrides: Any
) -> Response:
    body: dict[str, Any] = {"ingredient_id": ingredient_id, "display_order": 1}
    body.update(overrides)
    return client.post(
        f"{PRODUCTS}/{product_id}/ingredients", json=body, headers={"Idempotency-Key": key}
    )


def test_full_formula_is_listed_in_display_order(trader: TestClient) -> None:
    """전성분은 표시순서대로 나온다 — INCI 라벨 표기 순서의 원천이다 (§4.3)"""
    product_id = create_product()
    water = _register(trader, key="w", inci_name="Aqua", name_ko="정제수")
    active = _register(trader, key="n", inci_name="Niacinamide")
    assert water.status_code == 201 and active.status_code == 201

    second = _add(
        trader,
        product_id,
        key="pi2",
        ingredient_id=int(active.json()["id"]),
        display_order=2,
        concentration_pct="5.0",
    )
    first = _add(trader, product_id, key="pi1", ingredient_id=int(water.json()["id"]))
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listed = trader.get(f"{PRODUCTS}/{product_id}/ingredients").json()
    assert listed["total"] == 2
    assert [item["inci_name"] for item in listed["items"]] == ["Aqua", "Niacinamide"]
    assert listed["items"][0]["ingredient_name_ko"] == "정제수"
    # 함량은 비공개(=미입력)일 수 있다 — 문자열 Decimal로 내려온다(float 금지).
    # DB가 Numeric(7,4) 자릿수로 돌려주므로("5.0000") 값으로 비교한다.
    assert listed["items"][0]["concentration_pct"] is None
    assert Decimal(listed["items"][1]["concentration_pct"]) == Decimal("5.0")


def test_same_ingredient_twice_in_one_formula_is_rejected(
    trader: TestClient, ingredient_id: int
) -> None:
    product_id = create_product()
    first = _add(trader, product_id, key="d1", ingredient_id=ingredient_id)
    assert first.status_code == 201, first.text

    duplicate = _add(trader, product_id, key="d2", ingredient_id=ingredient_id, display_order=2)
    assert duplicate.status_code == 422, duplicate.text
    assert "이미 있습니다" in duplicate.text


@pytest.mark.group_k
def test_formula_of_a_missing_product_is_404(trader: TestClient, ingredient_id: int) -> None:
    missing = _add(trader, 999999, key="np", ingredient_id=ingredient_id)
    assert missing.status_code == 404, missing.text
