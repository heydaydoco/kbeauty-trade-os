"""A·B·I·J·K. 수출선적 API — SO 참조 생성·부분선적 1:N 잔량·초과 거부·역순 취소·SO 자동 수렴·출고지시 동결·라인·당사자·권한
(S3-2 PR-3a / WBS DoD③·검증 A / GC-A14 / ADR-0074·0075·0079 / design-A A4~A7 / design-C C1~C6).

DoD③ "부분선적 1:N 잔량 정확"·검증 A "잔량 0·초과 거부"를 HTTP로 관통한다. SO 상태는 같은 트랜잭션에서 수렴한다(IN_SHIPMENT ⇔ 살아 있는 선적 ≥ 1).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from tests.factories.shipments import (
    SHIPMENTS,
    SO,
    cancel,
    confirmed_so,
    create_body,
    create_shipment,
    created,
    release,
    rows,
    scalar,
    shipment_version,
    so_status,
    so_version,
)
from tests.factories.trade import (
    create_buyer,
    create_direct_so,
    create_priced_sku,
    create_supplier,
    idem,
    logged_in,
    unique,
)

pytestmark = pytest.mark.group_a


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _open(client: TestClient, so_id: int) -> list[int]:
    body = client.get(f"{SO}/{so_id}").json()
    return [line["shipment_open_quantity"] for line in body["lines"]]


def _events(aggregate_type: str, aggregate_id: int, event_type: str) -> list[dict[str, Any]]:
    return [
        r["payload"]
        for r in rows(
            "SELECT payload FROM events WHERE aggregate_type = :t AND aggregate_id = :i"
            " AND event_type = :e ORDER BY id",
            t=aggregate_type,
            i=aggregate_id,
            e=event_type,
        )
    ]


# ── A. 부분선적 1:N·잔량·초과·역순 취소·SO 수렴 ───────────────────────────────────


@pytest.mark.golden
def test_gc_a14_partial_shipments_drive_the_remaining_quantity_and_the_so_state(
    trade: TestClient,
) -> None:
    """GC-A14 — SO 라인 10: 선적 4 → SO 선적중·잔량 6 / 선적 6 → 잔량 0 / +1 → 409 EXCEEDS_OPEN(detail 잔량 0) / 첫 선적 취소 → 잔량 4·SO 선적중 유지 /
    둘째 취소 → 잔량 10·SO 확정 복귀 / 그 뒤 SO 취소 통과"""
    so = confirmed_so((10,))
    line = so["line_ids"][0]
    first = created(trade, so["id"], [(line, 4)])
    assert first["status"] == "PLANNED" and first["doc_number"].startswith("SH-")
    assert so_status(so["id"]) == "IN_SHIPMENT"
    assert _open(trade, so["id"]) == [6]
    assert first["lines"][0]["source_line"]["remaining_after"] == 6
    second = created(trade, so["id"], [(line, 6)])
    assert _open(trade, so["id"]) == [0] and so_status(so["id"]) == "IN_SHIPMENT"
    over = create_shipment(trade, so["id"], [(line, 1)])
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert over.json()["error"]["detail"] == {"open_quantity": {str(line): 0}}
    assert cancel(trade, first["id"]).status_code == 200
    assert _open(trade, so["id"]) == [4] and so_status(so["id"]) == "IN_SHIPMENT"
    assert cancel(trade, second["id"]).status_code == 200
    assert _open(trade, so["id"]) == [10] and so_status(so["id"]) == "CONFIRMED"
    response = trade.post(
        f"{SO}/{so['id']}/transitions",
        json={
            "to": "CANCELLED",
            "version": int(scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])),
            "reason": "바이어 취소",
        },
        headers=idem(),
    )
    assert response.status_code == 200, response.text
    assert so_status(so["id"]) == "CANCELLED"


@pytest.mark.golden
def test_gc_a14_a_live_shipment_blocks_so_cancel_with_successor_alive(trade: TestClient) -> None:
    """GC-A14(R-02) — 살아 있는 선적이 있는 SO(선적중) 취소 = 409 SUCCESSOR_ALIVE + detail.successors=[SH-…]"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 5)])
    response = trade.post(
        f"{SO}/{so['id']}/transitions",
        json={
            "to": "CANCELLED",
            "version": int(scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])),
            "reason": "바이어 취소",
        },
        headers=idem(),
    )
    assert response.status_code == 409
    assert _code(response) == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert response.json()["error"]["detail"] == {"successors": [shipment["doc_number"]]}
    assert so_status(so["id"]) == "IN_SHIPMENT"


def test_the_shipment_copies_the_source_and_takes_no_reentered_values(trade: TestClient) -> None:
    """원천 사본 — 통화·환율·결제조건·Incoterms·거래 상대·담당자·SKU·단가 = SO 값, 증빙일 = 오늘(KST — R-15, SO 증빙일과 무관)·원천 값 필드를 보내면 422(forbid)"""
    so = confirmed_so((3, 2), price=1250)
    so_row = rows("SELECT * FROM sales_orders WHERE id = :i", i=so["id"])[0]
    body = created(trade, so["id"], [(so["line_ids"][0], 3), (so["line_ids"][1], 1)])
    assert body["doc_date"] == today_kst().isoformat()
    assert body["currency"] == so_row["currency"] and body["fx_rate"] == "1350"
    assert body["fx_rate_date"] == so_row["fx_rate_date"].isoformat()
    assert body["payment_terms"]["payment_type"] == so_row["payment_type"]
    assert body["payment_terms"]["balance_days"] == so_row["balance_days"]
    assert body["incoterm"] == {"code": "FOB", "place": "Busan", "year": 2020}
    assert body["counterparty"] == {"partner_id": so["buyer"], "name": so_row["buyer_name"]}
    assert body["assignee"]["id"] == so_row["assignee_id"]
    assert body["shipment_kind"] == "EXPORT"
    assert body["source"] == {
        "kind": "SALES_ORDER",
        "id": so["id"],
        "doc_number": so_row["doc_number"],
        "status": "IN_SHIPMENT",
    }
    assert [
        (ln["quantity"], ln["unit_price_amount"], ln["line_amount"]) for ln in body["lines"]
    ] == [(3, 1250, 3750), (1, 1250, 1250)]
    assert body["total_amount"] == 5000 and body["total_text"] == "50.00"
    for field, value in (
        ("currency", "EUR"),
        ("fx_rate", "1"),
        ("unit_price_amount", 1),
        ("doc_date", "2026-01-01"),
        ("shipment_kind", "IMPORT"),
    ):
        payload = {**create_body([(so["line_ids"][1], 1)]), field: value}
        response = trade.post(f"{SO}/{so['id']}/shipments", json=payload, headers=idem())
        assert response.status_code == 422, field


def test_free_lines_copy_the_zero_price(trade: TestClient) -> None:
    """무상 라인 사본 — SO 무상 라인(단가 0)은 선적 라인도 무상·금액 0(무상 규약 승계)"""
    so = confirmed_so((5, 2), free_line=True)
    body = created(trade, so["id"], [(so["line_ids"][1], 2)])
    assert [
        (ln["is_free"], ln["unit_price_amount"], ln["line_amount"]) for ln in body["lines"]
    ] == [(True, 0, 0)]


@pytest.mark.parametrize("status", ["RECEIVED", "ON_HOLD", "CANCELLED"])
def test_only_a_confirmed_or_shipping_so_can_be_consumed(trade: TestClient, status: str) -> None:
    """원천 SO 자격 — 접수·보류·취소 SO로는 선적을 못 만든다(409 DOCUMENT_NOT_CONSUMABLE, 상태 무변 — 자동 확정 0)"""
    buyer = create_buyer()
    so = create_direct_so(buyer, [create_priced_sku()], quantity=5, buyer_po_no=unique("PO"))
    if status != "RECEIVED":
        from tests.factories.board import set_column

        set_column("sales_orders", so["id"], status=status)
    line = int(scalar("SELECT id FROM sales_order_lines WHERE so_id = :s", s=so["id"]))
    response = create_shipment(trade, so["id"], [(line, 1)])
    assert (
        response.status_code == 409
        and _code(response) == "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
    )
    assert so_status(so["id"]) == status
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 0


def test_error_priority_puts_404_and_409_before_422(trade: TestClient) -> None:
    """ADR-0079 ⑧(404→409→422, PR-3a 적대 검토 반영) — 잠금 전 무잠금 peek가 존재·상태를 먼저 판정한다:
    없는 선적 + 유형 불일치 당사자 = 404 / 취소 선적 + 유형 불일치·자동 역할 = 409 NOT_ACTIVE / 보류 SO + 유형 불일치 당사자로 생성·미리보기 = 409 /
    없는 SO + 유형 불일치 = 404 / 취소 선적의 자동 수하인 제외 = 409(422 아님) — 전부 저장 0"""
    supplier = create_supplier()  # SUPPLIER 유형 — FORWARDER 자리에 넣으면 422 유형 불일치
    wrong = [{"role": "FORWARDER", "partner_id": supplier}]
    missing = trade.post(f"{SHIPMENTS}/999999999/parties", json=wrong[0], headers=idem())
    assert missing.status_code == 404, missing.text
    so = confirmed_so((10,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 1)])
    assert cancel(trade, shipment["id"]).status_code == 200
    for body in (wrong[0], {"role": "CONSIGNEE", "partner_id": so["buyer"]}):
        closed = trade.post(f"{SHIPMENTS}/{shipment['id']}/parties", json=body, headers=idem())
        assert closed.status_code == 409 and _code(closed) == "SHIPMENTS.SHIPMENT.NOT_ACTIVE", body
    auto = next(p for p in shipment["parties"] if p["auto"])
    removed = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/parties/{auto['id']}", params={"version": auto["version"]}
    )
    assert removed.status_code == 409 and _code(removed) == "SHIPMENTS.SHIPMENT.NOT_ACTIVE"
    held = confirmed_so((10,))
    hold = trade.post(
        f"{SO}/{held['id']}/transitions",
        json={"to": "ON_HOLD", "version": so_version(held["id"]), "reason": "바이어 요청 보류"},
        headers=idem(),
    )
    assert hold.status_code == 200, hold.text
    line = held["line_ids"][0]
    for response in (
        create_shipment(trade, held["id"], [(line, 1)], parties=wrong),
        trade.post(
            f"{SO}/{held['id']}/shipments/preview", json=create_body([(line, 1)], parties=wrong)
        ),
    ):
        assert (
            response.status_code == 409
            and _code(response) == "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
        ), response.text
    assert create_shipment(trade, 999_999_999, [(line, 1)], parties=wrong).status_code == 404
    assert scalar("SELECT count(*) FROM shipments") == 1  # 취소된 첫 선적뿐
    assert scalar("SELECT count(*) FROM shipment_parties WHERE deleted_at IS NULL") == 1


def test_source_line_mismatch_duplicates_and_bad_quantities_are_422(trade: TestClient) -> None:
    """본문 라인 검증 — 다른 SO의 라인 = 422 LINE_MISMATCH(존재 비공개)·같은 라인 두 번·수량 0·국가 소문자 = 422, 없는 SO = 404"""
    so = confirmed_so((5,))
    other = confirmed_so((5,))
    mismatch = create_shipment(trade, so["id"], [(other["line_ids"][0], 1)])
    assert mismatch.status_code == 422 and _code(mismatch) == "SHIPMENTS.SOURCE.LINE_MISMATCH"
    line = so["line_ids"][0]
    assert create_shipment(trade, so["id"], [(line, 1), (line, 1)]).status_code == 422
    assert create_shipment(trade, so["id"], [(line, 0)]).status_code == 422
    assert create_shipment(trade, so["id"], [(line, 1)], dest="us").status_code == 422
    assert create_shipment(trade, 999_999_999, [(line, 1)]).status_code == 404
    assert scalar("SELECT count(*) FROM shipments") == 0 and so_status(so["id"]) == "CONFIRMED"


def test_preview_validates_like_create_and_writes_nothing(trade: TestClient) -> None:
    """미리보기 — 생성과 같은 값(잔량 전/후·금액·당사자)을 주고 저장 0(선적·이력·이벤트·멱등 키·SO 상태 무변), 초과도 같은 409"""
    so = confirmed_so((10,))
    line = so["line_ids"][0]
    events_before = scalar("SELECT count(*) FROM events")
    response = trade.post(f"{SO}/{so['id']}/shipments/preview", json=create_body([(line, 4)]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["lines"][0]["open_quantity_before"] == 10 and body["lines"][0]["remaining_after"] == 6
    )
    assert body["total_amount"] == 4000 and [p["role"] for p in body["parties"]] == ["CONSIGNEE"]
    assert scalar("SELECT count(*) FROM shipments") == 0 and so_status(so["id"]) == "CONFIRMED"
    assert scalar("SELECT count(*) FROM events") == events_before
    over = trade.post(f"{SO}/{so['id']}/shipments/preview", json=create_body([(line, 11)]))
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"


# ── 당사자 ──────────────────────────────────────────────────────────────────


def test_consignee_is_the_buyer_snapshot_and_requested_parties_are_typed(trade: TestClient) -> None:
    """수하인 = SO 바이어 자동 스냅샷(영문명·주소, 불변) / 포워더는 FORWARDER 유형만(아니면 422) / 자동 역할 지정 = 422 ROLE_NOT_ALLOWED"""
    so = confirmed_so((5,))
    forwarder = create_supplier(types=("FORWARDER",), name_en="Fast Freight Ltd.")
    supplier = create_supplier()
    line = so["line_ids"][0]
    body = created(
        trade, so["id"], [(line, 1)], parties=[{"role": "FORWARDER", "partner_id": forwarder}]
    )
    parties = {p["role"]: p for p in body["parties"]}
    assert (
        parties["CONSIGNEE"]["auto"] is True and parties["CONSIGNEE"]["partner_id"] == so["buyer"]
    )
    assert parties["CONSIGNEE"]["name_en"] == "Acme Trading Inc."
    assert parties["FORWARDER"] == {
        **parties["FORWARDER"],
        "auto": False,
        "name_en": "Fast Freight Ltd.",
    }
    wrong = create_shipment(
        trade, so["id"], [(line, 1)], parties=[{"role": "FORWARDER", "partner_id": supplier}]
    )
    assert wrong.status_code == 422 and _code(wrong) == "COMMON.VALIDATION.INVALID_FIELD"
    for role in ("CONSIGNEE", "SHIPPER"):
        response = create_shipment(
            trade, so["id"], [(line, 1)], parties=[{"role": role, "partner_id": so["buyer"]}]
        )
        assert (
            response.status_code == 422 and _code(response) == "SHIPMENTS.PARTY.ROLE_NOT_ALLOWED"
        ), role
        # 추가 경로도 같다 — 수하인(자동 스냅샷)·송하인(자사)은 POST /parties로 만들 수 없다(변이 점검에서 생존한 가드를 고정)
        added = trade.post(
            f"{SHIPMENTS}/{body['id']}/parties",
            json={"role": role, "partner_id": so["buyer"]},
            headers=idem(),
        )
        assert added.status_code == 422 and _code(added) == "SHIPMENTS.PARTY.ROLE_NOT_ALLOWED", role
    assert scalar("SELECT count(*) FROM shipment_parties WHERE shipment_id = :i", i=body["id"]) == 2


def test_missing_english_name_is_fail_visible(trade: TestClient) -> None:
    """바이어 영문명이 비면 선적 생성 = 422 ENGLISH_NAME_MISSING(서류 영문 원천 결측을 선적 시점에 드러낸다 — 저장 0)"""
    buyer = create_buyer(name_en=None)
    so = confirmed_so((5,), buyer=buyer)
    response = create_shipment(trade, so["id"], [(so["line_ids"][0], 1)])
    assert response.status_code == 422 and _code(response) == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"
    assert scalar("SELECT count(*) FROM shipments") == 0 and so_status(so["id"]) == "CONFIRMED"


def test_party_add_remove_rules_and_audit(trade: TestClient) -> None:
    """당사자 추가·제외 — 역할당 1건(409 ROLE_DUPLICATE)·자동 수하인 제외 422·당사자 version 409·제외 후 재추가 = 신규·audit 2종·취소 선적 409 NOT_ACTIVE"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 1)])
    notify = create_buyer(name_en="Notify Co.")
    added = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/parties",
        json={"role": "NOTIFY", "partner_id": notify},
        headers=idem(),
    )
    assert added.status_code == 201, added.text
    party = next(p for p in added.json()["parties"] if p["role"] == "NOTIFY")
    dup = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/parties",
        json={"role": "NOTIFY", "partner_id": notify},
        headers=idem(),
    )
    assert dup.status_code == 409 and _code(dup) == "SHIPMENTS.PARTY.ROLE_DUPLICATE"
    consignee = next(p for p in added.json()["parties"] if p["role"] == "CONSIGNEE")
    auto = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/parties/{consignee['id']}",
        params={"version": consignee["version"]},
    )
    assert auto.status_code == 422 and _code(auto) == "SHIPMENTS.PARTY.ROLE_NOT_ALLOWED"
    stale = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/parties/{party['id']}",
        params={"version": party["version"] + 1},
    )
    assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    removed = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/parties/{party['id']}", params={"version": party["version"]}
    )
    assert removed.status_code == 200 and "NOTIFY" not in {
        p["role"] for p in removed.json()["parties"]
    }
    again = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/parties",
        json={"role": "NOTIFY", "partner_id": notify},
        headers=idem(),
    )
    assert again.status_code == 201
    assert next(p for p in again.json()["parties"] if p["role"] == "NOTIFY")["id"] != party["id"]
    actions = [
        r["action"]
        for r in rows(
            "SELECT action FROM audit_log WHERE entity_type = 'shipments' AND entity_id = :i ORDER BY id",
            i=shipment["id"],
        )
    ]
    assert actions == ["shipments.party.added", "shipments.party.removed", "shipments.party.added"]
    assert cancel(trade, shipment["id"]).status_code == 200
    closed = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/parties",
        json={"role": "FORWARDER", "partner_id": create_supplier(types=("FORWARDER",))},
        headers=idem(),
    )
    assert closed.status_code == 409 and _code(closed) == "SHIPMENTS.SHIPMENT.NOT_ACTIVE"
    # 취소된 선적의 당사자 제외도 409 NOT_ACTIVE — 행·감사 그대로(변이 점검에서 생존한 가드를 고정)
    kept = next(p for p in again.json()["parties"] if p["role"] == "NOTIFY")
    frozen_out = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/parties/{kept['id']}", params={"version": kept["version"]}
    )
    assert frozen_out.status_code == 409 and _code(frozen_out) == "SHIPMENTS.SHIPMENT.NOT_ACTIVE"
    assert "NOTIFY" in {
        p["role"] for p in trade.get(f"{SHIPMENTS}/{shipment['id']}").json()["parties"]
    }
    assert (
        scalar(
            "SELECT count(*) FROM audit_log WHERE entity_type = 'shipments' AND entity_id = :i",
            i=shipment["id"],
        )
        == 3
    )


# ── 라인 편집·출고지시(동결)·취소 ────────────────────────────────────────────────


def test_line_edits_stay_within_the_remaining_quantity(trade: TestClient) -> None:
    """라인 — 추가(다른 SO 라인)·수량 증가는 잔량 + 현재 수량 안(초과 409)·같은 원천 라인 재추가 409·마지막 라인 삭제 409·헤더 version +1·합계 재계산"""
    so = confirmed_so((10, 5), price=100)
    a, b = so["line_ids"]
    shipment = created(trade, so["id"], [(a, 4)])
    v = shipment_version(shipment["id"])
    added = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/lines",
        json={"version": v, "source_line_id": b, "quantity": 5},
        headers=idem(),
    )
    assert added.status_code == 201, added.text
    assert added.json()["total_amount"] == 900 and added.json()["version"] == v + 1
    dup = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/lines",
        json={"version": v + 1, "source_line_id": a, "quantity": 1},
        headers=idem(),
    )
    assert dup.status_code == 409 and _code(dup) == "SHIPMENTS.LINE.DUPLICATE_SOURCE"
    line_a = next(ln for ln in added.json()["lines"] if ln["so_line_id"] == a)
    grown = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_a['id']}",
        json={"version": v + 1, "quantity": 10},
    )
    assert grown.status_code == 200 and grown.json()["total_amount"] == 1500
    over = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_a['id']}",
        json={"version": v + 2, "quantity": 11},
    )
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert over.json()["error"]["detail"] == {"open_quantity": {str(a): 10}}
    stale = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_a['id']}", json={"version": v, "quantity": 1}
    )
    assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    line_b = next(ln for ln in grown.json()["lines"] if ln["so_line_id"] == b)
    removed = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_b['id']}", params={"version": v + 2}
    )
    assert removed.status_code == 200 and [ln["so_line_id"] for ln in removed.json()["lines"]] == [
        a
    ]
    assert _open(trade, so["id"]) == [0, 5]
    last = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_a['id']}", params={"version": v + 3}
    )
    assert last.status_code == 409 and _code(last) == "SHIPMENTS.LINE.LAST_LINE"
    assert so_status(so["id"]) == "IN_SHIPMENT"


def test_release_order_freezes_lines_and_countries_but_not_free_fields(trade: TestClient) -> None:
    """출고지시(동결 액션) — frozen_at 기록·라인/국가 편집 409 FROZEN·메모/담당 편집은 허용·재출고지시 409·출고지시 뒤 취소 = SO 확정 복귀"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 5)])
    released = release(trade, shipment["id"])
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["status"] == "RELEASE_ORDERED" and body["frozen_at"] is not None
    v = body["version"]
    line = body["lines"][0]
    frozen = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line['id']}", json={"version": v, "quantity": 1}
    )
    assert frozen.status_code == 409 and _code(frozen) == "TRADE_DOCS.DOCUMENT.FROZEN"
    countries = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}", json={"version": v, "dest_country_code": "JP"}
    )
    assert countries.status_code == 409 and _code(countries) == "TRADE_DOCS.DOCUMENT.FROZEN"
    note = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}", json={"version": v, "internal_note": "부킹 확인"}
    )
    assert note.status_code == 200 and note.json()["internal_note"] == "부킹 확인"
    again = release(trade, shipment["id"])
    assert again.status_code == 409 and _code(again) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert cancel(trade, shipment["id"]).status_code == 200
    assert so_status(so["id"]) == "CONFIRMED"
    log = trade.get(f"{SHIPMENTS}/{shipment['id']}/status-log").json()
    assert [(i["from_status"], i["to_status"]) for i in reversed(log["items"])] == [
        (None, "PLANNED"),
        ("PLANNED", "RELEASE_ORDERED"),
        ("RELEASE_ORDERED", "CANCELLED"),
    ]
    assert log["total"] == 3 and log["size"] == 50


def test_cancel_requires_a_reason_and_only_cancelled_is_a_public_target(trade: TestClient) -> None:
    """취소 사유 필수(422 REASON_REQUIRED)·범용 전이의 to_status는 CANCELLED 1값(출고지시·RESERVED 5값은 스키마 422 — B: RESERVED 진입 0)"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 5)])
    no_reason = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/transitions",
        json={"to_status": "CANCELLED", "version": shipment_version(shipment["id"])},
        headers=idem(),
    )
    assert (
        no_reason.status_code == 422 and _code(no_reason) == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    )
    for target in ("RELEASE_ORDERED", "PICKING", "INSPECTED", "RELEASED", "SHIPPED", "CLOSED"):
        response = trade.post(
            f"{SHIPMENTS}/{shipment['id']}/transitions",
            json={"to_status": target, "version": shipment_version(shipment["id"]), "reason": "x"},
            headers=idem(),
        )
        assert response.status_code == 422, target
    assert scalar("SELECT status FROM shipments WHERE id = :i", i=shipment["id"]) == "PLANNED"


def test_events_and_so_history_carry_the_convergence(trade: TestClient) -> None:
    """이벤트 — 선적 created·status_changed(커널 payload), SO status_changed 2건(CONFIRMED→IN_SHIPMENT→CONFIRMED, automatic=true, cause_shipment_id) · SO 이력 자동 행 행위자 = 유발자"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 5)])
    assert cancel(trade, shipment["id"]).status_code == 200
    created_events = _events("shipments", shipment["id"], "shipments.shipment.created")
    assert len(created_events) == 1 and created_events[0]["doc_type"] == "SHIPMENT"
    assert (
        created_events[0]["partner_id"] == so["buyer"]
        and "cause_shipment_id" not in created_events[0]
    )
    # design-integrated §2.7 — created payload = shipment_id(doc_id)·doc_number·shipment_kind·so_id/po_id·partner_id·assignee_id
    for event in (
        *created_events,
        *_events("shipments", shipment["id"], "shipments.shipment.status_changed"),
    ):
        assert (event["doc_id"], event["shipment_kind"], event["so_id"], event["po_id"]) == (
            shipment["id"],
            "EXPORT",
            so["id"],
            None,
        )
        assert event["doc_number"] == shipment["doc_number"] and "assignee_id" in event
    so_created = _events("sales_orders", so["id"], "sales_orders.sales_order.status_changed")
    assert all(
        "shipment_kind" not in e and "so_id" not in e for e in so_created
    )  # 다른 전표 이벤트는 그대로
    so_events = _events("sales_orders", so["id"], "sales_orders.sales_order.status_changed")
    assert [
        (e["from_status"], e["to_status"], e["automatic"], e["cause_shipment_id"])
        for e in so_events
    ] == [
        ("CONFIRMED", "IN_SHIPMENT", True, shipment["id"]),
        ("IN_SHIPMENT", "CONFIRMED", True, shipment["id"]),
    ]
    history = rows(
        "SELECT from_status, to_status, automatic, actor_user_id FROM sales_order_status_log WHERE sales_order_id = :i ORDER BY id",
        i=so["id"],
    )
    auto = [r for r in history if r["automatic"]]
    assert [(r["from_status"], r["to_status"]) for r in auto] == [
        ("CONFIRMED", "IN_SHIPMENT"),
        ("IN_SHIPMENT", "CONFIRMED"),
    ]
    assert all(r["actor_user_id"] is not None for r in auto)


# ── J. 멱등·version ─────────────────────────────────────────────────────────────


@pytest.mark.group_j
def test_double_click_creates_one_shipment(trade: TestClient) -> None:
    """J-01 — 같은 Idempotency-Key 2회 → 선적 1건·채번 1회·SO 이력 수렴 1행·created 이벤트 1건, 두 응답 동일"""
    so = confirmed_so((5,))
    headers = idem()
    first = create_shipment(trade, so["id"], [(so["line_ids"][0], 2)], headers=headers)
    second = create_shipment(trade, so["id"], [(so["line_ids"][0], 2)], headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 1
    assert (
        scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :s AND to_status = 'IN_SHIPMENT'",
            s=so["id"],
        )
        == 1
    )
    assert (
        scalar("SELECT count(*) FROM events WHERE event_type = 'shipments.shipment.created'") == 1
    )


@pytest.mark.group_j
def test_stale_versions_are_409_without_side_effects(trade: TestClient) -> None:
    """J-10 — 헤더 편집·출고지시·취소에 낡은 version = 409 VERSION_CONFLICT, 상태·값 불변"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 5)])
    v = shipment_version(shipment["id"])
    assert (
        trade.patch(
            f"{SHIPMENTS}/{shipment['id']}", json={"version": v, "internal_note": "a"}
        ).status_code
        == 200
    )
    for response in (
        trade.patch(f"{SHIPMENTS}/{shipment['id']}", json={"version": v, "internal_note": "b"}),
        release(trade, shipment["id"], version=v),
        cancel(trade, shipment["id"], version=v),
    ):
        assert (
            response.status_code == 409 and _code(response) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
        )
    row = rows("SELECT status, internal_note FROM shipments WHERE id = :i", i=shipment["id"])[0]
    assert (row["status"], row["internal_note"]) == ("PLANNED", "a")


# ── K. 권한·부모-자식·Page·원가 키 ─────────────────────────────────────────────────


@pytest.mark.group_k
def test_logistics_operates_but_cannot_consume_or_cancel() -> None:
    """ADR-0079 — 물류: 출고지시·당사자·헤더 편집 허용 / 생성·미리보기·라인·취소 403(부작용 0)·조회 전 역할"""
    so = confirmed_so((5,))
    with logged_in(RoleCode.TRADE) as trade:
        shipment = created(trade, so["id"], [(so["line_ids"][0], 3)])
    with logged_in(RoleCode.LOGISTICS) as logistics:
        assert create_shipment(logistics, so["id"], [(so["line_ids"][0], 1)]).status_code == 403
        assert (
            logistics.post(
                f"{SO}/{so['id']}/shipments/preview", json=create_body([(so["line_ids"][0], 1)])
            ).status_code
            == 403
        )
        line_id = shipment["lines"][0]["id"]
        v = shipment_version(shipment["id"])
        assert (
            logistics.patch(
                f"{SHIPMENTS}/{shipment['id']}/lines/{line_id}", json={"version": v, "quantity": 1}
            ).status_code
            == 403
        )
        assert cancel(logistics, shipment["id"]).status_code == 403
        assert (
            logistics.patch(
                f"{SHIPMENTS}/{shipment['id']}", json={"version": v, "dest_country_code": "JP"}
            ).status_code
            == 200
        )
        forwarder = create_supplier(types=("FORWARDER",))
        assert (
            logistics.post(
                f"{SHIPMENTS}/{shipment['id']}/parties",
                json={"role": "FORWARDER", "partner_id": forwarder},
                headers=idem(),
            ).status_code
            == 201
        )
        released = release(logistics, shipment["id"])
        assert released.status_code == 200 and released.json()["status"] == "RELEASE_ORDERED"
        assert released.json()["allowed_actions"] == ["EDIT_META", "EDIT_PARTIES"]
        assert logistics.get(f"{SHIPMENTS}/{shipment['id']}").status_code == 200
    assert scalar("SELECT count(*) FROM shipments") == 1


@pytest.mark.group_k
@pytest.mark.parametrize("role", [RoleCode.CERT, RoleCode.VIEWER])
def test_cert_and_viewer_read_only_and_403_comes_before_404(role: RoleCode) -> None:
    """K-02·K-04 — 인증·조회 전용은 읽기만(쓰기 403), 없는 선적 id에 쓰기도 403(역할 검사가 존재 검사보다 먼저)"""
    so = confirmed_so((5,))
    with logged_in(RoleCode.TRADE) as trade:
        shipment = created(trade, so["id"], [(so["line_ids"][0], 3)])
    with logged_in(role) as client:
        assert client.get(f"{SHIPMENTS}/{shipment['id']}").status_code == 200
        assert client.get(SHIPMENTS).status_code == 200
        assert release(client, shipment["id"]).status_code == 403
        assert (
            client.patch(
                f"{SHIPMENTS}/999999999", json={"version": 1, "internal_note": "x"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"{SHIPMENTS}/999999999/release-order", json={"version": 1}, headers=idem()
            ).status_code
            == 403
        )
        assert client.get(f"{SHIPMENTS}/{shipment['id']}").json()["allowed_actions"] == []


@pytest.mark.group_k
def test_parent_child_mismatch_is_404_without_side_effects(trade: TestClient) -> None:
    """K-03 — 다른 선적의 라인·당사자 id를 경로에 섞으면 404(부작용 0 — 수량·version 불변)"""
    so = confirmed_so((5, 5))
    a = created(trade, so["id"], [(so["line_ids"][0], 2)])
    b = created(trade, so["id"], [(so["line_ids"][1], 2)])
    b_line = b["lines"][0]["id"]
    b_party = b["parties"][0]
    v = shipment_version(a["id"])
    assert (
        trade.patch(
            f"{SHIPMENTS}/{a['id']}/lines/{b_line}", json={"version": v, "quantity": 1}
        ).status_code
        == 404
    )
    assert (
        trade.delete(f"{SHIPMENTS}/{a['id']}/lines/{b_line}", params={"version": v}).status_code
        == 404
    )
    assert (
        trade.delete(
            f"{SHIPMENTS}/{a['id']}/parties/{b_party['id']}", params={"version": b_party["version"]}
        ).status_code
        == 404
    )
    assert shipment_version(a["id"]) == v
    assert scalar("SELECT quantity FROM shipment_lines WHERE id = :i", i=b_line) == 2


@pytest.mark.group_k
def test_list_is_paginated_filtered_and_carries_no_cost_keys(trade: TestClient) -> None:
    """K-05·K-06 — 목록 Page 봉투(기본 50)·SO·상태 필터·라인 수, 상세·목록 응답에 원가 계열 키 0"""
    so = confirmed_so((5,))
    other = confirmed_so((5,))
    mine = created(trade, so["id"], [(so["line_ids"][0], 1)])
    created(trade, other["id"], [(other["line_ids"][0], 1)])
    page = trade.get(SHIPMENTS, params={"so_id": so["id"]}).json()
    assert page["total"] == 1 and page["size"] == 50 and page["items"][0]["id"] == mine["id"]
    assert page["items"][0]["line_count"] == 1 and page["items"][0]["source"]["doc_number"]
    assert trade.get(SHIPMENTS, params={"status": ["RELEASE_ORDERED"]}).json()["total"] == 0
    assert (
        trade.get(SHIPMENTS, params={"status": ["PLANNED", "RELEASE_ORDERED"]}).json()["total"] == 2
    )
    assert trade.get(SHIPMENTS, params={"q": mine["doc_number"]}).json()["total"] == 1
    detail = trade.get(f"{SHIPMENTS}/{mine['id']}").json()
    text_dump = str(detail) + str(page)
    for forbidden in ("cost", "margin", "purchase_price", "unit_cost"):
        assert forbidden not in text_dump
    assert detail["lines"][0]["availability"] == {"status": "NOT_IMPLEMENTED"}
    assert detail["lines"][0]["dg"] == {"flag": False, "un_number": None, "dg_class": None}


@pytest.mark.group_k
def test_detail_query_count_is_fixed_regardless_of_line_count(trade: TestClient) -> None:
    """K(렌즈 7) — 상세 조회 쿼리 수는 라인 수와 무관한 상수다(N+1 0)"""
    from sqlalchemy import event

    from app.core.db.session import engine

    small = confirmed_so((5,))
    big = confirmed_so((5, 5, 5, 5, 5))
    one = created(trade, small["id"], [(small["line_ids"][0], 1)])
    five = created(trade, big["id"], [(lid, 1) for lid in big["line_ids"]])
    counts: list[int] = []
    for shipment_id in (one["id"], five["id"]):
        statements: list[str] = []

        def _count(*_args: Any, sink: list[str] = statements) -> None:
            sink.append("q")

        event.listen(engine, "before_cursor_execute", _count)
        try:
            assert trade.get(f"{SHIPMENTS}/{shipment_id}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        counts.append(len(statements))
    assert counts[0] == counts[1] and counts[0] <= 20, counts


@pytest.mark.group_h
def test_handover_moves_shipment_assignees_without_touching_version_or_history(
    trade: TestClient,
) -> None:
    """H(담당 이관 — ADR-0078 ④·0079 ⑨) — 일괄 이관이 선적 담당(assignee_id)만 옮기고 version·상태 이력·작성자는 건드리지 않는다(동결·취소 선적 포함)"""
    from app.modules.handover import service as handover
    from app.modules.handover.targets import ASSIGNMENT_TARGETS
    from tests.support.factories import create_user

    labels = [t.label for t in ASSIGNMENT_TARGETS]
    assert labels.index("shipments") == labels.index("purchase_orders") + 1  # LOCK_ORDER 순서
    so = confirmed_so((10,))
    planned = created(trade, so["id"], [(so["line_ids"][0], 2)])
    frozen = created(trade, so["id"], [(so["line_ids"][0], 3)])
    assert release(trade, frozen["id"]).status_code == 200
    dead = created(trade, so["id"], [(so["line_ids"][0], 1)])
    assert cancel(trade, dead["id"]).status_code == 200
    old_owner = planned["assignee"]["id"]
    new_owner = create_user(f"{unique('ship-new')}@example.com", roles=(RoleCode.LOGISTICS,))
    before = rows(
        "SELECT id, version, created_by_id FROM shipments WHERE so_id = :s ORDER BY id", s=so["id"]
    )
    logs = scalar("SELECT count(*) FROM shipment_status_log")
    result = handover.reassign_all(
        from_user_id=old_owner, to_user_id=new_owner, actor_user_id=new_owner
    )
    assert result.moved["shipments"] == 3
    for shipment in (planned, frozen, dead):
        assert trade.get(f"{SHIPMENTS}/{shipment['id']}").json()["assignee"]["id"] == new_owner
    after = rows(
        "SELECT id, version, created_by_id FROM shipments WHERE so_id = :s ORDER BY id", s=so["id"]
    )
    assert after == before and scalar("SELECT count(*) FROM shipment_status_log") == logs
