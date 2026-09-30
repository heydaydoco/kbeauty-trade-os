"""A·J·K. 견적(QT) API — 작성·라인·편집·동결 불변·조회 (S3-1 PR-5a / ADR-0052·0053 / design-A·B).

DoD ④ "확정 후 단가·환율 불변"의 QT 몫: 초안은 자유롭게 고쳐지고, 발행(동결) 후에는 CONTENT·ORIGIN 열 전수가 409이며
마스터가 바뀌어도 전표 값은 불변이다. 성공 방향(초안 편집·FREE 열 편집)을 함께 검증한다(양방향 원칙).
"""

from __future__ import annotations

import random
import re
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.time import to_kst, today_kst, utcnow
from app.modules.identity.models import RoleCode
from app.modules.platform.models import FeatureFlag
from app.modules.trade_docs.policy import ColumnClass, columns_of
from app.modules.trade_docs.transition import PAYLOAD_KEYS
from tests.factories.trade import (
    create_buyer,
    create_priced_sku,
    create_quotation_via_api,
    idem,
    issue_via_api,
    logged_in,
    map_buyer_item_code,
    quotation_payload,
    set_price,
    unique,
)
from tests.support.factories import create_market, create_partner, create_sku, create_user

pytestmark = pytest.mark.group_a

QT = "/api/v1/quotations"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


# ── 작성 ────────────────────────────────────────────────────────────────────


def test_create_draft_numbers_snapshots_and_records_birth(trade: TestClient) -> None:
    """작성 → DRAFT·KST 연도 채번·바이어 표기/주소 스냅샷·라인 스냅샷·합계 서버 계산·탄생 이력·created 이벤트"""
    buyer = create_buyer(name_en="Acme Trading Inc.", address_en="1 Main St, LA")
    sku = create_priced_sku(amount=1250)
    body = create_quotation_via_api(trade, buyer, [sku])
    year = to_kst(utcnow()).year
    assert re.fullmatch(rf"QT-{year}-0001", body["doc_number"])
    assert body["status"] == "DRAFT" and body["frozen_at"] is None
    assert body["buyer_name"] == "Acme Trading Inc." and body["buyer_address"] == "1 Main St, LA"
    assert body["version"] == 1
    assert body["total_amount"] == 12500 and body["total_text"] == "125.00"
    line = body["lines"][0]
    assert (line["unit_price_amount"], line["list_price_amount"], line["price_basis"]) == (
        1250,
        1250,
        "MASTER",
    )
    assert line["line_amount"] == 12500 and line["line_no"] == 1
    log = _rows("SELECT from_status, to_status, automatic, actor_user_id FROM quotation_status_log")
    assert [(r[0], r[1], r[2]) for r in log] == [(None, "DRAFT", False)]
    events = _rows("SELECT event_type, payload FROM events WHERE event_type LIKE 'quotations.%'")
    assert [e[0] for e in events] == ["quotations.quotation.created"]
    payload = events[0][1]
    assert set(payload) <= set(PAYLOAD_KEYS) and payload["partner_id"] == buyer
    assert payload["doc_type"] == "QUOTATION" and payload["from_status"] is None
    assert _rows("SELECT count(*) FROM audit_log WHERE entity_type = 'quotations'")[0][0] == 0


def test_the_number_year_is_the_kst_year_and_numbers_are_sequential(trade: TestClient) -> None:
    """번호는 QT-<KST 연도>-0001부터 1씩 — 실패한 요청은 번호를 소비하지 않는다"""
    buyer = create_buyer()
    first = create_quotation_via_api(trade, buyer)
    bad = trade.post(QT, json={**quotation_payload(buyer), "currency": "XXX"}, headers=idem())
    assert bad.status_code == 422
    second = create_quotation_via_api(trade, buyer)
    assert first["doc_number"].endswith("-0001") and second["doc_number"].endswith("-0002")


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "doc_number",
        "total_amount",
        "version",
        "frozen_at",
        "last_line_no",
        "id",
        "line_amount",
    ],
)
def test_server_owned_fields_are_not_accepted_on_create(trade: TestClient, field: str) -> None:
    """상태·채번·합계·version·동결 시각은 요청 스키마에 구조적으로 없다(extra=forbid → 422)"""
    buyer = create_buyer()
    response = trade.post(QT, json={**quotation_payload(buyer), field: 1}, headers=idem())
    assert response.status_code == 422, response.text
    assert _rows("SELECT count(*) FROM quotations")[0][0] == 0


def test_create_requires_an_idempotency_key_and_replays_the_first_result(trade: TestClient) -> None:
    """멱등 키 결여 400 · 같은 키 재전송은 최초 결과 그대로(전표 1건) · 같은 키 다른 본문은 409"""
    buyer = create_buyer()
    body = quotation_payload(buyer)
    assert trade.post(QT, json=body).status_code == 400
    key = idem()
    first = trade.post(QT, json=body, headers=key)
    second = trade.post(QT, json=body, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert _rows("SELECT count(*) FROM quotations")[0][0] == 1
    other = trade.post(QT, json={**body, "buyer_name": "다른 값"}, headers=key)
    assert other.status_code == 409


def test_a_free_line_and_the_buyer_item_code_snapshot_on_create(trade: TestClient) -> None:
    """생성 요청의 라인: 유상+무상(같은 SKU) 공존·품번 매핑 1건이면 스냅샷·무상 합계 0"""
    buyer = create_buyer()
    sku = create_priced_sku(amount=500)
    map_buyer_item_code(buyer, sku, "ACME-001")
    body = create_quotation_via_api(
        trade,
        buyer,
        lines=[
            {"sku_id": sku, "quantity": 4},
            {"sku_id": sku, "quantity": 1, "is_free": True, "price_reason": "샘플 증정"},
        ],
    )
    paid, free = body["lines"]
    assert paid["buyer_item_code"] == "ACME-001" and paid["line_amount"] == 2000
    assert free["is_free"] and free["unit_price_amount"] == 0 and free["price_basis"] == "MANUAL"
    assert body["total_amount"] == 2000


def _validation_cases() -> list[tuple[str, dict[str, Any], str]]:
    yesterday = (today_kst() - timedelta(days=1)).isoformat()
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    tt = {
        "payment_type": "TT_ADVANCE",
        "advance_pct": "30",
        "balance_anchor": "ETD_DATE",
        "balance_days": -7,
    }
    return [
        ("future_doc_date", {"doc_date": tomorrow}, "doc_date"),
        ("unknown_currency", {"currency": "XXX"}, "currency"),
        ("unknown_market", {"dest_market_code": "ZZ"}, "dest_market_code"),
        (
            "valid_until_before_doc",
            {"doc_date": yesterday, "valid_until": (today_kst() - timedelta(days=5)).isoformat()},
            "valid_until",
        ),
        (
            "valid_until_over_a_year",
            {"valid_until": (today_kst() + timedelta(days=400)).isoformat()},
            "valid_until",
        ),
        (
            "advance_three_decimals",
            {"payment_terms": {**tt, "advance_pct": "33.333"}},
            "payment_terms.advance_pct",
        ),
        (
            "advance_over_100",
            {"payment_terms": {**tt, "advance_pct": "100.01"}},
            "payment_terms.advance_pct",
        ),
        (
            "advance_zero",
            {"payment_terms": {**tt, "advance_pct": "0"}},
            "payment_terms.advance_pct",
        ),
        (
            "advance_missing",
            {"payment_terms": {"payment_type": "TT_ADVANCE"}},
            "payment_terms.advance_pct",
        ),
        (
            "full_advance_with_anchor",
            {"payment_terms": {**tt, "advance_pct": "100"}},
            "payment_terms.balance_anchor",
        ),
        (
            "partial_advance_no_anchor",
            {"payment_terms": {"payment_type": "TT_ADVANCE", "advance_pct": "30"}},
            "payment_terms.balance_anchor",
        ),
        (
            "deferred_with_pct",
            {
                "payment_terms": {
                    "payment_type": "TT_DEFERRED",
                    "advance_pct": "10",
                    "balance_anchor": "BL_DATE",
                    "balance_days": 30,
                }
            },
            "payment_terms.advance_pct",
        ),
        (
            "receipt_anchor_on_sales",
            {
                "payment_terms": {
                    "payment_type": "TT_DEFERRED",
                    "balance_anchor": "RECEIPT_DATE",
                    "balance_days": 30,
                }
            },
            "payment_terms.balance_anchor",
        ),
        (
            "negative_days_not_etd",
            {
                "payment_terms": {
                    "payment_type": "TT_DEFERRED",
                    "balance_anchor": "BL_DATE",
                    "balance_days": -5,
                }
            },
            "payment_terms.balance_days",
        ),
        (
            "days_out_of_range",
            {
                "payment_terms": {
                    "payment_type": "TT_DEFERRED",
                    "balance_anchor": "BL_DATE",
                    "balance_days": 400,
                }
            },
            "payment_terms.balance_days",
        ),
        (
            "unknown_payment_type",
            {"payment_terms": {"payment_type": "CASH"}},
            "payment_terms.payment_type",
        ),
        (
            "dat_2020",
            {"incoterm": {"code": "DAT", "place": "Busan", "year": 2020}},
            "incoterm.year",
        ),
        (
            "dpu_2010",
            {"incoterm": {"code": "DPU", "place": "Busan", "year": 2010}},
            "incoterm.year",
        ),
        ("incoterm_unknown", {"incoterm": {"code": "XYZ", "place": "Busan"}}, "incoterm.code"),
        ("incoterm_blank_place", {"incoterm": {"code": "FOB", "place": "  "}}, "incoterm.place"),
        (
            "incoterm_newline_place",
            {"incoterm": {"code": "FOB", "place": "Bu\nsan"}},
            "incoterm.place",
        ),
        (
            "incoterm_bad_year",
            {"incoterm": {"code": "FOB", "place": "Busan", "year": 2015}},
            "incoterm.year",
        ),
        ("fx_zero", {"fx_rate": "0"}, "fx_rate"),
        ("fx_text", {"fx_rate": "abc"}, "fx_rate"),
        ("fx_nine_decimals", {"fx_rate": "1.123456789"}, "fx_rate"),
        ("fx_over_million", {"fx_rate": "1000001"}, "fx_rate"),
        ("fx_date_after_doc_date", {"fx_rate_date": tomorrow}, "fx_rate_date"),
        ("krw_rate_not_one", {"currency": "KRW", "fx_rate": "2"}, "fx_rate"),
        ("unknown_assignee", {"assignee_id": 999999}, "assignee_id"),
    ]


@pytest.mark.parametrize(
    ("name", "override", "field"), _validation_cases(), ids=[c[0] for c in _validation_cases()]
)
def test_create_validation_rejects_bad_input_with_the_field_named(
    trade: TestClient, name: str, override: dict[str, Any], field: str
) -> None:
    """입력 검증 — 잘못된 값은 422이고 어느 필드인지 알려 주며 아무것도 저장하지 않는다(번호도 소비하지 않는다)"""
    buyer = create_buyer()
    good = trade.post(QT, json=quotation_payload(buyer), headers=idem())  # 양성 대조
    assert good.status_code == 201, good.text
    response = trade.post(QT, json=quotation_payload(buyer, **override), headers=idem())
    assert response.status_code == 422, (name, response.text)
    assert field in response.json()["error"]["detail"], (name, response.json())
    assert _rows("SELECT count(*) FROM quotations")[0][0] == 1


def test_a_non_buyer_partner_cannot_be_the_quotation_buyer(trade: TestClient) -> None:
    """바이어 유형이 아닌 거래처(공급사)는 422 — 사용 시점 재검증(fail-closed)"""
    supplier = create_partner(unique("SUP"), types=("SUPPLIER",))
    response = trade.post(QT, json=quotation_payload(supplier), headers=idem())
    assert response.status_code == 422
    assert "buyer_partner_id" in response.json()["error"]["detail"]


def test_krw_quotation_gets_rate_one_and_the_document_date(trade: TestClient) -> None:
    """KRW 견적은 서버가 환율 1·기준일=증빙일을 채운다(입력 없이도)"""
    buyer = create_buyer()
    sku = create_priced_sku(amount=15000, currency="KRW")
    payload = quotation_payload(buyer, currency="KRW", lines=[{"sku_id": sku, "quantity": 3}])
    payload.pop("fx_rate")
    response = trade.post(QT, json=payload, headers=idem())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["fx_rate"] == "1" and body["fx_rate_date"] == body["doc_date"]
    assert body["total_amount"] == 45000 and body["minor_units"] == 0


def test_lc_payment_is_input_only_when_the_flag_is_on(trade: TestClient) -> None:
    """L/C는 기능 플래그 lc가 켜졌을 때만 새로 입력할 수 있다(행 없음=꺼짐 fail-closed) — 켜면 통과"""
    buyer = create_buyer()
    lc = {"payment_terms": {"payment_type": "LC"}}
    denied = trade.post(QT, json=quotation_payload(buyer, **lc), headers=idem())
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "TRADE_DOCS.PAYMENT.LC_DISABLED"
    with unit_of_work() as uow:
        uow.session.add(FeatureFlag(code="lc", name_ko="L/C", is_enabled=False))
    off = trade.post(QT, json=quotation_payload(buyer, **lc), headers=idem())
    assert off.json()["error"]["code"] == "TRADE_DOCS.PAYMENT.LC_DISABLED"
    with unit_of_work() as uow:
        uow.session.execute(text("UPDATE feature_flags SET is_enabled = true WHERE code = 'lc'"))
    ok = trade.post(QT, json=quotation_payload(buyer, **lc), headers=idem())
    assert ok.status_code == 201, ok.text
    assert ok.json()["payment_terms"]["payment_type"] == "LC"


def test_copy_needs_a_dead_source_of_the_same_buyer_and_only_one_live_copy(
    trade: TestClient,
) -> None:
    """복제(copied_from_id): 원본이 취소·만료·같은 바이어일 때만 · 살아 있는 복제본은 원본당 1개"""
    buyer, other = create_buyer(), create_buyer(name_ko="다른 바이어")
    sku = create_priced_sku()
    source = create_quotation_via_api(trade, buyer, [sku])
    live = trade.post(
        QT, json=quotation_payload(buyer, copied_from_id=source["id"]), headers=idem()
    )
    assert live.status_code == 409  # DRAFT 원본은 살아 있다 — 복제 불가
    cancelled = trade.post(
        f"{QT}/{source['id']}/transitions",
        json={"to": "CANCELLED", "version": source["version"], "reason": "가격 재협상"},
        headers=idem(),
    )
    assert cancelled.status_code == 200, cancelled.text
    other_buyer = trade.post(
        QT, json=quotation_payload(other, copied_from_id=source["id"]), headers=idem()
    )
    assert other_buyer.status_code == 409
    assert other_buyer.json()["error"]["code"] == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    first = trade.post(
        QT, json=quotation_payload(buyer, copied_from_id=source["id"]), headers=idem()
    )
    assert first.status_code == 201 and first.json()["copied_from_id"] == source["id"]
    second = trade.post(
        QT, json=quotation_payload(buyer, copied_from_id=source["id"]), headers=idem()
    )
    assert second.status_code == 409  # 원본당 살아 있는 복제본은 1개
    unknown = trade.post(QT, json=quotation_payload(buyer, copied_from_id=999999), headers=idem())
    assert unknown.status_code == 409


# ── 라인 ────────────────────────────────────────────────────────────────────


def _add_line(client: TestClient, qt: dict[str, Any], **line: Any) -> Any:
    return client.post(f"{QT}/{qt['id']}/lines", json={"version": qt["version"], **line})


def _chain(qt: dict[str, Any], response: Any) -> dict[str, Any]:
    """라인 응답의 갱신된 헤더 version·합계를 다음 요청에 이어 쓴다(연속 편집이 자기 자신에게 409를 내지 않는다)"""
    assert response.status_code in (200, 201), response.text
    out = response.json()
    return {**qt, "version": out["header_version"], "total_amount": out["total_amount"]}


def test_line_operations_bump_the_header_version_by_exactly_one_and_recompute_the_total(
    trade: TestClient,
) -> None:
    """라인 추가·수정·제외마다 헤더 version이 정확히 +1, 합계는 서버가 라인에서 재계산한다"""
    buyer = create_buyer()
    a, b = create_priced_sku(amount=1000), create_priced_sku(amount=250)
    qt = create_quotation_via_api(trade, buyer)
    v0 = qt["version"]
    added = _add_line(trade, qt, sku_id=a, quantity=3)
    assert added.status_code == 201
    assert added.json()["header_version"] == v0 + 1 and added.json()["total_amount"] == 3000
    qt = _chain(qt, added)
    line_a = added.json()["line"]
    added_b = _add_line(trade, qt, sku_id=b, quantity=10)
    assert added_b.json()["header_version"] == v0 + 2 and added_b.json()["total_amount"] == 5500
    qt = _chain(qt, added_b)
    upd = trade.patch(
        f"{QT}/{qt['id']}/lines/{line_a['id']}", json={"version": qt["version"], "quantity": 5}
    )
    assert upd.json()["header_version"] == v0 + 3 and upd.json()["total_amount"] == 7500
    assert upd.json()["line"]["id"] == line_a["id"]  # 제자리 UPDATE — 안정 id
    qt = _chain(qt, upd)
    gone = trade.delete(f"{QT}/{qt['id']}/lines/{line_a['id']}", params={"version": qt["version"]})
    assert gone.json()["header_version"] == v0 + 4 and gone.json()["total_amount"] == 2500
    assert gone.json()["line"] is None
    detail = trade.get(f"{QT}/{qt['id']}").json()
    assert (
        detail["version"] == v0 + 4 and detail["total_amount"] == 2500 and len(detail["lines"]) == 1
    )


def test_stale_version_is_rejected_on_every_write(trade: TestClient) -> None:
    """옛 version으로 라인 추가·수정·제외·헤더 편집을 하면 409 — 라인만 바뀌어도 부모 낙관 잠금이 오른다"""
    buyer = create_buyer()
    sku = create_priced_sku()
    qt = create_quotation_via_api(trade, buyer, [sku])
    line = qt["lines"][0]
    stale = qt["version"]
    ok = trade.patch(f"{QT}/{qt['id']}", json={"version": stale, "internal_note": "메모"})
    assert ok.status_code == 200
    for response in (
        _add_line(trade, {**qt, "version": stale}, sku_id=create_priced_sku(), quantity=1),
        trade.patch(f"{QT}/{qt['id']}/lines/{line['id']}", json={"version": stale, "quantity": 2}),
        trade.delete(f"{QT}/{qt['id']}/lines/{line['id']}", params={"version": stale}),
        trade.patch(f"{QT}/{qt['id']}", json={"version": stale, "internal_note": "또"}),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "COMMON.CONCURRENCY.VERSION_CONFLICT"


def test_master_price_rules_on_add(trade: TestClient) -> None:
    """판가 없음=422 CATALOG.PRICE.NOT_EFFECTIVE(0·NULL 대체 금지, 라인·번호 카운터 무변) · 판가 0=무상은 명시로만"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer)
    no_price = create_sku(unique("NOPRICE"))
    missing = _add_line(trade, qt, sku_id=no_price, quantity=1)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "CATALOG.PRICE.NOT_EFFECTIVE"
    zero = create_priced_sku(amount=0)
    zero_resp = _add_line(trade, qt, sku_id=zero, quantity=1)
    assert zero_resp.status_code == 422 and "unit_price" in zero_resp.json()["error"]["detail"]
    detail = trade.get(f"{QT}/{qt['id']}").json()
    assert (
        detail["lines"] == [] and detail["last_line_no"] == 0 and detail["version"] == qt["version"]
    )


def test_manual_price_free_line_and_amount_limits(trade: TestClient) -> None:
    """수동 단가=MANUAL(그 시점 마스터 판가를 list로 보존)·자릿수 초과 거부(반올림 금지)·무상은 사유 필수·금액 상한"""
    buyer = create_buyer()
    sku = create_priced_sku(amount=1000)
    qt = create_quotation_via_api(trade, buyer)
    manual = _add_line(trade, qt, sku_id=sku, quantity=2, unit_price="8.5")
    assert manual.status_code == 201, manual.text
    line = manual.json()["line"]
    assert (line["unit_price_amount"], line["list_price_amount"], line["price_basis"]) == (
        850,
        1000,
        "MANUAL",
    )
    qt = _chain(qt, manual)
    other = create_priced_sku()
    too_precise = _add_line(trade, qt, sku_id=other, quantity=1, unit_price="8.505")
    assert too_precise.status_code == 422 and "unit_price" in too_precise.json()["error"]["detail"]
    zero_paid = _add_line(trade, qt, sku_id=other, quantity=1, unit_price="0")
    assert zero_paid.status_code == 422  # 0원은 is_free로 명시해야 한다
    free_no_reason = _add_line(trade, qt, sku_id=other, quantity=1, is_free=True)
    assert (
        free_no_reason.status_code == 422
        and "price_reason" in free_no_reason.json()["error"]["detail"]
    )
    free = _add_line(trade, qt, sku_id=other, quantity=3, is_free=True, price_reason="신제품 샘플")
    assert free.status_code == 201
    assert free.json()["line"]["unit_price_amount"] == 0 and free.json()["total_amount"] == 1700
    qt = _chain(qt, free)
    huge = create_priced_sku(amount=1)
    over = _add_line(trade, qt, sku_id=huge, quantity=99_999_999, unit_price="90071992547409.91")
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE"


def test_line_duplicates_discontinued_and_readd_after_delete(trade: TestClient) -> None:
    """같은 SKU 유상 2줄=409(유상+무상은 허용) · 단종 SKU 추가=422 · 제외 후 재추가 허용·라인 번호는 재사용 안 함"""
    buyer = create_buyer()
    sku = create_priced_sku()
    qt = create_quotation_via_api(trade, buyer)
    first = _add_line(trade, qt, sku_id=sku, quantity=1)
    qt = _chain(qt, first)
    dup = _add_line(trade, qt, sku_id=sku, quantity=2)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "TRADE_DOCS.LINE.SKU_DUPLICATE"
    free = _add_line(trade, qt, sku_id=sku, quantity=1, is_free=True, price_reason="증정")
    assert free.status_code == 201
    qt = _chain(qt, free)
    free_twice = _add_line(trade, qt, sku_id=sku, quantity=1, is_free=True, price_reason="또")
    assert free_twice.status_code == 409
    discontinued = create_priced_sku(status="DISCONTINUED")
    denied = _add_line(trade, qt, sku_id=discontinued, quantity=1)
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "TRADE_DOCS.LINE.SKU_DISCONTINUED"
    missing = _add_line(trade, qt, sku_id=999999, quantity=1)
    assert missing.status_code == 422 and "sku_id" in missing.json()["error"]["detail"]
    removed = trade.delete(
        f"{QT}/{qt['id']}/lines/{first.json()['line']['id']}", params={"version": qt["version"]}
    )
    qt = _chain(qt, removed)
    again = _add_line(trade, qt, sku_id=sku, quantity=4)
    assert again.status_code == 201
    assert again.json()["line"]["line_no"] == 3  # 1(삭제)·2(무상)·3 — 번호는 재사용하지 않는다


def test_line_update_rules_price_basis_and_sku_is_immutable(trade: TestClient) -> None:
    """라인 수정: 단가를 바꾸면 MANUAL · sku_id는 스키마에 없다(422) · 무상 해제엔 단가 필요"""
    buyer = create_buyer()
    sku = create_priced_sku(amount=1000)
    qt = create_quotation_via_api(trade, buyer, [sku])
    line = qt["lines"][0]
    base = f"{QT}/{qt['id']}/lines/{line['id']}"
    assert trade.patch(base, json={"version": qt["version"], "sku_id": 1}).status_code == 422
    changed = trade.patch(base, json={"version": qt["version"], "unit_price": "9"})
    assert changed.status_code == 200
    assert changed.json()["line"]["price_basis"] == "MANUAL"
    assert changed.json()["line"]["list_price_amount"] == 1000  # 라인 생성 시점 스냅샷 유지
    qt = _chain(qt, changed)
    same = trade.patch(base, json={"version": qt["version"], "quantity": 20})
    assert same.json()["line"]["line_amount"] == 18000  # 20 × 9.00 USD(최소단위 900)
    qt = _chain(qt, same)
    to_free = trade.patch(
        base, json={"version": qt["version"], "is_free": True, "price_reason": "프로모션"}
    )
    assert to_free.status_code == 200 and to_free.json()["total_amount"] == 0
    qt = _chain(qt, to_free)
    back = trade.patch(base, json={"version": qt["version"], "is_free": False})
    assert back.status_code == 422 and "unit_price" in back.json()["error"]["detail"]


def test_a_line_of_another_quotation_is_not_reachable_through_this_one(trade: TestClient) -> None:
    """다른 견적의 line_id를 섞으면 수정·제외 모두 404(IDOR — 소속 단언)"""
    buyer = create_buyer()
    sku = create_priced_sku()
    mine = create_quotation_via_api(trade, buyer, [sku])
    theirs = create_quotation_via_api(trade, buyer, [sku])
    foreign = theirs["lines"][0]["id"]
    patch = trade.patch(
        f"{QT}/{mine['id']}/lines/{foreign}", json={"version": mine["version"], "quantity": 9}
    )
    delete = trade.delete(f"{QT}/{mine['id']}/lines/{foreign}", params={"version": mine["version"]})
    assert patch.status_code == delete.status_code == 404
    after = trade.get(f"{QT}/{theirs['id']}").json()
    assert after["lines"][0]["quantity"] == 10 and after["version"] == theirs["version"]


def test_buyer_item_code_snapshot_rules(trade: TestClient) -> None:
    """바이어 품번: 매핑 2건이면 추측하지 않고 NULL(지정하면 그 값) · 집합 밖 지정은 422"""
    buyer = create_buyer()
    sku = create_priced_sku()
    map_buyer_item_code(buyer, sku, "A-1")
    map_buyer_item_code(buyer, sku, "A-2")
    qt = create_quotation_via_api(trade, buyer)
    unspecified = _add_line(trade, qt, sku_id=sku, quantity=1)
    assert unspecified.json()["line"]["buyer_item_code"] is None
    qt = _chain(qt, unspecified)
    line_id = unspecified.json()["line"]["id"]
    base = f"{QT}/{qt['id']}/lines/{line_id}"
    picked = trade.patch(base, json={"version": qt["version"], "buyer_item_code": "A-2"})
    assert picked.json()["line"]["buyer_item_code"] == "A-2"
    qt = _chain(qt, picked)
    outside = trade.patch(base, json={"version": qt["version"], "buyer_item_code": "ZZ-9"})
    assert outside.status_code == 422


def test_random_line_operations_keep_header_total_equal_to_the_line_sum(trade: TestClient) -> None:
    """랜덤 조작 60회 후 매번 헤더 합계 = 활성 라인 금액 합 — 야간 검산(verify)도 0건"""
    from app.modules.trade_docs.verify import verify_document_totals

    rng = random.Random(20260930)
    buyer = create_buyer()
    skus = [create_priced_sku(amount=rng.choice([1, 99, 1250, 777])) for _ in range(6)]
    qt = create_quotation_via_api(trade, buyer)
    live: dict[int, int] = {}  # line_id → sku
    for _ in range(60):
        action = rng.choice(["add", "add", "update", "delete"]) if live else "add"
        if action == "add":
            free_skus = [s for s in skus if s not in live.values()]
            if not free_skus:
                continue
            response = _add_line(
                trade, qt, sku_id=rng.choice(free_skus), quantity=rng.randint(1, 500)
            )
            live[response.json()["line"]["id"]] = response.json()["line"]["sku_id"]
        elif action == "update":
            line_id = rng.choice(list(live))
            response = trade.patch(
                f"{QT}/{qt['id']}/lines/{line_id}",
                json={"version": qt["version"], "quantity": rng.randint(1, 500)},
            )
        else:
            line_id = rng.choice(list(live))
            response = trade.delete(
                f"{QT}/{qt['id']}/lines/{line_id}", params={"version": qt["version"]}
            )
            del live[line_id]
        qt = _chain(qt, response)
        detail = trade.get(f"{QT}/{qt['id']}").json()
        assert detail["total_amount"] == sum(line["line_amount"] for line in detail["lines"])
    with unit_of_work() as uow:
        assert verify_document_totals(uow.session) == []


# ── 편집·동결 불변 ──────────────────────────────────────────────────────────


def test_draft_edit_changes_the_content_and_bumps_the_version(trade: TestClient) -> None:
    """초안 편집(성공 방향): 조건·환율·유효기간·바이어 표기가 바뀌고 version이 오른다 · 바이어 변경은 표기를 재복사한다"""
    old, new = (
        create_buyer(name_en="Old Buyer"),
        create_buyer(name_en="New Buyer", address_en="9 Elm St"),
    )
    create_market("JP")
    qt = create_quotation_via_api(trade, old)
    edited = trade.patch(
        f"{QT}/{qt['id']}",
        json={
            "version": qt["version"],
            "buyer_partner_id": new,
            "dest_market_code": "JP",
            "fx_rate": "1400",
            "payment_terms": {
                "payment_type": "TT_DEFERRED",
                "balance_anchor": "BL_DATE",
                "balance_days": 30,
            },
            "incoterm": {"code": "CIF", "place": "Tokyo", "year": 2020},
            "internal_note": "협상 중",
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["version"] == qt["version"] + 1
    assert (body["buyer_name"], body["buyer_address"]) == ("New Buyer", "9 Elm St")
    assert body["dest_market_code"] == "JP" and body["fx_rate"] == "1400"
    assert (
        body["payment_terms"]["payment_type"] == "TT_DEFERRED"
        and body["payment_terms"]["advance_pct"] is None
    )
    assert body["incoterm"]["code"] == "CIF" and body["internal_note"] == "협상 중"
    cleared = trade.patch(
        f"{QT}/{qt['id']}", json={"version": body["version"], "payment_terms": None}
    )
    assert cleared.json()["payment_terms"]["payment_type"] is None


def test_draft_currency_change_is_only_possible_before_any_line_exists(trade: TestClient) -> None:
    """통화 변경: 라인이 한 번이라도 있었으면 422(복합 FK — 제외된 라인 포함) · 없으면 가능 — KRW는 환율 1, 비KRW는 새로 입력"""
    buyer = create_buyer()
    sku = create_priced_sku(amount=1000)
    empty = create_quotation_via_api(trade, buyer)
    krw = trade.patch(f"{QT}/{empty['id']}", json={"version": empty["version"], "currency": "KRW"})
    assert krw.status_code == 200 and krw.json()["fx_rate"] == "1"
    eur = trade.patch(
        f"{QT}/{empty['id']}",
        json={"version": krw.json()["version"], "currency": "EUR", "fx_rate": "1500"},
    )
    assert eur.status_code == 200 and eur.json()["fx_rate"] == "1500"
    usd = trade.patch(
        f"{QT}/{empty['id']}", json={"version": eur.json()["version"], "currency": "USD"}
    )
    assert usd.json()["fx_rate"] is None  # 옛 환율을 다른 통화에 끌고 가지 않는다
    with_line = create_quotation_via_api(trade, buyer, [sku])
    blocked = trade.patch(
        f"{QT}/{with_line['id']}",
        json={"version": with_line["version"], "currency": "EUR", "fx_rate": "1500"},
    )
    assert blocked.status_code == 422 and "currency" in blocked.json()["error"]["detail"]
    line = with_line["lines"][0]
    removed = trade.delete(
        f"{QT}/{with_line['id']}/lines/{line['id']}", params={"version": with_line["version"]}
    )
    still = trade.patch(
        f"{QT}/{with_line['id']}",
        json={"version": removed.json()["header_version"], "currency": "EUR", "fx_rate": "1500"},
    )
    assert still.status_code == 422  # 제외(soft delete)된 라인도 FK로 통화를 잡고 있다


def _frozen_quotation(client: TestClient) -> tuple[dict[str, Any], int, int, int]:
    buyer = create_buyer()
    sku = create_priced_sku(amount=2000)
    qt = create_quotation_via_api(client, buyer, [sku])
    return issue_via_api(client, qt), buyer, sku, create_buyer(name_en="Other Buyer")


def _frozen_patches(other_buyer: int) -> dict[str, dict[str, Any]]:
    """PATCH 스키마가 노출하는 CONTENT 필드 → 현재값과 다른 값(필드별 하나씩)"""
    create_market("JP")
    return {
        "buyer_partner_id": {"buyer_partner_id": other_buyer},
        "dest_market_code": {"dest_market_code": "JP"},
        "currency": {"currency": "EUR"},
        "doc_date": {"doc_date": (today_kst() - timedelta(days=1)).isoformat()},
        "valid_until": {"valid_until": (today_kst() + timedelta(days=90)).isoformat()},
        "fx_rate": {"fx_rate": "1499"},
        "fx_rate_date": {"fx_rate_date": (today_kst() - timedelta(days=1)).isoformat()},
        "payment_terms": {
            "payment_terms": {
                "payment_type": "TT_DEFERRED",
                "balance_anchor": "BL_DATE",
                "balance_days": 60,
            }
        },
        "incoterm": {"incoterm": {"code": "CIF", "place": "Los Angeles", "year": 2020}},
        "buyer_name": {"buyer_name": "다른 표기"},
        "buyer_address": {"buyer_address": "다른 주소"},
    }


def test_frozen_quotation_rejects_every_content_field_and_stays_unchanged(
    trade: TestClient,
) -> None:
    """발행 후 CONTENT 전 필드 수정은 409 FROZEN(필드명만) · 행 전체(version 포함)가 그대로다 — 확정 후 불변(DoD ④)"""
    qt, _buyer, _sku, other = _frozen_quotation(trade)
    before = trade.get(f"{QT}/{qt['id']}").json()
    for name, body in _frozen_patches(other).items():
        response = trade.patch(f"{QT}/{qt['id']}", json={"version": qt["version"], **body})
        assert response.status_code == 409, (name, response.text)
        error = response.json()["error"]
        assert error["code"] == "TRADE_DOCS.DOCUMENT.FROZEN", name
        assert error["detail"]["fields"], name
        assert "2000" not in response.text and "Other" not in response.text  # 값·금액 미기재
    assert trade.get(f"{QT}/{qt['id']}").json() == before


def test_the_frozen_patch_matrix_covers_every_content_column_of_the_field_policy() -> None:
    """FIELD_POLICY의 CONTENT·ORIGIN 열은 전부 (a) PATCH 필드로 시험되거나 (b) API로 입력할 수 없는 열이다 — 누락 0"""
    content = columns_of("quotations", ColumnClass.CONTENT, ColumnClass.ORIGIN)
    covered_by_patch = {
        "buyer_partner_id", "dest_market_code", "currency", "doc_date", "valid_until", "fx_rate",
        "fx_rate_date", "buyer_name", "buyer_address", "payment_type", "advance_pct_bp",
        "balance_anchor", "balance_days", "incoterm_code", "incoterm_place", "incoterm_year",
    }  # fmt: skip
    #: PATCH로 입력할 수 없는 열 — 서버 파생(합계)·ORIGIN(생성 시 1회, 요청 스키마에 없음).
    not_api_editable = {"total_amount", "copied_from_id"}
    assert content == covered_by_patch | not_api_editable
    from app.modules.quotations.schemas import QuotationUpdateRequest

    assert "total_amount" not in QuotationUpdateRequest.model_fields
    assert "copied_from_id" not in QuotationUpdateRequest.model_fields
    assert set(_frozen_patches(0)) <= set(QuotationUpdateRequest.model_fields)


def test_frozen_quotation_rejects_line_changes(trade: TestClient) -> None:
    """발행 후 라인 추가·수정·제외는 전부 409 FROZEN — 라인 id·값 불변"""
    qt, _b, _s, _o = _frozen_quotation(trade)
    line = qt["lines"][0]
    other = create_priced_sku()
    responses = [
        _add_line(trade, qt, sku_id=other, quantity=1),
        trade.patch(
            f"{QT}/{qt['id']}/lines/{line['id']}", json={"version": qt["version"], "quantity": 99}
        ),
        trade.delete(f"{QT}/{qt['id']}/lines/{line['id']}", params={"version": qt["version"]}),
    ]
    assert [r.status_code for r in responses] == [409, 409, 409]
    assert {r.json()["error"]["code"] for r in responses} == {"TRADE_DOCS.DOCUMENT.FROZEN"}
    after = trade.get(f"{QT}/{qt['id']}").json()
    assert after["lines"] == qt["lines"] and after["version"] == qt["version"]


def test_free_columns_stay_editable_after_freeze_and_do_not_touch_content(
    trade: TestClient,
) -> None:
    """동결 후에도 내부 메모·담당자는 /meta로 수정 가능(성공 방향) — version만 오르고 CONTENT는 그대로 · /meta에 가격 필드는 없다"""
    qt, *_ = _frozen_quotation(trade)
    new_owner = create_user(f"{unique('owner')}@example.com", roles=(RoleCode.TRADE,))
    meta = trade.patch(
        f"{QT}/{qt['id']}/meta",
        json={"version": qt["version"], "internal_note": "발행 후 메모", "assignee_id": new_owner},
    )
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["internal_note"] == "발행 후 메모" and body["assignee_id"] == new_owner
    assert body["version"] == qt["version"] + 1
    for key in (
        "payment_terms",
        "incoterm",
        "total_amount",
        "lines",
        "fx_rate",
        "valid_until",
        "frozen_at",
    ):
        assert body[key] == qt[key], key
    assert (
        trade.patch(
            f"{QT}/{qt['id']}/meta", json={"version": body["version"], "fx_rate": "1"}
        ).status_code
        == 422
    )
    same_content = trade.patch(
        f"{QT}/{qt['id']}",
        json={"version": body["version"], "internal_note": "PATCH 경로도 FREE는 허용"},
    )
    assert same_content.status_code == 200
    inactive = trade.patch(
        f"{QT}/{qt['id']}/meta",
        json={"version": same_content.json()["version"], "assignee_id": 999999},
    )
    assert inactive.status_code == 422


def test_master_changes_never_reach_a_document_that_already_snapshotted(trade: TestClient) -> None:
    """마스터(판가·SKU명·바이어 영문명)를 바꿔도 발행된 견적 값은 불변 — 초안 라인도 자동 갱신되지 않는다(스냅샷=값 복사)"""
    from app.modules.catalog.models import Sku
    from app.modules.partners.models import Partner

    buyer = create_buyer(name_en="Snapshot Corp")
    sku = create_priced_sku(amount=3000)
    draft = create_quotation_via_api(trade, buyer, [sku])
    with unit_of_work() as uow:
        uow.session.get(Sku, sku).name_ko = "개명된 상품"  # type: ignore[union-attr]
        uow.session.get(Partner, buyer).name_en = "Renamed Corp"  # type: ignore[union-attr]
    set_price(sku, 9999, effective_from=today_kst())
    still = trade.get(f"{QT}/{draft['id']}").json()
    assert still["lines"][0]["unit_price_amount"] == 3000 and still["buyer_name"] == "Snapshot Corp"
    issued = issue_via_api(trade, draft)
    assert issued["lines"] == draft["lines"] and issued["total_amount"] == draft["total_amount"]
    reread = trade.get(f"{QT}/{draft['id']}").json()
    assert reread["lines"][0]["unit_price_amount"] == 3000
    assert (
        reread["lines"][0]["sku_name_ko"] == "테스트 SKU"
        and reread["buyer_name"] == "Snapshot Corp"
    )


def test_status_and_frozen_transitions_are_not_reachable_through_patch_or_transitions(
    trade: TestClient,
) -> None:
    """상태 PATCH는 스키마에 없다(422) · /transitions의 to에 발행·자동 상태는 못 온다(422) — 우회 표면 제거"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    assert (
        trade.patch(
            f"{QT}/{qt['id']}", json={"version": qt["version"], "status": "ISSUED"}
        ).status_code
        == 422
    )
    for target in ("ISSUED", "CONVERTED", "EXPIRED", "DRAFT"):
        response = trade.post(
            f"{QT}/{qt['id']}/transitions",
            json={"to": target, "version": qt["version"]},
            headers=idem(),
        )
        assert response.status_code == 422, target
    assert trade.get(f"{QT}/{qt['id']}").json()["status"] == "DRAFT"


def test_the_database_refuses_to_send_a_frozen_quotation_back_to_draft(trade: TestClient) -> None:
    """서비스를 우회한 SQL도 발행본을 DRAFT로 되돌릴 수 없다(frozen_matches_status CHECK — 23514)"""
    qt, *_ = _frozen_quotation(trade)
    with pytest.raises(Exception) as caught, engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET status = 'DRAFT' WHERE id = :i"), {"i": qt["id"]}
        )
    assert "frozen_matches_status" in str(caught.value)


def test_handover_moves_frozen_quotations_too_without_touching_their_content(
    trade: TestClient,
) -> None:
    """담당 일괄 이관은 동결(발행) 전표의 담당자도 넘긴다 — assignee_id는 FREE라 통과하고 나머지 값·상태는 불변(ADR-0015·0053)"""
    from app.modules.handover.service import reassign_all
    from tests.factories.trade import user_id_of

    buyer = create_buyer()
    sku = create_priced_sku(amount=700)
    draft = create_quotation_via_api(trade, buyer, [sku])
    frozen = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
    old_owner = frozen["assignee_id"]
    admin_email = f"{unique('handover')}@example.com"
    admin_id = create_user(admin_email, roles=(RoleCode.ADMIN,))
    new_owner = create_user(f"{unique('new-owner')}@example.com", roles=(RoleCode.TRADE,))
    result = reassign_all(from_user_id=old_owner, to_user_id=new_owner, actor_user_id=admin_id)
    assert result.moved["quotations"] == 2
    for qt in (draft, frozen):
        after = trade.get(f"{QT}/{qt['id']}").json()
        assert after["assignee_id"] == new_owner
        assert after["status"] == qt["status"] and after["total_amount"] == qt["total_amount"]
        assert after["lines"] == qt["lines"] and after["fx_rate"] == qt["fx_rate"]
    assert user_id_of(admin_email) == admin_id


# ── 조회·CSV·이력 ───────────────────────────────────────────────────────────


def test_list_filters_pagination_and_read_access_for_all_roles(trade: TestClient) -> None:
    """목록: 페이지 봉투(기본 50)·필터(상태·바이어·검색·기간)·VIEWER 조회 허용(원가·마진 필드 없음)"""
    b1, b2 = create_buyer(name_en="Alpha Corp"), create_buyer(name_en="Bravo Ltd")
    for buyer in (b1, b1, b2):
        create_quotation_via_api(trade, buyer)
    body = trade.get(QT).json()
    assert body["total"] == 3 and body["size"] == 50 and len(body["items"]) == 3
    assert body["items"][0]["id"] > body["items"][-1]["id"]  # 최신순
    assert trade.get(QT, params={"buyer_partner_id": b2}).json()["total"] == 1
    assert trade.get(QT, params={"q": "alpha"}).json()["total"] == 2
    assert trade.get(QT, params={"q": "%"}).json()["total"] == 0  # LIKE 와일드카드 이스케이프
    assert trade.get(QT, params={"status": "ISSUED"}).json()["total"] == 0
    assert (
        trade.get(QT, params={"date_from": (today_kst() + timedelta(days=1)).isoformat()}).json()[
            "total"
        ]
        == 0
    )
    assert trade.get(QT, params={"size": 2}).json()["items"].__len__() == 2
    assert trade.get(QT, params={"size": 201}).status_code == 422
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get(QT).status_code == 200
        first = body["items"][0]["id"]
        detail = viewer.get(f"{QT}/{first}")
        assert detail.status_code == 200
        assert not any(k in detail.text for k in ("cost", "margin", "purchase"))
    assert trade.get(f"{QT}/999999").status_code == 404


def test_csv_export_has_bom_header_rows_and_escapes_formulas(trade: TestClient) -> None:
    """CSV: UTF-8 BOM·한글 헤더·행 값 일치·수식 시작 셀은 `'` 이스케이프 · 필터 적용"""
    buyer = create_buyer(name_en="=SUM(A1)")
    sku = create_priced_sku(amount=1250)
    qt = create_quotation_via_api(trade, buyer, [sku])
    response = trade.get(f"{QT}/export.csv")
    assert response.status_code == 200
    assert response.content.startswith("﻿견적번호".encode())
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].lstrip("﻿").split(",")[:3] == ["견적번호", "증빙일", "상태"]
    assert qt["doc_number"] in lines[1] and "'=SUM(A1)" in lines[1] and "125.00" in lines[1]
    assert (
        len(trade.get(f"{QT}/export.csv", params={"status": "ISSUED"}).text.strip().splitlines())
        == 1
    )


def test_status_log_is_paged_newest_first_and_records_every_transition(trade: TestClient) -> None:
    """상태이력 조회: 최신순·페이지·행위자 이름·자동 표식 — 생성 1 + 전이당 1"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    issued = issue_via_api(trade, qt)
    cancelled = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": issued["version"], "reason": "바이어 요청"},
        headers=idem(),
    )
    assert cancelled.status_code == 200
    log = trade.get(f"{QT}/{qt['id']}/status-log").json()
    assert log["total"] == 3
    assert [(r["from_status"], r["to_status"]) for r in log["items"]] == [
        ("ISSUED", "CANCELLED"),
        ("DRAFT", "ISSUED"),
        (None, "DRAFT"),
    ]
    assert log["items"][0]["reason"] == "바이어 요청" and log["items"][0]["automatic"] is False
    assert log["items"][0]["actor_name"]
    assert (
        trade.get(f"{QT}/{qt['id']}/status-log", params={"size": 1}).json()["items"].__len__() == 1
    )
    events = _rows(
        "SELECT event_type, payload FROM events WHERE event_type LIKE 'quotations.%' ORDER BY id"
    )
    assert [e[0] for e in events] == [
        "quotations.quotation.created",
        "quotations.quotation.status_changed",
        "quotations.quotation.status_changed",
    ]
    for _kind, payload in events:
        assert set(payload) <= set(PAYLOAD_KEYS)
        assert "바이어 요청" not in str(payload)  # 사유 원문·금액은 이벤트에 싣지 않는다


def test_a_line_edit_that_leaves_the_total_unchanged_still_bumps_the_header_version(
    trade: TestClient,
) -> None:
    """금액이 안 바뀌는 라인 수정(사유 메모)도 헤더 version을 올린다 — 라인만 바뀌어도 부모 낙관 잠금 상승(S1-3 PR-3 결함 방지)"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer, [create_priced_sku(amount=500)])
    line = qt["lines"][0]
    response = trade.patch(
        f"{QT}/{qt['id']}/lines/{line['id']}",
        json={"version": qt["version"], "price_reason": "바이어 요청 메모"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == qt["total_amount"]
    assert response.json()["header_version"] == qt["version"] + 1
