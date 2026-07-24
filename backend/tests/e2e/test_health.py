"""헬스체크 — WBS S0-1 "스모크 테스트 1개 이상"."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.group_h

client = TestClient(app)

HEALTHZ = "/api/v1/system/healthz"
READYZ = "/api/v1/system/readyz"

#: 응답 어디에도 나오면 안 되는 조각들(내부 구조 노출 방지)
FORBIDDEN_IN_RESPONSE = ("kbos_app", "kbos_owner", "postgresql", "Traceback", "/app/")


def test_healthz_returns_200() -> None:
    """살아있음 확인은 DB 없이도 200을 준다"""
    response = client.get(HEALTHZ)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_sets_request_id_header() -> None:
    """모든 응답에 요청 추적용 X-Request-ID가 붙는다"""
    response = client.get(HEALTHZ)
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) == 32
    int(request_id, 16)  # hex가 아니면 여기서 실패


def test_request_ids_differ_per_request() -> None:
    """요청마다 다른 추적 번호가 부여된다"""
    first = client.get(HEALTHZ).headers["X-Request-ID"]
    second = client.get(HEALTHZ).headers["X-Request-ID"]
    assert first != second


def test_readyz_checks_db_and_migration() -> None:
    """준비 확인은 DB 연결과 마이그레이션 최신 여부를 함께 본다"""
    response = client.get(READYZ)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["db"]["status"] == "ok"
    assert payload["checks"]["migration"]["status"] == "ok"
    assert payload["app_env"] == "test"


@pytest.mark.group_k
def test_readyz_does_not_leak_internals() -> None:
    """헬스 응답에 접속정보·경로·스택트레이스가 실리지 않는다"""
    body = client.get(READYZ).text
    for fragment in FORBIDDEN_IN_RESPONSE:
        assert fragment not in body, f"헬스 응답에 {fragment!r}가 노출됐다"


def test_readyz_timestamp_is_utc() -> None:
    """확인 시각은 UTC로 기록된다 (§2 ADR-02)"""
    checked_at = client.get(READYZ).json()["checked_at"]
    assert checked_at.endswith("+00:00") or checked_at.endswith("Z")
