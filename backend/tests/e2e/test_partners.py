"""K·J. 거래처·품번 매핑·서명권자 — 다중 유형·여신 금액 규약·CSV (§4.6·§4.7·§12.2).

이 파일이 고정하는 것: 유형은 1개 이상이어야 한다는 것(§4.6이 거래처를
유형으로 정의한다), 여신한도가 금액 규약(최소단위+통화 쌍)을 따른다는 것,
품번 매핑은 BUYER 유형에만 붙는다는 것(§7.4 게이트의 전제), 그리고 CSV
내보내기의 수식 이스케이프(ADR-0027 — 부채 #17).
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

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
PARTNERS = "/api/v1/partners"
SIGNATORIES = "/api/v1/signatories"

Create = Callable[..., Response]


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
def create(trader: TestClient) -> Create:
    def _create(*, key: str = "ptn1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            # ★ 코드를 멱등 키에서 파생시킨다(고정 코드값 함정 — 주의 인계 ⑥).
            "partner_code": f"PTN-{key}"[:40],
            "name_ko": "한국콜마",
            "type_codes": ["OEM"],
        }
        body.update(overrides)
        return trader.post(PARTNERS, json=body, headers={"Idempotency-Key": key})

    return _create


# ── 등록 — 다중 유형·여신 (§4.6) ───────────────────────────────────────────


def test_partner_is_created_with_multiple_types(create: Create) -> None:
    """한 거래처가 공급사이면서 포워더다 — §4.6 "다중 유형"의 문면 그대로"""
    response = create(key="multi", type_codes=["FORWARDER", "SUPPLIER"])
    assert response.status_code == 201, response.text
    body = response.json()
    # 응답 유형은 정렬돼 있다 — 요청 순서에 흔들리지 않는다.
    assert body["type_codes"] == ["FORWARDER", "SUPPLIER"]
    assert body["credit_limit_amount"] is None
    assert body["credit_limit_currency"] is None


def test_credit_limit_is_stored_in_minor_units(create: Create) -> None:
    """USD 10,000.50 → 1000050 — 금액은 정수 최소단위다 (§2 ADR-02 / GC-G1)"""
    response = create(
        key="credit",
        type_codes=["BUYER"],
        credit_limit="10000.50",
        credit_limit_currency="usd",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["credit_limit_amount"] == 1_000_050
    assert body["credit_limit_currency"] == "USD"


def test_empty_type_list_is_rejected(create: Create) -> None:
    """유형 없는 거래처는 유령이다 — §7.7 소싱 제외·§7.4 게이트 어디에도 안 걸린다"""
    response = create(key="notype", type_codes=[])
    assert response.status_code == 422, response.text


def test_unknown_type_is_rejected(create: Create) -> None:
    response = create(key="badtype", type_codes=["MYSTERY"])
    assert response.status_code == 422, response.text


def test_credit_without_currency_is_rejected(create: Create) -> None:
    response = create(key="nopair", credit_limit="1000")
    assert response.status_code == 422, response.text


def test_unregistered_currency_is_rejected(create: Create) -> None:
    """통화 자릿수 출처는 money.py 하나다 — 미등록 통화는 저장 전에 거부한다"""
    response = create(key="badcur", credit_limit="1000", credit_limit_currency="XXX")
    assert response.status_code == 422, response.text


def test_duplicate_partner_code_is_rejected(create: Create) -> None:
    assert create(key="dup1", partner_code="PTN-DUP").status_code == 201
    response = create(key="dup2", partner_code="PTN-DUP")
    assert response.status_code == 422, response.text
    assert "이미 등록된" in response.json()["error"]["detail"]["partner_code"]


@pytest.mark.group_j
def test_double_click_creates_one_partner(create: Create, trader: TestClient) -> None:
    """같은 멱등 키 재수신은 최초 결과 반환이다 (§17.4 / GC-A3와 같은 계약)"""
    first = create(key="twice", partner_code="PTN-TWICE")
    second = create(key="twice", partner_code="PTN-TWICE")
    assert first.status_code == 201
    assert second.status_code == 201  # 재생은 최초 결과 그대로다(코드 포함)
    assert first.json()["id"] == second.json()["id"]
    listed = trader.get(PARTNERS, params={"size": 200})
    codes = [row["partner_code"] for row in listed.json()["items"]]
    assert codes.count("PTN-TWICE") == 1


def test_viewer_cannot_register_a_partner(viewer: TestClient) -> None:
    """마스터 등록은 무역·관리자의 일이다 (§18.1 역할 검증)"""
    response = viewer.post(
        PARTNERS,
        json={"partner_code": "PTN-V", "name_ko": "시도", "type_codes": ["SUPPLIER"]},
        headers={"Idempotency-Key": "viewer-try"},
    )
    assert response.status_code == 403, response.text


# ── 품번 매핑 (§4.6 customer_item_codes / §7.4의 전제) ─────────────────────


def test_item_code_is_registered_on_a_buyer(create: Create, trader: TestClient) -> None:
    partner_id = create(key="buyer1", type_codes=["BUYER"]).json()["id"]
    sku_id = create_sku("SKU-IC1")
    response = trader.post(
        f"{PARTNERS}/{partner_id}/item-codes",
        json={"sku_id": sku_id, "buyer_item_code": "AMZ-B00X"},
        headers={"Idempotency-Key": "ic1"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["sku_code"] == "SKU-IC1"


def test_item_code_on_a_non_buyer_is_rejected(create: Create, trader: TestClient) -> None:
    """품번 매핑은 바이어의 것이다 — 아니면 §7.4 게이트가 엉뚱한 품번을 신뢰한다"""
    partner_id = create(key="fwd1", type_codes=["FORWARDER"]).json()["id"]
    sku_id = create_sku("SKU-IC2")
    response = trader.post(
        f"{PARTNERS}/{partner_id}/item-codes",
        json={"sku_id": sku_id, "buyer_item_code": "AMZ-B00Y"},
        headers={"Idempotency-Key": "ic2"},
    )
    assert response.status_code == 422, response.text
    assert "바이어 유형" in response.json()["error"]["detail"]["partner_id"]


def test_duplicate_buyer_item_code_is_rejected(create: Create, trader: TestClient) -> None:
    partner_id = create(key="buyer2", type_codes=["BUYER"]).json()["id"]
    first = trader.post(
        f"{PARTNERS}/{partner_id}/item-codes",
        json={"sku_id": create_sku("SKU-IC3A"), "buyer_item_code": "AMZ-SAME"},
        headers={"Idempotency-Key": "ic3a"},
    )
    assert first.status_code == 201
    response = trader.post(
        f"{PARTNERS}/{partner_id}/item-codes",
        json={"sku_id": create_sku("SKU-IC3B"), "buyer_item_code": "AMZ-SAME"},
        headers={"Idempotency-Key": "ic3b"},
    )
    assert response.status_code == 422, response.text


# ── 서명권자 (§4.7 — 최소 헤더) ────────────────────────────────────────────


def test_signatory_is_created_and_listed(trader: TestClient) -> None:
    response = trader.post(
        SIGNATORIES,
        json={"name": "김서명", "title": "대표이사"},
        headers={"Idempotency-Key": "sig1"},
    )
    assert response.status_code == 201, response.text
    listed = trader.get(SIGNATORIES)
    names = [row["name"] for row in listed.json()["items"]]
    assert "김서명" in names


# ── CSV (§12.2 / ADR-0027) ─────────────────────────────────────────────────


def test_partners_csv_has_bom_and_korean(create: Create, trader: TestClient) -> None:
    create(key="csv1", partner_code="PTN-CSV", name_ko="한국콜마")
    response = trader.get(f"{PARTNERS}/export.csv")
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    assert "한국콜마" in text
    assert "PTN-CSV" in text


def test_partners_csv_escapes_formula_cells(create: Create, trader: TestClient) -> None:
    """★ 수식 시작 셀은 `'` 접두로 중화된다 (ADR-0027 — 부채 #17)

    거래처명 같은 자유 텍스트가 `=`로 시작하면 Excel이 수식으로 실행할 수
    있다. 이 관문은 통로(core/csv_export) 한 곳이라 여기 한 번의 실측이
    전 목록 내보내기를 대표한다(단위 테스트가 규칙 전체를 지킨다).
    """
    create(key="csv2", partner_code="PTN-EVIL", name_ko='=HYPERLINK("http://x")')
    response = trader.get(f"{PARTNERS}/export.csv")
    text = response.content.decode("utf-8")
    assert "'=HYPERLINK" in text
    assert "\r\n" in text  # 이스케이프가 CRLF 규율을 건드리지 않는다(RFC 4180)


def test_export_requires_authentication() -> None:
    with TestClient(app) as client:
        assert client.get(f"{PARTNERS}/export.csv").status_code == 401
