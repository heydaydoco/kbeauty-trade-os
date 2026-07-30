"""C. 인증·원산지 — 성분 스크리닝 3분류 (DESIGN.md §4.3 / §1 비범위 / WBS S1-2 DoD ①).

스크리닝은 판정이 아니다 — 사람이 등록한 규칙과 전성분의 기계적 대조 리포트다.
이 파일이 고정하는 것: 3분류(금지/제한초과/미등재), fail-closed 2건(함량 미입력
→ 보수 분류, 빈 전성분 → 명시적 오류), 고정 문구 "참고용·근거 확인"과 판정
워딩의 **부재**(웹 세션 판정 D — GC v1.1 다섯 케이스가 여기 있다).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_ingredient,
    create_ingredient_rule,
    create_product,
    create_user,
)

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
PRODUCTS = "/api/v1/products"


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "trade@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def _add_line(
    client: TestClient, product_id: int, ingredient_id: int, *, key: str, **overrides: Any
) -> None:
    body: dict[str, Any] = {"ingredient_id": ingredient_id, "display_order": 1}
    body.update(overrides)
    response = client.post(
        f"{PRODUCTS}/{product_id}/ingredients", json=body, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 201, response.text


def _screen(client: TestClient, product_id: int, *countries: str) -> Any:
    return client.get(f"{PRODUCTS}/{product_id}/screening", params={"country": list(countries)})


# ── 3분류 (GC v1.1 — 분류별 1건) ───────────────────────────────────────────


@pytest.mark.golden
def test_prohibited_ingredient_is_reported(trader: TestClient) -> None:
    """[GC] 금지 — 대상국에 PROHIBITED 규칙이 있으면 근거와 함께 검출된다"""
    product_id = create_product()
    ingredient_id = create_ingredient("Hydroquinone")
    create_ingredient_rule(ingredient_id, country_code="US", rule_type="PROHIBITED")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="1.0")

    report = _screen(trader, product_id, "US")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["checked_ingredient_count"] == 1
    (finding,) = body["findings"]
    assert finding["classification"] == "PROHIBITED"
    assert finding["country_code"] == "US"
    assert finding["inci_name"] == "Hydroquinone"
    # 근거가 함께 온다(ADR-03) — 사람이 확인하러 갈 곳이다.
    assert finding["source_url"].startswith("https://")
    assert finding["last_verified_on"] is not None


@pytest.mark.golden
def test_over_limit_ingredient_is_reported(trader: TestClient) -> None:
    """[GC] 제한초과 — 함량이 한도를 넘으면 한도·함량이 함께 검출된다"""
    product_id = create_product()
    ingredient_id = create_ingredient("Retinol")
    create_ingredient_rule(
        ingredient_id, country_code="EU", rule_type="RESTRICTED", max_concentration_pct="0.3"
    )
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="1.0")

    body = _screen(trader, product_id, "EU").json()
    (finding,) = body["findings"]
    assert finding["classification"] == "OVER_LIMIT"
    assert Decimal(finding["concentration_pct"]) == Decimal("1.0")
    assert Decimal(finding["max_concentration_pct"]) == Decimal("0.3")
    assert body["within_limit_count"] == 0


@pytest.mark.golden
def test_unlisted_ingredient_is_reported(trader: TestClient) -> None:
    """[GC] 미등재 — 규칙 행이 없으면 "근거 없음·확인 필요"의 뜻으로 검출된다"""
    product_id = create_product()
    ingredient_id = create_ingredient("Bakuchiol")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="0.5")

    body = _screen(trader, product_id, "JP").json()
    (finding,) = body["findings"]
    assert finding["classification"] == "UNLISTED"
    # 규칙이 없으므로 근거도 없다 — 이 부재가 "확인 필요"의 신호다.
    assert finding["source_url"] is None
    assert finding["max_concentration_pct"] is None


def test_within_limit_is_counted_not_flagged(trader: TestClient) -> None:
    """한도 이내는 검출이 아니라 집계다 — 사실 서술만 한다 (판정 워딩 금지)"""
    product_id = create_product()
    ingredient_id = create_ingredient("Niacinamide")
    create_ingredient_rule(
        ingredient_id, country_code="US", rule_type="RESTRICTED", max_concentration_pct="10"
    )
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="5.0")

    body = _screen(trader, product_id, "US").json()
    assert body["findings"] == []
    assert body["within_limit_count"] == 1
    assert body["checked_ingredient_count"] == 1


# ── fail-closed 2건 (GC v1.1) ──────────────────────────────────────────────


@pytest.mark.golden
def test_missing_concentration_on_a_restricted_ingredient_is_conservative(
    trader: TestClient,
) -> None:
    """[GC] 함량 미입력 + 제한 규칙 = 제한초과 버킷 (모르는 것은 문제 쪽으로 센다)"""
    product_id = create_product()
    ingredient_id = create_ingredient("Salicylic Acid")
    create_ingredient_rule(
        ingredient_id, country_code="US", rule_type="RESTRICTED", max_concentration_pct="2.0"
    )
    _add_line(trader, product_id, ingredient_id, key="l1")  # 함량 없이

    body = _screen(trader, product_id, "US").json()
    (finding,) = body["findings"]
    assert finding["classification"] == "OVER_LIMIT"
    assert finding["concentration_pct"] is None
    assert "함량 미입력" in finding["note"]


@pytest.mark.golden
def test_empty_formula_is_an_explicit_error(trader: TestClient) -> None:
    """[GC] 전성분 0건 = 명시적 오류 — 빈 리포트가 "문제 없음"으로 읽히면 안 된다"""
    product_id = create_product()

    report = _screen(trader, product_id, "US")
    assert report.status_code == 422, report.text
    assert report.json()["error"]["code"] == "INGREDIENTS.FORMULA.EMPTY"


# ── 리포트의 지위 (§1 비범위 / 웹 세션 판정 D) ─────────────────────────────


def test_notice_is_fixed_and_no_verdict_wording_appears(trader: TestClient) -> None:
    """고정 문구는 있고, 판정 워딩은 **없다** (부정 단언 — 웹 세션 판정 D-②)

    금지 검출과 한도 이내가 섞인 리포트 전문에서 본다 — 어느 분기도
    "적합/통과/합격" 류의 단어를 만들면 안 된다.
    """
    product_id = create_product()
    banned = create_ingredient("Hydroquinone")
    fine = create_ingredient("Niacinamide")
    create_ingredient_rule(banned, country_code="US", rule_type="PROHIBITED")
    create_ingredient_rule(
        fine, country_code="US", rule_type="RESTRICTED", max_concentration_pct="10"
    )
    _add_line(trader, product_id, banned, key="l1", concentration_pct="1.0")
    _add_line(trader, product_id, fine, key="l2", display_order=2, concentration_pct="5.0")

    report = _screen(trader, product_id, "US")
    assert report.status_code == 200
    assert report.json()["notice"] == "참고용·근거 확인"
    for verdict_word in ("적합", "통과", "합격"):
        assert verdict_word not in report.text, f"판정 워딩 발견: {verdict_word}"


def test_screening_blocks_nothing_and_stores_nothing(trader: TestClient) -> None:
    """금지가 검출돼도 아무것도 차단·저장되지 않는다 — 게이트가 아니다 (§1 비범위)

    두 번 호출해 같은 결과가 나오는 것으로 부작용 부재를 실측한다(상태가
    쌓였다면 두 번째 결과가 달라질 것이다). 구조적 부재는
    tests/architecture/test_screening_is_stateless.py가 지킨다.
    """
    product_id = create_product()
    ingredient_id = create_ingredient("Hydroquinone")
    create_ingredient_rule(ingredient_id, country_code="US", rule_type="PROHIBITED")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="1.0")

    first = _screen(trader, product_id, "US")
    assert first.status_code == 200
    assert first.json()["findings"][0]["classification"] == "PROHIBITED"

    # 검출 이후에도 등록·조회 어느 것도 막히지 않는다.
    still_open = trader.get(f"{PRODUCTS}/{product_id}/ingredients")
    assert still_open.status_code == 200

    second = _screen(trader, product_id, "US")
    assert second.json() == first.json()


# ── 대상국 처리 ─────────────────────────────────────────────────────────────


def test_multiple_countries_and_lowercase_are_normalized(trader: TestClient) -> None:
    """복수 대상국·소문자 입력 — 국가마다 대조되고 코드는 대문자로 정규화된다"""
    product_id = create_product()
    ingredient_id = create_ingredient("Retinol")
    create_ingredient_rule(ingredient_id, country_code="EU", rule_type="PROHIBITED")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="0.2")

    body = _screen(trader, product_id, "us", "EU").json()
    assert body["countries"] == ["US", "EU"]
    by_country = {
        finding["country_code"]: finding["classification"] for finding in body["findings"]
    }
    assert by_country == {"US": "UNLISTED", "EU": "PROHIBITED"}


@pytest.mark.group_k
def test_screening_a_missing_product_is_404(trader: TestClient) -> None:
    missing = _screen(trader, 999999, "US")
    assert missing.status_code == 404, missing.text


# ── 리뷰 보강 (S1-2 PR-1 적대적 리뷰 검출분) ────────────────────────────────


def test_invalid_country_format_is_rejected(trader: TestClient) -> None:
    """국가코드 형식 오류는 어느 값이 틀렸는지 짚어서 422다 (3자·숫자 혼입)"""
    product_id = create_product()
    for bad in ("USA", "U1"):
        rejected = _screen(trader, product_id, bad)
        assert rejected.status_code == 422, rejected.text
        assert "영문 2자" in rejected.text


def test_concentration_equal_to_the_limit_is_within_limit(trader: TestClient) -> None:
    """함량 == 한도는 한도 이내다 — '이내'의 정의선을 여기서 고정한다 (초과만 검출)"""
    product_id = create_product()
    ingredient_id = create_ingredient("Salicylic Acid")
    create_ingredient_rule(
        ingredient_id, country_code="US", rule_type="RESTRICTED", max_concentration_pct="2.0"
    )
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="2.0")

    body = _screen(trader, product_id, "US").json()
    assert body["findings"] == []
    assert body["within_limit_count"] == 1


def test_archived_ingredient_master_is_still_screened(trader: TestClient) -> None:
    """성분 마스터를 보관 처리해도 전성분 라인은 계속 검사된다 — fail-open 차단

    마스터 soft delete가 검사 대상을 조용히 줄이면 금지 성분이 "문제 없음" 모양의
    리포트로 샌다. 라인의 제외는 전성분에서 빼는 것으로만 일어난다 — 전성분
    목록(list_product_ingredients)과 같은 기준이다(리뷰 검출·수정 고정).
    """
    from app.core.db.uow import unit_of_work
    from app.core.time import utcnow
    from app.modules.ingredients.models import Ingredient

    product_id = create_product()
    ingredient_id = create_ingredient("Hydroquinone")
    create_ingredient_rule(ingredient_id, country_code="US", rule_type="PROHIBITED")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="1.0")

    with unit_of_work() as uow:
        row = uow.session.get(Ingredient, ingredient_id)
        assert row is not None
        row.deleted_at = utcnow()

    report = _screen(trader, product_id, "US").json()
    assert report["checked_ingredient_count"] == 1
    assert report["findings"][0]["classification"] == "PROHIBITED"
    # 전성분 목록도 같은 기준으로 그 라인을 계속 보여준다 — 두 읽기 경로의 일치.
    listed = trader.get(f"{PRODUCTS}/{product_id}/ingredients").json()
    assert listed["total"] == 1


@pytest.mark.group_k
def test_screening_changes_no_row_counts(trader: TestClient) -> None:
    """스크리닝 전후 전 테이블 행 수가 그대로다 — '미저장'을 행 수로 실측한다

    소스 스캔(test_screening_is_stateless)은 스크리닝 모듈 파일만 보므로, 다른
    모듈을 경유한 쓰기는 여기서 잡는다.
    """
    from sqlalchemy import func, select, table

    from app.core.db.uow import unit_of_work
    from app.registry import Base

    product_id = create_product()
    ingredient_id = create_ingredient("Hydroquinone")
    create_ingredient_rule(ingredient_id, country_code="US", rule_type="PROHIBITED")
    _add_line(trader, product_id, ingredient_id, key="l1", concentration_pct="1.0")

    def _counts() -> dict[str, int]:
        with unit_of_work() as uow:
            return {
                name: uow.session.execute(
                    select(func.count()).select_from(table(name))
                ).scalar_one()
                for name in Base.metadata.tables
            }

    before = _counts()
    assert _screen(trader, product_id, "US").status_code == 200
    after = _counts()
    assert after == before, {
        name: (before[name], after[name]) for name in before if before[name] != after[name]
    }
