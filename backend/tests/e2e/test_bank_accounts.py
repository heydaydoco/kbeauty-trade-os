"""A·K. 은행 계좌 마스터 — ADMIN 전용 쓰기·ADMIN/TRADE 조회·중복·스냅샷 격리·마스킹 (S3-1 PR-6a / design-A A8).

발행된 PI는 계좌 값을 복사해 갖는다 — 계좌를 수정·비활성해도 과거 PI는 불변(test_proforma_invoices가 시험). 이 파일은 마스터 자체의
계약(권한·검증·중복·낙관 잠금·비활성·CSV·로그 마스킹)을 시험한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from tests.factories.trade import idem, logged_in, unique

pytestmark = pytest.mark.group_a

BANK = "/api/v1/bank-accounts"


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": unique("USD 주거래"),
        "currency": "USD",
        "beneficiary_name": "Kbeauty Trading Co., Ltd.",
        "beneficiary_address": "1 Gangnam-daero, Seoul, KR",
        "bank_name": "Synthetic Bank",
        "bank_address": "2 Teheran-ro, Seoul, KR",
        "account_no": "110-123-456789",
        "swift_code": "SYNTKRSE",
    }
    body.update(overrides)
    return body


@pytest.fixture
def admin():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.ADMIN) as client:
        yield client


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(BANK, json=_body(**overrides), headers=idem())
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def test_admin_creates_reads_updates_and_deactivates(admin: TestClient) -> None:
    """등록 → 조회 → 수정(version 상승) → 비활성(soft delete) — 비활성 계좌는 목록·상세에서 사라진다"""
    created = _create(admin, swift_code="synt kr se".replace(" ", ""))  # 소문자 입력은 대문자로
    assert created["swift_code"] == "SYNTKRSE" and created["version"] == 1
    assert admin.get(f"{BANK}/{created['id']}").json()["account_no"] == "110-123-456789"
    updated = admin.patch(
        f"{BANK}/{created['id']}",
        json={"version": 1, "bank_name": "  New Bank  ", "swift_code": "SYNTKRSEXXX"},
    )
    assert updated.status_code == 200 and updated.json()["bank_name"] == "New Bank"
    assert updated.json()["version"] == 2 and updated.json()["swift_code"] == "SYNTKRSEXXX"
    stale = admin.patch(f"{BANK}/{created['id']}", json={"version": 1, "bank_name": "낡음"})
    assert stale.status_code == 409
    gone = admin.delete(f"{BANK}/{created['id']}", params={"version": 2})
    assert gone.status_code == 200
    assert admin.get(f"{BANK}/{created['id']}").status_code == 404
    assert admin.get(BANK).json()["total"] == 0
    assert admin.delete(f"{BANK}/{created['id']}", params={"version": 3}).status_code == 404


def test_currency_is_immutable_and_unknown_fields_are_rejected(admin: TestClient) -> None:
    """통화는 수정 요청에 없다(422) — 새 계좌를 등록한다. 알 수 없는 필드도 422(extra=forbid)"""
    created = _create(admin)
    assert (
        admin.patch(f"{BANK}/{created['id']}", json={"version": 1, "currency": "KRW"}).status_code
        == 422
    )
    assert admin.post(BANK, json={**_body(), "id": 5}, headers=idem()).status_code == 422
    assert admin.post(BANK, json=_body(currency="XXX"), headers=idem()).status_code == 422


@pytest.mark.parametrize("swift", ["SYNTKRS", "SYNTKRSEX", "SYNT-KRSE", "SYNTKRSEXXXX", "  "])
def test_invalid_swift_codes_are_422(admin: TestClient, swift: str) -> None:
    """SWIFT는 영대문자·숫자 8자 또는 11자 — 그 밖은 422"""
    assert admin.post(BANK, json=_body(swift_code=swift), headers=idem()).status_code == 422


def test_blank_and_control_characters_are_rejected(admin: TestClient) -> None:
    """공백뿐인 값·개행이 든 값은 422 — 서류에 그대로 찍히는 값이라 제어문자를 막는다"""
    assert admin.post(BANK, json=_body(bank_name="   "), headers=idem()).status_code == 422
    assert admin.post(BANK, json=_body(account_no="110\n123"), headers=idem()).status_code == 422


def test_duplicate_account_number_and_label_are_rejected_but_reusable_after_deactivation(
    admin: TestClient,
) -> None:
    """같은 (정규화 계좌번호, SWIFT)는 422 — 공백·하이픈 표기 차이도 같은 계좌 · 같은 이름 422 · 비활성 뒤 재등록 허용"""
    first = _create(admin, label="주거래 USD", account_no="110-123-456789")
    same_number = admin.post(
        BANK, json=_body(label="다른 이름", account_no="110 123 456789"), headers=idem()
    )
    assert same_number.status_code == 422 and "account_no" in same_number.json()["error"]["detail"]
    same_label = admin.post(
        BANK, json=_body(label="주거래 USD", account_no="999-000-111"), headers=idem()
    )
    assert same_label.status_code == 422 and "label" in same_label.json()["error"]["detail"]
    other_swift = admin.post(
        BANK,
        json=_body(label="다른 SWIFT", account_no="110-123-456789", swift_code="OTHRKRSE"),
        headers=idem(),
    )
    assert other_swift.status_code == 201  # 같은 번호라도 SWIFT가 다르면 다른 계좌
    assert admin.delete(f"{BANK}/{first['id']}", params={"version": 1}).status_code == 200
    again = admin.post(
        BANK, json=_body(label="주거래 USD", account_no="110-123-456789"), headers=idem()
    )
    assert again.status_code == 201  # 비활성 계좌는 번호·이름을 점유하지 않는다


def test_create_is_idempotent_and_needs_a_key(admin: TestClient) -> None:
    """키 결여 400 · 같은 키 재전송은 최초 결과(행 1건)"""
    body = _body()
    assert admin.post(BANK, json=body).status_code == 400
    key = idem()
    first = admin.post(BANK, json=body, headers=key)
    second = admin.post(BANK, json=body, headers=key)
    assert first.status_code == 201 and first.json() == second.json()
    assert _scalar("SELECT count(*) FROM bank_accounts") == 1


def test_list_filters_by_currency_paginates_and_exports(admin: TestClient) -> None:
    """목록 기본 50·통화 필터·통화/이름 순 · CSV(BOM·계좌번호 포함 — 관리자·무역 열람 범위) · 필터 반영"""
    _create(admin, label="B-USD", currency="USD", account_no="1")
    _create(admin, label="A-USD", currency="USD", account_no="2")
    _create(admin, label="K-KRW", currency="KRW", account_no="3")
    listing = admin.get(BANK).json()
    assert listing["size"] == 50 and listing["total"] == 3
    assert [i["label"] for i in listing["items"]] == ["K-KRW", "A-USD", "B-USD"]
    assert admin.get(BANK, params={"currency": "usd"}).json()["total"] == 2
    assert admin.get(BANK, params={"currency": "US"}).status_code == 422
    page2 = admin.get(BANK, params={"size": 2, "page": 2}).json()
    assert [i["label"] for i in page2["items"]] == ["B-USD"]
    csv = admin.get(f"{BANK}/export.csv", params={"currency": "KRW"})
    assert csv.content.startswith(b"\xef\xbb\xbf")
    lines = csv.content.decode("utf-8-sig").strip().splitlines()
    assert (
        lines[0].startswith("이름,통화")
        and len(lines) == 2
        and "K-KRW" in lines[1]
        and ",3," in lines[1]
    )


def test_trade_reads_but_cannot_write_and_other_roles_cannot_even_read() -> None:
    """무역: 조회 200·쓰기 403 / 물류·인증·조회 역할: 전부 403(계좌번호는 서류 작성자에게만) — 서버측 역할 게이트"""
    with logged_in(RoleCode.ADMIN) as admin:
        account = _create(admin)
    with logged_in(RoleCode.TRADE) as trade:
        assert trade.get(BANK).status_code == 200
        assert trade.get(f"{BANK}/{account['id']}").status_code == 200
        assert trade.get(f"{BANK}/export.csv").status_code == 200
        assert trade.post(BANK, json=_body(), headers=idem()).status_code == 403
        assert (
            trade.patch(
                f"{BANK}/{account['id']}", json={"version": 1, "bank_name": "x"}
            ).status_code
            == 403
        )
        assert trade.delete(f"{BANK}/{account['id']}", params={"version": 1}).status_code == 403
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert client.get(BANK).status_code == 403
            assert client.get(f"{BANK}/{account['id']}").status_code == 403
            assert client.get(f"{BANK}/export.csv").status_code == 403
    assert _scalar("SELECT count(*) FROM bank_accounts") == 1
    assert _scalar("SELECT bank_name FROM bank_accounts") == "Synthetic Bank"


def test_the_account_number_never_reaches_events_audit_or_logs(
    admin: TestClient, capfd: pytest.CaptureFixture[str]
) -> None:
    """계좌번호는 이벤트·audit·로그 어디에도 남지 않는다 — 마스터 변경은 이벤트·audit를 발행하지 않고 로그 마스킹(_account_no 접미)이 있다"""
    secret = "SECRET-ACCT-4242"
    created = _create(admin, account_no=secret)
    admin.patch(f"{BANK}/{created['id']}", json={"version": 1, "bank_name": "Renamed"})
    assert _scalar("SELECT count(*) FROM events WHERE payload::text LIKE :p", p=f"%{secret}%") == 0
    assert (
        _scalar("SELECT count(*) FROM audit_log WHERE detail::text LIKE :p", p=f"%{secret}%") == 0
    )
    from app.core.logging.redaction import is_sensitive_key

    assert is_sensitive_key("account_no") and is_sensitive_key("bank_account_no")
    captured = capfd.readouterr()
    assert secret not in captured.out + captured.err
