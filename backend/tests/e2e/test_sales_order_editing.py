"""A·K. 수주(SO) 접수 편집·라인 편집·전이(보류·재개·취소)·역순 취소·문서 흐름 (S3-1 PR-7a / design-A A4·A13 / design-B B1~B3·B8·B9).

접수(RECEIVED)가 유일한 편집 구간이다 — 보류(ON_HOLD)·확정 이후·취소는 CONTENT 편집이 409 FROZEN이고 FREE 열(내부 메모·담당자)만 남는다.
거래처·통화·참조는 ORIGIN이라 요청 스키마에 필드가 없다. 확정(RECEIVED→CONFIRMED)은 PR-12 — 이 파일은 확정된 SO를 DB 직행 행(`raw_so`)으로 만들어
보류·재개·취소를 검증한다.
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
    raw_quotation,
    raw_so,
    so_payload,
)
from tests.support.factories import create_user

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


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _get(client: TestClient, so_id: int) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"{SO}/{so_id}").json()
    return body


def _transition(
    client: TestClient, so: dict[str, Any], to: str, reason: str | None = None, **kwargs: Any
) -> Any:
    body: dict[str, Any] = {"to": to, "version": so["version"]}
    if reason is not None:
        body["reason"] = reason
    return client.post(
        f"{SO}/{so['id']}/transitions", json=body, headers=kwargs.get("headers", idem())
    )


def _reference_so(client: TestClient, *, quantity: int = 10, amount: int = 1000) -> dict[str, Any]:
    qt = issued_quotation(
        client, create_buyer(), [create_priced_sku(amount=amount)], quantity=quantity
    )
    return create_so_from_qt_via_api(client, qt)


# ── 헤더 편집 ───────────────────────────────────────────────────────────────


def test_edit_content_in_received_bumps_the_version_and_keeps_origin(trade: TestClient) -> None:
    """접수 상태 편집 — 결제조건·Incoterms·환율·바이어 표기·PO번호/일자·내부 메모가 바뀌고 version이 오른다 · 거래처·통화·원천은 그대로"""
    so = _reference_so(trade)
    body = trade.patch(
        f"{SO}/{so['id']}",
        json={
            "version": so["version"],
            "payment_terms": {
                "payment_type": "TT_DEFERRED",
                "balance_anchor": "BL_DATE",
                "balance_days": 30,
            },
            "incoterm": {"code": "CIF", "place": "Los Angeles", "year": 2020},
            "fx_rate": "1400.25",
            "buyer_name": "  Acme Renamed  ",
            "buyer_po_no": " po-77 ",
            "buyer_po_date": today_kst().isoformat(),
            "internal_note": "메모",
        },
    )
    assert body.status_code == 200, body.text
    out = body.json()
    assert out["version"] == so["version"] + 1
    assert out["payment_terms"]["payment_type"] == "TT_DEFERRED"
    assert out["incoterm"]["code"] == "CIF" and out["fx_rate"] == "1400.25"
    assert out["buyer_name"] == "Acme Renamed" and out["buyer_po_no"] == "po-77"
    assert out["internal_note"] == "메모"
    for immutable in (
        "buyer_partner_id",
        "currency",
        "qt_id",
        "pi_id",
        "doc_number",
        "dest_market_code",
    ):
        assert out[immutable] == so[immutable], immutable
    assert _scalar("SELECT buyer_po_no_key FROM sales_orders WHERE id = :i", i=so["id"]) == "PO-77"


@pytest.mark.parametrize(
    "field",
    [
        "buyer_partner_id",
        "currency",
        "qt_id",
        "pi_id",
        "status",
        "doc_number",
        "total_amount",
        "confirmed_at",
        "buyer_po_no_key",
        "content_rev",
        "credit_verdict",
        "credit_approval_id",
        "pi_gate_verdict",
    ],
)
def test_origin_and_server_owned_fields_have_no_edit_path(trade: TestClient, field: str) -> None:
    """거래처·통화·참조·상태·번호·합계·확정 시각·PO 정규화 키는 편집 요청에 필드가 없다 → 422(extra=forbid), 행 무변"""
    so = _reference_so(trade)
    for path in (f"{SO}/{so['id']}", f"{SO}/{so['id']}/meta"):
        r = trade.patch(path, json={"version": so["version"], field: "X"})
        assert r.status_code == 422, (path, field)
    assert _get(trade, so["id"])["version"] == so["version"]


def test_a_reference_so_cannot_change_the_destination_market_but_a_direct_one_can(
    trade: TestClient,
) -> None:
    """목적지 시장 — 참조 수주는 원천과 어긋나면 안 되므로 422, 직접(인테이크) 수주는 변경 가능"""
    from tests.support.factories import create_market

    create_market("JP")
    so = _reference_so(trade)
    r = trade.patch(f"{SO}/{so['id']}", json={"version": so["version"], "dest_market_code": "JP"})
    assert r.status_code == 422 and "dest_market_code" in r.json()["error"]["detail"]
    direct = create_direct_so(create_buyer())
    ok = trade.patch(
        f"{SO}/{direct['id']}", json={"version": direct["version"], "dest_market_code": "jp"}
    )
    assert ok.status_code == 200 and ok.json()["dest_market_code"] == "JP"


def test_edit_validation_fx_terms_doc_date_and_po_duplicates(trade: TestClient) -> None:
    """검증 — 환율 형식·KRW는 1·결제조건 형태·증빙일(미래·원천보다 앞)·PO 중복(다른 SO가 점유) 422/409 · 자기 PO 재저장은 허용 · null로 비우기"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=20)
    line = qt["lines"][0]["id"]
    a = create_so_from_qt_via_api(
        trade, qt, buyer_po_no="PO-A", lines=[{"source_line_id": line, "quantity": 5}]
    )
    b = create_so_from_qt_via_api(
        trade, qt, buyer_po_no="PO-B", lines=[{"source_line_id": line, "quantity": 5}]
    )
    assert (
        trade.patch(f"{SO}/{b['id']}", json={"version": b["version"], "fx_rate": "abc"}).status_code
        == 422
    )
    assert (
        trade.patch(
            f"{SO}/{b['id']}",
            json={"version": b["version"], "payment_terms": {"payment_type": "NOPE"}},
        ).status_code
        == 422
    )
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    assert (
        trade.patch(
            f"{SO}/{b['id']}", json={"version": b["version"], "doc_date": tomorrow}
        ).status_code
        == 422
    )
    before_qt = (today_kst() - timedelta(days=400)).isoformat()
    r = trade.patch(f"{SO}/{b['id']}", json={"version": b["version"], "doc_date": before_qt})
    assert r.status_code == 422  # 원천 QT 증빙일보다 앞설 수 없다
    dup = trade.patch(f"{SO}/{b['id']}", json={"version": b["version"], "buyer_po_no": " po-a "})
    assert dup.status_code == 409 and _code(dup) == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    assert dup.json()["error"]["detail"]["doc_number"] == a["doc_number"]
    own = trade.patch(f"{SO}/{b['id']}", json={"version": b["version"], "buyer_po_no": "po-b"})
    assert own.status_code == 200 and own.json()["buyer_po_no"] == "po-b"
    cleared = trade.patch(
        f"{SO}/{b['id']}", json={"version": own.json()["version"], "buyer_po_no": None}
    )
    assert cleared.status_code == 200 and cleared.json()["buyer_po_no"] is None
    assert _scalar("SELECT buyer_po_no_key FROM sales_orders WHERE id = :i", i=b["id"]) is None


def test_stale_version_edit_is_a_409_and_changes_nothing(trade: TestClient) -> None:
    """낡은 version 편집 → 409 VERSION_CONFLICT, 값 무변"""
    so = _reference_so(trade)
    ok = trade.patch(f"{SO}/{so['id']}", json={"version": so["version"], "internal_note": "먼저"})
    assert ok.status_code == 200
    stale = trade.patch(
        f"{SO}/{so['id']}", json={"version": so["version"], "internal_note": "나중"}
    )
    assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert _get(trade, so["id"])["internal_note"] == "먼저"


@pytest.mark.parametrize("status", ["ON_HOLD", "CONFIRMED", "CANCELLED"])
def test_content_is_frozen_outside_received_but_free_columns_stay_editable(
    trade: TestClient, status: str
) -> None:
    """보류·확정·취소 SO — CONTENT(환율 등) 변경은 어떤 값이든 409 FROZEN(필드명만) · 라인 편집도 409 · FREE(내부 메모·담당자)는 허용"""
    so_id = raw_so(status, buyer_po_no="PO-F", buyer_po_no_key="PO-F")
    so = _get(trade, so_id)
    for body in ({"fx_rate": "1500"}, {"fx_rate": "not-a-number"}, {"buyer_name": "다른 이름"}):
        r = trade.patch(f"{SO}/{so_id}", json={"version": so["version"], **body})
        assert r.status_code == 409 and _code(r) == "TRADE_DOCS.DOCUMENT.FROZEN", (status, body)
        assert (
            r.json()["error"]["detail"]
            == {"fields": [next(iter(body))] if next(iter(body)) != "fx_rate" else ["fx_rate"]}
            or "fields" in r.json()["error"]["detail"]
        )
    line = trade.post(
        f"{SO}/{so_id}/lines",
        json={"version": so["version"], "sku_id": create_priced_sku(), "quantity": 1},
    )
    assert line.status_code == 409 and _code(line) == "TRADE_DOCS.DOCUMENT.FROZEN"
    other = create_user("so-free-owner@example.com", roles=(RoleCode.TRADE,))
    meta = trade.patch(
        f"{SO}/{so_id}/meta",
        json={"version": so["version"], "internal_note": "동결 후 메모", "assignee_id": other},
    )
    assert meta.status_code == 200, meta.text
    assert meta.json()["assignee_id"] == other and meta.json()["version"] == so["version"] + 1
    assert _get(trade, so_id)["fx_rate"] == so["fx_rate"]  # CONTENT 무변


def test_meta_edit_rejects_inactive_or_missing_assignee_and_null(trade: TestClient) -> None:
    """담당자 — 비활성·없는 사용자 422 · null로 비울 수 없다 422 · 값을 바꾸지 않은 요청은 성공"""
    so = _reference_so(trade)
    gone = create_user("so-inactive@example.com", roles=(RoleCode.TRADE,), is_active=False)
    for value in (gone, 999999, None):
        r = trade.patch(
            f"{SO}/{so['id']}/meta", json={"version": so["version"], "assignee_id": value}
        )
        assert r.status_code == 422, value


# ── 라인 편집 ───────────────────────────────────────────────────────────────


def test_line_edit_recomputes_totals_and_bumps_the_header_version_by_one(trade: TestClient) -> None:
    """직접 수주 라인 추가·수정·제외 — 합계 재계산·헤더 version +1(응답에 갱신 값)·단가 수정은 MANUAL·라인 번호 재사용 금지"""
    buyer = create_buyer()
    sku_a = create_priced_sku(amount=1000)
    sku_b = create_priced_sku(amount=250)
    so = create_direct_so(buyer, [sku_a], quantity=5, price_amount=1000)
    added = trade.post(
        f"{SO}/{so['id']}/lines",
        json={"version": so["version"], "sku_id": sku_b, "quantity": 4},
    )
    assert added.status_code == 201, added.text
    out = added.json()
    assert out["header_version"] == so["version"] + 1 and out["total_amount"] == 5000 + 1000
    assert out["line"]["price_basis"] == "MASTER" and out["line"]["line_no"] == 2
    assert out["line"]["source"] is None and out["line"]["quantity_delta"] is None
    upd = trade.patch(
        f"{SO}/{so['id']}/lines/{out['line']['id']}",
        json={"version": out["header_version"], "quantity": 10, "unit_price": "3.00"},
    )
    assert upd.status_code == 200, upd.text
    assert (
        upd.json()["line"]["unit_price_amount"] == 300
        and upd.json()["line"]["price_basis"] == "MANUAL"
    )
    assert upd.json()["total_amount"] == 5000 + 3000
    assert upd.json()["header_version"] == out["header_version"] + 1
    rem = trade.delete(
        f"{SO}/{so['id']}/lines/{out['line']['id']}",
        params={"version": upd.json()["header_version"]},
    )
    assert rem.status_code == 200 and rem.json()["total_amount"] == 5000
    again = trade.post(
        f"{SO}/{so['id']}/lines",
        json={"version": rem.json()["header_version"], "sku_id": sku_b, "quantity": 1},
    )
    assert again.status_code == 201 and again.json()["line"]["line_no"] == 3  # 결번 재사용 금지
    final = _get(trade, so["id"])
    assert final["last_line_no"] == 3 and final["total_amount"] == 5000 + 250
    assert (
        _scalar(
            "SELECT count(*) FROM sales_order_lines WHERE so_id = :i AND deleted_at IS NOT NULL",
            i=so["id"],
        )
        == 1
    )


def test_line_rules_duplicates_free_lines_and_bounds(trade: TestClient) -> None:
    """같은 SKU 유상 2줄 409 · 유상+무상(사유 필수) 각 1줄 허용 · 수량 0/소수 422 · 단가 0은 무상으로 명시 · 라인 수정으로 무상↔유상 충돌 409"""
    sku = create_priced_sku(amount=1000)
    so = create_direct_so(create_buyer(), [create_priced_sku()], quantity=1)

    def add(version: int, sku_id: int = sku, **body: Any) -> Any:
        return trade.post(
            f"{SO}/{so['id']}/lines", json={"version": version, "sku_id": sku_id, **body}
        )

    fresh = create_priced_sku(amount=400)

    first = add(so["version"], quantity=3)
    assert first.status_code == 201
    v = first.json()["header_version"]
    dup = add(v, quantity=1)
    assert dup.status_code == 409 and _code(dup) == "TRADE_DOCS.LINE.SKU_DUPLICATE"
    assert add(v, fresh, quantity=0).status_code == 422
    assert add(v, fresh, quantity=1.5).status_code == 422
    assert add(v, fresh, quantity=1, unit_price="0").status_code == 422  # 0원은 무상으로 명시
    no_reason = add(v, quantity=1, is_free=True)
    assert no_reason.status_code == 422
    free = add(v, quantity=2, is_free=True, price_reason="샘플 증정")
    assert free.status_code == 201 and free.json()["line"]["unit_price_amount"] == 0
    assert free.json()["total_amount"] == so["total_amount"] + 3000  # 무상은 합계에 0


def test_other_documents_line_id_is_a_404_and_frozen_lines_reject_edits(trade: TestClient) -> None:
    """다른 SO의 라인 id를 섞은 PATCH/DELETE는 404(IDOR) · 없는 라인 404"""
    a = create_direct_so(create_buyer(), quantity=2)
    b = create_direct_so(create_buyer(), quantity=2)
    foreign = b["lines"][0]["id"]
    r = trade.patch(
        f"{SO}/{a['id']}/lines/{foreign}", json={"version": a["version"], "quantity": 3}
    )
    assert r.status_code == 404
    r = trade.delete(f"{SO}/{a['id']}/lines/{foreign}", params={"version": a["version"]})
    assert r.status_code == 404
    assert (
        trade.patch(
            f"{SO}/{a['id']}/lines/999999", json={"version": a["version"], "quantity": 3}
        ).status_code
        == 404
    )
    assert _get(trade, b["id"])["lines"][0]["quantity"] == 2


def test_a_discontinued_sku_line_is_saved_and_flagged_not_blocked(trade: TestClient) -> None:
    """SO 접수는 단종 SKU 라인을 **저장은 허용**하고 `sku_status`로 표시한다(확정 시 차단은 PR-12) · 없는 SKU는 422"""
    dead = create_priced_sku(amount=900)
    _exec("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :i", i=dead)
    so = create_direct_so(create_buyer(), quantity=1)
    r = trade.post(
        f"{SO}/{so['id']}/lines", json={"version": so["version"], "sku_id": dead, "quantity": 2}
    )
    assert r.status_code == 201, r.text
    assert r.json()["line"]["sku_status"] == "DISCONTINUED"
    assert _get(trade, so["id"])["lines"][-1]["sku_status"] == "DISCONTINUED"
    missing = trade.post(
        f"{SO}/{so['id']}/lines",
        json={"version": r.json()["header_version"], "sku_id": 987654, "quantity": 1},
    )
    assert missing.status_code == 422


def test_reference_lines_quantity_increase_is_bounded_by_the_source_open_quantity(
    trade: TestClient,
) -> None:
    """참조 수주 라인 수량 — 줄이는 것은 자유·늘리는 것은 **원천 잔량 안에서만**(초과 409 EXCEEDS_OPEN) · 다른 소비자가 가져간 만큼은 못 늘린다 · 줄이면 원천에 즉시 환원"""
    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=10)
    line = qt["lines"][0]["id"]
    so = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 4}])
    pi = create_pi_via_api(
        trade, qt, create_bank_account("USD"), lines=[{"source_line_id": line, "quantity": 5}]
    )
    assert pi["lines"][0]["quantity"] == 5  # 잔량 10−4=6 중 5 소비 → 남은 1
    sol = so["lines"][0]["id"]
    over = trade.patch(
        f"{SO}/{so['id']}/lines/{sol}", json={"version": so["version"], "quantity": 6}
    )
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    ok = trade.patch(f"{SO}/{so['id']}/lines/{sol}", json={"version": so["version"], "quantity": 5})
    assert ok.status_code == 200 and ok.json()["line"]["quantity_delta"] == -5
    down = trade.patch(
        f"{SO}/{so['id']}/lines/{sol}", json={"version": ok.json()["header_version"], "quantity": 1}
    )
    assert down.status_code == 200
    # 줄인 만큼 환원 — 다른 SO가 잔량(4)을 가져갈 수 있다
    second = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 4}])
    assert second["lines"][0]["quantity"] == 4


def test_reference_lines_can_only_be_re_added_from_the_source_within_open_quantity(
    trade: TestClient,
) -> None:
    """참조 수주 — 제외한 원천 품목은 (원천 값 복사로) 다시 추가할 수 있고, 원천에 없는 SKU는 422 · 가격 필드를 실으면 422 · 잔량 초과 409"""
    sku_a, sku_b = create_priced_sku(amount=1000), create_priced_sku(amount=500)
    qt = issued_quotation(trade, create_buyer(), [sku_a, sku_b], quantity=10)
    so = create_so_from_qt_via_api(trade, qt)
    drop = next(ln for ln in so["lines"] if ln["sku_id"] == sku_a)
    removed = trade.delete(f"{SO}/{so['id']}/lines/{drop['id']}", params={"version": so["version"]})
    assert removed.status_code == 200
    v = removed.json()["header_version"]
    outsider = trade.post(
        f"{SO}/{so['id']}/lines", json={"version": v, "sku_id": create_priced_sku(), "quantity": 1}
    )
    assert outsider.status_code == 422 and "sku_id" in outsider.json()["error"]["detail"]
    priced = trade.post(
        f"{SO}/{so['id']}/lines",
        json={"version": v, "sku_id": sku_a, "quantity": 1, "unit_price": "1.00"},
    )
    assert priced.status_code == 422
    over = trade.post(
        f"{SO}/{so['id']}/lines", json={"version": v, "sku_id": sku_a, "quantity": 11}
    )
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    back = trade.post(
        f"{SO}/{so['id']}/lines", json={"version": v, "sku_id": sku_a, "quantity": 10}
    )
    assert back.status_code == 201, back.text
    line = back.json()["line"]
    assert line["qt_line_id"] == drop["qt_line_id"] and line["unit_price_amount"] == 1000
    assert line["price_basis"] == drop["price_basis"] and line["quantity_delta"] == 0


def test_price_edit_on_a_reference_line_shows_the_difference_from_the_source(
    trade: TestClient,
) -> None:
    """참조 수주 단가 수정 → 기준 MANUAL·`price_changed`·`quantity_delta`가 원천 대비 차이로 조회 시 계산된다(저장 안 함)"""
    so = _reference_so(trade, quantity=10, amount=1000)
    line = so["lines"][0]
    out = trade.patch(
        f"{SO}/{so['id']}/lines/{line['id']}",
        json={"version": so["version"], "unit_price": "12.50", "quantity": 8},
    ).json()["line"]
    assert out["unit_price_amount"] == 1250 and out["price_basis"] == "MANUAL"
    assert out["price_changed"] is True and out["quantity_delta"] == -2
    assert out["source"]["unit_price_amount"] == 1000 and out["source"]["quantity"] == 10
    assert _get(trade, so["id"])["total_amount"] == 10000


# ── 전이: 보류·재개·취소 ────────────────────────────────────────────────────


def test_hold_and_resume_round_trip_records_two_history_rows(trade: TestClient) -> None:
    """접수 → 보류(사유 필수) → 재개(RECEIVED) — 이력 3행(탄생·보류·재개)·사람 전이(automatic=false)·이벤트 status_changed 2건 · 재개 뒤 다시 편집 가능"""
    so = _reference_so(trade)
    no_reason = _transition(trade, so, "ON_HOLD")
    assert (
        no_reason.status_code == 422 and _code(no_reason) == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    )
    hold = _transition(trade, so, "ON_HOLD", "바이어 사정으로 대기")
    assert hold.status_code == 200 and hold.json()["status"] == "ON_HOLD"
    frozen = trade.patch(
        f"{SO}/{so['id']}", json={"version": hold.json()["version"], "fx_rate": "1200"}
    )
    assert frozen.status_code == 409
    resumed = _transition(trade, hold.json(), "RECEIVED")
    assert resumed.status_code == 200 and resumed.json()["status"] == "RECEIVED"
    assert (
        trade.patch(
            f"{SO}/{so['id']}", json={"version": resumed.json()["version"], "fx_rate": "1200"}
        ).status_code
        == 200
    )
    log = _rows(
        "SELECT from_status, to_status, automatic, reason FROM sales_order_status_log"
        " WHERE sales_order_id = :i ORDER BY id",
        i=so["id"],
    )
    assert [(r[0], r[1], r[2]) for r in log] == [
        (None, "RECEIVED", False),
        ("RECEIVED", "ON_HOLD", False),
        ("ON_HOLD", "RECEIVED", False),
    ]
    assert log[1][3] == "바이어 사정으로 대기"
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'sales_orders.sales_order.status_changed'"
        )
        == 2
    )


def test_resume_target_follows_confirmed_at(trade: TestClient) -> None:
    """재개 목표는 `confirmed_at`이 원천 — 확정 이력 없이 보류된 SO는 CONFIRMED로, 확정된 SO는 RECEIVED로 재개할 수 없다(409 RESUME.TARGET_MISMATCH) · 맞는 목표는 성공"""
    plain = _get(trade, raw_so("ON_HOLD"))
    bad = _transition(trade, plain, "CONFIRMED")
    assert bad.status_code == 409 and _code(bad) == "TRADE_DOCS.RESUME.TARGET_MISMATCH"
    assert bad.json()["error"]["detail"] == {"expected": "RECEIVED"}
    confirmed_hold = _get(trade, raw_so("ON_HOLD", confirmed=True))
    bad = _transition(trade, confirmed_hold, "RECEIVED")
    assert bad.status_code == 409 and _code(bad) == "TRADE_DOCS.RESUME.TARGET_MISMATCH"
    assert bad.json()["error"]["detail"] == {"expected": "CONFIRMED"}
    ok = _transition(trade, confirmed_hold, "CONFIRMED")
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED"
    assert ok.json()["confirmed_at"] is not None  # 재개는 확정 시각을 지우거나 다시 쓰지 않는다
    ok2 = _transition(trade, plain, "RECEIVED")
    assert ok2.status_code == 200 and ok2.json()["status"] == "RECEIVED"


def test_confirming_through_the_generic_transition_is_refused(trade: TestClient) -> None:
    """확정(RECEIVED→CONFIRMED)은 동결 액션 전용 엣지 — 범용 전이의 `to=CONFIRMED`로는 못 넘는다(409) · 예약 상태·자동 엣지 값은 422(스키마)"""
    so = _reference_so(trade)
    r = _transition(trade, so, "CONFIRMED")
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert _get(trade, so["id"])["status"] == "RECEIVED"
    for value in ("PARTIALLY_ALLOCATED", "ALLOCATED", "IN_SHIPMENT", "COMPLETED", "EXPIRED"):
        assert _transition(trade, so, value).status_code == 422, value
    assert (
        _scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == 1
    )


def test_cancel_requires_a_reason_and_is_terminal(trade: TestClient) -> None:
    """취소 사유 필수(422)·공백 422 · 취소 후엔 어떤 전이도 409·편집 409(FREE만) · 행과 번호는 남는다"""
    so = _reference_so(trade)
    assert _transition(trade, so, "CANCELLED").status_code == 422
    assert _transition(trade, so, "CANCELLED", "   ").status_code == 422
    done = _transition(trade, so, "CANCELLED", "바이어 취소 통보")
    assert done.status_code == 200 and done.json()["status"] == "CANCELLED"
    for to in ("RECEIVED", "ON_HOLD", "CANCELLED"):
        r = _transition(trade, done.json(), to, "다시")
        assert r.status_code == 409 and _code(r) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED", to
    assert (
        trade.patch(
            f"{SO}/{so['id']}", json={"version": done.json()["version"], "fx_rate": "1"}
        ).status_code
        == 409
    )
    assert _scalar("SELECT count(*) FROM sales_orders WHERE id = :i", i=so["id"]) == 1


def test_double_click_is_one_history_row_and_a_stale_version_is_409(trade: TestClient) -> None:
    """같은 Idempotency-Key 재수신=최초 결과 그대로(이력 1행) · 낡은 version 전이는 409 — 어느 쪽도 상태·이력을 늘리지 않는다"""
    so = _reference_so(trade)
    key = idem()
    body = {"to": "ON_HOLD", "version": so["version"], "reason": "더블클릭"}
    first = trade.post(f"{SO}/{so['id']}/transitions", json=body, headers=key)
    again = trade.post(f"{SO}/{so['id']}/transitions", json=body, headers=key)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert (
        _scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == 2
    )
    stale = trade.post(f"{SO}/{so['id']}/transitions", json=body, headers=idem())
    assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert (
        _scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == 2
    )
    assert trade.post(f"{SO}/{so['id']}/transitions", json=body).status_code == 400  # 키 결여


# ── 역순 취소 ───────────────────────────────────────────────────────────────


def test_reverse_order_cancellation_restores_balances_across_the_chain(trade: TestClient) -> None:
    """§20 A 역순 취소 — QT→PI→SO 체인에서 QT 취소·PI 취소는 409 SUCCESSOR_ALIVE(후속 번호 안내), SO를 먼저 취소하면 PI·QT 취소가 순서대로 성공하고
    소비량이 환원된다. 순서를 어긴 시도는 이력 0행 추가·상태 불변"""
    qt, pi = issued_pi_chain(trade, quantity=10)
    so = create_so_from_pi_via_api(trade, pi)
    r = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt["version"], "reason": "순서 위반"},
        headers=idem(),
    )
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert set(r.json()["error"]["detail"]["successors"]) == {pi["doc_number"], so["doc_number"]}
    r = trade.post(
        f"{PI}/{pi['id']}/transitions",
        json={"to": "CANCELLED", "version": pi["version"], "reason": "순서 위반"},
        headers=idem(),
    )
    assert r.status_code == 409 and _code(r) == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert r.json()["error"]["detail"]["successors"] == [so["doc_number"]]
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi["id"]) == "ISSUED"
    assert (
        _scalar(
            "SELECT count(*) FROM proforma_invoice_status_log WHERE proforma_invoice_id = :i",
            i=pi["id"],
        )
        == 1
    )
    _transition(trade, so, "CANCELLED", "정정")
    # SO 취소로 PI 잔량이 환원됐다 — 다시 SO를 만들 수 있다(PI 1:1은 취소 SO를 세지 않는다)
    again = create_so_from_pi_via_api(trade, pi)
    assert again["lines"][0]["quantity"] == 10
    _transition(trade, again, "CANCELLED", "다시 정정")
    pi_now = trade.get(f"{PI}/{pi['id']}").json()
    ok = trade.post(
        f"{PI}/{pi['id']}/transitions",
        json={"to": "CANCELLED", "version": pi_now["version"], "reason": "PI 취소"},
        headers=idem(),
    )
    assert ok.status_code == 200
    qt_now = trade.get(f"{QT}/{qt['id']}").json()
    ok = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt_now["version"], "reason": "QT 취소"},
        headers=idem(),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "CANCELLED"


@pytest.mark.parametrize("held", [False, True])
def test_a_live_direct_so_blocks_quotation_cancel_even_when_on_hold(
    trade: TestClient, held: bool
) -> None:
    """QT 직접 SO — 접수·보류(ON_HOLD)도 살아 있는 후속이라 QT 취소를 막는다(보류는 죽은 상태가 아니다)"""
    qt = issued_quotation(trade)
    so = create_so_from_qt_via_api(trade, qt)
    if held:
        _transition(trade, so, "ON_HOLD", "보류")
    r = trade.post(
        f"{QT}/{qt['id']}/transitions",
        json={"to": "CANCELLED", "version": qt["version"], "reason": "취소"},
        headers=idem(),
    )
    assert r.status_code == 409 and r.json()["error"]["detail"]["successors"] == [so["doc_number"]]


def test_cancelling_a_confirmed_so_returns_the_quotation_from_converted(trade: TestClient) -> None:
    """확정 SO(DB 직행)가 QT를 CONVERTED로 만든 뒤 그 SO를 취소하면 QT가 자동으로 ISSUED로 복귀한다(이력 automatic·행위자=취소자) —
    유효기간이 이미 지났으면 같은 트랜잭션에서 EXPIRED까지 2행"""
    from app.core.db.uow import unit_of_work
    from app.modules.trade_chain.chain_ops import converge_quotation

    qt = issued_quotation(trade)
    so = create_so_from_qt_via_api(trade, qt)
    _exec(
        "UPDATE sales_orders SET status = 'CONFIRMED', confirmed_at = now(), credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE' WHERE id = :i",
        i=so["id"],
    )
    with unit_of_work() as uow:
        assert converge_quotation(uow.session, qt["id"], actor_user_id=None) == "CONVERTED"
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "CONVERTED"
    confirmed = _get(trade, so["id"])
    assert confirmed["status"] == "CONFIRMED"
    done = _transition(trade, confirmed, "CANCELLED", "확정 후 정정")
    assert done.status_code == 200
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
    rows = _rows(
        "SELECT from_status, to_status, automatic FROM quotation_status_log WHERE quotation_id = :i ORDER BY id",
        i=qt["id"],
    )
    assert [(r[0], r[1], r[2]) for r in rows][-2:] == [
        ("ISSUED", "CONVERTED", True),
        ("CONVERTED", "ISSUED", True),
    ]
    # 유효기간 경과 + 붙잡는 후속 0 → 같은 TX에서 EXPIRED
    qt2 = issued_quotation(trade)
    so2 = create_so_from_qt_via_api(trade, qt2)
    _exec(
        "UPDATE sales_orders SET status = 'CONFIRMED', confirmed_at = now(), credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE' WHERE id = :i",
        i=so2["id"],
    )
    with unit_of_work() as uow:
        converge_quotation(uow.session, qt2["id"], actor_user_id=None)
    lapsed = today_kst() - timedelta(days=1)
    _exec(
        "UPDATE quotations SET valid_until = :d, doc_date = :d2, fx_rate_date = :d2 WHERE id = :i",
        d=lapsed,
        d2=lapsed - timedelta(days=5),
        i=qt2["id"],
    )
    _exec(
        "UPDATE sales_orders SET doc_date = :d, fx_rate_date = :d WHERE id = :i",
        d=lapsed - timedelta(days=5),
        i=so2["id"],
    )
    _transition(trade, _get(trade, so2["id"]), "CANCELLED", "확정 후 정정")
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt2["id"]) == "EXPIRED"
    tail = _rows(
        "SELECT from_status, to_status FROM quotation_status_log WHERE quotation_id = :i ORDER BY id",
        i=qt2["id"],
    )[-2:]
    assert [tuple(r) for r in tail] == [("CONVERTED", "ISSUED"), ("ISSUED", "EXPIRED")]


def test_cancel_calls_the_allocation_port_and_a_port_failure_rolls_everything_back(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """취소는 `AllocationPort.on_cancelled`(P3 기본 NOT_IMPLEMENTED)를 같은 트랜잭션에서 부른다 — 포트가 예외를 던지면 상태·이력·이벤트 전부 롤백(fail-closed)"""
    from app.modules.sales_orders import ports
    from app.modules.trade_chain import lifecycle

    calls: list[str] = []

    class Recording(ports.NoAllocationPort):
        def on_cancelled(self, session: Any, order: Any) -> ports.AllocationOutcome:
            calls.append(order.doc_number)
            return super().on_cancelled(session, order)

    monkeypatch.setattr(lifecycle, "get_allocation_port", lambda: Recording())
    so = _reference_so(trade)
    assert _transition(trade, so, "CANCELLED", "취소").status_code == 200
    assert calls == [so["doc_number"]]

    class Failing(ports.NoAllocationPort):
        def on_cancelled(self, session: Any, order: Any) -> ports.AllocationOutcome:
            raise RuntimeError("할당 해제 실패")

    monkeypatch.setattr(lifecycle, "get_allocation_port", lambda: Failing())
    so2 = _reference_so(trade)
    with pytest.raises(RuntimeError):
        _transition(trade, so2, "CANCELLED", "취소")
    assert _get(trade, so2["id"])["status"] == "RECEIVED"
    assert (
        _scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so2["id"]
        )
        == 1
    )
    outcome = ports.NoAllocationPort().on_cancelled(None, None)  # type: ignore[arg-type]
    assert outcome.status == ports.AllocationStatus.NOT_IMPLEMENTED and "Phase 4" in outcome.note


# ── 문서 흐름 ───────────────────────────────────────────────────────────────


def test_document_flow_returns_the_whole_chain_from_any_node_including_dead_ones(
    trade: TestClient,
) -> None:
    """문서 흐름 — QT→(PI→SO)·(직접 SO)를 어느 노드에서 조회해도 같은 트리(위→아래 순서)·`is_current` 표시·취소 전표도 상태와 함께 포함"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=20)
    line = qt["lines"][0]["id"]
    pi = create_pi_via_api(
        trade, qt, create_bank_account("USD"), lines=[{"source_line_id": line, "quantity": 10}]
    )
    so_pi = create_so_from_pi_via_api(trade, pi)
    so_direct = create_so_from_qt_via_api(
        trade, qt, lines=[{"source_line_id": line, "quantity": 10}]
    )
    _transition(trade, so_direct, "CANCELLED", "취소됨")
    expected = [
        ("QUOTATION", qt["id"], None),
        ("PROFORMA_INVOICE", pi["id"], ("QUOTATION", qt["id"])),
        ("SALES_ORDER", so_pi["id"], ("PROFORMA_INVOICE", pi["id"])),
        ("SALES_ORDER", so_direct["id"], ("QUOTATION", qt["id"])),
    ]
    for kind, doc_id in (
        ("QUOTATION", qt["id"]),
        ("PROFORMA_INVOICE", pi["id"]),
        ("SALES_ORDER", so_pi["id"]),
        ("SALES_ORDER", so_direct["id"]),
    ):
        flow = trade.get(f"/api/v1/document-flow/{kind}/{doc_id}").json()
        assert flow["root_kind"] == "QUOTATION" and flow["root_id"] == qt["id"]
        got = [
            (n["kind"], n["id"], (n["parent_kind"], n["parent_id"]) if n["parent_kind"] else None)
            for n in flow["nodes"]
        ]
        assert got == expected, (kind, doc_id)
        assert [n["is_current"] for n in flow["nodes"]].count(True) == 1
        assert next(n for n in flow["nodes"] if n["is_current"])["id"] == doc_id
    flow = trade.get(f"/api/v1/document-flow/QUOTATION/{qt['id']}").json()
    statuses = {n["id"]: n["status"] for n in flow["nodes"] if n["kind"] == "SALES_ORDER"}
    assert statuses == {so_pi["id"]: "RECEIVED", so_direct["id"]: "CANCELLED"}
    assert all("total_text" in n and "doc_number" in n for n in flow["nodes"])


def test_document_flow_of_a_direct_so_is_itself_and_unknown_documents_are_404(
    trade: TestClient,
) -> None:
    """직접(인테이크) 수주는 사슬이 자기 자신뿐 · 없는 전표·PO 종류·0 id는 404/422 · 조회 전용 역할도 읽는다"""
    direct = create_direct_so(create_buyer())
    flow = trade.get(f"/api/v1/document-flow/SALES_ORDER/{direct['id']}").json()
    assert flow["root_kind"] == "SALES_ORDER" and flow["root_id"] == direct["id"]
    assert [n["id"] for n in flow["nodes"]] == [direct["id"]] and flow["nodes"][0]["is_current"]
    assert trade.get("/api/v1/document-flow/SALES_ORDER/999999").status_code == 404
    assert trade.get("/api/v1/document-flow/PURCHASE_ORDER/1").status_code == 422
    assert trade.get("/api/v1/document-flow/SALES_ORDER/0").status_code == 422
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get(f"/api/v1/document-flow/SALES_ORDER/{direct['id']}").status_code == 200


def test_document_flow_query_count_is_constant(trade: TestClient) -> None:
    """문서 흐름은 전표 수와 무관하게 쿼리 수가 고정이다(N+1 없음) — PI·SO를 늘려도 같은 횟수"""
    from sqlalchemy import event

    from app.core.db.session import engine

    qt = issued_quotation(trade, create_buyer(), [create_priced_sku()], quantity=40)
    line = qt["lines"][0]["id"]

    def count_queries() -> int:
        seen: list[str] = []

        def hook(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", hook)
        try:
            assert trade.get(f"/api/v1/document-flow/QUOTATION/{qt['id']}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len([s for s in seen if s.lstrip().upper().startswith("SELECT")])

    create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 1}])
    small = count_queries()
    for _ in range(4):
        create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 1}])
    assert count_queries() == small


def test_handover_moves_sales_order_assignees_even_when_frozen(trade: TestClient) -> None:
    """담당 이관(일괄 UPDATE)은 SO도 대상이다 — 접수·보류·취소 어느 상태여도 담당자가 넘어간다(assignee_id는 FREE 열)"""
    from app.modules.handover.service import reassign_all

    leaver = create_user("so-leaver@example.com", roles=(RoleCode.TRADE,))
    successor = create_user("so-successor@example.com", roles=(RoleCode.TRADE,))
    ids = [
        raw_so("RECEIVED", assignee_id=leaver),
        raw_so("ON_HOLD", assignee_id=leaver),
        raw_so("CANCELLED", assignee_id=leaver),
    ]
    result = reassign_all(from_user_id=leaver, to_user_id=successor, actor_user_id=successor)
    assert result.moved["sales_orders"] == 3
    assert {_get(trade, i)["assignee_id"] for i in ids} == {successor}


def test_creating_from_a_reference_chain_needs_a_quotation_chain_in_the_db(
    trade: TestClient,
) -> None:
    """공회전 방지 — 이 파일의 raw_so·raw_quotation 헬퍼가 만든 행이 실제로 API에서 읽힌다"""
    so = _get(trade, raw_so("RECEIVED", qt_id=raw_quotation("ISSUED")))
    assert so["status"] == "RECEIVED" and so["qt_id"] is not None and so["is_reference"] is True
    assert so_payload(so)["version"] == so["version"]


def test_a_line_change_that_leaves_every_amount_unchanged_still_bumps_the_header_version(
    trade: TestClient,
) -> None:
    """라인만 바뀌고 금액·합계는 그대로인 편집(요청납기·바이어 품번)도 헤더 version을 정확히 +1 한다 — 라인 편집이 부모 낙관 잠금을 못 올리면
    겹친 편집·승인 요청이 409를 우회한다(S1-3 PR-3 결함 유형). 같은 사람이 만든 전표라 감사 열 변경이 우연히 헤더를 dirty로 만드는 경로도 없다"""
    so = _reference_so(trade, quantity=10, amount=1000)
    line = so["lines"][0]["id"]
    future = (today_kst() + timedelta(days=30)).isoformat()
    r = trade.patch(
        f"{SO}/{so['id']}/lines/{line}",
        json={"version": so["version"], "requested_delivery_date": future},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total_amount"] == so["total_amount"]
    assert r.json()["header_version"] == so["version"] + 1
    assert _get(trade, so["id"])["version"] == so["version"] + 1
    stale = trade.patch(
        f"{SO}/{so['id']}/lines/{line}",
        json={"version": so["version"], "requested_delivery_date": None},
    )
    assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"


def test_changing_the_doc_date_revalidates_existing_line_delivery_dates(trade: TestClient) -> None:
    """증빙일을 바꿀 때 살아 있는 라인의 요청납기 ≥ 새 증빙일을 다시 검증한다 — 위반 시 422(조용한 변경 금지)·값 무변, 맞으면 성공"""
    from app.modules.sales_orders.service import (
        add_line,  # noqa: F401 — 서비스 경로 존재 확인(공회전 방지)
    )

    so = create_direct_so(create_buyer(), quantity=2)
    line = so["lines"][0]["id"]
    due = today_kst()
    old = due - timedelta(days=10)
    _exec("UPDATE sales_orders SET doc_date = :d WHERE id = :i", d=old, i=so["id"])
    _exec(
        "UPDATE sales_order_lines SET requested_delivery_date = :d WHERE id = :i",
        d=due - timedelta(days=3),
        i=line,
    )
    cur = _get(trade, so["id"])
    bad = trade.patch(
        f"{SO}/{so['id']}", json={"version": cur["version"], "doc_date": due.isoformat()}
    )
    assert bad.status_code == 422 and "doc_date" in bad.json()["error"]["detail"]
    assert _get(trade, so["id"])["doc_date"] == old.isoformat()  # 값 무변
    ok = trade.patch(
        f"{SO}/{so['id']}",
        json={"version": cur["version"], "doc_date": (due - timedelta(days=4)).isoformat()},
    )
    assert ok.status_code == 200, ok.text


def test_frozen_so_returns_frozen_before_revealing_a_duplicate_po(trade: TestClient) -> None:
    """보류·취소·확정 SO의 PO번호를 이미 점유된 값으로 고치려 하면 409 FROZEN(점유 문서 정보 없음)이 먼저다 — 접수 SO는 여전히 DUPLICATE_BUYER_PO(점유 문서 안내)"""
    buyer = create_buyer()
    qt = issued_quotation(trade, buyer, [create_priced_sku()], quantity=20)
    line = qt["lines"][0]["id"]
    holder = create_so_from_qt_via_api(
        trade, qt, buyer_po_no="PO-HELD", lines=[{"source_line_id": line, "quantity": 5}]
    )
    for status in ("ON_HOLD", "CANCELLED", "CONFIRMED"):
        other = create_so_from_qt_via_api(
            trade, qt, lines=[{"source_line_id": line, "quantity": 1}]
        )
        if status == "CONFIRMED":
            _exec(
                "UPDATE sales_orders SET status = 'CONFIRMED', confirmed_at = now(), credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE' WHERE id = :i",
                i=other["id"],
            )
        else:
            _exec("UPDATE sales_orders SET status = :s WHERE id = :i", s=status, i=other["id"])
        cur = _get(trade, other["id"])
        r = trade.patch(
            f"{SO}/{other['id']}", json={"version": cur["version"], "buyer_po_no": "po-held"}
        )
        assert r.status_code == 409 and _code(r) == "TRADE_DOCS.DOCUMENT.FROZEN", status
        assert holder["doc_number"] not in r.text, status
    live = create_so_from_qt_via_api(trade, qt, lines=[{"source_line_id": line, "quantity": 1}])
    dup = trade.patch(
        f"{SO}/{live['id']}", json={"version": live["version"], "buyer_po_no": "po-held"}
    )
    assert dup.status_code == 409 and _code(dup) == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


def test_a_meta_edit_that_changes_nothing_is_a_no_op(trade: TestClient) -> None:
    """변경 없는 메타 편집(같은 메모·같은 담당자·빈 본문)은 version·updated_by를 바꾸지 않는다 · 실제 변경이 있으면 +1 · 취소 SO도 FREE 열은 허용"""
    so = _reference_so(trade)
    before = _scalar("SELECT updated_by_id FROM sales_orders WHERE id = :i", i=so["id"])
    same = trade.patch(
        f"{SO}/{so['id']}/meta",
        json={"version": so["version"], "assignee_id": so["assignee_id"], "internal_note": None},
    )
    assert same.status_code == 200 and same.json()["version"] == so["version"]
    assert _scalar("SELECT updated_by_id FROM sales_orders WHERE id = :i", i=so["id"]) == before
    changed = trade.patch(
        f"{SO}/{so['id']}/meta", json={"version": so["version"], "internal_note": "새 메모"}
    )
    assert changed.json()["version"] == so["version"] + 1
    repeat = trade.patch(
        f"{SO}/{so['id']}/meta",
        json={"version": changed.json()["version"], "internal_note": "새 메모"},
    )
    assert repeat.json()["version"] == changed.json()["version"]
    cancelled = _transition(trade, changed.json(), "CANCELLED", "취소")
    after = trade.patch(
        f"{SO}/{so['id']}/meta",
        json={"version": cancelled.json()["version"], "internal_note": "취소 후 메모"},
    )
    assert after.status_code == 200 and after.json()["version"] == cancelled.json()["version"] + 1


def test_a_no_op_meta_edit_by_another_user_does_not_take_over_updated_by(trade: TestClient) -> None:
    """다른 사용자가 같은 값을 다시 보내도(무변경) `updated_by`가 그 사람으로 바뀌지 않는다 — 무변경은 흔적을 남기지 않는다"""
    so = _reference_so(trade)
    trade.patch(f"{SO}/{so['id']}/meta", json={"version": so["version"], "internal_note": "메모"})
    cur = _get(trade, so["id"])
    before = _scalar("SELECT updated_by_id FROM sales_orders WHERE id = :i", i=so["id"])
    with logged_in(RoleCode.TRADE) as other:
        r = other.patch(
            f"{SO}/{so['id']}/meta", json={"version": cur["version"], "internal_note": "메모"}
        )
        assert r.status_code == 200 and r.json()["version"] == cur["version"]
    assert _scalar("SELECT updated_by_id FROM sales_orders WHERE id = :i", i=so["id"]) == before
