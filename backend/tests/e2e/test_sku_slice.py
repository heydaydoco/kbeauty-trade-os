"""K. 보안·품질 — SKU 등록→목록→CSV 끝-끝 관통 (DESIGN.md §19 Phase 0 / §12.2 / §18.4).

§22 렌즈 11(워크스루)이 요구하는 "입구부터 출구까지 1회 관통"을 자동 테스트로
고정한다. 화면 하나만 되는 것과 입구-출구가 이어지는 것은 다른 상태다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.csv_export import UTF8_BOM
from app.core.pagination import DEFAULT_PAGE_SIZE
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
SKUS = "/api/v1/skus"
EXPORT = "/api/v1/skus/export.csv"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login_as(client: TestClient, email: str) -> None:
    response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text


def _register(
    client: TestClient, code: str, name_ko: str, *, key: str, name_en: str | None = None
) -> Response:
    return client.post(
        SKUS,
        json={"sku_code": code, "name_ko": name_ko, "name_en": name_en},
        headers={"Idempotency-Key": key},
    )


@pytest.fixture
def trader(client: TestClient) -> TestClient:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")
    return client


# ── 관통 ───────────────────────────────────────────────────────────────────


def test_register_then_list_then_export(trader: TestClient) -> None:
    """등록 → 목록 → CSV가 한 줄로 이어진다 (§22 렌즈 11 워크스루)"""
    created = _register(trader, "SER-001", "수분 세럼 30ml", key="k1", name_en="Hydra Serum 30ml")
    assert created.status_code == 201
    sku_id = created.json()["id"]

    listed = trader.get(SKUS).json()
    assert listed["total"] == 1
    assert listed["items"][0]["sku_code"] == "SER-001"

    detail = trader.get(f"{SKUS}/{sku_id}").json()
    assert detail["name_ko"] == "수분 세럼 30ml"

    export = trader.get(EXPORT)
    assert export.status_code == 200
    assert "수분 세럼 30ml" in export.text


# ── 등록 ───────────────────────────────────────────────────────────────────


def test_duplicate_sku_code_is_rejected(trader: TestClient) -> None:
    """같은 품번은 두 번 등록되지 않는다"""
    _register(trader, "SER-001", "첫 등록", key="k1")
    response = _register(trader, "SER-001", "중복 시도", key="k2")

    assert response.status_code == 422
    assert "품번" in response.json()["error"]["detail"]["sku_code"]


def test_double_click_registers_one_sku(trader: TestClient) -> None:
    """더블클릭해도 SKU는 1건이다 (§17.4 / GC-A3)"""
    first = _register(trader, "SER-001", "수분 세럼", key="same")
    second = _register(trader, "SER-001", "수분 세럼", key="same")

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert trader.get(SKUS).json()["total"] == 1


def test_registration_without_idempotency_key_is_rejected(trader: TestClient) -> None:
    """멱등 키 없는 등록은 거절된다"""
    response = trader.post(SKUS, json={"sku_code": "SER-001", "name_ko": "키 없음"})
    assert response.status_code == 400


def test_viewer_cannot_register(client: TestClient) -> None:
    """조회 역할은 SKU를 등록할 수 없다 (§2 조회는 읽기 전용)"""
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    _login_as(client, "viewer@example.com")

    assert _register(client, "SER-001", "안 됨", key="k1").status_code == 403


def test_viewer_can_read_and_export(client: TestClient) -> None:
    """조회 역할도 목록과 내보내기는 쓸 수 있다"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))

    with TestClient(app) as trader:
        _login_as(trader, "trade@example.com")
        _register(trader, "SER-001", "수분 세럼", key="k1")

    _login_as(client, "viewer@example.com")
    assert client.get(SKUS).json()["total"] == 1
    assert client.get(EXPORT).status_code == 200


def test_unknown_status_is_rejected(trader: TestClient) -> None:
    """상태값은 정해진 것만 받는다"""
    response = trader.post(
        SKUS,
        json={"sku_code": "SER-001", "name_ko": "잘못된 상태", "status": "SOLD_OUT"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 422


# ── 목록 (§18.4) ───────────────────────────────────────────────────────────


def test_list_is_paginated_with_default_50(trader: TestClient) -> None:
    """목록 기본 크기는 50이다"""
    body = trader.get(SKUS).json()
    assert body["size"] == DEFAULT_PAGE_SIZE
    assert set(body) == {"items", "total", "page", "size"}


def test_list_pages_do_not_overlap(trader: TestClient) -> None:
    """page·size가 실제로 결과를 자른다"""
    for index in range(4):
        _register(trader, f"SER-{index:03d}", f"세럼 {index}", key=f"k{index}")

    first = trader.get(SKUS, params={"size": 2, "page": 1}).json()
    second = trader.get(SKUS, params={"size": 2, "page": 2}).json()

    assert first["total"] == second["total"] == 4
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


# ── CSV 내보내기 (§12.2) ───────────────────────────────────────────────────


def test_csv_starts_with_utf8_bom(trader: TestClient) -> None:
    """CSV는 UTF-8 BOM으로 시작한다

    ★ BOM이 없으면 Windows Excel에서 한글이 전부 깨져 보이고, 사용자는
      "시스템이 한글을 못 쓴다"고 판단한다 — 데이터는 멀쩡한데도.
    """
    _register(trader, "SER-001", "수분 세럼", key="k1")

    body = trader.get(EXPORT).content.decode("utf-8")

    assert body.startswith(UTF8_BOM)


def test_csv_round_trips_korean_without_loss(trader: TestClient) -> None:
    """한글·쉼표·따옴표가 든 값이 CSV 왕복에서 손실되지 않는다"""
    tricky = '앰플, "고농축" 50ml'
    _register(trader, "AMP-001", tricky, key="k1")

    text = trader.get(EXPORT).content.decode("utf-8").lstrip(UTF8_BOM)
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0] == ["품번", "품명(국문)", "품명(영문)", "상태"]
    assert rows[1][1] == tricky


def test_csv_uses_crlf_line_endings(trader: TestClient) -> None:
    """줄바꿈은 CRLF다 (RFC 4180 — Excel이 기대하는 형식)"""
    _register(trader, "SER-001", "수분 세럼", key="k1")
    assert "\r\n" in trader.get(EXPORT).content.decode("utf-8")


def test_csv_filename_is_sent_utf8_encoded(trader: TestClient) -> None:
    """한글 파일명은 RFC 5987 형식으로 내려간다 (헤더 인코딩 에러 방지)"""
    response = trader.get(EXPORT)
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "attachment" in disposition


def test_csv_is_not_cached(trader: TestClient) -> None:
    """내보내기 응답은 캐시되지 않는다 (권한별로 내용이 다르다)"""
    assert trader.get(EXPORT).headers["cache-control"] == "no-store"


def test_empty_export_still_has_the_header_row(trader: TestClient) -> None:
    """등록된 SKU가 없어도 머리글은 나온다 (빈 파일은 오류로 오해된다)"""
    text = trader.get(EXPORT).content.decode("utf-8").lstrip(UTF8_BOM)
    assert list(csv.reader(io.StringIO(text))) == [["품번", "품명(국문)", "품명(영문)", "상태"]]


def test_export_requires_authentication(client: TestClient) -> None:
    """로그인 없이 내보내기를 부를 수 없다"""
    assert client.get(EXPORT).status_code == 401
