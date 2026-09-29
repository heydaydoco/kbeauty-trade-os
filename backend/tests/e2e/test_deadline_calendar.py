"""C·K. 기일 캘린더 API·인증 목록의 담당자 표시 — 실 HTTP 대표 (§14 ④ 인증 보드 / S2-3 PR-3 안건 ⑥).

★ 서비스 전수는 tests/integration/test_deadline_calendar.py다. 여기서는 실 HTTP로: 권한(전 역할
  열람)·입력 오류의 안내(422)·페이지 계약(기본 50·상한 200)·응답 모양·인증 목록의 `assignee_name`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
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
    create_link_document,
    create_requirement_template,
    create_sku_with_axes,
    create_user,
)

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
CALENDAR = "/api/v1/deadlines/calendar"
CERTIFICATIONS = "/api/v1/certifications"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles, display_name="보드 담당")
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("cal-viewer@example.com", RoleCode.VIEWER)


def _window(days: int = 30) -> dict[str, str]:
    today = today_kst()
    return {"from": today.isoformat(), "to": (today + timedelta(days=days)).isoformat()}


def _seed() -> tuple[int, int]:
    """(인증 id, 문서 id) — 둘 다 오늘 기준 창 안."""
    profile = create_item_profile("PRF-E2E-CAL")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    sku, _ = create_sku_with_axes("E2E-CAL", profile_id=profile, name_ko="수분 세럼")
    assignee = create_user(
        "cal-assignee@example.com", roles=(RoleCode.CERT,), display_name="김인증"
    )
    cert = create_certification_instance(
        template,
        "SKU",
        sku,
        status="APPROVED",
        expires_on=today_kst() + timedelta(days=10),
        assignee_id=assignee,
    )
    doc = create_link_document(sku, valid_until=today_kst() + timedelta(days=5))
    return cert, doc


def test_calendar_requires_a_session() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get(CALENDAR, params=_window()).status_code == 401


@pytest.mark.parametrize(
    "role", [RoleCode.ADMIN, RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER]
)
def test_every_role_can_read_the_calendar(role: RoleCode) -> None:
    """전 역할 열람 — 원가·마진 필드가 없다"""
    cert, doc = _seed()
    for client in _client(f"cal-{role.value.lower()}@example.com", role):
        body = client.get(CALENDAR, params=_window()).json()
        assert {(item["kind"], item["id"]) for item in body["items"]} == {
            ("CERTIFICATION", cert),
            ("DOCUMENT", doc),
        }


def test_calendar_items_carry_what_the_board_shows(viewer: TestClient) -> None:
    cert, doc = _seed()
    body = viewer.get(CALENDAR, params=_window()).json()
    assert set(body) == {"items", "total", "page", "size"}
    assert [item["kind"] for item in body["items"]] == ["DOCUMENT", "CERTIFICATION"]  # 날짜순
    document, certification = body["items"]
    assert document["date"] == (today_kst() + timedelta(days=5)).isoformat()
    assert document["title"].endswith("· E2E-CAL") and document["status"] is None
    assert (certification["id"], certification["status"]) == (cert, "APPROVED")
    assert certification["title"] == "[US] 스냅샷 — 수분 세럼"
    assert certification["assignee_name"] == "김인증"
    assert certification["is_overdue"] is False and document["id"] == doc


def test_bad_windows_get_a_korean_422(viewer: TestClient) -> None:
    today = today_kst()
    backwards = viewer.get(
        CALENDAR, params={"from": today.isoformat(), "to": (today - timedelta(days=1)).isoformat()}
    )
    assert backwards.status_code == 422
    assert "종료일" in backwards.json()["error"]["detail"]["to"]

    too_long = viewer.get(CALENDAR, params=_window(days=200))
    assert too_long.status_code == 422
    assert "최대 93일" in too_long.json()["error"]["detail"]["to"]

    assert viewer.get(CALENDAR).status_code == 422  # 기간 필수
    assert viewer.get(CALENDAR, params={"from": "내일", "to": "모레"}).status_code == 422


def test_calendar_page_contract(viewer: TestClient) -> None:
    """기본 50·상한 200 — 목록 계약(§18.4)"""
    sku, _ = create_sku_with_axes("CAL-PAGE")
    for offset in range(53):
        create_link_document(sku, valid_until=today_kst() + timedelta(days=1), tag=f"page{offset}")
    first = viewer.get(CALENDAR, params=_window()).json()
    assert (first["page"], first["size"], first["total"], len(first["items"])) == (1, 50, 53, 50)
    second = viewer.get(CALENDAR, params={**_window(), "page": 2}).json()
    assert len(second["items"]) == 3
    assert viewer.get(CALENDAR, params={**_window(), "size": 201}).status_code == 422


def test_overdue_items_are_flagged(viewer: TestClient) -> None:
    """지난 기일은 도과 표시 — 계산값이라 만료일이 어제면 바로 참"""
    sku, _ = create_sku_with_axes("CAL-OVD")
    yesterday: date = today_kst() - timedelta(days=1)
    create_link_document(sku, valid_until=yesterday)
    body = viewer.get(
        CALENDAR,
        params={"from": (yesterday - timedelta(days=1)).isoformat(), "to": yesterday.isoformat()},
    ).json()
    assert [item["is_overdue"] for item in body["items"]] == [True]


def test_certification_list_shows_the_assignee_name(viewer: TestClient) -> None:
    """보드 카드용 — 인증 목록이 담당자 이름을 싣는다(id만으로는 이름을 알 수 없다)"""
    cert, _ = _seed()
    listed: dict[str, Any] = viewer.get(CERTIFICATIONS).json()
    row = next(row for row in listed["items"] if row["id"] == cert)
    assert row["assignee_name"] == "김인증" and row["assignee_id"] is not None
    detail = viewer.get(f"{CERTIFICATIONS}/{cert}").json()
    assert detail["assignee_name"] == "김인증"
