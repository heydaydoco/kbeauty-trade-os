"""A·K. 수주(SO) API — 참조 생성 2단(QT/PI→SO)·소비·환원·잔량·원천 자격·PI→SO 1:1·중복 바이어 PO·복제 (S3-1 PR-7a / ADR-0052·0053 / design-A A4).

DoD ① "참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음)"의 2단: 요청 본문에 원천에 있는 값(SKU·단가·통화·환율·거래처·결제조건)을 다시 받는
필드가 **구조적으로 없고**, 서버가 원천 라인에서 값을 복사한다. SO는 RECEIVED(접수)로만 태어나고 QT 수주전환(CONVERTED)은 확정 시점이다.
DoD ② **중복 바이어 PO 0건**: 같은 (바이어, 정규화 PO번호)의 비취소 SO는 두 건이 될 수 없다 — 취소·삭제 후에는 다시 쓴다.
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
from app.modules.sales_orders.schemas import SalesOrderReferenceRequest, SoLineRequest
from app.modules.trade_docs.transition import PAYLOAD_KEYS
from tests.factories.trade import (
    create_bank_account,
    create_buyer,
    create_direct_so,
    create_pi_via_api,
    create_priced_sku,
    create_so_from_pi_via_api,
    create_so_from_qt_via_api,
    idem,
    issued_pi_chain,
    issued_quotation,
    logged_in,
    set_price,
    so_payload,
)
from tests.factories.trade import (
    unique as unique_code,
)

pytestmark = pytest.mark.group_a

QT = "/api/v1/quotations"
PI = "/api/v1/proforma-invoices"
SO = "/api/v1/sales-orders"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


def _scalar(sql: str, **params: Any) -> Any:
    return _rows(sql, **params)[0][0]


def _exec(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def _from_qt(client: TestClient, qt: dict[str, Any], **overrides: Any) -> Any:
    return client.post(
        f"{QT}/{qt['id']}/sales-orders", json=so_payload(qt, **overrides), headers=idem()
    )


def _from_pi(client: TestClient, pi: dict[str, Any], **overrides: Any) -> Any:
    return client.post(
        f"{PI}/{pi['id']}/sales-orders", json=so_payload(pi, **overrides), headers=idem()
    )


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _cancel_so(
    client: TestClient, so: dict[str, Any], reason: str = "테스트 취소"
) -> dict[str, Any]:
    response = client.post(
        f"{SO}/{so['id']}/transitions",
        json={"to": "CANCELLED", "version": so["version"], "reason": reason},
        headers=idem(),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ── 생성: 값 복사·스냅샷 ─────────────────────────────────────────────────────


def test_create_from_a_quotation_copies_every_value_without_re_entry(trade: TestClient) -> None:
    """QT→SO 생성 → 통화·환율·바이어 표기·결제조건·Incoterms·라인 단가가 QT에서 복사되고 상태는 RECEIVED다(재입력 0·확정 아님)"""
    buyer = create_buyer(name_en="Acme Trading Inc.")
    sku = create_priced_sku(amount=1250)
    qt = issued_quotation(trade, buyer, [sku], quantity=10)
    body = create_so_from_qt_via_api(trade, qt)
    assert body["status"] == "RECEIVED" and body["version"] == 1 and body["confirmed_at"] is None
    assert body["doc_number"].startswith("SO-") and body["doc_number"].endswith("-0001")
    assert body["qt_id"] == qt["id"] and body["qt_doc_number"] == qt["doc_number"]
    assert body["pi_id"] is None and body["is_reference"] is True
    for field in ("currency", "fx_rate", "fx_rate_date", "buyer_name", "dest_market_code"):
        assert body[field] == qt[field], field
    assert body["buyer_partner_id"] == buyer
    assert body["payment_terms"] == qt["payment_terms"] and body["incoterm"] == qt["incoterm"]
    assert body["total_amount"] == 12500 and body["total_text"] == "125.00"
    line, src = body["lines"][0], qt["lines"][0]
    assert line["qt_line_id"] == src["id"] and line["pi_line_id"] is None and line["quantity"] == 10
    for field in ("sku_id", "sku_code", "unit_price_amount", "list_price_amount", "price_basis"):
        assert line[field] == src[field], field
    assert line["source"]["kind"] == "QT_LINE" and line["source"]["quantity"] == 10
    assert line["quantity_delta"] == 0 and line["price_changed"] is False
    assert line["sku_status"] == "ACTIVE"


def test_create_records_birth_history_event_and_no_audit_and_leaves_the_source_untouched(
    trade: TestClient,
) -> None:
    """생성 → 탄생 이력 1행(RECEIVED)·created 이벤트(화이트리스트 payload)·audit 0 · 원천 QT는 version·상태 불변(수주전환은 SO 확정 — X-18)"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer)
    body = create_so_from_qt_via_api(trade, qt)
    log = _rows(
        "SELECT from_status, to_status, automatic FROM sales_order_status_log"
        " WHERE sales_order_id = :i",
        i=body["id"],
    )
    assert [(r[0], r[1], r[2]) for r in log] == [(None, "RECEIVED", False)]
    events = _rows(
        "SELECT payload FROM events WHERE event_type = 'sales_orders.sales_order.created'"
    )
    assert len(events) == 1
    payload = events[0][0]
    assert set(payload) <= set(PAYLOAD_KEYS) and payload["doc_type"] == "SALES_ORDER"
    assert payload["partner_id"] == buyer and payload["to_status"] == "RECEIVED"
    assert _scalar("SELECT count(*) FROM audit_log WHERE entity_type = 'sales_orders'") == 0
    after = trade.get(f"{QT}/{qt['id']}").json()
    assert (after["status"], after["version"]) == ("ISSUED", qt["version"])


def test_create_from_a_pi_copies_from_the_pi_and_fills_the_root_quotation(
    trade: TestClient,
) -> None:
    """PI→SO 생성 → 조건·환율·단가는 PI 값, `qt_id`는 PI의 QT로 채워지고 라인은 PI 라인을 가리킨다(QT 조상은 PI 라인에서 유도) · PI는 무변"""
    sku = create_priced_sku(amount=800)
    qt, pi = issued_pi_chain(trade, quantity=12, sku_ids=[sku])
    body = create_so_from_pi_via_api(trade, pi)
    assert body["pi_id"] == pi["id"] and body["pi_doc_number"] == pi["doc_number"]
    assert body["qt_id"] == qt["id"] and body["qt_doc_number"] == qt["doc_number"]
    for field in ("currency", "fx_rate", "buyer_name", "dest_market_code", "buyer_partner_id"):
        assert body[field] == pi[field], field
    assert body["payment_terms"] == pi["payment_terms"] and body["incoterm"] == pi["incoterm"]
    line, src = body["lines"][0], pi["lines"][0]
    assert line["pi_line_id"] == src["id"] and line["qt_line_id"] is None and line["quantity"] == 12
    assert line["source"]["kind"] == "PI_LINE" and line["unit_price_amount"] == 800
    after = trade.get(f"{PI}/{pi['id']}").json()
    assert (after["status"], after["version"]) == ("ISSUED", pi["version"])


def test_dod_one_the_whole_chain_needs_only_ids_and_versions(trade: TestClient) -> None:
    """DoD ① QT→PI→SO를 **원천 id+version(+PI는 유효기간·계좌)만으로** 관통하고 SO 라인이 QT 스냅샷과 값이 같다 —
    마스터 판가를 바꾼 뒤에도 값은 QT 그대로(재입력 화면 없음)"""
    sku = create_priced_sku(amount=1500)
    qt = issued_quotation(trade, create_buyer(), [sku], quantity=20)
    pi = create_pi_via_api(trade, qt, create_bank_account("USD"))
    set_price(sku, 99999, effective_from=today_kst())
    so = create_so_from_pi_via_api(trade, pi)
    assert so["lines"][0]["unit_price_amount"] == qt["lines"][0]["unit_price_amount"] == 1500
    assert so["total_amount"] == qt["total_amount"] and so["currency"] == qt["currency"]
    flow = trade.get(f"/api/v1/document-flow/SALES_ORDER/{so['id']}").json()
    assert [n["kind"] for n in flow["nodes"]] == ["QUOTATION", "PROFORMA_INVOICE", "SALES_ORDER"]


# ── 요청 스키마의 구조적 보증 ───────────────────────────────────────────────────


def test_the_reference_request_schema_has_no_field_that_restates_source_values() -> None:
    """요청 스키마 필드 집합 스냅샷 — 원천에 있는 값(SKU·단가·통화·환율·거래처·시장·결제조건)을 다시 받는 필드가 없다"""
    assert set(SalesOrderReferenceRequest.model_fields) == {
        "version",
        "doc_date",
        "buyer_po_no",
        "buyer_po_date",
        "lines",
        "assignee_id",
        "internal_note",
        "copied_from_id",
    }
    assert set(SoLineRequest.model_fields) == {
        "source_line_id",
        "quantity",
        "requested_delivery_date",
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
        "pi_id",
        "confirmed_at",
        "buyer_po_no_key",
    ],
)
def test_restating_or_server_owned_fields_are_rejected_on_create(
    trade: TestClient, field: str
) -> None:
    """원천 값 재입력·서버 소유 필드(상태·번호·합계·확정 시각·PO 정규화 키)를 본문에 실으면 422(extra=forbid) — SO 행은 생기지 않는다"""
    qt = issued_quotation(trade)
    response = trade.post(
        f"{QT}/{qt['id']}/sales-orders", json={**so_payload(qt), field: "USD"}, headers=idem()
    )
    assert response.status_code == 422, response.text
    assert _scalar("SELECT count(*) FROM sales_orders") == 0


def test_create_requires_the_idempotency_key_and_replays_the_first_result(
    trade: TestClient,
) -> None:
    """Idempotency-Key 결여는 400 · 같은 키 재수신(더블클릭)=최초 결과를 그대로(SO 1건·번호 1개·이력 1행)"""
    qt = issued_quotation(trade)
    assert trade.post(f"{QT}/{qt['id']}/sales-orders", json=so_payload(qt)).status_code == 400
    key = idem()
    first = trade.post(f"{QT}/{qt['id']}/sales-orders", json=so_payload(qt), headers=key)
    again = trade.post(f"{QT}/{qt['id']}/sales-orders", json=so_payload(qt), headers=key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert _scalar("SELECT count(*) FROM sales_orders") == 1
    assert _scalar("SELECT count(*) FROM sales_order_status_log") == 1


def test_a_stale_source_version_is_a_409(trade: TestClient) -> None:
    """낡은 원천 version → 409 VERSION_CONFLICT(QT·PI 공통) — SO는 생기지 않는다"""
    qt, pi = issued_pi_chain(trade)
    assert trade.patch(
        f"{QT}/{qt['id']}/meta", json={"version": qt["version"], "internal_note": "변경"}
    ).is_success
    assert _from_qt(trade, qt).status_code == 409
    assert trade.patch(
        f"{PI}/{pi['id']}/meta", json={"version": pi["version"], "internal_note": "변경"}
    ).is_success
    response = _from_pi(trade, pi)
    assert response.status_code == 409
    assert _code(response) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert _scalar("SELECT count(*) FROM sales_orders") == 0


# ── 소비·환원·잔량 ──────────────────────────────────────────────────────────


def test_consumption_is_shared_between_pi_and_direct_so_and_restored_on_cancel(
    trade: TestClient,
) -> None:
    """QT 라인 10 = PI 4 + 직접 SO 6 허용, 1 초과는 409 EXCEEDS_OPEN · SO를 취소하면 그만큼 즉시 환원되어 다시 만들 수 있다"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    create_pi_via_api(
        trade, qt, create_bank_account("USD"), lines=[{"source_line_id": line, "quantity": 4}]
    )
    so = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 6}])
    assert so["total_amount"] == 6000
    over = _from_qt(trade, qt, lines=[{"source_line_id": line, "quantity": 1}])
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert over.json()["error"]["detail"]["open_quantity"] == {str(line): 0}
    _cancel_so(trade, so)
    again = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 6}])
    assert again["lines"][0]["quantity"] == 6 and again["doc_number"] != so["doc_number"]


def test_omitting_lines_takes_the_whole_open_quantity_of_every_line(trade: TestClient) -> None:
    """`lines` 생략 → 잔량이 남은 라인 전부를 잔량 전부로(PI가 4 가져간 뒤엔 6) · 전량 소진이면 409(잔량 0 안내)"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    create_pi_via_api(
        trade, qt, create_bank_account("USD"), lines=[{"source_line_id": line, "quantity": 4}]
    )
    so = create_so_from_qt_via_api(trade, qt)
    assert so["lines"][0]["quantity"] == 6
    exhausted = _from_qt(trade, qt)
    assert exhausted.status_code == 409 and _code(exhausted) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"


def test_partial_quantity_delivery_dates_and_boundaries(trade: TestClient) -> None:
    """잔량 정확히 == 성공 · +1 초과 409 · 0·음수·소수 수량 422 · 요청납기는 증빙일 이전이면 422·이후면 저장"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    for bad in (0, -1, 1.5, "3"):
        r = _from_qt(trade, qt, lines=[{"source_line_id": line, "quantity": bad}])
        assert r.status_code == 422, bad
    past = (today_kst() - timedelta(days=1)).isoformat()
    r = _from_qt(
        trade,
        qt,
        lines=[{"source_line_id": line, "quantity": 3, "requested_delivery_date": past}],
    )
    assert r.status_code == 422 and "requested_delivery_date" in str(r.json()["error"]["detail"])
    future = (today_kst() + timedelta(days=45)).isoformat()
    ok = create_so_from_qt_via_api(
        trade,
        qt,
        lines=[{"source_line_id": line, "quantity": 10, "requested_delivery_date": future}],
    )
    assert ok["lines"][0]["quantity"] == 10
    assert ok["lines"][0]["requested_delivery_date"] == future


def test_a_line_of_another_quotation_is_rejected_without_revealing_it(trade: TestClient) -> None:
    """다른 QT의 라인 id를 섞으면 422(존재 여부를 알려 주지 않는다 — IDOR) · 같은 라인 두 번 지정 422"""
    qt_a = issued_quotation(trade)
    qt_b = issued_quotation(trade)
    r = _from_qt(trade, qt_a, lines=[{"source_line_id": qt_b["lines"][0]["id"], "quantity": 1}])
    assert r.status_code == 422 and "lines[0].source_line_id" in r.json()["error"]["detail"]
    dup = [{"source_line_id": qt_a["lines"][0]["id"], "quantity": 1}] * 2
    assert _from_qt(trade, qt_a, lines=dup).status_code == 422


def test_pi_lines_are_consumed_by_the_so_and_restored_when_it_is_cancelled(
    trade: TestClient,
) -> None:
    """PI 라인 소비 — PI 10 중 SO가 3 가져가면 PI 1:1이라 두 번째는 못 만들고, 취소하면 다시 전량을 만들 수 있다"""
    _qt, pi = issued_pi_chain(trade, quantity=10)
    line = pi["lines"][0]["id"]
    so = create_so_from_pi_via_api(trade, pi, lines=[{"source_line_id": line, "quantity": 3}])
    assert so["lines"][0]["quantity"] == 3 and so["lines"][0]["quantity_delta"] == -7
    blocked = _from_pi(trade, pi)
    assert blocked.status_code == 409 and _code(blocked) == "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED"
    _cancel_so(trade, so)
    full = create_so_from_pi_via_api(trade, pi)
    assert full["lines"][0]["quantity"] == 10


# ── PI → SO 활성 1:1 ────────────────────────────────────────────────────────


def test_a_pi_can_have_only_one_live_sales_order_but_a_cancelled_one_frees_it(
    trade: TestClient,
) -> None:
    """PI→SO 활성 1:1 — 두 번째 생성은 409 ALREADY_CONVERTED(점유 문서번호 안내), 취소 후 재생성 허용, 세 번째는 다시 409"""
    _qt, pi = issued_pi_chain(trade)
    first = create_so_from_pi_via_api(trade, pi)
    second = _from_pi(trade, pi)
    assert second.status_code == 409 and _code(second) == "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED"
    assert second.json()["error"]["detail"]["doc_number"] == first["doc_number"]
    _cancel_so(trade, first)
    replacement = create_so_from_pi_via_api(trade, pi)
    assert replacement["id"] != first["id"]
    assert _from_pi(trade, pi).status_code == 409
    assert (
        _scalar(
            "SELECT count(*) FROM sales_orders WHERE pi_id = :p AND status <> 'CANCELLED'",
            p=pi["id"],
        )
        == 1
    )


# ── 원천 자격 ───────────────────────────────────────────────────────────────


def test_a_draft_cancelled_or_expired_quotation_cannot_be_the_source(trade: TestClient) -> None:
    """초안·취소·만료 QT는 원천이 될 수 없다(409 PARENT.NOT_USABLE) — 초안은 동결 전이라 참조할 수 없다"""
    from tests.factories.trade import create_quotation_via_api

    draft = create_quotation_via_api(trade, create_buyer(), [create_priced_sku()])
    r = _from_qt(trade, draft)
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.PARENT.NOT_USABLE"
    qt = issued_quotation(trade)
    cancel = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt["version"], "reason": "테스트"},
        headers=idem(),
    )
    assert cancel.status_code == 200
    r = _from_qt(trade, cancel.json())
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.PARENT.NOT_USABLE"
    assert _scalar("SELECT count(*) FROM sales_orders") == 0


def _lapse(table: str, doc_id: int, valid_until: Any) -> None:
    """유효기간을 지정일로 옮기고 증빙일·환율 기준일을 그보다 앞으로 당겨 CHECK(valid_until ≥ doc_date, fx_date ≤ doc_date)를 지킨다."""
    _exec(
        f"UPDATE {table} SET valid_until = :d, doc_date = :d2, fx_rate_date = :d2 WHERE id = :i",
        d=valid_until,
        d2=valid_until - timedelta(days=5),
        i=doc_id,
    )


def test_the_quotation_validity_is_checked_directly_not_by_status(trade: TestClient) -> None:
    """유효기간 직접 검사 — 만료 스윕이 안 돌아 상태가 ISSUED여도 valid_until이 어제면 422 VALIDITY.EXPIRED, 당일까지는 허용"""
    qt = issued_quotation(trade)
    _lapse("quotations", qt["id"], today_kst() - timedelta(days=1))
    r = _from_qt(trade, trade.get(f"{QT}/{qt['id']}").json())
    assert r.status_code == 422 and _code(r) == "TRADE_DOCS.VALIDITY.EXPIRED"
    _lapse("quotations", qt["id"], today_kst())  # 당일 KST 24:00까지 유효(포함 경계)
    ok = _from_qt(trade, trade.get(f"{QT}/{qt['id']}").json())
    assert ok.status_code == 201, ok.text


def test_pi_source_status_and_validity_rules(trade: TestClient) -> None:
    """PI 원천 자격 — 취소·만료 PI 409 · 미입금 발행 PI는 유효기간 경과 시 422 · **입금이 붙은 PI(일부·완납)는 경과해도 허용**(선수금이 갇히지 않는다)"""
    for dead in ("CANCELLED", "EXPIRED"):
        _qt, pi = issued_pi_chain(trade)
        _exec("UPDATE proforma_invoices SET status = :s WHERE id = :i", s=dead, i=pi["id"])
        r = _from_pi(trade, trade.get(f"{PI}/{pi['id']}").json())
        assert r.status_code == 409 and _code(r) == "TRADE_DOCS.PARENT.NOT_USABLE", dead
    lapsed = today_kst() - timedelta(days=1)
    _qt, unpaid = issued_pi_chain(trade)
    _lapse("proforma_invoices", unpaid["id"], lapsed)
    r = _from_pi(trade, trade.get(f"{PI}/{unpaid['id']}").json())
    assert r.status_code == 422 and _code(r) == "TRADE_DOCS.VALIDITY.EXPIRED"
    for paid in ("PARTIALLY_PAID", "PAID"):
        _qt, pi = issued_pi_chain(trade)
        _lapse("proforma_invoices", pi["id"], lapsed)
        _exec("UPDATE proforma_invoices SET status = :s WHERE id = :i", s=paid, i=pi["id"])
        ok = _from_pi(trade, trade.get(f"{PI}/{pi['id']}").json())
        assert ok.status_code == 201, (paid, ok.text)


def test_deleted_source_skus_are_listed_and_can_be_excluded(trade: TestClient) -> None:
    """참조 생성 시 원천 라인 SKU가 **삭제**되었으면 전체 거부가 아니라 요청 lines 순서 기준 인덱스로 422 열거하고, 제외하면 성공한다"""
    ok_sku = create_priced_sku(amount=500)
    dead_sku = create_priced_sku(amount=700)
    qt = issued_quotation(trade, create_buyer(), [ok_sku, dead_sku], quantity=5)
    _exec("UPDATE skus SET deleted_at = now() WHERE id = :i", i=dead_sku)
    r = _from_qt(trade, qt)
    assert r.status_code == 422 and _code(r) == "TRADE_DOCS.LINE.SKU_DISCONTINUED"
    assert list(r.json()["error"]["detail"]) == ["lines[1].source_line_id"]
    keep = next(ln["id"] for ln in qt["lines"] if ln["sku_id"] == ok_sku)
    ok = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": keep, "quantity": 5}])
    assert [ln["sku_id"] for ln in ok["lines"]] == [ok_sku]


def test_discontinued_source_skus_are_accepted_and_flagged(trade: TestClient) -> None:
    """단종(DISCONTINUED) SKU는 SO 접수에서 허용(add_line과 같은 자격 — 바이어가 실제 보낸 PO) — 참조 생성(QT·PI)이 성공하고 `sku_status`로 표시된다(확정 차단은 PR-12)"""
    sku = create_priced_sku(amount=500)
    _qt, pi = issued_pi_chain(trade, sku_ids=[sku], quantity=5)
    qt2 = issued_quotation(
        trade, create_buyer(), [sku], quantity=5
    )  # 단종 전에 발행된 견적(발행 자체는 단종을 차단한다)
    _exec("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :i", i=sku)
    from_pi = create_so_from_pi_via_api(trade, pi)
    assert from_pi["lines"][0]["sku_status"] == "DISCONTINUED"
    direct = create_so_from_qt_via_api(trade, qt2)
    assert direct["lines"][0]["sku_status"] == "DISCONTINUED"


# ── 중복 바이어 PO 0건 ──────────────────────────────────────────────────────


def test_a_duplicate_buyer_po_is_rejected_with_the_occupying_document(trade: TestClient) -> None:
    """DoD ② 같은 (바이어, PO번호)의 두 번째 비취소 SO는 409 DUPLICATE_BUYER_PO — detail은 점유 문서번호·상태뿐(금액 없음)"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    first = create_so_from_qt_via_api(
        trade, qt, buyer_po_no="PO-2026-001", lines=[{"source_line_id": line, "quantity": 3}]
    )
    r = _from_qt(
        trade, qt, buyer_po_no="PO-2026-001", lines=[{"source_line_id": line, "quantity": 3}]
    )
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    detail = r.json()["error"]["detail"]
    assert detail == {"doc_number": first["doc_number"], "status": "RECEIVED"}
    assert _scalar("SELECT count(*) FROM sales_orders") == 1


@pytest.mark.parametrize(
    "variant",
    [
        "po-2026-001",
        "  PO-2026-001  ",
        "P O - 2 0 2 6 - 0 0 1",
        "PO​-2026‍-001",
        "ＰＯ－２０２６－００１",
        "PO\u20132026\u2014001",
        "PO-2026-\u3164001\ufe0f",
    ],
)
def test_po_number_normalization_catches_case_space_zero_width_and_fullwidth(
    trade: TestClient, variant: str
) -> None:
    """정규화(NFKC→제로폭 제거→대문자→공백 제거)로 소문자·앞뒤/중간 공백·제로폭·전각 변형이 모두 같은 PO로 잡힌다"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    create_so_from_qt_via_api(
        trade, qt, buyer_po_no="PO-2026-001", lines=[{"source_line_id": line, "quantity": 2}]
    )
    r = _from_qt(trade, qt, buyer_po_no=variant, lines=[{"source_line_id": line, "quantity": 2}])
    assert r.status_code == 409, variant
    assert _code(r) == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


def test_punctuation_is_preserved_and_other_buyers_may_reuse_a_po_number(trade: TestClient) -> None:
    """구두점은 보존('PO-1'≠'PO1' — 과병합 방지) · 다른 바이어는 같은 PO번호를 쓸 수 있다 · PO 미기재 SO는 몇 건이든"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=30)
    line = qt["lines"][0]["id"]

    def one(po: str | None, qty: int = 3) -> Any:
        extra = {} if po is None else {"buyer_po_no": po}
        return _from_qt(trade, qt, lines=[{"source_line_id": line, "quantity": qty}], **extra)

    assert one("PO-1").status_code == 201
    assert one("PO1").status_code == 201  # 구두점 보존 — 서로 다른 PO
    assert one("PO-1").status_code == 409
    assert one(None).status_code == 201 and one(None).status_code == 201
    assert one("   ").status_code == 201  # 공백뿐 = 미기재
    other = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=5)
    r = _from_qt(trade, other, buyer_po_no="PO-1")
    assert r.status_code == 201, r.text  # 다른 바이어


def test_a_cancelled_or_deleted_so_releases_the_po_number(trade: TestClient) -> None:
    """정정 = 취소+신규 — 취소 SO는 PO번호를 점유하지 않는다(같은 번호로 다시 등록 성공) · soft delete된 SO도 점유하지 않는다"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=30)
    line = qt["lines"][0]["id"]
    lines = [{"source_line_id": line, "quantity": 3}]
    first = create_so_from_qt_via_api(trade, qt, buyer_po_no="PO-X1", lines=lines)
    _cancel_so(trade, first)
    second = create_so_from_qt_via_api(trade, qt, buyer_po_no="PO-X1", lines=lines)
    assert second["buyer_po_no"] == "PO-X1"
    _exec("UPDATE sales_orders SET deleted_at = now() WHERE id = :i", i=second["id"])
    third = create_so_from_qt_via_api(trade, qt, buyer_po_no="PO-X1", lines=lines)
    assert third["id"] not in (first["id"], second["id"])


def test_direct_landing_also_enforces_the_duplicate_po_rule(trade: TestClient) -> None:
    """직접(인테이크형) 착지 `create_received_sales_order`도 같은 규칙 — 참조 SO가 점유한 PO를 직접 SO가 다시 못 쓴다(경로 무관 0건)"""
    from app.core.errors.exceptions import AppError

    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=5)
    create_so_from_qt_via_api(trade, qt, buyer_po_no="PO-DIRECT-1")
    with pytest.raises(AppError) as caught:
        create_direct_so(buyer, buyer_po_no="po-direct-1")
    assert caught.value.code == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    assert _scalar("SELECT count(*) FROM sales_orders") == 1
    assert create_direct_so(buyer, buyer_po_no="PO-DIRECT-2")["is_reference"] is False


def test_po_number_and_date_validation(trade: TestClient) -> None:
    """PO번호 61자·정규화 후 빈 값(제로폭뿐) 422 · PO 일자는 미래 422 · 60자 정확히는 허용"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=30)
    line = qt["lines"][0]["id"]
    lines = [{"source_line_id": line, "quantity": 1}]
    assert _from_qt(trade, qt, buyer_po_no="A" * 61, lines=lines).status_code == 422
    assert _from_qt(trade, qt, buyer_po_no="​‍", lines=lines).status_code == 422
    future = (today_kst() + timedelta(days=1)).isoformat()
    assert _from_qt(trade, qt, buyer_po_date=future, lines=lines).status_code == 422
    ok = _from_qt(
        trade, qt, buyer_po_no="A" * 60, buyer_po_date=today_kst().isoformat(), lines=lines
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["buyer_po_date"] == today_kst().isoformat()


# ── 복제(취소 후 재접수) ────────────────────────────────────────────────────


def test_copy_from_a_cancelled_so_of_the_same_buyer_only(trade: TestClient) -> None:
    """복제 원본 자격 — 같은 바이어의 **취소된** SO만(X-13) · 살아 있는 SO·다른 바이어 SO는 409 COPY.SOURCE_NOT_ELIGIBLE · 살아 있는 복제본은 원본당 1건"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=30)
    line = qt["lines"][0]["id"]
    lines = [{"source_line_id": line, "quantity": 2}]
    live = create_so_from_qt_via_api(trade, qt, lines=lines)
    r = _from_qt(trade, qt, lines=lines, copied_from_id=live["id"])
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    _cancel_so(trade, live)
    other_qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=5)
    other = create_so_from_qt_via_api(trade, other_qt)
    _cancel_so(trade, other)
    r = _from_qt(trade, qt, lines=lines, copied_from_id=other["id"])  # 다른 바이어의 취소 SO
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    copy = _from_qt(trade, qt, lines=lines, copied_from_id=live["id"])
    assert copy.status_code == 201 and copy.json()["copied_from_id"] == live["id"]
    again = _from_qt(trade, qt, lines=lines, copied_from_id=live["id"])
    assert again.status_code == 409 and _code(again) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"


# ── 조회·목록·CSV ───────────────────────────────────────────────────────────


def test_list_filters_search_pagination_and_csv(trade: TestClient) -> None:
    """목록 — 기본 페이지 50·상태/QT/PI/바이어 필터·q(수주번호·바이어명·PO번호) · CSV는 BOM+한글 헤더·원가 열 없음"""
    buyer = create_buyer(name_en="Search Buyer Ltd.")
    _qt, pi = issued_pi_chain(trade, buyer_partner_id=buyer, quantity=10)
    from_pi = create_so_from_pi_via_api(trade, pi, buyer_po_no="ZZ-7788")
    other_qt = issued_quotation(trade, create_buyer(), [create_priced_sku()])
    direct = create_so_from_qt_via_api(trade, other_qt)
    page = trade.get(SO).json()
    assert page["size"] == 50 and page["total"] == 2
    assert {it["doc_number"] for it in page["items"]} == {
        from_pi["doc_number"],
        direct["doc_number"],
    }
    assert trade.get(SO, params={"pi_id": pi["id"]}).json()["total"] == 1
    assert trade.get(SO, params={"qt_id": other_qt["id"]}).json()["items"][0]["id"] == direct["id"]
    assert trade.get(SO, params={"q": "zz-77"}).json()["total"] == 1
    assert trade.get(SO, params={"q": "Search Buyer"}).json()["total"] == 1
    assert trade.get(SO, params={"status": "CANCELLED"}).json()["total"] == 0
    assert trade.get(SO, params={"buyer_partner_id": buyer}).json()["total"] == 1
    csv = trade.get(f"{SO}/export.csv", params={"q": "ZZ-7788"})
    assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbf")
    lines = csv.content.decode("utf-8-sig").splitlines()
    assert lines[0].split(",")[:4] == ["수주번호", "증빙일", "상태", "바이어"]
    assert (
        from_pi["doc_number"] in lines[1] and "ZZ-7788" in lines[1] and pi["doc_number"] in lines[1]
    )
    header = lines[0]
    assert not any(word in header for word in ("원가", "마진", "cost", "margin"))


def test_status_log_is_paginated_newest_first(trade: TestClient) -> None:
    """상태이력 조회 — 최신순·페이지네이션 기본 50 · 탄생→보류→재개 3행"""
    qt = issued_quotation(trade)
    so = create_so_from_qt_via_api(trade, qt)
    hold = trade.post(
        f"{SO}/{so['id']}/transitions",
        json={"to": "ON_HOLD", "version": so["version"], "reason": "바이어 요청"},
        headers=idem(),
    ).json()
    trade.post(
        f"{SO}/{so['id']}/transitions",
        json={"to": "RECEIVED", "version": hold["version"]},
        headers=idem(),
    )
    page = trade.get(f"{SO}/{so['id']}/status-log").json()
    assert page["size"] == 50 and page["total"] == 3
    assert [(r["from_status"], r["to_status"]) for r in page["items"]] == [
        ("ON_HOLD", "RECEIVED"),
        ("RECEIVED", "ON_HOLD"),
        (None, "RECEIVED"),
    ]
    assert page["items"][1]["reason"] == "바이어 요청" and page["items"][1]["actor_name"]


def test_missing_documents_are_404_and_all_roles_can_read(trade: TestClient) -> None:
    """없는 SO 상세·이력·CSV 필터 404/422 · 조회 전용 역할도 상세를 읽는다(원가·마진 필드 없음)"""
    assert trade.get(f"{SO}/999999").status_code == 404
    assert trade.get(f"{SO}/999999/status-log").status_code == 404
    so = create_so_from_qt_via_api(trade, issued_quotation(trade))
    with logged_in(RoleCode.VIEWER) as viewer:
        body = viewer.get(f"{SO}/{so['id']}").json()
        assert body["doc_number"] == so["doc_number"]
        assert not any("cost" in key or "margin" in key for key in body)
        assert viewer.post(f"{SO}/{so['id']}/lines", json={}).status_code == 403


def test_doc_date_rules_on_create(trade: TestClient) -> None:
    """증빙일 — 미래(KST)는 422 · 원천 문서의 증빙일보다 앞서면 422 · 원천과 같은 날·오늘은 허용(소급 입력은 원천 이후에 한해 자유)"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=40)
    line = qt["lines"][0]["id"]
    lines = [{"source_line_id": line, "quantity": 1}]
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    assert _from_qt(trade, qt, doc_date=tomorrow, lines=lines).status_code == 422
    earlier = (today_kst() - timedelta(days=1)).isoformat()
    _exec(
        "UPDATE quotations SET doc_date = :d, fx_rate_date = :d WHERE id = :i",
        d=today_kst(),
        i=qt["id"],
    )
    qt = trade.get(f"{QT}/{qt['id']}").json()
    before = _from_qt(trade, qt, doc_date=earlier, lines=lines)
    assert before.status_code == 422 and "doc_date" in before.json()["error"]["detail"]
    same = _from_qt(trade, qt, doc_date=today_kst().isoformat(), lines=lines)
    assert same.status_code == 201 and same.json()["doc_date"] == today_kst().isoformat()


def test_direct_landing_rejects_the_same_sku_twice_and_unknown_buyers() -> None:
    """직접 착지 — 같은 (SKU, 유무상) 두 줄은 409 LINE.SKU_DUPLICATE(라인 인덱스 안내) · 바이어 유형이 아닌 거래처는 422 — 경로 무관 규칙"""
    from app.core.errors.exceptions import AppError
    from tests.support.factories import create_partner

    buyer = create_buyer()
    sku = create_priced_sku(amount=500)
    with pytest.raises(AppError) as caught:
        create_direct_so(buyer, [sku, sku])
    assert caught.value.code == "TRADE_DOCS.LINE.SKU_DUPLICATE"
    assert "lines[1].sku_id" in (caught.value.detail or {})
    supplier = create_partner(unique_code("SUP"), types=("SUPPLIER",))
    with pytest.raises(AppError) as wrong:
        create_direct_so(supplier, [create_priced_sku()])
    assert wrong.value.code == "COMMON.VALIDATION.INVALID_FIELD"
    assert _scalar("SELECT count(*) FROM sales_orders") == 0


def test_a_live_successor_of_an_so_blocks_its_cancellation(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SO 취소 가드 — 살아 있는 후속(S3-2 선적의 대역)이 있으면 409 SUCCESSOR_ALIVE(후속 번호 안내)이고 상태·이력 무변, 후속이 취소되면 성공한다"""
    from app.modules.sales_orders import ports
    from app.modules.trade_chain import lifecycle
    from app.modules.trade_docs.constants import DocKind
    from tests.factories.trade import fake_successors

    port_calls: list[str] = []

    class Recording(ports.NoAllocationPort):
        def on_cancelled(self, session: Any, order: Any) -> ports.AllocationOutcome:
            port_calls.append(order.doc_number)
            return super().on_cancelled(session, order)

    monkeypatch.setattr(lifecycle, "get_allocation_port", lambda: Recording())
    so = create_so_from_qt_via_api(trade, issued_quotation(trade))
    with fake_successors(
        monkeypatch, fk_column="so_id", table_name="scratch_shipments", parent=DocKind.SALES_ORDER
    ) as shipments:
        ship = shipments.add(so["id"], status="ISSUED", number="SH-2026-0001")
        r = trade.post(
            f"{SO}/{so['id']}/transitions",
            json={"to": "CANCELLED", "version": so["version"], "reason": "선적 있음"},
            headers=idem(),
        )
        assert r.status_code == 409 and _code(r) == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert r.json()["error"]["detail"]["successors"] == ["SH-2026-0001"]
        assert (
            port_calls == []
        )  # 후속 검사가 할당 포트 호출보다 먼저다(막힐 취소가 할당 해제를 시도하지 않는다)
        assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so["id"]) == "RECEIVED"
        assert (
            _scalar(
                "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"]
            )
            == 1
        )
        shipments.set_status(ship, "CANCELLED")
        assert _cancel_so(trade, so)["status"] == "CANCELLED"
        assert port_calls == [so["doc_number"]]
