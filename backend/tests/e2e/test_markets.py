"""K·J·C. 시장 마스터 — 권한·정규화·멱등·낙관 잠금·CSV (§5.1 / S2-1 판정 ⑤·§17.2·§17.4).

이 파일이 고정하는 것: 편집(등록·정식화)은 인증+관리자뿐이라는 것(판정 ⑤ —
무역·조회는 열람), 코드는 대문자 2자로 정규화된다는 것(기존 country_code
'us'→'US' 선례), 전역 UNIQUE라 중복 코드가 안내와 함께 거부된다는 것,
확정·생성이 idempotency key로 멱등이라는 것(§17.4), 정식화 편집이 version
낙관 잠금을 따른다는 것(§17.2 — 409 문구), 그리고 CSV가 BOM·CRLF라는 것.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
MARKETS = "/api/v1/markets"

Create = Callable[..., Response]


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("cert@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer@example.com", RoleCode.VIEWER)


@pytest.fixture
def create(cert: TestClient) -> Create:
    def _create(*, key: str = "mkt1", code: str = "US", **overrides: Any) -> Response:
        body: dict[str, Any] = {"code": code, "name_ko": "미국"}
        body.update(overrides)
        return cert.post(MARKETS, json=body, headers={"Idempotency-Key": key})

    return _create


# ── 등록·정규화 (§5.1) ─────────────────────────────────────────────────────


def test_create_market(create: Create) -> None:
    """시장 등록 — 201, 코드·이름이 그대로 돌아온다"""
    response = create()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "US"
    assert body["name_ko"] == "미국"
    assert body["version"] == 1


def test_lowercase_code_is_normalized(create: Create) -> None:
    """소문자 코드는 대문자로 정규화된다 ('us'→'US' — country_code 선례)"""
    response = create(code="eu", name_ko="유럽연합")
    assert response.status_code == 201, response.text
    assert response.json()["code"] == "EU"


def test_malformed_code_is_rejected(create: Create) -> None:
    """숫자 섞인 코드는 422 — 형식은 대문자 2자뿐(DB CHECK와 같은 규칙)"""
    response = create(code="U1")
    assert response.status_code == 422, response.text


def test_duplicate_code_is_rejected_with_guidance(create: Create) -> None:
    """같은 코드 재등록은 422 + 안내 (전역 UNIQUE — 판정 조건 3)"""
    assert create(key="dup-a").status_code == 201
    response = create(key="dup-b")
    assert response.status_code == 422, response.text
    assert "이미 등록된 시장 코드" in response.text


# ── 멱등 (§17.4 / §20 J) ──────────────────────────────────────────────────


def test_create_is_idempotent(cert: TestClient, create: Create) -> None:
    """같은 키 재요청 = 최초 결과 재생, 행은 1건 (더블클릭 흡수)"""
    first = create(key="mkt-idem")
    replay = create(key="mkt-idem")
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()

    listing = cert.get(MARKETS)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


# ── 권한 (판정 ⑤ — 편집=인증+관리자, 무역·조회=열람) ────────────────────────


def test_trade_cannot_edit_but_can_read(trader: TestClient) -> None:
    """무역 역할: 등록 403, 열람 200"""
    denied = trader.post(
        MARKETS,
        json={"code": "JP", "name_ko": "일본"},
        headers={"Idempotency-Key": "mkt-trade"},
    )
    assert denied.status_code == 403, denied.text
    assert trader.get(MARKETS).status_code == 200


def test_viewer_can_read(create: Create, viewer: TestClient) -> None:
    """조회 역할: 목록·상세 열람 가능 — 시장에는 원가성 필드가 없다"""
    market_id = create().json()["id"]
    assert viewer.get(MARKETS).status_code == 200
    assert viewer.get(f"{MARKETS}/{market_id}").status_code == 200


# ── 정식화 편집 (§17.2 낙관 잠금) ───────────────────────────────────────────


def test_update_market_names(cert: TestClient, create: Create) -> None:
    """정식화: MIG 계보 행(name_ko=코드)을 사람이 이름으로 바꾸는 경로"""
    body = create(code="CA", name_ko="CA").json()
    response = cert.patch(
        f"{MARKETS}/{body['id']}",
        json={"version": body["version"], "name_ko": "캐나다", "name_en": "Canada"},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name_ko"] == "캐나다"
    assert updated["version"] == body["version"] + 1
    assert updated["code"] == "CA"  # 코드는 불변이다(FK 참조 값)


def test_stale_version_is_conflict(cert: TestClient, create: Create) -> None:
    """낡은 version으로 저장하면 409 — §17.2 지정 문구"""
    body = create(code="GB", name_ko="영국").json()
    first = cert.patch(
        f"{MARKETS}/{body['id']}", json={"version": body["version"], "name_ko": "영국(UK)"}
    )
    assert first.status_code == 200
    stale = cert.patch(
        f"{MARKETS}/{body['id']}", json={"version": body["version"], "name_ko": "영국"}
    )
    assert stale.status_code == 409, stale.text
    assert "다른 사용자가 먼저 수정" in stale.text


def test_trade_cannot_update(create: Create, trader: TestClient) -> None:
    """무역 역할은 정식화도 403 (판정 ⑤)"""
    body = create(code="AU", name_ko="호주").json()
    response = trader.patch(
        f"{MARKETS}/{body['id']}", json={"version": body["version"], "name_ko": "호주 수정"}
    )
    assert response.status_code == 403, response.text


# ── CSV (§12.2 — BOM·CRLF·표시용 목록) ─────────────────────────────────────


def test_export_csv_has_bom_and_crlf(cert: TestClient, create: Create) -> None:
    """CSV: BOM으로 시작·CRLF·헤더 고정 (표시용 — 왕복 아님·ID 열 없음)"""
    create(code="US", name_ko="미국")
    response = cert.get(f"{MARKETS}/export.csv")
    assert response.status_code == 200
    text = response.text
    assert text.startswith("﻿")
    assert "\r\n" in text
    first_line = text.lstrip("﻿").split("\r\n", 1)[0]
    assert first_line == "시장코드,시장명,영문명,메모"
    assert "US,미국" in text
