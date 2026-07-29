"""K. 보안·품질 — 국가별 HS 세번 기록 (DESIGN.md §4.1·ADR-03 / ADR-0019 / GC-B3).

시스템은 HS를 판정하지 않는다(§1 비범위). 여기 있는 것은 **사람이 확인한 값과
그 근거를 받아 적는 경로**이고, 이 파일은 "근거 없이는 못 적는다"를 고정한다.
자동 판정 기능의 부재는 tests/architecture/test_no_hs_auto_classification.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.pagination import DEFAULT_PAGE_SIZE
from app.core.time import today_kst
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_product, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
SKUS = "/api/v1/skus"

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
def sku_id(trader: TestClient) -> int:
    response = trader.post(
        SKUS,
        json={"sku_code": "SER-001", "name_ko": "수분 세럼", "product_id": create_product()},
        headers={"Idempotency-Key": "sku"},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


@pytest.fixture
def record(trader: TestClient, sku_id: int) -> Record:
    def _record(*, key: str = "hs1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            "country_code": "US",
            "hs_version": "HS2022",
            "hs_code": "3304990000",
            "tariff_note": "MFN 0% (2026년 기준, 관세사 확인 필요)",
            "source_url": "https://hts.usitc.gov/",
            "last_verified_on": today_kst().isoformat(),
        }
        body.update(overrides)
        return trader.post(f"{SKUS}/{sku_id}/hs-codes", json=body, headers={"Idempotency-Key": key})

    return _record


# ── 기록 ───────────────────────────────────────────────────────────────────


def test_record_then_list(trader: TestClient, sku_id: int, record: Record) -> None:
    """세번을 적고 목록에서 다시 본다 (§22 렌즈 11)"""
    created = record()
    assert created.status_code == 201, created.text

    listed = trader.get(f"{SKUS}/{sku_id}/hs-codes").json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["country_code"] == "US"
    assert item["hs_code"] == "3304990000"
    assert item["source_url"] == "https://hts.usitc.gov/"


def test_country_code_is_normalized_to_upper_case(record: Record) -> None:
    """국가 코드는 대문자로 정규화된다 (us와 US가 다른 행이 되면 안 된다)"""
    assert record(country_code="us").json()["country_code"] == "US"


@pytest.mark.parametrize("written", ["3304.99.0000", "3304 99 0000", "3304990000"])
def test_dotted_and_spaced_codes_normalize_to_the_same_digits(record: Record, written: str) -> None:
    """표기가 달라도 같은 세번으로 저장된다 (국가마다 점 위치가 다르다)

    ★ 표기 그대로 저장하면 "3304.99"와 "330499"가 다른 행으로 들어와 유일키가
      중복을 못 잡는다.
    """
    assert record(hs_code=written).json()["hs_code"] == "3304990000"


def test_same_country_and_version_cannot_be_recorded_twice(record: Record) -> None:
    """같은 SKU·국가·HS 버전은 한 번만 기록된다 (§17.4 부분 유니크)"""
    record(key="a")
    response = record(key="b")

    assert response.status_code == 422
    assert "이미 등록" in response.json()["error"]["detail"]["country_code"]


def test_a_different_hs_version_coexists(trader: TestClient, sku_id: int, record: Record) -> None:
    """★ 같은 국가라도 HS 버전이 다르면 함께 존재한다 (GC-B3)

    개정 때 기존 행을 덮어쓰면 "과거 판정은 당시 버전으로 표시"가 마스터 쪽에서
    무너진다. 신·구 버전이 공존해야 감사에 답할 수 있다.
    """
    record(key="a", hs_version="HS2017", hs_code="3304991000")
    second = record(key="b", hs_version="HS2022", hs_code="3304990000")

    assert second.status_code == 201, second.text
    listed = trader.get(f"{SKUS}/{sku_id}/hs-codes").json()
    assert listed["total"] == 2
    assert {item["hs_version"] for item in listed["items"]} == {"HS2017", "HS2022"}


def test_double_click_records_one_row(trader: TestClient, sku_id: int, record: Record) -> None:
    """더블클릭해도 1건이다 (§17.4 / GC-A3)"""
    first = record(key="same")
    second = record(key="same")

    assert first.json() == second.json()
    assert trader.get(f"{SKUS}/{sku_id}/hs-codes").json()["total"] == 1


# ── 근거 강제 (ADR-03) ─────────────────────────────────────────────────────


def test_recording_without_evidence_is_rejected(trader: TestClient, sku_id: int) -> None:
    """근거링크 없는 세번은 기록되지 않는다 (ADR-03 — 근거링크+최종확인일 필수)"""
    response = trader.post(
        f"{SKUS}/{sku_id}/hs-codes",
        json={
            "country_code": "US",
            "hs_version": "HS2022",
            "hs_code": "3304990000",
            "last_verified_on": today_kst().isoformat(),
        },
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 422


def test_a_non_link_is_not_evidence(record: Record) -> None:
    """ "확인함" 같은 문구는 근거가 아니다 (열어볼 수 있는 링크여야 한다)"""
    assert record(source_url="관세사에게 확인함").status_code == 422


def test_future_verification_date_is_rejected(record: Record) -> None:
    """최종확인일이 미래면 거절된다 (확인은 이미 한 일이다)"""
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    response = record(last_verified_on=tomorrow)

    assert response.status_code == 422
    assert "미래" in response.json()["error"]["detail"]["last_verified_on"]


def test_today_in_korea_is_accepted(record: Record) -> None:
    """★ 한국 날짜 기준 오늘은 통과한다 (§22 렌즈 6)

    UTC 날짜로 비교하면 한국의 00~09시에 적은 오늘 날짜가 미래로 걸린다.
    """
    assert record(last_verified_on=today_kst().isoformat()).status_code == 201


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("country_code", "USA"),
        ("hs_version", "2022"),
        ("hs_code", "33"),
    ],
)
def test_malformed_values_are_rejected(record: Record, field: str, value: str) -> None:
    """형식이 어긋난 값은 거절된다 (국가 2자리·HSxxxx·숫자 6~12자리)"""
    assert record(**{field: value}).status_code == 422


# ── 권한·페이지네이션 ──────────────────────────────────────────────────────


def test_viewer_cannot_record_but_can_read(sku_id: int) -> None:
    """조회 역할은 세번을 적을 수 없고 볼 수는 있다 (§18.1)"""
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))

    with TestClient(app) as client:
        client.post(LOGIN, json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD})
        written = client.post(
            f"{SKUS}/{sku_id}/hs-codes",
            json={
                "country_code": "US",
                "hs_version": "HS2022",
                "hs_code": "3304990000",
                "source_url": "https://hts.usitc.gov/",
                "last_verified_on": today_kst().isoformat(),
            },
            headers={"Idempotency-Key": "k1"},
        )
        assert written.status_code == 403
        assert client.get(f"{SKUS}/{sku_id}/hs-codes").status_code == 200


def test_unknown_sku_returns_404(trader: TestClient) -> None:
    """없는 SKU의 세번 목록은 404다"""
    assert trader.get(f"{SKUS}/999999/hs-codes").status_code == 404


def test_list_is_paginated_with_default_50(trader: TestClient, sku_id: int) -> None:
    """목록 기본 크기는 50이다 (§18.4)"""
    body = trader.get(f"{SKUS}/{sku_id}/hs-codes").json()
    assert body["size"] == DEFAULT_PAGE_SIZE
    assert set(body) == {"items", "total", "page", "size"}
