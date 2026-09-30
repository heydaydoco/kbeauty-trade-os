"""K·J. S3-1 PR-2 플랫폼 기반 — 사용자 선택 목록·역할 보유자·기능 플래그·로그 마스킹."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.logging.redaction import is_sensitive_key
from app.main import app
from app.modules.identity import service as identity
from app.modules.identity.models import RoleCode
from app.modules.platform.service import is_feature_enabled
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOOKUP = "/api/v1/users/lookup"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, email: str) -> None:
    assert client.post("/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}).is_success


def test_lookup_requires_login(client: TestClient) -> None:
    """비로그인은 사용자 선택 목록을 볼 수 없다"""
    assert client.get(LOOKUP).status_code == 401


def test_lookup_is_open_to_every_role_but_shows_names_only(client: TestClient) -> None:
    """조회 역할도 표시명을 볼 수 있지만 이메일·역할·상태는 응답에 없다(활성 사용자만)"""
    create_user("viewer@x.kbos", roles=(RoleCode.VIEWER,), display_name="조회자")
    create_user("gone@x.kbos", roles=(RoleCode.TRADE,), display_name="퇴사자", is_active=False)
    _login(client, "viewer@x.kbos")

    response = client.get(LOOKUP)
    assert response.status_code == 200
    body = response.json()
    names = [item["display_name"] for item in body["items"]]
    assert "조회자" in names and "퇴사자" not in names
    for item in body["items"]:
        assert set(item) == {"id", "display_name"}
    assert body["total"] == 1


def test_lookup_is_paginated(client: TestClient) -> None:
    """목록은 페이지네이션을 따른다(기본 50)"""
    for i in range(3):
        create_user(f"u{i}@x.kbos", roles=(RoleCode.TRADE,), display_name=f"사용자{i}")
    _login(client, "u0@x.kbos")
    body = client.get(LOOKUP, params={"size": 2}).json()
    assert len(body["items"]) == 2 and body["total"] == 3


def test_holders_of_role_excludes_inactive_and_deleted() -> None:
    """역할 보유자는 활성·미삭제 사용자만이다 — 비활성 계정에 알림을 보내면 조용한 정체가 된다"""
    a = create_user("a@x.kbos", roles=(RoleCode.ADMIN,))
    create_user("b@x.kbos", roles=(RoleCode.ADMIN,), is_active=False)
    c = create_user("c@x.kbos", roles=(RoleCode.ADMIN, RoleCode.TRADE))
    d = create_user("d@x.kbos", roles=(RoleCode.ADMIN,))
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET deleted_at = now() WHERE id = :i"), {"i": d})
    with unit_of_work() as uow:
        assert identity.holders_of_role(uow.session, RoleCode.ADMIN) == [a, c]
        assert identity.holders_of_role(uow.session, RoleCode.CERT) == []


def test_feature_flag_absent_or_deleted_is_off() -> None:
    """플래그 행이 없거나 삭제됐으면 꺼짐이다(fail-closed)"""
    with unit_of_work() as uow:
        assert is_feature_enabled(uow.session, "lc") is False
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO feature_flags (code, name_ko, is_enabled, version)"
                " VALUES ('lc', 'L/C', true, 1)"
            )
        )
    with unit_of_work() as uow:
        assert is_feature_enabled(uow.session, "lc") is True
    with engine.begin() as conn:
        conn.execute(text("UPDATE feature_flags SET deleted_at = now() WHERE code='lc'"))
    with unit_of_work() as uow:
        assert is_feature_enabled(uow.session, "lc") is False


@pytest.mark.parametrize(
    "name",
    ["beneficiary_account_no", "bank_account_number", "account_no", "account_number"],
)
def test_bank_account_columns_are_masked_in_logs(name: str) -> None:
    """은행 계좌번호 계열 키는 로그에서 가려진다(전표 은행정보 스냅샷)"""
    assert is_sensitive_key(name)
