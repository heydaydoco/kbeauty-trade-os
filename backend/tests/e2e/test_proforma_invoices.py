"""A·K. 선수금 청구서(PI) API — QT 참조 생성·미리보기·소비·환원·원천 자격·조회 (S3-1 PR-6a / ADR-0052·0053 / design-A A4·A8).

DoD ① "참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음)"의 1단: 요청 본문에 원천 QT에 있는 값(SKU·단가·통화·환율·거래처·
결제조건)을 다시 받는 필드가 **구조적으로 없고**, 서버가 원천에서 값을 복사한다. 마스터가 바뀌어도 PI 값은 불변이다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.proforma_invoices.schemas import ProformaInvoiceCreateRequest
from app.modules.trade_docs.transition import PAYLOAD_KEYS
from tests.factories.trade import (
    create_bank_account,
    create_buyer,
    create_pi_via_api,
    create_priced_sku,
    create_quotation_via_api,
    idem,
    issue_via_api,
    issued_quotation,
    logged_in,
    pi_payload,
    set_price,
)

pytestmark = pytest.mark.group_a

QT = "/api/v1/quotations"
PI = "/api/v1/proforma-invoices"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


def _scalar(sql: str, **params: Any) -> Any:
    return _rows(sql, **params)[0][0]


def _create(client: TestClient, qt: dict[str, Any], bank: int, **overrides: Any) -> Any:
    return client.post(
        f"{QT}/{qt['id']}/proforma-invoices",
        json=pi_payload(qt, bank, **overrides),
        headers=idem(),
    )


def _preview(client: TestClient, qt: dict[str, Any], bank: int, **overrides: Any) -> Any:
    return client.post(
        f"{QT}/{qt['id']}/proforma-invoices/preview", json=pi_payload(qt, bank, **overrides)
    )


# ── 생성: 값 복사·스냅샷 ─────────────────────────────────────────────────────


def test_create_copies_every_value_from_the_quotation_without_re_entry(trade: TestClient) -> None:
    """PI 생성 → 통화·환율·바이어 표기·결제조건·Incoterms·라인 단가가 QT에서 복사되고 합계·선수금은 서버가 계산한다(재입력 0)"""
    buyer = create_buyer(name_en="Acme Trading Inc.", address_en="1 Main St, LA")
    sku = create_priced_sku(amount=1250)
    qt = issued_quotation(trade, buyer, [sku], quantity=10)
    bank = create_bank_account("USD")
    body = create_pi_via_api(trade, qt, bank)
    assert body["status"] == "ISSUED" and body["version"] == 1
    assert body["doc_number"].startswith("PI-") and body["doc_number"].endswith("-0001")
    assert body["qt_id"] == qt["id"] and body["qt_doc_number"] == qt["doc_number"]
    for field in ("currency", "fx_rate", "fx_rate_date", "buyer_name", "buyer_address"):
        assert body[field] == qt[field], field
    assert body["dest_market_code"] == qt["dest_market_code"]
    assert body["buyer_partner_id"] == buyer
    assert body["payment_terms"] == qt["payment_terms"] and body["incoterm"] == qt["incoterm"]
    assert body["total_amount"] == 12500 and body["total_text"] == "125.00"
    line = body["lines"][0]
    src = qt["lines"][0]
    assert line["qt_line_id"] == src["id"] and line["quantity"] == 10
    for field in ("sku_id", "sku_code", "unit_price_amount", "list_price_amount", "price_basis"):
        assert line[field] == src[field], field
    # 선수금 30% — 저장하지 않고 서버가 계산(HALF_UP, 잔금=총액−선수금)
    assert body["advance"] == {
        "advance_amount": 3750,
        "advance_text": "37.50",
        "balance_amount": 8750,
        "balance_text": "87.50",
    }
    assert body["frozen_at"] is not None and body["is_lapsed"] is False
    assert body["bank"]["account_id"] == bank and body["bank"]["swift_code"] == "SYNTKRSE"


def test_create_records_birth_history_event_and_no_audit_and_leaves_the_quotation_untouched(
    trade: TestClient,
) -> None:
    """생성 → 탄생 이력 1행(ISSUED)·created 이벤트(화이트리스트 payload)·audit 0·원천 QT는 version·상태 불변(수주전환은 SO 확정)"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer)
    body = create_pi_via_api(trade, qt)
    log = _rows(
        "SELECT from_status, to_status, automatic FROM proforma_invoice_status_log"
        " WHERE proforma_invoice_id = :i",
        i=body["id"],
    )
    assert [(r[0], r[1], r[2]) for r in log] == [(None, "ISSUED", False)]
    events = _rows(
        "SELECT payload FROM events WHERE event_type = 'proforma_invoices.proforma_invoice.created'"
    )
    assert len(events) == 1
    payload = events[0][0]
    assert set(payload) <= set(PAYLOAD_KEYS) and payload["doc_type"] == "PROFORMA_INVOICE"
    assert payload["partner_id"] == buyer and payload["to_status"] == "ISSUED"
    assert _scalar("SELECT count(*) FROM audit_log WHERE entity_type = 'proforma_invoices'") == 0
    after = trade.get(f"{QT}/{qt['id']}").json()
    assert (after["status"], after["version"]) == (
        "ISSUED",
        qt["version"],
    )  # PI 생성은 QT를 건드리지 않는다


def test_master_changes_after_creation_do_not_touch_the_pi(trade: TestClient) -> None:
    """계좌·SKU 판가를 바꾸거나 계좌를 비활성해도 발행된 PI 값은 불변이다(스냅샷) — 참조 생성은 마스터를 다시 읽지 않는다"""
    sku = create_priced_sku(amount=1000)
    qt = issued_quotation(trade, create_buyer(), [sku])
    bank = create_bank_account("USD", account_no="777-000-999")
    first = create_pi_via_api(
        trade, qt, bank, lines=[{"source_line_id": qt["lines"][0]["id"], "quantity": 4}]
    )
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE bank_accounts SET account_no = '000-changed', bank_name = 'Other', deleted_at = now()"
                " WHERE id = :i"
            ),
            {"i": bank},
        )
    set_price(sku, 99999, effective_from=today_kst())  # 마스터 판가 변경
    again = trade.get(f"{PI}/{first['id']}").json()
    assert (
        again["bank"]["account_no"] == "777-000-999"
        and again["bank"]["bank_name"] == "Synthetic Bank"
    )
    # 마스터 판가가 바뀐 뒤 같은 QT에서 2번째 PI를 만들어도 단가는 QT 스냅샷 값이다
    second = create_pi_via_api(trade, qt, create_bank_account("USD"))
    assert second["lines"][0]["unit_price_amount"] == 1000
    assert second["lines"][0]["quantity"] == 6  # 잔량 전부(10−4)


# ── 요청 스키마의 구조적 보증 ───────────────────────────────────────────────────


def test_the_create_request_schema_has_no_field_that_restates_source_values() -> None:
    """요청 스키마 필드 집합 스냅샷 — 원천 QT에 있는 값(SKU·단가·통화·환율·거래처·시장)을 다시 받는 필드가 없다"""
    assert set(ProformaInvoiceCreateRequest.model_fields) == {
        "version",
        "doc_date",
        "valid_until",
        "bank_account_id",
        "lines",
        "overrides",
        "copied_from_id",
    }
    from app.modules.proforma_invoices.schemas import PiLineRequest, PiOverrides

    assert set(PiLineRequest.model_fields) == {"source_line_id", "quantity"}
    assert set(PiOverrides.model_fields) == {
        "payment_terms",
        "incoterm",
        "internal_note",
        "assignee_id",
    }


@pytest.mark.parametrize(
    "field",
    [
        "sku_id",
        "unit_price",
        "currency",
        "fx_rate",
        "buyer_partner_id",
        "dest_market_code",
        "status",
        "doc_number",
        "total_amount",
        "payment_terms",
        "incoterm",
        "qt_id",
        "frozen_at",
    ],
)
def test_restating_or_server_owned_fields_are_rejected_on_create(
    trade: TestClient, field: str
) -> None:
    """원천 값 재입력·서버 소유 필드를 본문에 실으면 422(extra=forbid) — PI 행은 생기지 않는다"""
    qt = issued_quotation(trade)
    response = trade.post(
        f"{QT}/{qt['id']}/proforma-invoices",
        json={**pi_payload(qt, create_bank_account()), field: "USD"},
        headers=idem(),
    )
    assert response.status_code == 422, response.text
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


@pytest.mark.parametrize(
    "field", ["currency", "fx_rate", "unit_price", "sku_id", "buyer_partner_id"]
)
def test_overrides_whitelist_excludes_price_currency_rate_party(
    trade: TestClient, field: str
) -> None:
    """재정의 화이트리스트에 환율·통화·단가·거래처·SKU가 없다 — overrides에 실으면 422"""
    qt = issued_quotation(trade)
    response = trade.post(
        f"{QT}/{qt['id']}/proforma-invoices",
        json={**pi_payload(qt, create_bank_account()), "overrides": {field: "x"}},
        headers=idem(),
    )
    assert response.status_code == 422, response.text


def test_overrides_can_change_payment_terms_incoterm_note_and_assignee(trade: TestClient) -> None:
    """허용된 재정의(결제조건·Incoterms·내부 메모·담당자)는 반영되고 나머지는 원천 값 그대로다"""
    from tests.factories.trade import user_id_of  # noqa: F401
    from tests.support.factories import create_user

    other = create_user("pi-owner@example.com", roles=(RoleCode.TRADE,))
    qt = issued_quotation(trade)
    body = create_pi_via_api(
        trade,
        qt,
        overrides={
            "payment_terms": {"payment_type": "TT_ADVANCE", "advance_pct": "100"},
            "incoterm": {"code": "CIF", "place": "Los Angeles", "year": 2020},
            "internal_note": "  긴급 건  ",
            "assignee_id": other,
        },
    )
    assert (
        body["payment_terms"]["advance_pct"] == "100"
        and body["payment_terms"]["balance_anchor"] is None
    )
    assert body["incoterm"] == {"code": "CIF", "place": "Los Angeles", "year": 2020}
    assert body["internal_note"] == "긴급 건" and body["assignee_id"] == other
    assert (
        body["advance"]["advance_amount"] == body["total_amount"]
        and body["advance"]["balance_amount"] == 0
    )
    assert body["currency"] == qt["currency"] and body["fx_rate"] == qt["fx_rate"]


# ── 미리보기: 비저장 ────────────────────────────────────────────────────────


def test_preview_persists_nothing_and_matches_the_created_pi(trade: TestClient) -> None:
    """미리보기 → PI·이력·이벤트·멱등 레코드·번호 카운터가 그대로이고, 같은 본문의 생성 결과와 값이 같다"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku(amount=700)], quantity=8)
    bank = create_bank_account("USD")

    def snapshot() -> tuple[Any, ...]:
        return (
            _scalar("SELECT count(*) FROM proforma_invoices"),
            _scalar("SELECT count(*) FROM proforma_invoice_lines"),
            _scalar("SELECT count(*) FROM proforma_invoice_status_log"),
            _scalar("SELECT count(*) FROM events WHERE event_type LIKE 'proforma_invoices.%'"),
            _scalar(
                "SELECT count(*) FROM idempotency_keys WHERE endpoint LIKE '%proforma-invoices%'"
            ),
            _rows("SELECT last_number FROM doc_number_seq WHERE prefix = 'PI'"),
            _scalar("SELECT version FROM quotations WHERE id = :i", i=qt["id"]),
        )

    before = snapshot()
    preview = _preview(trade, qt, bank)
    assert preview.status_code == 200, preview.text
    assert snapshot() == before
    body = preview.json()
    assert body["source_version"] == qt["version"] and body["qt_doc_number"] == qt["doc_number"]
    assert body["lines"][0]["open_quantity_before"] == 8 and body["lines"][0]["quantity"] == 8
    created = create_pi_via_api(trade, qt, bank)
    for field in (
        "currency",
        "total_amount",
        "valid_until",
        "buyer_name",
        "payment_terms",
        "incoterm",
        "advance",
        "bank",
    ):
        assert body[field] == created[field] or field == "bank", field
    assert body["bank"]["account_no"] == created["bank"]["account_no"]
    assert [ln["quantity"] for ln in body["lines"]] == [ln["quantity"] for ln in created["lines"]]


def test_preview_runs_the_same_validations_as_create(trade: TestClient) -> None:
    """미리보기도 초안 QT(409)·잔량 초과(409)·잘못된 통화 계좌(422)를 같은 코드로 거절한다 — 통과하면 생성도 통과하는 예고"""
    draft = create_quotation_via_api(trade, create_buyer(), [create_priced_sku()])
    bank = create_bank_account("USD")
    assert _preview(trade, draft, bank).status_code == 409
    qt = issued_quotation(trade)
    line = qt["lines"][0]["id"]
    over = _preview(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 11}])
    assert (
        over.status_code == 409
        and over.json()["error"]["code"] == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    )
    wrong = _preview(trade, qt, create_bank_account("KRW"))
    assert wrong.status_code == 422 and "bank_account_id" in wrong.json()["error"]["detail"]


# ── 소비·환원·잔량 경계 ─────────────────────────────────────────────────────


def _open(client: TestClient, qt: dict[str, Any], bank: int) -> int:
    """미리보기의 open_quantity_before로 잔량을 읽는다(전량 소진이면 0)."""
    response = _preview(client, qt, bank)
    if response.status_code == 409:
        return 0
    return int(response.json()["lines"][0]["open_quantity_before"])


def test_open_quantity_boundaries_partial_exact_and_plus_one(trade: TestClient) -> None:
    """잔량 10: 6 → 잔량 4, +1(5)은 409 EXCEEDS_OPEN(detail=잔량), 정확히 4는 성공 → 잔량 0(전량 소진은 409)"""
    qt = issued_quotation(trade, quantity=10)
    line = qt["lines"][0]["id"]
    bank = create_bank_account("USD")
    assert (
        _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 6}]).status_code == 201
    )
    assert _open(trade, qt, bank) == 4
    over = _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 5}])
    assert over.status_code == 409
    error = over.json()["error"]
    assert error["code"] == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert error["detail"]["open_quantity"] == {str(line): 4}
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 1  # 실패한 시도는 흔적이 없다
    assert (
        _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 4}]).status_code == 201
    )
    assert _open(trade, qt, bank) == 0
    # 전량 소진 — 라인 생략(잔량 전부)도, 1개 요청도 409
    assert _create(trade, qt, bank).status_code == 409
    assert (
        _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 1}]).status_code == 409
    )


def test_cancelled_pi_returns_its_quantity_to_the_quotation(trade: TestClient) -> None:
    """PI 취소 → 그 수량이 QT 잔량으로 환원돼 같은 수량으로 새 PI를 다시 만들 수 있다"""
    qt = issued_quotation(trade, quantity=10)
    bank = create_bank_account("USD")
    first = create_pi_via_api(trade, qt, bank)
    assert _create(trade, qt, bank).status_code == 409  # 전량 소진
    cancelled = trade.post(
        f"{PI}/{first['id']}/transitions",
        json={"to": "CANCELLED", "version": first["version"], "reason": "바이어 요청으로 재발행"},
        headers=idem(),
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert _open(trade, qt, bank) == 10
    assert _create(trade, qt, bank).status_code == 201


def test_expired_pi_returns_its_quantity_to_the_quotation(trade: TestClient) -> None:
    """PI가 EXPIRED(스윕)가 되면 수량이 QT로 환원된다 — 만료 PI가 QT를 죽은 재고로 잡지 않는다"""
    qt = issued_quotation(trade, quantity=10)
    bank = create_bank_account("USD")
    first = create_pi_via_api(trade, qt, bank)
    assert _open(trade, qt, bank) == 0
    with owner_engine.begin() as connection:  # 유효기간을 어제로(생성 검증 밖에서 시간 경과를 흉내)
        connection.execute(
            text(
                "UPDATE proforma_invoices SET valid_until = :d, doc_date = :d, fx_rate_date = :d WHERE id = :i"
            ),
            {"d": today_kst() - timedelta(days=1), "i": first["id"]},
        )
    from app.modules.trade_chain.expiry_sweep import sweep_expired_documents

    counts = sweep_expired_documents()
    assert counts["expired_pi"] == 1
    assert _open(trade, qt, bank) == 10


def test_free_lines_are_consumed_like_paid_lines(trade: TestClient) -> None:
    """무상(FOC) 라인도 수량이 소비되고 복사된다 — 같은 SKU 유상 1줄+무상 1줄 구조를 그대로 잇는다"""
    sku = create_priced_sku(amount=500)
    buyer = create_buyer()
    qt = issue_via_api(
        trade,
        create_quotation_via_api(
            trade,
            buyer,
            lines=[
                {"sku_id": sku, "quantity": 20},
                {"sku_id": sku, "quantity": 2, "is_free": True, "price_reason": "샘플 증정"},
            ],
        ),
    )
    body = create_pi_via_api(trade, qt)
    free = next(ln for ln in body["lines"] if ln["is_free"])
    assert (
        free["unit_price_amount"] == 0
        and free["price_reason"] == "샘플 증정"
        and free["quantity"] == 2
    )
    assert body["total_amount"] == 10000  # 무상은 금액 0


def test_a_pi_of_only_free_lines_is_rejected(trade: TestClient) -> None:
    """합계 0(전 라인 무상)은 선수금 청구서가 될 수 없다 — 422, PI 없음"""
    sku = create_priced_sku(amount=500)
    qt = issue_via_api(
        trade,
        create_quotation_via_api(
            trade,
            create_buyer(),
            lines=[{"sku_id": sku, "quantity": 2, "is_free": True, "price_reason": "샘플"}],
        ),
    )
    response = _create(trade, qt, create_bank_account("USD"))
    assert response.status_code == 422 and "lines" in response.json()["error"]["detail"]
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


# ── 원천 자격: 상태·유효기간(직접 검사) ───────────────────────────────────────────


def _make_qt_lapsed(qt_id: int, days_ago: int = 1) -> None:
    """QT 유효기간을 지난 날짜로 옮긴다(증빙일·환율 기준일도 같이 — CHECK 유지). 스윕은 돌리지 않는다."""
    day = today_kst() - timedelta(days=days_ago)
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE quotations SET valid_until = :d, doc_date = :d, fx_rate_date = :d WHERE id = :i"
            ),
            {"d": day, "i": qt_id},
        )


def test_draft_cancelled_and_expired_quotations_cannot_be_a_source(trade: TestClient) -> None:
    """초안·취소·만료 QT는 원천 자격이 없다 — 409 PARENT_NOT_USABLE(detail=상태), PI 없음"""
    bank = create_bank_account("USD")
    draft = create_quotation_via_api(trade, create_buyer(), [create_priced_sku()])
    cancelled = issued_quotation(trade)
    assert trade.post(
        f"{QT}/{cancelled['id']}/transitions",
        json={"to": "CANCELLED", "version": cancelled["version"], "reason": "테스트"},
        headers=idem(),
    ).is_success
    expired = issued_quotation(trade)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET status = 'EXPIRED' WHERE id = :i"), {"i": expired["id"]}
        )
    for qt, status in ((draft, "DRAFT"), (cancelled, "CANCELLED"), (expired, "EXPIRED")):
        current = trade.get(f"{QT}/{qt['id']}").json()  # 취소·상태 변경으로 version이 올랐다
        response = _create(trade, current, bank)
        error = response.json()["error"]
        assert response.status_code == 409, (status, response.text)
        assert error["code"] == "TRADE_DOCS.PARENT.NOT_USABLE" and error["detail"] == {
            "status": status
        }
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


def test_a_converted_quotation_is_a_valid_source(trade: TestClient) -> None:
    """수주전환(CONVERTED) QT도 잔량이 남으면 추가 PI의 원천이다(추가 PI/SO 생성 허용 — A4)"""
    qt = issued_quotation(trade)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET status = 'CONVERTED' WHERE id = :i"), {"i": qt["id"]}
        )
    assert _create(trade, qt, create_bank_account("USD")).status_code == 201


def test_validity_is_checked_directly_even_when_the_sweep_has_not_run(trade: TestClient) -> None:
    """유효기간 경과 QT(상태는 아직 ISSUED — 스윕 미실행)로 PI 생성 → 422 VALIDITY_EXPIRED, 상태·이력·번호 불변"""
    qt = issued_quotation(trade)
    _make_qt_lapsed(qt["id"])
    seq_before = _rows("SELECT last_number FROM doc_number_seq WHERE prefix = 'PI'")
    response = _create(trade, qt, create_bank_account("USD"))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "TRADE_DOCS.VALIDITY.EXPIRED"
    assert (
        _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
    )  # 상태를 쓰지 않고 거절
    assert (
        _scalar("SELECT count(*) FROM quotation_status_log WHERE quotation_id = :i", i=qt["id"])
        == 2
    )
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0
    assert _rows("SELECT last_number FROM doc_number_seq WHERE prefix = 'PI'") == seq_before


def test_the_validity_boundary_is_inclusive_of_today_kst(trade: TestClient) -> None:
    """유효기간 당일(KST)까지 유효 — valid_until = 오늘이면 생성 성공, 어제면 422"""
    today_qt = issue_via_api(
        trade,
        create_quotation_via_api(
            trade, create_buyer(), [create_priced_sku()], valid_until=today_kst().isoformat()
        ),
    )
    assert _create(trade, today_qt, create_bank_account("USD")).status_code == 201
    yesterday_qt = issued_quotation(trade)
    _make_qt_lapsed(yesterday_qt["id"], 1)
    assert _create(trade, yesterday_qt, create_bank_account("USD")).status_code == 422


def test_pi_dates_are_validated_like_the_quotation(trade: TestClient) -> None:
    """증빙일: 미래 422·원천 QT 증빙일보다 앞서면 422·오늘 허용 / 유효기간: 어제 422·오늘 허용·증빙일+365일 초과 422"""
    qt = issued_quotation(trade)
    bank = create_bank_account("USD")
    today = today_kst()
    cases = [
        ({"doc_date": (today + timedelta(days=1)).isoformat()}, "doc_date"),
        ({"doc_date": (today - timedelta(days=1)).isoformat()}, "doc_date"),  # QT 증빙일=오늘
        ({"valid_until": (today - timedelta(days=1)).isoformat()}, "valid_until"),
        ({"valid_until": (today + timedelta(days=366)).isoformat()}, "valid_until"),
    ]
    for overrides, field in cases:
        response = _create(trade, qt, bank, **overrides)
        assert response.status_code == 422, (overrides, response.text)
        assert field in response.json()["error"]["detail"], overrides
    ok = _create(trade, qt, bank, valid_until=today.isoformat(), doc_date=today.isoformat())
    assert ok.status_code == 201, ok.text


def test_a_backdated_pi_is_allowed_after_the_source_date(trade: TestClient) -> None:
    """소급 입력(증빙일=과거)은 원천 QT 증빙일 이후라면 허용된다(§21) — 과거 상한 없음"""
    qt = issued_quotation(trade)
    past = today_kst() - timedelta(days=10)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET doc_date = :d, fx_rate_date = :d WHERE id = :i"),
            {"d": past - timedelta(days=5), "i": qt["id"]},
        )
    response = _create(trade, qt, create_bank_account("USD"), doc_date=past.isoformat())
    assert response.status_code == 201 and response.json()["doc_date"] == past.isoformat()


# ── 은행 계좌 검증 ─────────────────────────────────────────────────────────────


def test_the_bank_account_must_be_active_and_in_the_pi_currency(trade: TestClient) -> None:
    """다른 통화·비활성·없는 계좌 → 422(bank_account_id 안내) — 잘못된 통화 입금 안내를 막는다. 응답에 계좌번호가 없다"""
    qt = issued_quotation(trade)
    krw = create_bank_account("KRW", account_no="KRW-SECRET-1")
    inactive = create_bank_account("USD", account_no="USD-SECRET-2")
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE bank_accounts SET deleted_at = now() WHERE id = :i"), {"i": inactive}
        )
    for bank in (krw, inactive, 999_999):
        response = _create(trade, qt, bank)
        assert response.status_code == 422, (bank, response.text)
        assert "bank_account_id" in response.json()["error"]["detail"]
        assert "SECRET" not in response.text
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


# ── 멱등·낙관 잠금 ─────────────────────────────────────────────────────────────


def test_create_requires_an_idempotency_key_and_replays_the_first_result(trade: TestClient) -> None:
    """키 결여 400 · 같은 키 같은 본문 재전송=최초 결과(PI 1건·번호 1개) · 같은 키 다른 본문 409"""
    qt = issued_quotation(trade)
    bank = create_bank_account("USD")
    body = pi_payload(qt, bank)
    url = f"{QT}/{qt['id']}/proforma-invoices"
    assert trade.post(url, json=body).status_code == 400
    headers = idem()
    first = trade.post(url, json=body, headers=headers)
    second = trade.post(url, json=body, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == second.json()
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 1
    changed = trade.post(
        url,
        json={**body, "valid_until": (today_kst() + timedelta(days=40)).isoformat()},
        headers=headers,
    )
    assert (
        changed.status_code == 409
        and changed.json()["error"]["code"] == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    )


def test_a_stale_quotation_version_is_a_409(trade: TestClient) -> None:
    """화면이 본 QT version이 낡으면(다른 곳에서 QT를 고침) 409 — 잔량·조건을 잘못 보고 생성하는 일을 막는다"""
    qt = issued_quotation(trade)
    assert trade.patch(
        f"{QT}/{qt['id']}/meta", json={"version": qt["version"], "internal_note": "변경"}
    ).is_success
    response = _create(trade, qt, create_bank_account("USD"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


def test_a_failed_creation_does_not_consume_a_number(trade: TestClient) -> None:
    """실패한 생성(잔량 초과)은 채번하지 않는다 — 다음 PI 번호는 결번 없이 이어진다"""
    qt = issued_quotation(trade, quantity=5)
    bank = create_bank_account("USD")
    line = qt["lines"][0]["id"]
    first = _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 3}])
    bad = _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 3}])
    third = _create(trade, qt, bank, lines=[{"source_line_id": line, "quantity": 2}])
    assert (first.status_code, bad.status_code, third.status_code) == (201, 409, 201)
    assert first.json()["doc_number"].endswith("-0001") and third.json()["doc_number"].endswith(
        "-0002"
    )


# ── 라인 선택 ─────────────────────────────────────────────────────────────────


def test_line_selection_subset_default_and_foreign_line(trade: TestClient) -> None:
    """라인 부분 선택(일부 라인·일부 수량) · 생략=잔량 있는 전 라인 · 다른 QT의 라인·중복 지정은 422(IDOR 안내 없음)"""
    skus = [
        create_priced_sku(amount=100),
        create_priced_sku(amount=200),
        create_priced_sku(amount=300),
    ]
    qt = issued_quotation(trade, create_buyer(), skus, quantity=10)
    other = issued_quotation(trade)
    bank = create_bank_account("USD")
    ids = [ln["id"] for ln in qt["lines"]]
    partial = _create(
        trade,
        qt,
        bank,
        lines=[
            {"source_line_id": ids[2], "quantity": 3},
            {"source_line_id": ids[0], "quantity": 10},
        ],
    )
    assert partial.status_code == 201
    got = partial.json()["lines"]
    assert [(ln["qt_line_id"], ln["quantity"], ln["line_no"]) for ln in got] == [
        (ids[0], 10, 1),
        (ids[2], 3, 2),
    ]
    assert partial.json()["total_amount"] == 10 * 100 + 3 * 300
    rest = create_pi_via_api(trade, qt, bank)  # 생략 → 잔량 있는 라인만(ids[0]은 소진 제외)
    assert [(ln["qt_line_id"], ln["quantity"]) for ln in rest["lines"]] == [
        (ids[1], 10),
        (ids[2], 7),
    ]
    foreign = _create(trade, other, bank, lines=[{"source_line_id": ids[0], "quantity": 1}])
    assert (
        foreign.status_code == 422
        and "lines[0].source_line_id" in foreign.json()["error"]["detail"]
    )
    dup = _create(
        trade,
        issued_quotation(trade),
        bank,
        lines=[{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 1}],
    )
    assert dup.status_code == 422


@pytest.mark.parametrize("quantity", [0, -1, 100_000_000, True, "3"])
def test_invalid_line_quantities_are_422(trade: TestClient, quantity: Any) -> None:
    """수량은 1..99,999,999 정수 — 0·음수·상한 초과·불리언·문자열은 422(PI 없음)"""
    qt = issued_quotation(trade)
    response = _create(
        trade,
        qt,
        create_bank_account("USD"),
        lines=[{"source_line_id": qt["lines"][0]["id"], "quantity": quantity}],
    )
    assert response.status_code == 422, (quantity, response.text)
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


def test_a_discontinued_sku_line_must_be_excluded_by_the_user(trade: TestClient) -> None:
    """원천 라인 SKU가 단종되면 그 라인이 든 PI는 422 LINE.SKU_DISCONTINUED(라인 지목) — 제외하고 만들면 성공한다"""
    skus = [create_priced_sku(amount=100), create_priced_sku(amount=200)]
    qt = issued_quotation(trade, create_buyer(), skus)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :i"), {"i": skus[0]}
        )
    bank = create_bank_account("USD")
    response = _create(trade, qt, bank)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "TRADE_DOCS.LINE.SKU_DISCONTINUED"
    assert list(error["detail"]) == ["lines[0].source_line_id"]
    ok = _create(trade, qt, bank, lines=[{"source_line_id": qt["lines"][1]["id"], "quantity": 10}])
    assert ok.status_code == 201


def test_a_soft_deleted_buyer_fails_closed(trade: TestClient) -> None:
    """바이어가 삭제·유형 상실이면 생성은 422(사용 시점 재검증 — fail-closed)"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE partners SET deleted_at = now() WHERE id = :i"), {"i": buyer}
        )
    response = _create(trade, qt, create_bank_account("USD"))
    assert response.status_code == 422, response.text
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


# ── 복제(copied_from_id) ───────────────────────────────────────────────────────


def _cancel_pi(client: TestClient, pi: dict[str, Any], reason: str = "테스트 취소") -> Any:
    return client.post(
        f"{PI}/{pi['id']}/transitions",
        json={"to": "CANCELLED", "version": pi["version"], "reason": reason},
        headers=idem(),
    )


def test_copy_from_a_dead_pi_of_the_same_quotation_only_once(trade: TestClient) -> None:
    """복제는 취소·만료된 같은 QT의 PI에서만, 살아 있는 복제본은 원본당 1개(DB 부분 유니크+서비스) — 그 밖은 409"""
    qt = issued_quotation(trade)
    bank = create_bank_account("USD")
    first = create_pi_via_api(trade, qt, bank)
    live_copy = _create(trade, qt, bank, copied_from_id=first["id"])  # 원본이 살아 있다
    assert live_copy.status_code == 409
    assert live_copy.json()["error"]["code"] == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    assert _cancel_pi(trade, first).status_code == 200
    copy = _create(trade, qt, bank, copied_from_id=first["id"])
    assert copy.status_code == 201 and copy.json()["copied_from_id"] == first["id"]
    assert copy.json()["lines"][0]["quantity"] == 10  # 취소분이 환원돼 같은 수량으로 재발행
    again = _create(trade, qt, bank, copied_from_id=first["id"])
    assert (
        again.status_code == 409
    )  # 살아 있는 복제본이 이미 있다(잔량 소진 이전에 원본 자격에서 걸린다)
    other = issued_quotation(trade)
    foreign = _create(trade, other, bank, copied_from_id=first["id"])
    assert foreign.status_code == 409  # 다른 QT의 PI는 원본이 될 수 없다


# ── 취소 전이·수렴·역순 취소 ────────────────────────────────────────────────────


def test_cancel_requires_a_reason_and_records_history_and_a_status_changed_event(
    trade: TestClient,
) -> None:
    """PI 취소: 사유 없으면 422 REASON_REQUIRED · 있으면 200 · 이력 2행(탄생+취소)·status_changed 이벤트(사유 미포함)"""
    qt = issued_quotation(trade)
    pi = create_pi_via_api(trade, qt)
    url = f"{PI}/{pi['id']}/transitions"
    missing = trade.post(url, json={"to": "CANCELLED", "version": pi["version"]}, headers=idem())
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    blank = trade.post(
        url, json={"to": "CANCELLED", "version": pi["version"], "reason": "  "}, headers=idem()
    )
    assert blank.status_code == 422
    done = _cancel_pi(trade, pi, "바이어가 주문을 취소")
    assert done.status_code == 200 and done.json()["status"] == "CANCELLED"
    log = trade.get(f"{PI}/{pi['id']}/status-log").json()
    assert [(i["from_status"], i["to_status"], i["automatic"]) for i in log["items"]] == [
        ("ISSUED", "CANCELLED", False),
        (None, "ISSUED", False),
    ]
    assert log["items"][0]["reason"] == "바이어가 주문을 취소"
    events = _rows(
        "SELECT payload FROM events WHERE event_type = 'proforma_invoices.proforma_invoice.status_changed'"
    )
    assert (
        len(events) == 1
        and "reason" not in str(events[0][0])
        and "바이어가" not in str(events[0][0])
    )
    # 종결 상태 재취소는 409(엣지 없음)
    again = trade.post(
        url,
        json={"to": "CANCELLED", "version": done.json()["version"], "reason": "재취소"},
        headers=idem(),
    )
    assert again.status_code == 409


@pytest.mark.parametrize("to", ["EXPIRED", "PAID", "PARTIALLY_PAID", "ISSUED"])
def test_automatic_targets_cannot_be_requested_through_the_public_transition(
    trade: TestClient, to: str
) -> None:
    """입금 수렴·만료 상태는 공개 전이의 to로 요청할 수 없다(스키마 Literal에서 구조적으로 제외 → 422)"""
    pi = create_pi_via_api(trade, issued_quotation(trade))
    response = trade.post(
        f"{PI}/{pi['id']}/transitions",
        json={"to": to, "version": pi["version"], "reason": "x"},
        headers=idem(),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("paid_status", ["PARTIALLY_PAID", "PAID"])
def test_a_pi_with_payments_cannot_be_cancelled(trade: TestClient, paid_status: str) -> None:
    """입금이 붙은 PI(일부입금·입금완료)는 취소 엣지가 없어 409 — 입금 역기록으로 ISSUED 복귀 후에만"""
    pi = create_pi_via_api(trade, issued_quotation(trade))
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE proforma_invoices SET status = :s WHERE id = :i"),
            {"s": paid_status, "i": pi["id"]},
        )
    response = trade.post(
        f"{PI}/{pi['id']}/transitions",
        json={"to": "CANCELLED", "version": pi["version"] + 0, "reason": "취소 시도"},
        headers=idem(),
    )
    # 직접 UPDATE는 version을 올리지 않아 낙관 잠금을 통과하고, 엣지 검사에서 409 TRANSITION.NOT_ALLOWED
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"


def test_cancel_is_idempotent_by_key_and_version_checked(trade: TestClient) -> None:
    """같은 키 재전송=최초 결과(이력 1행) · 낡은 version 409"""
    pi = create_pi_via_api(trade, issued_quotation(trade))
    url = f"{PI}/{pi['id']}/transitions"
    body = {"to": "CANCELLED", "version": pi["version"], "reason": "더블클릭"}
    key = idem()
    first = trade.post(url, json=body, headers=key)
    second = trade.post(url, json=body, headers=key)
    assert first.status_code == 200 and first.json() == second.json()
    assert (
        _scalar("SELECT count(*) FROM proforma_invoice_status_log WHERE to_status = 'CANCELLED'")
        == 1
    )
    stale = create_pi_via_api(trade, issued_quotation(trade))
    trade.patch(
        f"{PI}/{stale['id']}/meta", json={"version": stale["version"], "internal_note": "n"}
    )
    conflict = trade.post(
        f"{PI}/{stale['id']}/transitions",
        json={"to": "CANCELLED", "version": stale["version"], "reason": "낡은 화면"},
        headers=idem(),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "COMMON.CONCURRENCY.VERSION_CONFLICT"


def test_reverse_order_cancellation_guards_the_quotation_while_a_pi_lives(
    trade: TestClient,
) -> None:
    """살아 있는 PI가 있는 QT는 취소·개정 발행이 409 SUCCESSOR_ALIVE(detail=PI 번호) — PI를 먼저 취소하면 QT 취소 성공"""
    qt = issued_quotation(trade)
    pi = create_pi_via_api(trade, qt)
    cancel_qt = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt["version"], "reason": "QT 먼저 취소 시도"},
        headers=idem(),
    )
    assert cancel_qt.status_code == 409
    error = cancel_qt.json()["error"]
    assert error["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert error["detail"] == {"successors": [pi["doc_number"]]}
    revise = trade.post(
        f"{QT}/{qt['id']}/revisions", json={"version": qt["version"]}, headers=idem()
    )
    assert revise.status_code == 409
    assert _cancel_pi(trade, pi).status_code == 200
    qt_now = trade.get(f"{QT}/{qt['id']}").json()
    ok = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt_now["version"], "reason": "PI 취소 뒤 QT 취소"},
        headers=idem(),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "CANCELLED"


def test_cancelling_the_last_pi_of_a_lapsed_quotation_expires_the_quotation(
    trade: TestClient,
) -> None:
    """유효기간이 지난 QT를 붙잡던 마지막 PI가 취소되면 부모 QT가 같은 트랜잭션에서 EXPIRED로 수렴한다(이력 자동 1행 추가)"""
    qt = issued_quotation(trade)
    pi = create_pi_via_api(trade, qt)
    _make_qt_lapsed(qt["id"])
    assert (
        _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
    )  # 후속이 붙잡고 있다
    assert _cancel_pi(trade, pi).status_code == 200
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "EXPIRED"
    last = _rows(
        "SELECT from_status, to_status, automatic, actor_user_id IS NOT NULL FROM quotation_status_log"
        " WHERE quotation_id = :i ORDER BY id DESC LIMIT 1",
        i=qt["id"],
    )[0]
    assert tuple(last) == (
        "ISSUED",
        "EXPIRED",
        True,
        True,
    )  # 자동 전이지만 행위자(취소한 사람)가 남는다


# ── FREE 열 편집·조회·CSV·권한 ───────────────────────────────────────────────────


def test_meta_edit_is_allowed_after_freeze_and_touches_nothing_else(trade: TestClient) -> None:
    """내부 메모·담당자는 동결 후에도 고칠 수 있고(version 상승), 가격·조건 필드는 요청 스키마에 없다(422)"""
    from tests.support.factories import create_user

    pi = create_pi_via_api(trade, issued_quotation(trade))
    other = create_user("meta-owner@example.com", roles=(RoleCode.TRADE,))
    ok = trade.patch(
        f"{PI}/{pi['id']}/meta",
        json={"version": pi["version"], "internal_note": " 메모 ", "assignee_id": other},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["internal_note"] == "메모" and body["assignee_id"] == other
    assert body["version"] == pi["version"] + 1 and body["status"] == "ISSUED"
    for field in ("total_amount", "valid_until", "currency", "bank"):
        assert body[field] == pi[field], field
    bad = trade.patch(
        f"{PI}/{pi['id']}/meta", json={"version": body["version"], "valid_until": "2099-01-01"}
    )
    assert bad.status_code == 422
    stale = trade.patch(
        f"{PI}/{pi['id']}/meta", json={"version": pi["version"], "internal_note": "낡음"}
    )
    assert stale.status_code == 409
    inactive = create_user("gone@example.com", roles=(RoleCode.TRADE,))
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET is_active = false WHERE id = :i"), {"i": inactive}
        )
    assert (
        trade.patch(
            f"{PI}/{pi['id']}/meta", json={"version": body["version"], "assignee_id": inactive}
        ).status_code
        == 422
    )


def test_list_filters_pagination_and_lapsed_badge(trade: TestClient) -> None:
    """목록: 기본 페이지 50·상태·QT·검색어·기간 필터·최신순 · is_lapsed는 미입금 발행 상태에서만 파생"""
    buyer = create_buyer(name_en="Filter Buyer Ltd")
    qt1 = issued_quotation(trade, buyer, quantity=10)
    qt2 = issued_quotation(trade, create_buyer(name_en="Other Co"))
    bank = create_bank_account("USD")
    a = create_pi_via_api(
        trade, qt1, bank, lines=[{"source_line_id": qt1["lines"][0]["id"], "quantity": 4}]
    )
    b = create_pi_via_api(trade, qt1, bank)
    c = create_pi_via_api(trade, qt2, bank)
    assert _cancel_pi(trade, b).status_code == 200
    everything = trade.get(PI).json()
    assert everything["size"] == 50 and everything["total"] == 3
    assert [i["id"] for i in everything["items"]] == [c["id"], b["id"], a["id"]]
    assert trade.get(PI, params={"status": "CANCELLED"}).json()["total"] == 1
    assert trade.get(PI, params={"qt_id": qt1["id"]}).json()["total"] == 2
    assert trade.get(PI, params={"q": "filter buyer"}).json()["total"] == 2
    assert trade.get(PI, params={"q": a["doc_number"]}).json()["total"] == 1
    assert (
        trade.get(PI, params={"date_from": (today_kst() + timedelta(days=1)).isoformat()}).json()[
            "total"
        ]
        == 0
    )
    assert trade.get(PI, params={"size": 2, "page": 2}).json()["items"][0]["id"] == a["id"]
    assert trade.get(PI, params={"size": 201}).status_code == 422
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE proforma_invoices SET valid_until = :d, doc_date = :d, fx_rate_date = :d WHERE id = ANY(:ids)"
            ),
            {"d": today_kst() - timedelta(days=2), "ids": [a["id"], b["id"]]},
        )
    badges = {i["id"]: i["is_lapsed"] for i in trade.get(PI).json()["items"]}
    assert badges == {a["id"]: True, b["id"]: False, c["id"]: False}  # 취소 PI는 배지 대상이 아니다


def test_export_csv_has_bom_kst_dates_and_never_the_account_number(trade: TestClient) -> None:
    """CSV: UTF-8 BOM·한글 헤더·필터 반영·선수금/잔금 열 · 은행 계좌번호는 싣지 않는다"""
    qt = issued_quotation(trade)
    bank = create_bank_account("USD", account_no="987-654-321")
    pi = create_pi_via_api(trade, qt, bank)
    response = trade.get(f"{PI}/export.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    text_body = response.content.decode("utf-8-sig")
    header, *rows = text_body.strip().splitlines()
    assert header.split(",")[:3] == ["PI번호", "견적번호", "증빙일"]
    assert len(rows) == 1 and pi["doc_number"] in rows[0] and qt["doc_number"] in rows[0]
    assert "987-654-321" not in text_body and "Synthetic Bank" not in text_body
    assert "30.00" in rows[0] and "70.00" in rows[0]  # 합계 100.00의 선수금 30%·잔금
    assert (
        trade.get(f"{PI}/export.csv", params={"status": "CANCELLED"})
        .content.decode("utf-8-sig")
        .count("\n")
        == 1
    )


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT])
def test_read_only_roles_can_read_but_not_create_cancel_or_edit(role: RoleCode) -> None:
    """VIEWER·물류·인증: PI 조회는 되고(전 역할 열람) 생성·미리보기·취소·메타 편집은 403 — 서버측 역할 게이트"""
    with logged_in(RoleCode.TRADE) as trade:
        qt = issued_quotation(trade)
        bank = create_bank_account("USD")
        pi = create_pi_via_api(trade, qt, bank)
    with logged_in(role) as client:
        assert client.get(f"{PI}/{pi['id']}").status_code == 200
        assert client.get(PI).status_code == 200
        assert (
            client.post(
                f"{QT}/{qt['id']}/proforma-invoices", json=pi_payload(qt, bank), headers=idem()
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"{QT}/{qt['id']}/proforma-invoices/preview", json=pi_payload(qt, bank)
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"{PI}/{pi['id']}/transitions",
                json={"to": "CANCELLED", "version": pi["version"], "reason": "x"},
                headers=idem(),
            ).status_code
            == 403
        )
        assert (
            client.patch(f"{PI}/{pi['id']}/meta", json={"version": pi["version"]}).status_code
            == 403
        )
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 1


def test_the_pi_surface_has_no_create_edit_line_or_delete_endpoints() -> None:
    """PI 자체 POST·라인 편집·DELETE 엔드포인트가 없다 — PI는 QT 참조 생성으로만 태어나고 폐기는 CANCELLED 전이뿐(생성=발행=동결)"""
    from app.main import app

    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if "/proforma-invoices" in path
    }
    assert ("POST", "/api/v1/proforma-invoices") not in routes
    assert not any(m == "DELETE" for m, _ in routes)
    assert not any("/lines" in p for _, p in routes)
    assert routes == {
        ("GET", "/api/v1/proforma-invoices"),
        ("GET", "/api/v1/proforma-invoices/export.csv"),
        ("GET", "/api/v1/proforma-invoices/{pi_id}"),
        ("GET", "/api/v1/proforma-invoices/{pi_id}/status-log"),
        ("PATCH", "/api/v1/proforma-invoices/{pi_id}/meta"),
        ("POST", "/api/v1/proforma-invoices/{pi_id}/transitions"),
        ("POST", "/api/v1/quotations/{qt_id}/proforma-invoices"),
        ("POST", "/api/v1/quotations/{qt_id}/proforma-invoices/preview"),
    }
