"""A·K. 구매 발주서(PO) API — 생성(=발행=사람 1클릭)·미리보기·단가 원천·공급사 유형·복제·메타·목록·CSV (S3-1 PR-8a / ADR-0057 / design-A A12 / design-F F2~F5).

PO는 **초안이 없다** — 생성 요청 자체가 발행=발주 확정=동결이다. 단가는 미입력이면 마스터 매입가(`price_at(PURCHASE, 증빙일)`, MASTER)·입력이면 수동(MANUAL)이고
부재는 0·NULL로 대체하지 않고 422 `CATALOG.PRICE.NOT_EFFECTIVE`다. 공급사 유형은 생성 1회 검증(PURCHASE=SUPPLIER∨OEM·OEM_PRODUCTION=OEM)이다.
원가 마스킹(VIEWER)은 G 그룹(`test_purchase_order_cost_masking.py`)이 본다 — 여기는 쓰기 역할(ADMIN·TRADE) 기준의 기능 전수다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.csv_export import UTF8_BOM
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import ForbiddenError
from app.core.time import today_kst
from app.modules.catalog.models import Sku
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.trade_docs.transition import PAYLOAD_KEYS
from tests.factories.trade import (
    create_buyer,
    create_po_via_api,
    create_priced_sku,
    create_purchase_priced_sku,
    create_supplier,
    idem,
    logged_in,
    po_payload,
    raw_po,
    set_price,
    unique,
)
from tests.support.factories import create_sku, create_user

pytestmark = pytest.mark.group_a

PO = "/api/v1/purchase-orders"


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


def _create(client: TestClient, payload: dict[str, Any], **headers: str) -> Any:
    return client.post(PO, json=payload, headers=headers or idem())


# ── 생성 = 발행 ─────────────────────────────────────────────────────────────


def test_create_issues_a_po_with_master_purchase_prices_and_birth_records(
    trade: TestClient,
) -> None:
    """생성은 곧 발행 — ISSUED·동결 시각·채번·라인 스냅샷(MASTER)·합계·탄생 이력·created 이벤트가 한 트랜잭션에 생긴다(감사 기록 없음)"""
    supplier = create_supplier()
    sku_a = create_purchase_priced_sku(amount=500)
    sku_b = create_purchase_priced_sku(amount=1250)
    body = create_po_via_api(trade, supplier, [sku_a, sku_b])
    assert re.fullmatch(r"PO-\d{4}-\d{4}", body["doc_number"]) and body["doc_number"].endswith(
        "0001"
    )
    assert body["status"] == "ISSUED" and body["po_kind"] == "PURCHASE"
    assert body["frozen_at"] and body["oc_received_on"] is None and body["oc_reference"] is None
    assert body["supplier_name"] == "Synthetic Supply Co."  # 영문명 스냅샷
    assert [ln["price_basis"] for ln in body["lines"]] == ["MASTER", "MASTER"]
    assert [ln["unit_cost"] for ln in body["lines"]] == [500, 1250]
    assert [ln["line_cost"] for ln in body["lines"]] == [5000, 12500]
    assert body["total_cost"] == 17500 and body["total_text"] == "175.00"
    assert body["currency"] == "USD" and body["fx_rate"] == "1350.5" and body["last_line_no"] == 2
    log = _rows(
        "SELECT from_status, to_status, automatic, actor_user_id FROM purchase_order_status_log"
    )
    assert [(r[0], r[1], r[2]) for r in log] == [(None, "ISSUED", False)]
    events = _rows(
        "SELECT event_type, payload FROM events WHERE event_type LIKE 'purchase_orders.%'"
    )
    assert [e[0] for e in events] == ["purchase_orders.purchase_order.created"]
    payload = events[0][1]
    assert set(payload) <= set(PAYLOAD_KEYS) and payload["partner_id"] == supplier
    assert payload["doc_type"] == "PURCHASE_ORDER" and payload["from_status"] is None
    assert _scalar("SELECT count(*) FROM audit_log WHERE entity_type = 'purchase_orders'") == 0


def test_the_number_year_is_the_kst_year_and_failed_requests_do_not_consume_numbers(
    trade: TestClient,
) -> None:
    """번호는 PO-<KST 연도>-0001부터 1씩 — 실패한 요청은 번호를 소비하지 않는다(채번은 마지막 단계)"""
    supplier = create_supplier()
    sku = create_purchase_priced_sku()
    first = create_po_via_api(trade, supplier, [sku])
    bad = _create(trade, po_payload(supplier, [sku], currency="XXX"))
    assert bad.status_code == 422
    second = create_po_via_api(trade, supplier, [create_purchase_priced_sku()])
    assert first["doc_number"].startswith(f"PO-{today_kst().year}-")
    assert first["doc_number"].endswith("-0001") and second["doc_number"].endswith("-0002")


def test_manual_unit_cost_is_manual_basis_and_never_calls_the_master_lookup(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """단가를 직접 입력하면 MANUAL이고 마스터 매입가 조회(price_at)를 부르지 않는다 — 마스터와 같은 값이어도 MANUAL, 마스터 이력이 없는 SKU도 발주된다"""
    from app.modules.purchase_orders import service

    def _boom(**_: Any) -> Any:
        raise AssertionError("수동 입력 라인이 price_at을 불렀다")

    monkeypatch.setattr(service, "price_at", _boom)
    sku = create_sku(unique("NOPRICE"))  # 매입가 이력 없음
    same_as_master = create_purchase_priced_sku(amount=500)
    body = create_po_via_api(
        trade,
        None,
        [],
        lines=[
            {"sku_id": sku, "quantity": 3, "unit_cost": "12.34"},
            {"sku_id": same_as_master, "quantity": 2, "unit_cost": "5"},
        ],
    )
    assert [ln["price_basis"] for ln in body["lines"]] == ["MANUAL", "MANUAL"]
    assert [ln["unit_cost"] for ln in body["lines"]] == [1234, 500]
    assert body["total_cost"] == 3 * 1234 + 2 * 500


def test_the_master_unit_cost_is_the_price_effective_on_the_document_date(
    trade: TestClient,
) -> None:
    """마스터 매입가는 **증빙일 기준** 발효일 이하 최댓값 1행 — 발효일 당일·전날 경계·소급 증빙일에서 그 시점 단가"""
    sku = create_purchase_priced_sku(amount=100, effective_from=date(2020, 1, 1))
    set_price(sku, 200, price_type="PURCHASE", effective_from=date(2026, 3, 1))
    set_price(sku, 300, price_type="PURCHASE", effective_from=date(2026, 9, 1))
    supplier = create_supplier()

    def unit(doc_date: str | None) -> int:
        extra = {"doc_date": doc_date} if doc_date else {}
        body = create_po_via_api(trade, supplier, [sku], **extra)
        return int(body["lines"][0]["unit_cost"])

    assert unit("2026-02-28") == 100  # 발효일 전날
    assert unit("2026-03-01") == 200  # 발효일 당일
    assert unit("2026-08-31") == 200
    assert unit("2026-09-01") == 300
    assert unit(None) == 300  # 오늘(KST) 기준 최신


def test_a_missing_master_cost_is_a_visible_422_not_a_zero_and_saves_nothing(
    trade: TestClient,
) -> None:
    """기준일에 적용되는 매입가가 없으면 0·NULL로 대체하지 않고 422 CATALOG.PRICE.NOT_EFFECTIVE — 전표·라인·번호·이벤트 0건, 라인 위치 필드로 안내"""
    supplier = create_supplier()
    no_price = create_sku(unique("NP"))
    sales_only = create_priced_sku(
        amount=900
    )  # 판가(SALES)만 있고 매입가는 없다 — 종류가 섞이지 않는다
    wrong_currency = create_purchase_priced_sku(
        amount=500, currency="KRW"
    )  # USD 발주에 KRW 매입가만
    future_only = create_purchase_priced_sku(effective_from=today_kst() + timedelta(days=5))
    for sku in (no_price, sales_only, wrong_currency, future_only):
        response = _create(trade, po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": 1}]))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "CATALOG.PRICE.NOT_EFFECTIVE"
        assert "lines[0].unit_cost" in response.json()["error"]["detail"]
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0
    assert _scalar("SELECT count(*) FROM purchase_order_lines") == 0
    assert _scalar("SELECT count(*) FROM events WHERE event_type LIKE 'purchase_orders.%'") == 0
    assert _scalar("SELECT count(*) FROM doc_number_seq WHERE prefix = 'PO'") == 0


def test_a_later_master_price_change_never_alters_an_issued_po(trade: TestClient) -> None:
    """발행 뒤 마스터 매입가가 바뀌어도 PO 라인·합계는 그대로다(스냅샷은 생성 시 1회)"""
    sku = create_purchase_priced_sku(amount=500)
    body = create_po_via_api(trade, None, [sku])
    set_price(sku, 99999, price_type="PURCHASE", effective_from=today_kst())
    again = trade.get(f"{PO}/{body['id']}").json()
    assert again["lines"][0]["unit_cost"] == 500 and again["total_cost"] == 5000


@pytest.mark.parametrize(
    "raw",
    ["0", "0.00", "-1", "abc", "1e3", "", "1.234", "1,2,3", "9" * 20],
)
def test_bad_manual_unit_costs_are_rejected_without_echoing_the_value(
    trade: TestClient, raw: str
) -> None:
    """단가 0·음수·비숫자·지수 표기·자릿수 초과(USD 소수 2자리)·과대는 422 — 반올림 금지, 응답 본문에 입력값이 되돌아오지 않는다"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    response = _create(
        trade, po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": raw}])
    )
    assert response.status_code == 422, response.text
    if len(raw) > 3:
        assert raw not in response.text
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0


def test_a_line_cost_over_the_safe_integer_is_rejected(trade: TestClient) -> None:
    """수량×단가가 2^53−1을 넘으면 422 LINE.AMOUNT_OUT_OF_RANGE — DB 오버플로 500이 아니다"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    response = _create(
        trade,
        po_payload(
            supplier,
            [],
            lines=[{"sku_id": sku, "quantity": 99_999_999, "unit_cost": "90000000000000.00"}],
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE"


# ── 공급사 유형 · po_kind ────────────────────────────────────────────────────


def test_supplier_type_rules_per_po_kind(trade: TestClient) -> None:
    """PURCHASE=SUPPLIER 또는 OEM 하나 이상, OEM_PRODUCTION=OEM 필수 — 유형 없음·BUYER 전용·SUPPLIER만으로 OEM 생산 발주·삭제된 거래처·없는 id는 422, 겸유 거래처는 두 구분 모두 통과"""
    sku = create_purchase_priced_sku()
    untyped = create_supplier(types=())
    buyer_only = create_buyer()
    supplier_only = create_supplier(types=("SUPPLIER",))
    oem_only = create_supplier(types=("OEM",))
    both = create_supplier(types=("SUPPLIER", "OEM"))

    def attempt(partner: int, kind: str) -> Any:
        return _create(trade, po_payload(partner, [sku], po_kind=kind))

    for partner, kind in (
        (untyped, "PURCHASE"),
        (buyer_only, "PURCHASE"),
        (supplier_only, "OEM_PRODUCTION"),
        (buyer_only, "OEM_PRODUCTION"),
        (999_999, "PURCHASE"),
    ):
        response = attempt(partner, kind)
        assert response.status_code == 422, (partner, kind, response.text)
        assert "supplier_partner_id" in response.json()["error"]["detail"]
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0
    for partner, kind in (
        (supplier_only, "PURCHASE"),
        (oem_only, "PURCHASE"),
        (oem_only, "OEM_PRODUCTION"),
        (both, "PURCHASE"),
        (both, "OEM_PRODUCTION"),
    ):
        response = attempt(partner, kind)
        assert response.status_code == 201, (partner, kind, response.text)
        assert response.json()["po_kind"] == kind
    assert _scalar("SELECT count(*) FROM purchase_orders") == 5


def test_a_soft_deleted_supplier_or_a_released_type_cannot_be_ordered_from(
    trade: TestClient,
) -> None:
    """삭제된 거래처·유형 링크를 해제한 거래처는 평가 불능=통과가 아니라 422(fail-closed) — 이미 발행된 PO는 그대로 조회된다(역사 전표)"""
    sku = create_purchase_priced_sku()
    supplier = create_supplier(types=("SUPPLIER",))
    issued = create_po_via_api(trade, supplier, [sku])
    _exec("UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p", p=supplier)
    response = _create(trade, po_payload(supplier, [sku]))
    assert response.status_code == 422
    assert (
        trade.get(f"{PO}/{issued['id']}").status_code == 200
    )  # 유형 해제는 기발행 PO를 무효화하지 않는다
    gone = create_supplier()
    _exec("UPDATE partners SET deleted_at = now() WHERE id = :p", p=gone)
    assert _create(trade, po_payload(gone, [sku])).status_code == 422


def test_the_supplier_is_locked_for_key_share_during_creation(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """생성은 공급사 행을 FOR KEY SHARE로 잠그고 미리보기는 잠그지 않는다(유형 해제 임포트[FOR UPDATE]와 직렬화 · 여신 NO KEY UPDATE와는 비충돌)"""
    from app.modules.partners import service as partners

    seen: list[bool] = []
    real = partners.require_partner_of_any_type

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(bool(kwargs.get("lock")))
        return real(*args, **kwargs)

    monkeypatch.setattr(partners, "require_partner_of_any_type", spy)
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    assert trade.post(f"{PO}/preview", json=po_payload(supplier, [sku])).status_code == 200
    assert _create(trade, po_payload(supplier, [sku])).status_code == 201
    assert seen == [False, True]


# ── 라인 규칙 ───────────────────────────────────────────────────────────────


def test_line_rules_duplicate_discontinued_deleted_and_quantity(trade: TestClient) -> None:
    """같은 SKU 두 줄 409·단종 SKU 422·삭제/없는 SKU 422·수량 0/음수/소수/문자/상한 초과 422·요청납기 증빙일 이전 422 — 전부 전표 0건"""
    supplier = create_supplier()
    ok = create_purchase_priced_sku()
    discontinued = create_purchase_priced_sku(status="DISCONTINUED")
    deleted = create_purchase_priced_sku()
    with unit_of_work() as uow:
        sku = uow.session.get(Sku, deleted)
        assert sku is not None
        sku.deleted_at = today_kst()  # type: ignore[assignment]
    cases: list[tuple[list[dict[str, Any]], int]] = [
        ([{"sku_id": ok, "quantity": 1}, {"sku_id": ok, "quantity": 2}], 409),
        ([{"sku_id": discontinued, "quantity": 1}], 422),
        ([{"sku_id": deleted, "quantity": 1}], 422),
        ([{"sku_id": 987654, "quantity": 1}], 422),
        ([{"sku_id": ok, "quantity": 0}], 422),
        ([{"sku_id": ok, "quantity": -3}], 422),
        ([{"sku_id": ok, "quantity": 1.5}], 422),
        ([{"sku_id": ok, "quantity": "5"}], 422),
        ([{"sku_id": ok, "quantity": 100_000_000}], 422),
        ([{"sku_id": ok, "quantity": 1, "requested_delivery_date": "2000-01-01"}], 422),
    ]
    for lines, expected in cases:
        response = _create(trade, po_payload(supplier, [], lines=lines))
        assert response.status_code == expected, (lines, response.text)
    codes = {
        r.json()["error"]["code"]
        for r in (
            _create(trade, po_payload(supplier, [], lines=cases[0][0])),
            _create(trade, po_payload(supplier, [], lines=cases[1][0])),
        )
    }
    assert codes == {"TRADE_DOCS.LINE.SKU_DUPLICATE", "TRADE_DOCS.LINE.SKU_DISCONTINUED"}
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0


def test_requested_delivery_date_on_or_after_the_document_date_is_kept(trade: TestClient) -> None:
    """요청납기는 증빙일 당일·이후면 저장된다"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    due = (today_kst() + timedelta(days=30)).isoformat()
    body = create_po_via_api(
        trade,
        supplier,
        [],
        lines=[{"sku_id": sku, "quantity": 2, "requested_delivery_date": due}],
    )
    assert body["lines"][0]["requested_delivery_date"] == due


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "doc_number",
        "total_cost",
        "version",
        "frozen_at",
        "last_line_no",
        "id",
        "line_cost",
        "material_id",
        "item_type",
        "oc_received_on",
        "oc_reference",
    ],
)
def test_server_owned_and_out_of_scope_fields_are_not_accepted_on_create(
    trade: TestClient, field: str
) -> None:
    """상태·채번·합계·version·동결 시각·OC와 자재 PO 밀반입(material_id·item_type)은 요청 스키마에 구조적으로 없다(extra=forbid → 422)"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    response = _create(trade, {**po_payload(supplier, [sku]), field: 1})
    assert response.status_code == 422, response.text
    line_smuggle = po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": 1, field: 1}])
    assert _create(trade, line_smuggle).status_code == 422
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0


def test_completeness_payment_terms_incoterm_fx_and_lines_are_required(trade: TestClient) -> None:
    """동결 완결성 — 결제조건·Incoterms·환율(비KRW)·라인이 없으면 500이 아니라 422 DOCUMENT.INCOMPLETE(누락 필드 목록). KRW는 환율 1을 서버가 채운다"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    bare = {
        "supplier_partner_id": supplier,
        "currency": "USD",
        "lines": [{"sku_id": sku, "quantity": 1}],
    }
    response = _create(trade, bare)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "TRADE_DOCS.DOCUMENT.INCOMPLETE"
    assert {"payment_terms", "incoterm", "fx_rate"} <= set(error["detail"])
    empty = _create(trade, po_payload(supplier, [], lines=[]))
    assert empty.status_code == 422 and "lines" in empty.json()["error"]["detail"]
    krw_sku = create_purchase_priced_sku(currency="KRW", amount=700)
    payload = po_payload(supplier, [krw_sku], currency="KRW")
    payload.pop("fx_rate")
    created = _create(trade, payload)
    assert created.status_code == 201, created.text
    assert created.json()["fx_rate"] == "1" and created.json()["minor_units"] == 0


def test_receipt_date_anchor_is_allowed_for_purchase_and_lc_is_closed(trade: TestClient) -> None:
    """입고 확정일(RECEIPT_DATE) 기산은 PO에서 허용(판매 체인은 422) · L/C는 기능 플래그가 꺼져 있어 422 LC_DISABLED(fail-closed)"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    ok = _create(trade, po_payload(supplier, [sku]))
    assert ok.status_code == 201 and ok.json()["payment_terms"]["balance_anchor"] == "RECEIPT_DATE"
    lc = _create(trade, po_payload(supplier, [sku], payment_terms={"payment_type": "LC"}))
    assert lc.status_code == 422
    assert lc.json()["error"]["code"] == "TRADE_DOCS.PAYMENT.LC_DISABLED"


def test_the_document_date_cannot_be_in_the_future_but_backdating_is_allowed(
    trade: TestClient,
) -> None:
    """증빙일은 오늘(KST)보다 미래일 수 없고 소급은 허용된다(§21)"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    future = (today_kst() + timedelta(days=1)).isoformat()
    assert _create(trade, po_payload(supplier, [sku], doc_date=future)).status_code == 422
    past = create_po_via_api(trade, supplier, [sku], doc_date="2026-01-15")
    assert past["doc_date"] == "2026-01-15"


# ── 미리보기 ────────────────────────────────────────────────────────────────


def test_preview_returns_the_same_plan_as_create_and_writes_nothing(trade: TestClient) -> None:
    """미리보기는 생성과 같은 검증·단가 계산의 결과를 주고 채번·이력·이벤트·멱등 키·전표를 **하나도 만들지 않는다**"""
    supplier = create_supplier()
    skus = [create_purchase_priced_sku(amount=500), create_purchase_priced_sku(amount=1250)]
    payload = po_payload(supplier, skus)
    payload["lines"][1]["unit_cost"] = "99.99"
    before = {
        table: _scalar(f"SELECT count(*) FROM {table}")
        for table in (
            "purchase_orders",
            "purchase_order_status_log",
            "events",
            "idempotency_keys",
            "doc_number_seq",
        )
    }
    preview = trade.post(f"{PO}/preview", json=payload)
    assert preview.status_code == 200, preview.text
    after = {table: _scalar(f"SELECT count(*) FROM {table}") for table in before}
    assert after == before
    assert "id" not in preview.json() and "doc_number" not in preview.json()
    created = _create(trade, payload).json()
    plan = preview.json()
    assert plan["total_cost"] == created["total_cost"] == 5000 + 10 * 9999
    assert [
        (ln["sku_id"], ln["unit_cost"], ln["line_cost"], ln["price_basis"]) for ln in plan["lines"]
    ] == [
        (ln["sku_id"], ln["unit_cost"], ln["line_cost"], ln["price_basis"])
        for ln in created["lines"]
    ]
    assert [ln["price_basis"] for ln in plan["lines"]] == ["MASTER", "MANUAL"]


def test_preview_fails_exactly_like_create(trade: TestClient) -> None:
    """같은 잘못된 본문은 미리보기도 같은 422(같은 코드)다 — 매입가 부재·공급사 유형·완결성"""
    supplier = create_supplier(types=("SUPPLIER",))
    no_price = create_sku(unique("NP"))
    bodies = [
        po_payload(supplier, [], lines=[{"sku_id": no_price, "quantity": 1}]),
        po_payload(supplier, [create_purchase_priced_sku()], po_kind="OEM_PRODUCTION"),
        {"supplier_partner_id": supplier, "currency": "USD", "lines": []},
    ]
    for body in bodies:
        preview = trade.post(f"{PO}/preview", json=body)
        created = _create(trade, body)
        assert preview.status_code == created.status_code == 422
        assert preview.json()["error"]["code"] == created.json()["error"]["code"]


def test_preview_takes_no_idempotency_key_and_requires_a_write_role() -> None:
    """미리보기는 멱등 키가 필요 없고(저장 없음) 쓰기 역할만 호출한다 — LOGISTICS·CERT·VIEWER는 403"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert client.post(f"{PO}/preview", json=po_payload(supplier, [sku])).status_code == 403
    with logged_in(RoleCode.ADMIN) as admin:
        assert admin.post(f"{PO}/preview", json=po_payload(supplier, [sku])).status_code == 200


# ── 멱등 · 역할 ─────────────────────────────────────────────────────────────


def test_create_requires_an_idempotency_key_and_replays_the_first_result(trade: TestClient) -> None:
    """멱등 키 결여 400 · 같은 키 재전송은 최초 결과 그대로(PO 1건·번호 1개·이벤트 1건) · 같은 키 다른 본문은 409"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    body = po_payload(supplier, [sku])
    assert trade.post(PO, json=body).status_code == 400
    key = idem()
    first = trade.post(PO, json=body, headers=key)
    second = trade.post(PO, json=body, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert _scalar("SELECT count(*) FROM purchase_orders") == 1
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'purchase_orders.purchase_order.created'"
        )
        == 1
    )
    assert _scalar("SELECT count(*) FROM doc_number_seq WHERE prefix = 'PO'") == 1
    other = trade.post(PO, json={**body, "internal_note": "다른 값"}, headers=key)
    assert other.status_code == 409


def test_write_endpoints_reject_the_non_trade_roles() -> None:
    """생성·메타·전이는 무역(관리자 상시 통과) 전용 — LOGISTICS·CERT·VIEWER 403, 전표는 만들어지지 않는다"""
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    po = raw_po()
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert (
                client.post(PO, json=po_payload(supplier, [sku]), headers=idem()).status_code == 403
            )
            assert client.patch(f"{PO}/{po}/meta", json={"version": 1}).status_code == 403
            assert (
                client.post(
                    f"{PO}/{po}/transitions",
                    json={"to": "CANCELLED", "version": 1, "reason": "x"},
                    headers=idem(),
                ).status_code
                == 403
            )
    assert _scalar("SELECT count(*) FROM purchase_orders") == 1  # raw_po 1건뿐


def test_the_write_services_also_refuse_a_role_that_cannot_see_cost() -> None:
    """방어 이중화 — 라우터 게이트를 우회해 서비스를 직접 불러도 원가를 못 보는 역할은 생성·미리보기·메타가 거절된다(ForbiddenError)"""
    from app.modules.purchase_orders import service

    viewer = AuthenticatedUser(
        id=create_user(f"{unique('v')}@example.com", roles=(RoleCode.VIEWER,)),
        email="v@example.com",
        display_name="v",
        roles=frozenset({RoleCode.VIEWER}),
        session_id=0,
    )
    supplier, sku = create_supplier(), create_purchase_priced_sku()
    payload = po_payload(supplier, [sku])
    with pytest.raises(ForbiddenError):
        service.create_purchase_order(actor=viewer, idempotency_key=unique("k"), payload=payload)
    with pytest.raises(ForbiddenError):
        service.preview_purchase_order(actor=viewer, payload=payload)
    with pytest.raises(ForbiddenError):
        service.update_meta(actor=viewer, po_id=1, payload={"version": 1})
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0


def test_there_is_no_delete_no_put_and_no_generic_patch(trade: TestClient) -> None:
    """PO에는 DELETE·PUT·본문 PATCH가 없다 — 폐기는 취소 전이뿐이고 행·번호는 남는다. 잘못된 입력은 취소+신규(복제)로 고친다"""
    po = create_po_via_api(trade)
    url = f"{PO}/{po['id']}"
    assert trade.delete(url).status_code == 405
    assert trade.put(url, json={}).status_code == 405
    assert trade.patch(url, json={"version": po["version"]}).status_code == 405
    assert trade.get(f"{PO}/999999").status_code == 404


# ── 복제(취소+신규) ──────────────────────────────────────────────────────────


def test_copy_from_a_cancelled_po_of_the_same_supplier_only(trade: TestClient) -> None:
    """복제는 원본이 취소(죽은 상태)이고 같은 공급사일 때만 — 살아 있는 원본·다른 공급사·없는 원본 409, 살아 있는 복제본은 원본당 하나, 복제본을 취소하면 다시 복제할 수 있다"""
    supplier, other_supplier, sku = (
        create_supplier(),
        create_supplier(),
        create_purchase_priced_sku(),
    )
    source = create_po_via_api(trade, supplier, [sku])
    live_attempt = _create(trade, po_payload(supplier, [sku], copied_from_id=source["id"]))
    assert live_attempt.status_code == 409
    assert live_attempt.json()["error"]["code"] == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    cancel = trade.post(
        f"{PO}/{source['id']}/transitions",
        json={"to": "CANCELLED", "version": source["version"], "reason": "단가 오기"},
        headers=idem(),
    )
    assert cancel.status_code == 200
    assert (
        _create(trade, po_payload(other_supplier, [sku], copied_from_id=source["id"])).status_code
        == 409
    )
    assert _create(trade, po_payload(supplier, [sku], copied_from_id=999_999)).status_code == 409
    copy = _create(trade, po_payload(supplier, [sku], copied_from_id=source["id"]))
    assert copy.status_code == 201 and copy.json()["copied_from_id"] == source["id"]
    second = _create(trade, po_payload(supplier, [sku], copied_from_id=source["id"]))
    assert second.status_code == 409  # 살아 있는 복제본이 이미 있다
    trade.post(
        f"{PO}/{copy.json()['id']}/transitions",
        json={"to": "CANCELLED", "version": copy.json()["version"], "reason": "재입력"},
        headers=idem(),
    )
    assert (
        _create(trade, po_payload(supplier, [sku], copied_from_id=source["id"])).status_code == 201
    )
    # 원본은 불변(취소 사유만 이력에 남는다)
    assert trade.get(f"{PO}/{source['id']}").json()["lines"][0]["unit_cost"] == 500


# ── 메타(FREE 4열) ──────────────────────────────────────────────────────────


def test_meta_edits_free_columns_bumps_version_and_noop_leaves_no_trace(trade: TestClient) -> None:
    """메모·담당자는 동결 후에도 고친다(version +1) · 무변경 요청은 version·updated_by를 건드리지 않는다 · 낡은 version 409 · 비활성 담당자 422"""
    po = create_po_via_api(trade)
    url = f"{PO}/{po['id']}/meta"
    other = create_user(f"{unique('asg')}@example.com", roles=(RoleCode.TRADE,))
    changed = trade.patch(
        url, json={"version": po["version"], "internal_note": "  납기 확인  ", "assignee_id": other}
    )
    assert changed.status_code == 200
    body = changed.json()
    assert body["internal_note"] == "납기 확인" and body["assignee_id"] == other
    assert body["version"] == po["version"] + 1
    noop = trade.patch(url, json={"version": body["version"], "internal_note": "납기 확인"})
    assert noop.status_code == 200 and noop.json()["version"] == body["version"]
    stale = trade.patch(url, json={"version": po["version"], "internal_note": "x"})
    assert stale.status_code == 409
    inactive = create_user(f"{unique('off')}@example.com", roles=(RoleCode.TRADE,))
    _exec("UPDATE users SET is_active = false WHERE id = :u", u=inactive)
    assert (
        trade.patch(url, json={"version": body["version"], "assignee_id": inactive}).status_code
        == 422
    )
    assert (
        trade.patch(url, json={"version": body["version"], "assignee_id": None}).status_code == 422
    )
    # 가격·조건 필드는 메타 스키마에 없다
    assert trade.patch(url, json={"version": body["version"], "total_cost": 1}).status_code == 422
    assert (
        trade.patch(url, json={"version": body["version"], "po_kind": "OEM_PRODUCTION"}).status_code
        == 422
    )


def test_frozen_content_cannot_be_reached_from_any_write_path(trade: TestClient) -> None:
    """동결 열(품목·수량·원가·통화·공급사·po_kind)을 바꾸는 API 경로가 없다 — 메타 본문에 넣으면 422, 행은 그대로"""
    po = create_po_via_api(trade)
    snapshot = _rows("SELECT * FROM purchase_orders WHERE id = :i", i=po["id"])
    for field in ("quantity", "unit_cost", "currency", "supplier_partner_id", "po_kind", "lines"):
        response = trade.patch(f"{PO}/{po['id']}/meta", json={"version": po["version"], field: 1})
        assert response.status_code == 422, field
    assert _rows("SELECT * FROM purchase_orders WHERE id = :i", i=po["id"]) == snapshot


# ── 목록 · 필터 · 정렬 · CSV ──────────────────────────────────────────────────


@pytest.fixture
def three_pos(trade: TestClient) -> dict[str, Any]:
    """발행일·공급사·구분·SKU가 다른 PO 3건(첫째는 OC 기록 완료)"""
    sup_a = create_supplier(code="SUP-A", name_en="Alpha Supply", types=("SUPPLIER", "OEM"))
    sup_b = create_supplier(code="SUP-B", name_en="Beta Supply", types=("SUPPLIER",))
    sku_a = create_purchase_priced_sku("PSKU-AAA")
    sku_b = create_purchase_priced_sku("PSKU-BBB")
    po1 = create_po_via_api(trade, sup_a, [sku_a], doc_date="2026-03-01")
    po2 = create_po_via_api(trade, sup_b, [sku_b], doc_date="2026-06-01")
    po3 = create_po_via_api(
        trade, sup_a, [sku_a, sku_b], doc_date="2026-09-01", po_kind="OEM_PRODUCTION"
    )
    confirmed = trade.post(
        f"{PO}/{po1['id']}/transitions",
        json={
            "to": "SUPPLIER_CONFIRMED",
            "version": po1["version"],
            "oc_received_on": "2026-03-05",
            "oc_reference": "OC-1",
        },
        headers=idem(),
    )
    assert confirmed.status_code == 200
    return {"a": sup_a, "b": sup_b, "pos": [po1, po2, po3]}


def test_list_is_paginated_newest_first_with_filters(
    trade: TestClient, three_pos: dict[str, Any]
) -> None:
    """목록은 기본 50·최신 발행순, 상태·공급사·구분·증빙일 범위·담당자 필터가 AND로 걸리고 total은 필터 후 건수다"""
    ids = [po["id"] for po in three_pos["pos"]]
    body = trade.get(PO).json()
    assert body["size"] == 50 and body["total"] == 3
    assert [item["id"] for item in body["items"]] == ids[::-1]
    assert trade.get(PO, params={"size": 2}).json()["total"] == 3
    assert len(trade.get(PO, params={"size": 2}).json()["items"]) == 2
    assert trade.get(PO, params={"size": 201}).status_code == 422
    assert {
        i["id"] for i in trade.get(PO, params={"status": "SUPPLIER_CONFIRMED"}).json()["items"]
    } == {ids[0]}
    assert {
        i["id"]
        for i in trade.get(PO, params={"supplier_partner_id": three_pos["b"]}).json()["items"]
    } == {ids[1]}
    assert {
        i["id"] for i in trade.get(PO, params={"po_kind": "OEM_PRODUCTION"}).json()["items"]
    } == {ids[2]}
    window = trade.get(PO, params={"date_from": "2026-05-01", "date_to": "2026-07-01"}).json()
    assert [i["id"] for i in window["items"]] == [ids[1]]
    assert trade.get(PO, params={"po_kind": "NOPE"}).status_code == 422


def test_search_matches_number_supplier_name_and_sku_code_but_never_cost(
    trade: TestClient, three_pos: dict[str, Any]
) -> None:
    """q는 발주번호·공급사명·SKU 코드 부분 일치 — 원가 값(5000·50.00)으로는 찾을 수 없다(원가 검색 채널 없음)"""
    ids = [po["id"] for po in three_pos["pos"]]

    def found(q: str) -> set[int]:
        return {item["id"] for item in trade.get(PO, params={"q": q}).json()["items"]}

    assert found(three_pos["pos"][0]["doc_number"]) == {ids[0]}
    assert found("alpha") == {ids[0], ids[2]}
    assert found("PSKU-BBB") == {ids[1], ids[2]}
    assert found("psku-aaa") == {ids[0], ids[2]}
    assert found("5000") == set() and found("50.00") == set()
    assert found("%") == set()  # LIKE 와일드카드는 이스케이프된다


def test_sort_is_a_whitelist_without_cost_keys_and_unknown_filters_do_not_filter(
    trade: TestClient, three_pos: dict[str, Any]
) -> None:
    """정렬은 발행일·번호·상태·공급사명·생성일 화이트리스트 — total_cost·unit_cost·currency 정렬은 422. 원가 범위 필터 파라미터는 없어서 붙여도 무시된다(결과 동일)"""
    ids = [po["id"] for po in three_pos["pos"]]
    asc = trade.get(PO, params={"sort": "doc_date", "order": "asc"}).json()["items"]
    assert [i["id"] for i in asc] == ids
    by_supplier = trade.get(PO, params={"sort": "supplier_name", "order": "asc"}).json()["items"]
    assert (
        by_supplier[0]["supplier_name"] == "Alpha Supply"
        and by_supplier[-1]["supplier_name"] == "Beta Supply"
    )
    for key in (
        "total_cost",
        "unit_cost",
        "line_cost",
        "currency",
        "price_basis",
        "id; DROP TABLE x",
    ):
        assert trade.get(PO, params={"sort": key}).status_code == 422, key
    assert trade.get(PO, params={"order": "sideways"}).status_code == 422
    plain = trade.get(PO).json()
    for extra in ({"total_cost_min": 10**9}, {"min_total_cost": 10**9}, {"currency": "KRW"}):
        assert trade.get(PO, params=extra).json() == plain


def test_csv_export_uses_the_cost_header_for_write_roles_with_the_same_filters(
    trade: TestClient, three_pos: dict[str, Any]
) -> None:
    """CSV는 BOM+헤더 상수(원가 포함)+목록과 같은 필터 — 합계·통화 셀이 있고, 수식 시작 문자열 셀은 이스케이프된다"""
    from app.modules.purchase_orders.service import EXPORT_HEADER_WITH_COST

    _exec(
        "UPDATE purchase_orders SET supplier_name = '=cmd|calc' WHERE id = :i",
        i=three_pos["pos"][1]["id"],
    )
    response = trade.get(f"{PO}/export.csv")
    assert response.status_code == 200
    assert response.content.decode("utf-8").startswith(UTF8_BOM)
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0] == ",".join(EXPORT_HEADER_WITH_COST)
    assert len(lines) == 4 and "USD" in lines[1]
    assert "'=cmd|calc" in response.text and ",=cmd" not in response.text
    filtered = trade.get(f"{PO}/export.csv", params={"status": "SUPPLIER_CONFIRMED"})
    assert len(filtered.content.decode("utf-8-sig").splitlines()) == 2
    assert three_pos["pos"][0]["doc_number"] in filtered.text


def test_csv_export_refuses_over_the_row_cap_instead_of_truncating(
    trade: TestClient, three_pos: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한 초과는 조용히 자르지 않고 422(조건을 좁히게 한다) — 상한 이하는 전건 출력"""
    from app.modules.purchase_orders import service

    monkeypatch.setattr(service, "EXPORT_MAX_ROWS", 2)
    assert trade.get(f"{PO}/export.csv").status_code == 422
    assert trade.get(f"{PO}/export.csv", params={"status": "ISSUED"}).status_code == 200
    monkeypatch.setattr(service, "EXPORT_MAX_ROWS", 3)
    assert len(trade.get(f"{PO}/export.csv").content.decode("utf-8-sig").splitlines()) == 4


def test_the_status_log_lists_the_birth_and_every_transition_newest_first(
    trade: TestClient,
) -> None:
    """상태이력: 탄생(ISSUED)+전이가 최신순으로 — 행위자 이름·사유가 보이고 자동 표식은 항상 false"""
    po = create_po_via_api(trade)
    trade.post(
        f"{PO}/{po['id']}/transitions",
        json={"to": "CANCELLED", "version": po["version"], "reason": "공급사 사정"},
        headers=idem(),
    )
    log = trade.get(f"{PO}/{po['id']}/status-log").json()
    assert [(e["from_status"], e["to_status"]) for e in log["items"]] == [
        ("ISSUED", "CANCELLED"),
        (None, "ISSUED"),
    ]
    assert log["items"][0]["reason"] == "공급사 사정" and log["items"][0]["automatic"] is False
    assert log["items"][0]["actor_name"]
    assert trade.get(f"{PO}/999999/status-log").status_code == 404


def test_handover_moves_po_assignees_without_touching_version_or_history(trade: TestClient) -> None:
    """담당 이관 대상에 PO가 등록돼 있다 — 일괄 이관이 assignee_id만 옮기고 version·상태이력·작성자(created_by)는 건드리지 않는다"""
    from app.modules.handover import service as handover
    from app.modules.handover.targets import ASSIGNMENT_TARGETS

    assert "purchase_orders" in {t.label for t in ASSIGNMENT_TARGETS}
    po = create_po_via_api(trade)
    old_owner = po["assignee_id"]
    new_owner = create_user(f"{unique('new')}@example.com", roles=(RoleCode.TRADE,))
    before = _rows("SELECT version, created_by_id FROM purchase_orders WHERE id = :i", i=po["id"])[
        0
    ]
    result = handover.reassign_all(
        from_user_id=old_owner, to_user_id=new_owner, actor_user_id=new_owner
    )
    assert result.moved["purchase_orders"] == 1
    after = trade.get(f"{PO}/{po['id']}").json()
    assert after["assignee_id"] == new_owner
    assert (
        _rows("SELECT version, created_by_id FROM purchase_orders WHERE id = :i", i=po["id"])[0]
        == before
    )
    assert _scalar("SELECT count(*) FROM purchase_order_status_log") == 1
