"""K. 보안·품질 — 에러 응답 봉투 (DESIGN.md §18.4)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import NotFoundError
from app.main import create_app

pytestmark = pytest.mark.group_k

# 오류를 일부러 일으키는 임시 라우트를 붙인 앱.
_probe = APIRouter(prefix="/api/v1/_probe")


@_probe.get("/boom")
def _boom() -> None:
    raise RuntimeError("내부 상세: 접속 실패 postgresql+psycopg://kbos_app:leakpw12345@db/x")


@_probe.get("/notfound")
def _notfound() -> None:
    raise NotFoundError(detail={"자원": "SKU-999"}, log_context={"internal_id": 42})


@_probe.post("/echo")
def _echo(payload: dict) -> dict:  # type: ignore[type-arg]
    return payload


app = create_app()
app.include_router(_probe)
client = TestClient(app, raise_server_exceptions=False)

ENVELOPE_KEYS = {"code", "message", "detail", "request_id"}


def test_unhandled_exception_returns_envelope_without_internals() -> None:
    """처리되지 않은 예외는 500 표준 봉투를 주고 내부 상세를 노출하지 않는다"""
    response = client.get("/api/v1/_probe/boom")
    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["code"] == str(ErrorCode.INTERNAL_UNEXPECTED)
    assert body["error"]["request_id"]
    # 내부 상세·비밀번호·스택트레이스가 응답에 없어야 한다
    raw = response.text
    for leaked in ("leakpw12345", "postgresql", "Traceback", "RuntimeError"):
        assert leaked not in raw


def test_app_error_carries_user_detail_not_log_context() -> None:
    """AppError는 detail(사용자용)만 응답에 싣고 log_context는 싣지 않는다"""
    response = client.get("/api/v1/_probe/notfound")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == str(ErrorCode.RESOURCE_NOT_FOUND)
    assert error["detail"] == {"자원": "SKU-999"}
    assert "internal_id" not in response.text  # log_context는 로그에만


def test_validation_error_uses_same_envelope_and_hides_input() -> None:
    """검증 오류도 같은 봉투를 쓰고, 사용자가 보낸 원본 값을 되돌려주지 않는다"""
    response = client.post(
        "/api/v1/_probe/echo", content=b"not-json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == str(ErrorCode.VALIDATION_INVALID_FIELD)
    # Pydantic 오류의 input/ctx가 새지 않는다
    assert "not-json" not in response.text
    assert '"input"' not in response.text
    assert '"ctx"' not in response.text


def test_message_is_korean() -> None:
    """사용자용 메시지는 한국어다"""
    error = client.get("/api/v1/_probe/boom").json()["error"]
    assert any("가" <= ch <= "힣" for ch in error["message"])
