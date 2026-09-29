"""C·K. 준비도 매트릭스 API — 대표 소수·권한·페이지 계약 (§5.3 / WBS S2-3 DoD / 조건 C).

★ 전수는 서비스·순수 함수 층이고(tests/unit/test_readiness_rules.py·
  tests/integration/test_readiness_matrix.py), 여기서는 실 HTTP로 대표만 본다:
  ① **DoD "만료일 변경 → 매트릭스 즉시 반영"** — 인증을 API로 승인까지 끌고 가 🟢을
    보고, 만료일을 과거로 정정하면 **같은 세션에서 바로 🔴**이다(집계 저장 없음의 증명 —
    저장된 집계가 있었다면 재계산 전까지 🟢이 남는다).
  ② 셀 4색 각 1 ③ 세트 롤업 ④ 전 역할 열람 ⑤ K: 그리드형 응답의 **페이지 계약**
    (기존 K 자동 스캔은 list_ 명명 엔드포인트만 보므로, 그리드형은 명시 케이스가 고정한다).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_certification_instance,
    create_item_profile,
    create_market,
    create_requirement_template,
    create_set_sku,
    create_sku_with_axes,
    create_user,
)

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
MATRIX = "/api/v1/readiness/matrix"
CERTIFICATIONS = "/api/v1/certifications"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("matrix-cert@example.com", RoleCode.CERT)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("matrix-viewer@example.com", RoleCode.VIEWER)


def _cell(body: dict[str, Any], sku_code: str, market: str = "US") -> dict[str, Any]:
    row = next(row for row in body["items"] if row["sku_code"] == sku_code)
    return next(cell for cell in row["cells"] if cell["market_code"] == market)


# ── 권한 ─────────────────────────────────────────────────────────────────────


def test_matrix_requires_a_session() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get(MATRIX).status_code == 401


@pytest.mark.parametrize(
    "role", [RoleCode.ADMIN, RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER]
)
def test_every_role_can_read_the_matrix(role: RoleCode) -> None:
    """전 역할 열람 — 원가·마진 필드가 없어 마스킹 비대상이다"""
    create_market("US")
    for client in _client(f"matrix-{role.value.lower()}@example.com", role):
        response = client.get(MATRIX)
        assert response.status_code == 200, response.text
        assert response.json()["as_of"] == today_kst().isoformat()


# ── 4색 + 세트 (한 화면) ─────────────────────────────────────────────────────


def test_the_grid_shows_all_four_colors(viewer: TestClient) -> None:
    """셀 4색 각 1 — 🟢 승인 / 🟡 심사중 / 🔴 미착수 / ⚪ 요건 없음"""
    profile = create_item_profile("PRF-GRID")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    far = today_kst() + timedelta(days=500)
    green, _ = create_sku_with_axes("G-GREEN", profile_id=profile)
    yellow, _ = create_sku_with_axes("G-YELLOW", profile_id=profile)
    red, _ = create_sku_with_axes("G-RED", profile_id=profile)
    create_sku_with_axes("G-GRAY")  # 품목군 없음 → 요건 0건
    create_certification_instance(template, "SKU", green, status="APPROVED", expires_on=far)
    create_certification_instance(template, "SKU", yellow, status="IN_REVIEW")
    create_certification_instance(template, "SKU", red, status="NOT_STARTED")

    body = viewer.get(MATRIX).json()
    assert [_cell(body, code)["color"] for code in ("G-GREEN", "G-YELLOW", "G-RED", "G-GRAY")] == [
        "GREEN",
        "YELLOW",
        "RED",
        "GRAY",
    ]
    green_cell = _cell(body, "G-GREEN")
    assert (green_cell["required"], green_cell["approved"]) == (1, 1)
    item = green_cell["items"][0]
    assert item["template_name"] == "MoCRA 제품 리스팅" and item["axis"] == "SKU"
    assert item["status"] == "APPROVED" and item["certification_id"] is not None


def test_a_set_row_rolls_up_its_components(viewer: TestClient) -> None:
    profile = create_item_profile("PRF-SETROLL")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    far = today_kst() + timedelta(days=500)
    ok, _ = create_sku_with_axes("R-OK", profile_id=profile)
    blocked, _ = create_sku_with_axes("R-BLOCKED", profile_id=profile)
    create_set_sku("R-SET", [ok, blocked])
    create_certification_instance(template, "SKU", ok, status="APPROVED", expires_on=far)
    body = viewer.get(MATRIX).json()
    set_cell = _cell(body, "R-SET")
    assert set_cell["color"] == "RED"  # 구성품 하나가 미충족
    assert {item["via_component_sku_code"] for item in set_cell["items"]} == {"R-OK", "R-BLOCKED"}
    row = next(row for row in body["items"] if row["sku_code"] == "R-SET")
    assert row["kind"] == "SET" and [c["sku_code"] for c in row["components"]] == [
        "R-OK",
        "R-BLOCKED",
    ]


# ── DoD: 만료일 변경 → 매트릭스 즉시 반영 (집계 저장 없음의 증명) ──────────────


def _approve(client: TestClient, certification: dict[str, Any], expires_on: str) -> dict[str, Any]:
    version = certification["version"]
    for to, extra in (
        ("PREPARING", {}),
        ("SUBMITTED", {"applied_on": "2026-01-10"}),
        ("IN_REVIEW", {}),
        (
            "APPROVED",
            {"approved_on": "2026-02-01", "valid_from": "2026-02-01", "expires_on": expires_on},
        ),
    ):
        moved = client.post(
            f"{CERTIFICATIONS}/{certification['id']}/transitions",
            json={"to": to, "version": version, **extra},
            headers={"Idempotency-Key": f"matrix-{certification['id']}-{to}"},
        )
        assert moved.status_code == 200, moved.text
        certification = moved.json()
        version = certification["version"]
    return certification


def test_correcting_the_expiry_date_changes_the_cell_immediately(cert: TestClient) -> None:
    """DoD — 승인(🟢) → 만료일을 과거로 정정 → 다음 조회에서 즉시 🔴. 재계산 배치는 없다"""
    profile = create_item_profile("PRF-DOD")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    sku_id, _ = create_sku_with_axes("DOD-1", profile_id=profile)
    created = cert.post(
        CERTIFICATIONS,
        json={"template_id": template, "target_type": "SKU", "target_id": sku_id},
        headers={"Idempotency-Key": "matrix-dod"},
    )
    assert created.status_code == 201, created.text
    assert _cell(cert.get(MATRIX).json(), "DOD-1")["color"] == "RED"  # 미착수

    far = (today_kst() + timedelta(days=400)).isoformat()
    approved = _approve(cert, created.json(), far)
    assert _cell(cert.get(MATRIX).json(), "DOD-1")["color"] == "GREEN"

    yesterday = (today_kst() - timedelta(days=1)).isoformat()
    corrected = cert.patch(
        f"{CERTIFICATIONS}/{approved['id']}",
        json={"version": approved["version"], "expires_on": yesterday},
    )
    assert corrected.status_code == 200, corrected.text
    cell = _cell(cert.get(MATRIX).json(), "DOD-1")
    assert cell["color"] == "RED"
    assert cell["items"][0]["status"] == "EXPIRED"

    # 다시 미래로 정정하면 즉시 🟢 복귀 — 양방향이다
    restored = cert.patch(
        f"{CERTIFICATIONS}/{corrected.json()['id']}",
        json={"version": corrected.json()["version"], "expires_on": far},
    )
    assert restored.status_code == 200, restored.text
    assert _cell(cert.get(MATRIX).json(), "DOD-1")["color"] == "GREEN"


# ── K: 그리드형 응답의 페이지 계약 ────────────────────────────────────────────


def test_matrix_page_contract(viewer: TestClient) -> None:
    """기본 50·상한 200·1부터 — 기존 K 자동 스캔이 못 잡는 그리드형 응답의 명시 케이스"""
    create_market("US")
    create_market("CA")
    for index in range(52):
        create_sku_with_axes(f"PG-{index:03d}")

    first = viewer.get(MATRIX).json()
    assert (first["page"], first["size"], first["total"], len(first["items"])) == (1, 50, 52, 50)
    assert set(first) >= {"items", "total", "page", "size", "markets", "as_of", "scope_note"}
    assert [market["code"] for market in first["markets"]] == ["CA", "US"]
    assert all(len(row["cells"]) == len(first["markets"]) for row in first["items"])

    second = viewer.get(MATRIX, params={"page": 2}).json()
    assert (second["page"], len(second["items"])) == (2, 2)
    assert viewer.get(MATRIX, params={"size": 5}).json()["size"] == 5

    assert viewer.get(MATRIX, params={"size": 201}).status_code == 422  # 상한 200
    assert viewer.get(MATRIX, params={"size": 0}).status_code == 422
    assert viewer.get(MATRIX, params={"page": 0}).status_code == 422
    assert viewer.get(MATRIX, params={"kind": "BUNDLE"}).status_code == 422
    assert viewer.get(MATRIX, params={"q": ""}).status_code == 422


def test_the_scope_note_names_the_excluded_axis(viewer: TestClient) -> None:
    """조건 A — 모집합 안내 1줄이 응답에 실려 있고 성분 제외를 밝힌다(🟢 과신 방지)"""
    note = viewer.get(MATRIX).json()["scope_note"]
    assert "성분" in note and "판매가능" in note
    assert "SKU" in note and "자사" in note and "제조사" in note


def test_the_endpoint_is_read_only(viewer: TestClient) -> None:
    """쓰기 메서드가 없다 — 계산값이라 저장할 것이 없다"""
    for method in ("post", "put", "patch", "delete"):
        response = getattr(viewer, method)(MATRIX)
        assert response.status_code == 405, (method, response.status_code)
