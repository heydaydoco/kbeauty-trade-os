"""J. 안전 계약 — 잠금 대기 초과·교착은 500이 아니라 409다 (S3-1 ADR-0059).

kbos_app 역할은 lock_timeout 5초라, 같은 행을 다른 요청이 잡고 있으면 사용자 요청이
55P03으로 실패한다. 이 오류가 "서버 내부 오류"로 새면 사용자는 재시도해도 되는지 알 수 없다.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db.session import engine
from app.core.errors.codes import ErrorCode
from app.core.errors.handlers import handle_db_error
from app.modules.numbering.models import DocNumberSeq  # noqa: F401 — 테이블 등록

pytestmark = [pytest.mark.group_j]


class _FakeRequest:
    class state:
        request_id = "req-test"

    class url:
        path = "/x"


def _seed_counter() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO doc_number_seq (prefix, year, last_number) VALUES ('LK', 2026, 0)"
                " ON CONFLICT DO NOTHING"
            )
        )


def test_real_lock_timeout_is_mapped_to_409() -> None:
    """같은 행을 다른 트랜잭션이 잡은 채 대기 시간이 넘으면 LOCK_BUSY 409가 나간다"""
    _seed_counter()
    holder = engine.connect()
    holder_tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM doc_number_seq WHERE prefix='LK' FOR UPDATE"))
        with engine.connect() as waiter, waiter.begin():
            waiter.execute(text("SET LOCAL lock_timeout = '150ms'"))
            with pytest.raises(DBAPIError) as caught:
                waiter.execute(text("SELECT 1 FROM doc_number_seq WHERE prefix='LK' FOR UPDATE"))
    finally:
        holder_tx.rollback()
        holder.close()

    assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    response = asyncio.run(handle_db_error(_FakeRequest(), caught.value))  # type: ignore[arg-type]
    assert response.status_code == 409
    assert str(ErrorCode.CONCURRENCY_LOCK_BUSY).encode() in response.body


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("db")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "status"),
    [("55P03", 409), ("40P01", 409), ("57014", 500), ("23505", 500)],
)
def test_only_lock_contention_becomes_409(sqlstate: str, status: int) -> None:
    """55P03·40P01만 409다 — 30초 초과(57014)에 '다른 사용자가 처리 중'은 거짓이다"""
    exc = DBAPIError("stmt", {}, _Orig(sqlstate))
    response = asyncio.run(handle_db_error(_FakeRequest(), exc))  # type: ignore[arg-type]
    assert response.status_code == status


def test_handler_is_registered_on_the_real_app() -> None:
    """앱에 실제로 등록돼 있다 — 라우트가 55P03을 던지면 봉투 409가 나간다(등록 줄 삭제 시 실패)"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.errors.handlers import register_exception_handlers

    probe = FastAPI()
    register_exception_handlers(probe)

    @probe.get("/lock")
    def _lock() -> None:
        raise DBAPIError("stmt", {}, _Orig("55P03"))

    @probe.get("/other")
    def _other() -> None:
        raise DBAPIError("stmt", {}, _Orig("23505"))

    client = TestClient(probe, raise_server_exceptions=False)
    busy = client.get("/lock")
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == str(ErrorCode.CONCURRENCY_LOCK_BUSY)
    other = client.get("/other")
    assert other.status_code == 500
    assert other.json()["error"]["code"] == str(ErrorCode.INTERNAL_UNEXPECTED)
