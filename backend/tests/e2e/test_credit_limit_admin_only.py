"""H·K. 여신한도 등록·변경은 관리자만 — 무역이 한도를 올린 뒤 확정하는 우회를 닫는다 (S3-1 E9).

무역 담당이 자기 손으로 한도를 올리면 §7.10 "여신 초과 수주 승인 게이트"가 무력해진다. 한도 값이
바뀌는 쓰기 경로 2곳(등록 API·임포트 확정)을 모두 막는다. 판정은 스테이징 생성자가 아니라
확정 시점 행위자다 — 무역이 올린 파일을 관리자가 확정하는 것은 허용된다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_partner, create_user

pytestmark = pytest.mark.group_h

PARTNERS = "/api/v1/partners"
EXPORT = "/api/v1/partners/export.csv"
STAGE = "/api/v1/imports/partners/staging"
STAGING = "/api/v1/imports/staging"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        yield client


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("plain-trade@example.com", RoleCode.TRADE)


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("plain-admin@example.com", RoleCode.ADMIN)


def _body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "partner_code": "PTN-CL1",
        "name_ko": "여신 거래처",
        "type_codes": ["BUYER"],
    }
    body.update(over)
    return body


def _credit_rows() -> list[dict[str, object]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT detail, entity_id FROM audit_log WHERE action LIKE 'partners.credit_limit.%'"
                " ORDER BY id"
            )
        ).all()
    return [{"detail": r[0], "entity_id": r[1]} for r in rows]


def test_trade_cannot_register_a_partner_with_a_credit_limit(trader: TestClient) -> None:
    """무역이 한도를 값으로 실어 등록하면 403, 한도 없이 등록은 종전대로 201"""
    denied = trader.post(
        PARTNERS,
        json=_body(credit_limit="1000", credit_limit_currency="USD"),
        headers={"Idempotency-Key": "cl1"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "PARTNERS.CREDIT_LIMIT.ADMIN_ONLY"
    assert (
        trader.post(PARTNERS, json=_body(), headers={"Idempotency-Key": "cl2"}).status_code == 201
    )
    assert _credit_rows() == []


def test_admin_can_register_with_a_credit_limit_and_it_is_audited(admin: TestClient) -> None:
    """관리자는 한도 포함 등록이 되고 audit 1행이 남는다"""
    created = admin.post(
        PARTNERS,
        json=_body(credit_limit="1000", credit_limit_currency="USD"),
        headers={"Idempotency-Key": "cl3"},
    )
    assert created.status_code == 201, created.text
    rows = _credit_rows()
    assert len(rows) == 1 and rows[0]["detail"] == {"amount": 100000, "currency": "USD"}


def _export_rows(client: TestClient) -> list[list[str]]:
    return list(csv.reader(io.StringIO(client.get(EXPORT).content.decode("utf-8-sig"))))


def _stage(client: TestClient, rows: list[list[str]], key: str) -> dict[str, object]:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    response = client.post(
        STAGE,
        files={"file": ("p.csv", buffer.getvalue().encode("utf-8-sig"), "text/csv")},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirm(client: TestClient, staging_id: object, key: str):  # type: ignore[no-untyped-def]
    return client.post(f"{STAGING}/{staging_id}/confirm", headers={"Idempotency-Key": key})


def test_import_confirm_by_trade_is_rejected_when_credit_limit_changes(
    trader: TestClient, admin: TestClient
) -> None:
    """무역이 확정하면 여신 열을 바꾸는 배치는 통째로 거부되고 DB는 그대로다(부분 반영 0)"""
    partner_id = create_partner("PTN-IMP", name_ko="임포트 거래처", types=("BUYER",))
    other_id = create_partner("PTN-IMP2", name_ko="다른 거래처", types=("BUYER",))
    rows = _export_rows(trader)
    header = rows[0]
    limit_col, cur_col, name_col = (
        header.index("여신한도"),
        header.index("여신통화"),
        header.index("거래처명"),
    )
    for row in rows[1:]:
        if row[header.index("거래처코드")] == "PTN-IMP":
            row[limit_col], row[cur_col] = "500", "USD"
        else:
            row[name_col] = "이름만 바뀜"
    staged = _stage(trader, rows, "imp1")
    denied = _confirm(trader, staged["id"], "imp1-c")
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "PARTNERS.CREDIT_LIMIT.ADMIN_ONLY"
    with engine.connect() as conn:
        names = dict(conn.execute(text("SELECT id, name_ko FROM partners")).all())
    assert (
        names[other_id] == "다른 거래처" and names[partner_id] == "임포트 거래처"
    )  # 부분 반영 없음

    # 같은 스테이징을 관리자가 확정하면 통과하고 audit이 남는다(판정은 확정 행위자 기준).
    ok = _confirm(admin, staged["id"], "imp1-c2")
    assert ok.status_code == 200, ok.text
    changed = [r for r in _credit_rows() if r["entity_id"] == partner_id]
    assert changed and changed[0]["detail"]["new_amount"] == 50000  # type: ignore[index]


def test_unchanged_credit_columns_pass_for_trade(trader: TestClient) -> None:
    """한도 열이 값 그대로면 무역도 통과한다 — 이름만 고치는 왕복 흐름 보존"""
    create_partner("PTN-KEEP", name_ko="유지", types=("BUYER",))
    rows = _export_rows(trader)
    rows[1][rows[0].index("거래처명")] = "이름 변경"
    staged = _stage(trader, rows, "imp2")
    assert _confirm(trader, staged["id"], "imp2-c").status_code == 200


def test_credit_limit_registry_flag_is_set() -> None:
    """공회전 방지 — partners 임포트 대상에 관리자 전용 필드가 실제로 설정돼 있다"""
    from app.modules.imports.registry import IMPORT_TARGETS

    assert IMPORT_TARGETS["partners"].admin_only_fields == {
        "credit_limit_amount",
        "credit_limit_currency",
    }
    assert not getattr(IMPORT_TARGETS["skus"], "admin_only_fields", frozenset())
