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


def test_import_new_row_with_a_credit_limit_needs_admin_and_is_audited(
    trader: TestClient, admin: TestClient
) -> None:
    """신규 행에 한도 값이 실리면 무역 확정은 403(NEW 경로), 관리자 확정은 통과+set audit"""
    create_partner("PTN-BASE", name_ko="기존", types=("BUYER",))
    rows = _export_rows(trader)
    header = rows[0]
    new = [""] * len(header)
    new[header.index("거래처코드")], new[header.index("거래처명")] = "PTN-NEWCL", "한도 신규"
    new[header.index("유형")] = "BUYER"
    new[header.index("여신한도")], new[header.index("여신통화")] = "700", "USD"
    staged = _stage(trader, [header, *rows[1:], new], "imp3")
    denied = _confirm(trader, staged["id"], "imp3-c")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PARTNERS.CREDIT_LIMIT.ADMIN_ONLY"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM partners WHERE partner_code='PTN-NEWCL'")).scalar_one() == 0
    assert _confirm(admin, staged["id"], "imp3-c2").status_code == 200
    sets = [r for r in _credit_rows() if r["detail"] == {"amount": 70000, "currency": "USD"}]
    assert len(sets) == 1


@pytest.mark.parametrize(
    ("initial", "new_limit", "new_currency", "expected_audit"),
    [
        (("1000", "USD"), "1000", "EUR", {"old_currency": "USD", "new_currency": "EUR"}),  # 통화만 변경
        (("1000", "USD"), "", "", {"old_amount": 100000, "new_amount": None}),  # 값 → NULL('관리 해제')
    ],
)
def test_currency_only_and_clearing_changes_are_admin_only_and_audited(
    trader: TestClient,
    admin: TestClient,
    initial: tuple[str, str],
    new_limit: str,
    new_currency: str,
    expected_audit: dict[str, object],
) -> None:
    """통화만 바꾸거나 한도를 지우는 변경도 '변경'이다 — 무역은 403, 관리자는 통과+audit"""
    created = admin.post(
        PARTNERS,
        json={
            "partner_code": "PTN-CH",
            "name_ko": "변경",
            "type_codes": ["BUYER"],
            "credit_limit": initial[0],
            "credit_limit_currency": initial[1],
        },
        headers={"Idempotency-Key": "chg0"},
    )
    assert created.status_code == 201, created.text
    rows = _export_rows(trader)
    header = rows[0]
    rows[1][header.index("여신한도")], rows[1][header.index("여신통화")] = new_limit, new_currency
    staged = _stage(trader, rows, "chg1")
    assert _confirm(trader, staged["id"], "chg1-c").status_code == 403
    assert _confirm(admin, staged["id"], "chg1-c2").status_code == 200
    changed = [r for r in _credit_rows() if "old_amount" in r["detail"]]  # type: ignore[operator]
    assert len(changed) == 1
    for key, value in expected_audit.items():
        assert changed[0]["detail"][key] == value  # type: ignore[index]


def test_unchanged_credit_import_writes_no_credit_audit(admin: TestClient) -> None:
    """한도가 그대로인 왕복은 변경 audit을 남기지 않는다"""
    admin.post(
        PARTNERS,
        json=_body(partner_code="PTN-SAME", credit_limit="10", credit_limit_currency="USD"),
        headers={"Idempotency-Key": "same0"},
    )
    before = len(_credit_rows())
    rows = _export_rows(admin)
    rows[1][rows[0].index("거래처명")] = "이름만"
    staged = _stage(admin, rows, "same1")
    assert _confirm(admin, staged["id"], "same1-c").status_code == 200
    assert len(_credit_rows()) == before
