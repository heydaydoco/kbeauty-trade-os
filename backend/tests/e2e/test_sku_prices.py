"""G. AI·보안 (조회 역할 원가 마스킹) + A. 전표·정합 (단가 이력).

DESIGN.md §2 권한·통제 / §18.1 / ADR-0017·0018 / §20 G.

★ 이 파일의 핵심은 "조회 역할에게 매입가 **행이 없다**"이다. 필드만 비우거나
  403으로 막으면 존재 사실과 건수가 새어 나가고, 그 건수 자체가 원가 변경
  횟수라는 정보다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_sku, create_user

pytestmark = [pytest.mark.group_g, pytest.mark.group_a]

LOGIN = "/api/v1/auth/login"
SKUS = "/api/v1/skus"

Record = Callable[..., Response]


def _login(client: TestClient, email: str) -> None:
    response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture
def sku_id() -> int:
    return create_sku()


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        _login(client, "trade@example.com")
        yield client


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    with TestClient(app) as client:
        _login(client, "viewer@example.com")
        yield client


@pytest.fixture
def record(trader: TestClient, sku_id: int) -> Record:
    def _record(*, key: str = "p1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            "price_type": "SALES",
            "currency": "KRW",
            "amount": "12000",
            "effective_from": "2026-01-01",
        }
        body.update(overrides)
        return trader.post(f"{SKUS}/{sku_id}/prices", json=body, headers={"Idempotency-Key": key})

    return _record


# ── 등록·환산 (§2 ADR-02) ──────────────────────────────────────────────────


def test_krw_has_no_minor_unit(record: Record) -> None:
    """KRW 12000은 최소단위로도 12000이다 (소수 자릿수 0)"""
    body = record().json()

    assert body["amount"] == 12000
    assert body["currency"] == "KRW"


def test_usd_is_stored_in_cents(record: Record) -> None:
    """USD 12.34는 1234센트로 저장된다 (float을 거치지 않는다 — GC-G1)"""
    body = record(currency="USD", amount="12.34").json()

    assert body["amount"] == 1234


def test_a_rounded_currency_uses_half_up(record: Record) -> None:
    """자릿수를 넘는 입력은 반올림된다 (통화별 규칙은 money.py가 유일한 출처)"""
    assert record(currency="USD", amount="12.345").json()["amount"] == 1235


def test_an_unregistered_currency_is_rejected(record: Record) -> None:
    """등록되지 않은 통화는 거절된다"""
    response = record(currency="XYZ")

    assert response.status_code == 422


def test_zero_is_allowed(record: Record) -> None:
    """0원 단가를 막지 않는다 (사은품이 실재하고 §20 B도 무상을 인정한다)"""
    assert record(amount="0").json()["amount"] == 0


def test_the_same_effective_date_cannot_be_recorded_twice(record: Record) -> None:
    """같은 (종류·통화·발효일)은 한 번만 (§17.4 — 겹치는 이력이 생기면 안 된다)"""
    record(key="a")
    response = record(key="b", amount="13000")

    assert response.status_code == 422
    assert "발효일" in response.json()["error"]["detail"]["effective_from"]


def test_double_click_records_one_price(trader: TestClient, sku_id: int, record: Record) -> None:
    """더블클릭해도 1건이다 (§17.4 / GC-A3)"""
    first = record(key="same")
    second = record(key="same")

    assert first.json() == second.json()
    assert trader.get(f"{SKUS}/{sku_id}/prices").json()["total"] == 1


# ── 지금 적용되는 단가 ─────────────────────────────────────────────────────


def test_is_current_marks_the_applicable_row(
    trader: TestClient, sku_id: int, record: Record
) -> None:
    """미래 발효 행이 있어도 "지금 적용되는" 행이 표시된다"""
    record(key="a", effective_from="2026-01-01", amount="10000")
    record(key="b", effective_from="2999-01-01", amount="99000")

    items = trader.get(f"{SKUS}/{sku_id}/prices").json()["items"]
    current = [item for item in items if item["is_current"]]

    assert len(current) == 1
    assert current[0]["amount"] == 10000


# ── ★ 원가 마스킹 (§2 / §18.1 / ADR-0018 / §20 G) ──────────────────────────


def test_a_viewer_does_not_see_purchase_rows_at_all(
    trader: TestClient, viewer: TestClient, sku_id: int, record: Record
) -> None:
    """★ 조회 역할의 목록에는 매입가 행이 없고 건수에도 안 잡힌다

    필드만 비우면 화면에서 "0원"으로 읽히고, total을 전체로 두면 "안 보이는
    행이 몇 건 있는지"가 새어 나간다 — 그 건수가 곧 원가 변경 횟수다.
    """
    record(key="s", price_type="SALES", amount="12000")
    record(key="p", price_type="PURCHASE", amount="7000")

    seen_by_trader = trader.get(f"{SKUS}/{sku_id}/prices").json()
    seen_by_viewer = viewer.get(f"{SKUS}/{sku_id}/prices").json()

    assert seen_by_trader["total"] == 2
    assert {item["price_type"] for item in seen_by_trader["items"]} == {"SALES", "PURCHASE"}

    assert seen_by_viewer["total"] == 1
    assert {item["price_type"] for item in seen_by_viewer["items"]} == {"SALES"}
    # 금액 자체가 응답 어디에도 없다.
    assert "7000" not in seen_by_viewer.__str__()


def test_a_viewer_gets_404_not_403_for_a_purchase_row(
    viewer: TestClient, sku_id: int, record: Record
) -> None:
    """★ 감춰야 하는 행의 단건 조회는 403이 아니라 404다 (ADR-0018)

    403은 "네가 볼 수 없는 원가가 여기 존재한다"를 알려 준다. 존재 사실만으로도
    마진 추정의 단서가 되므로, 마스킹은 값이 아니라 **존재에 대한 지식**을 막는다.
    """
    price_id = record(key="p", price_type="PURCHASE", amount="7000").json()["id"]

    response = viewer.get(f"{SKUS}/{sku_id}/prices/{price_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COMMON.RESOURCE.NOT_FOUND"


def test_a_viewer_can_read_sales_prices(viewer: TestClient, sku_id: int, record: Record) -> None:
    """판가는 조회 역할도 본다 (마스킹이 과하게 넓지 않다는 자기검사)"""
    price_id = record(key="s", price_type="SALES", amount="12000").json()["id"]

    response = viewer.get(f"{SKUS}/{sku_id}/prices/{price_id}")

    assert response.status_code == 200
    assert response.json()["amount"] == 12000


def test_a_trader_sees_the_purchase_row(trader: TestClient, sku_id: int, record: Record) -> None:
    """무역 역할은 매입가를 본다 (§2는 '조회'만 마스킹 대상으로 규정한다)"""
    price_id = record(key="p", price_type="PURCHASE", amount="7000").json()["id"]

    assert trader.get(f"{SKUS}/{sku_id}/prices/{price_id}").json()["amount"] == 7000


def test_a_viewer_cannot_record_prices(viewer: TestClient, sku_id: int) -> None:
    """조회 역할은 단가를 등록할 수 없다 (§18.1)"""
    response = viewer.post(
        f"{SKUS}/{sku_id}/prices",
        json={
            "price_type": "SALES",
            "currency": "KRW",
            "amount": "12000",
            "effective_from": "2026-01-01",
        },
        headers={"Idempotency-Key": "k1"},
    )

    assert response.status_code == 403


def test_unknown_sku_returns_404(trader: TestClient) -> None:
    """없는 SKU의 단가 목록은 404다"""
    assert trader.get(f"{SKUS}/999999/prices").status_code == 404


# ── 통화 자릿수 출처 (부채 #9) ─────────────────────────────────────────────


def test_currency_minor_units_are_served_from_one_place(trader: TestClient) -> None:
    """통화별 자릿수를 API가 알려 준다 — 화면이 같은 표를 복사하지 않게

    프런트에 표를 복사해 두면 통화가 추가될 때 한쪽만 갱신되고, 같은 금액이
    서버와 화면에서 다르게 보인다. 그 차이는 정산 대사에서야 드러난다.
    """
    body = trader.get("/api/v1/system/currencies", params={"size": 200}).json()
    units = {item["code"]: item["minor_units"] for item in body["items"]}

    assert units["KRW"] == 0
    assert units["USD"] == 2
    assert "XYZ" not in units
