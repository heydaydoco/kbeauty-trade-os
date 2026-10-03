"""A·G·K. 오더 인테이크(MANUAL) API — 착지·중복 PO 거부·스냅샷·품번 해석·STALE_MAPPING·상태 전이·복제 재접수 (S3-1 PR-13a / ADR-0071 / design-D D1·D2·D4·D5).

DoD ② **중복 바이어 PO 0건**의 착지 쪽 증명: 같은 (바이어, 정규화 PO번호)는 PENDING 인테이크로도, 비취소 SO로도 두 번 점유할 수 없다 — 거부·취소 뒤에는 다시 쓴다.
인테이크는 PENDING으로만 착지하고(상태·SKU·PO 키 필드가 요청에 없다) CONFIRMED로 가는 길은 사람 1클릭 `confirm`뿐이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.modules.identity.models import RoleCode
from tests.factories.intake import (
    INTAKES,
    code_of,
    confirm,
    execute,
    future,
    get,
    intake_payload,
    line_body,
    register,
    rows,
    scalar,
    world,
)
from tests.factories.trade import (
    create_direct_so,
    create_priced_sku,
    idem,
    logged_in,
    map_buyer_item_code,
    set_price,
    unique,
)

pytestmark = pytest.mark.group_a

SO = "/api/v1/sales-orders"
DUP = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _cancel_so(client: Any, so_id: int) -> None:
    body = client.get(f"{SO}/{so_id}").json()
    response = client.post(
        f"{SO}/{so_id}/transitions",
        json={"to": "CANCELLED", "version": body["version"], "reason": "테스트 취소"},
        headers=idem(),
    )
    assert response.status_code == 200, response.text


# ── 착지 ─────────────────────────────────────────────────────────────────────


def test_register_lands_as_pending_with_server_resolved_skus(trade: Any) -> None:
    """수동 등록은 PENDING으로만 착지하고 품번→SKU는 서버가 해석해 저장한다 — 원본(original)은 제출 본문 그대로다"""
    w = world(lines=2)
    body = register(trade, w, po_no=" PO-2026-001 ")
    assert body["status"] == "PENDING" and body["version"] == 1 and body["source_kind"] == "MANUAL"
    assert body["buyer_po_no"] == "PO-2026-001"  # strip 원문
    assert [ln["sku_id"] for ln in body["lines"]] == w["sku_ids"]
    assert all(ln["mapping_state"] == "MAPPED" for ln in body["lines"])
    assert (
        body["lines"][0]["unit_price_amount"] == 1000
        and body["lines"][0]["unit_price_text"] == "10.00"
    )
    assert body["total_amount"] == 2 * 5 * 1000
    assert body["original"]["kind"] == "MANUAL"
    assert (
        body["original"]["header"]["buyer_po_no"] == " PO-2026-001 "
    )  # 제출 본문 무손실(strip 전)
    assert [ln["buyer_item_code"] for ln in body["original"]["lines"]] == w["codes"]
    assert "sku_id" not in str(body["original"]["lines"])  # 서버 해석값은 원본에 싣지 않는다
    # 이벤트 — id만, 금액·메모 없음
    payload = scalar(
        "SELECT payload FROM events WHERE event_type = 'order_intakes.order_intake.created'"
    )
    assert set(payload) == {"intake_id", "buyer_partner_id", "assignee_id", "source_kind"}


def test_unmapped_codes_land_as_pending_with_null_sku(trade: Any) -> None:
    """미매핑 품번은 오류가 아니라 sku_id NULL로 착지한다(검토 화면이 등록을 유도한다) — 서버가 SKU를 추측하지 않는다"""
    w = world()
    body = register(trade, w, lines=[line_body("NO-SUCH-CODE")])
    assert body["lines"][0]["sku_id"] is None and body["lines"][0]["mapping_state"] == "UNMAPPED"
    # 같은 코드의 대소문자 변형은 자동 매칭하지 않는다(정확 일치)
    lower = register(trade, w, lines=[line_body(w["codes"][0].lower())])
    assert lower["lines"][0]["sku_id"] is None


@pytest.mark.group_g
def test_write_schemas_have_no_status_sku_or_key_fields(trade: Any) -> None:
    """요청 본문에 status·sku_id·buyer_po_no_key·source_*·extracted_snapshot을 실어 보내면 422(extra=forbid) — 직행·우회 표면이 구조적으로 없다"""
    w = world()
    for extra in (
        {"status": "CONFIRMED"},
        {"buyer_po_no_key": "X"},
        {"source_kind": "CSV"},
        {"sales_order_id": 1},
        {"extracted_snapshot": {}},
        {"decided_at": "2026-01-01T00:00:00Z"},
    ):
        r = trade.post(INTAKES, json=intake_payload(w, **extra), headers=idem())
        assert r.status_code == 422, (extra, r.text)
    bad_line = intake_payload(w, lines=[{**line_body(w["codes"][0]), "sku_id": w["sku_ids"][0]}])
    assert trade.post(INTAKES, json=bad_line, headers=idem()).status_code == 422
    assert scalar("SELECT count(*) FROM order_intakes") == 0  # 양성 대조: 정상 본문은 아래에서 착지
    assert register(trade, w)["status"] == "PENDING"


def test_input_validation_is_fail_visible(trade: Any) -> None:
    """수량·단가·통화·시장·날짜·거래처 유형 위반은 전부 422 — 0원·반올림·미등록 통화 500 없음"""
    w = world()
    cases: list[dict[str, Any]] = [
        {"lines": [line_body(w["codes"][0], quantity=0)]},
        {"lines": [line_body(w["codes"][0], quantity=100_000_000)]},
        {"lines": [line_body(w["codes"][0], unit_price="0")]},
        {"lines": [line_body(w["codes"][0], unit_price="10.005")]},  # 반올림 금지
        {"lines": [line_body(w["codes"][0], unit_price="-1")]},
        {"lines": [line_body(w["codes"][0], requested_delivery_date="2000-01-01")]},
        {"currency": "ZZZ"},
        {"dest_market_code": "ZZ"},
        {"buyer_po_date": "2999-01-01"},
        {"buyer_po_no": "​ ​"},  # 유효 문자 없음
        {"buyer_po_no": "P" * 61},
    ]
    for override in cases:
        r = trade.post(INTAKES, json=intake_payload(w, **override), headers=idem())
        assert r.status_code == 422, (override, r.status_code, r.text)
    assert (
        trade.post(
            INTAKES, json=intake_payload(w, buyer_partner_id=999_999), headers=idem()
        ).status_code
        == 422
    )
    assert scalar("SELECT count(*) FROM order_intakes") == 0
    assert register(trade, w)["status"] == "PENDING"  # 양성 대조


def test_line_limit_is_200(trade: Any) -> None:
    """라인 상한 200 — 200개는 통과·201개는 422(스키마)"""
    w = world()
    ok = intake_payload(w, lines=[line_body(w["codes"][0], quantity=1) for _ in range(200)])
    assert trade.post(INTAKES, json=ok, headers=idem()).status_code == 201
    over = intake_payload(w, lines=[line_body(w["codes"][0], quantity=1) for _ in range(201)])
    assert trade.post(INTAKES, json=over, headers=idem()).status_code == 422


# ── 중복 바이어 PO (DoD ②) ────────────────────────────────────────────────────


def test_duplicate_po_is_rejected_at_landing_for_pending_occupancy(trade: Any) -> None:
    """같은 (바이어, PO키)의 PENDING 인테이크가 있으면 두 번째 착지는 409 — 다른 PO·다른 바이어는 통과(양성 대조)"""
    w = world()
    first = register(trade, w, po_no="PO-DUP-1")
    r = trade.post(INTAKES, json=intake_payload(w, po_no="PO-DUP-1"), headers=idem())
    assert r.status_code == 409 and code_of(r) == DUP
    assert r.json()["error"]["detail"] == {
        "intake_id": first["id"],
        "status": "PENDING",
    }  # 금액 없음
    assert scalar("SELECT count(*) FROM order_intakes") == 1
    assert register(trade, w, po_no="PO-DUP-2")["status"] == "PENDING"
    other = world()
    assert (
        register(trade, other, po_no="PO-DUP-1")["status"] == "PENDING"
    )  # 다른 바이어는 같은 PO번호 가능


@pytest.mark.parametrize(
    "variant",
    [
        "po-dup-1",  # 대소문자
        " PO-DUP-1 ",  # 공백
        "PO–DUP-1",  # en-dash
        "PO-DUP​-1",  # 제로폭
        "ＰＯ-DUP-1",  # 전각
        "PO - DUP - 1",  # 내부 공백
    ],
)
def test_duplicate_po_variants_normalize_to_the_same_key(trade: Any, variant: str) -> None:
    """정규화 변형(대소문자·공백·대시류·제로폭·전각)은 같은 키라 중복이다 — 구두점이 다른 'PO-DUP1'은 별개(과병합 방지)"""
    w = world()
    register(trade, w, po_no="PO-DUP-1")
    r = trade.post(INTAKES, json=intake_payload(w, po_no=variant), headers=idem())
    assert r.status_code == 409 and code_of(r) == DUP, variant
    assert (
        register(trade, w, po_no="PO-DUP1")["status"] == "PENDING"
    )  # 양성 대조(구두점 다르면 별개)


def test_po_is_reusable_after_reject_and_blocked_while_pending(trade: Any) -> None:
    """거부된 인테이크는 키를 해방한다 — 거부 후 같은 PO 재등록 성공, 거부 전에는 409"""
    w = world()
    first = register(trade, w, po_no="PO-RJ-1")
    assert (
        trade.post(INTAKES, json=intake_payload(w, po_no="PO-RJ-1"), headers=idem()).status_code
        == 409
    )
    rej = trade.post(
        f"{INTAKES}/{first['id']}/reject",
        json={"version": first["version"], "reason": "바이어가 PO를 철회함"},
        headers=idem(),
    )
    assert rej.status_code == 200 and rej.json()["status"] == "REJECTED"
    again = register(trade, w, po_no="PO-RJ-1")
    assert again["status"] == "PENDING" and again["id"] != first["id"]


def test_active_so_occupies_the_po_and_cancellation_releases_it(trade: Any) -> None:
    """살아 있는 비취소 SO가 같은 PO를 점유하면 착지 409(점유 문서번호 안내) — SO 취소 후에는 재사용"""
    w = world()
    so = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="PO-SO-1")
    r = trade.post(INTAKES, json=intake_payload(w, po_no="po-so-1"), headers=idem())
    assert r.status_code == 409 and code_of(r) == DUP
    assert r.json()["error"]["detail"]["doc_number"] == so["doc_number"]
    _cancel_so(trade, so["id"])
    assert register(trade, w, po_no="PO-SO-1")["status"] == "PENDING"


def test_confirmed_intake_keeps_the_po_occupied_through_its_so(trade: Any) -> None:
    """CONFIRMED 인테이크의 PO는 SO가 점유한다 — 재등록 409, SO 취소 후 재등록 성공(정정=취소+신규)"""
    w = world()
    first = register(trade, w, po_no="PO-CF-1")
    done = confirm(trade, first)
    assert done.status_code == 201, done.text
    so_id = done.json()["sales_order_id"]
    assert (
        trade.post(INTAKES, json=intake_payload(w, po_no="PO-CF-1"), headers=idem()).status_code
        == 409
    )
    _cancel_so(trade, so_id)
    assert register(trade, w, po_no="PO-CF-1")["status"] == "PENDING"


def test_soft_deleted_intake_does_not_hold_the_po(trade: Any) -> None:
    """soft delete된 인테이크는 키를 점유하지 않는다(부분 유니크 deleted_at IS NULL) — 재유입은 부활이 아니라 신규"""
    w = world()
    first = register(trade, w, po_no="PO-SD-1")
    assert (
        trade.post(INTAKES, json=intake_payload(w, po_no="PO-SD-1"), headers=idem()).status_code
        == 409
    )
    execute("UPDATE order_intakes SET deleted_at = now() WHERE id = :i", i=first["id"])
    again = register(trade, w, po_no="PO-SD-1")
    assert again["id"] != first["id"]


def test_confirm_is_refused_when_an_so_took_the_po_after_landing(trade: Any) -> None:
    """착지 뒤 같은 PO의 SO가 생겼다면(참조 생성 등) 접수 확정은 409 — 인테이크는 PENDING 그대로, SO는 늘지 않는다"""
    w = world()
    intake = register(trade, w, po_no="PO-LATE-1")
    create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="PO-LATE-1")
    before = scalar("SELECT count(*) FROM sales_orders")
    r = confirm(trade, intake)
    assert r.status_code == 409 and code_of(r) == DUP
    assert scalar("SELECT count(*) FROM sales_orders") == before
    assert get(trade, intake["id"])["status"] == "PENDING"


# ── 확정 = SO 접수 생성 ────────────────────────────────────────────────────────


def test_confirm_creates_an_so_whose_values_match_the_intake_snapshot(trade: Any) -> None:
    """확정은 SO(RECEIVED)를 만들고 헤더·라인 전 필드가 인테이크 값과 일치한다 — BUYER_PO 기준·접수 시점 마스터 판가 스냅샷·백링크"""
    w = world(lines=2)
    sku_a, sku_b = w["sku_ids"]
    set_price(
        sku_b, 1500, effective_from=date(2021, 1, 1)
    )  # 기준가 = 발효일 최댓값 규칙(prices_at)
    body = register(
        trade,
        w,
        po_no="PO-VAL-1",
        buyer_po_date="2026-09-01",
        lines=[
            line_body(
                w["codes"][0], quantity=7, unit_price="10.50", requested_delivery_date=future(45)
            ),
            line_body(w["codes"][1], quantity=3, unit_price="9.99"),
        ],
    )
    done = confirm(trade, body)
    assert done.status_code == 201, done.text
    out = done.json()
    assert out["doc_number"].startswith("SO-") and out["intake"]["status"] == "CONFIRMED"
    so = trade.get(f"{SO}/{out['sales_order_id']}").json()
    assert so["status"] == "RECEIVED" and so["currency"] == "USD" and so["dest_market_code"] == "US"
    assert so["buyer_partner_id"] == w["buyer"] and so["buyer_po_no"] == "PO-VAL-1"
    assert so["buyer_po_date"] == "2026-09-01"
    assert so["qt_id"] is None and so["pi_id"] is None
    assert so["assignee_id"] == body["assignee_id"]
    assert so["payment_terms"]["payment_type"] is None  # 결제조건·Incoterms·환율은 SO 편집에서 입력
    line1, line2 = so["lines"]
    assert (line1["sku_id"], line1["quantity"], line1["unit_price_amount"]) == (sku_a, 7, 1050)
    assert line1["buyer_item_code"] == w["codes"][0] and line1["requested_delivery_date"] == future(
        45
    )
    assert (line2["sku_id"], line2["quantity"], line2["unit_price_amount"]) == (sku_b, 3, 999)
    stored = rows(
        "SELECT price_basis, list_price_amount, is_free FROM sales_order_lines WHERE so_id = :s ORDER BY line_no",
        s=out["sales_order_id"],
    )
    assert [tuple(r) for r in stored] == [("BUYER_PO", 1000, False), ("BUYER_PO", 1500, False)]
    assert so["total_amount"] == 7 * 1050 + 3 * 999
    # 인테이크 쪽: CONFIRMED·백링크·결정자·이벤트
    after = get(trade, body["id"])
    assert after["status"] == "CONFIRMED" and after["sales_order_id"] == out["sales_order_id"]
    assert after["decided_at"] is not None and after["decided_by_id"] is not None
    events = rows(
        "SELECT payload FROM events WHERE event_type = 'order_intakes.order_intake.status_changed'"
    )
    assert len(events) == 1 and set(events[0][0]) == {
        "intake_id",
        "from_status",
        "to_status",
        "buyer_partner_id",
        "assignee_id",
        "source_kind",
        "sales_order_id",
    }
    assert (
        scalar("SELECT count(*) FROM events WHERE event_type = 'sales_orders.sales_order.created'")
        == 1
    )


def test_later_master_changes_do_not_touch_the_created_so(trade: Any) -> None:
    """확정 뒤 마스터(품번 매핑·판가)를 바꿔도 SO는 불변이다 — 참조 복사(ADR-05)"""
    w = world()
    body = register(trade, w)
    out = confirm(trade, body).json()
    before = rows(
        "SELECT unit_price_amount, list_price_amount, buyer_item_code, sku_id FROM sales_order_lines WHERE so_id = :s",
        s=out["sales_order_id"],
    )
    set_price(w["sku_ids"][0], 5000, effective_from=date(2022, 1, 1))
    execute("UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p", p=w["buyer"])
    assert (
        rows(
            "SELECT unit_price_amount, list_price_amount, buyer_item_code, sku_id FROM sales_order_lines WHERE so_id = :s",
            s=out["sales_order_id"],
        )
        == before
    )


def test_price_moq_and_readiness_do_not_block_intake_confirmation(trade: Any) -> None:
    """가격 편차·MOQ·시장 준비도는 인테이크 시점에 **정보**일 뿐 접수 확정을 막지 않는다(SO 확정 시점에 판정) — 게이트 GET이 값을 보여 준다"""
    w = world()
    body = register(
        trade, w, lines=[line_body(w["codes"][0], quantity=1, unit_price="99.00")]
    )  # 기준가 10.00의 9.9배
    report = trade.get(f"{INTAKES}/{body['id']}/gates").json()
    price = [g for g in report["gates"] if g["gate_code"] == "PRICE_DEVIATION"]
    assert price and price[0]["level"] == "BLOCK" and price[0]["blocks_intake_confirm"] is False
    assert {g["gate_code"] for g in report["gates"]} <= {
        "ITEM_MAPPING",
        "DUPLICATE_PO",
        "PRICE_DEVIATION",
        "MARKET_READINESS",
        "MOQ",
    }  # 여신·PI 입금은 SO 확정 전용
    assert report["intake_confirmable"] is True and report["phase"] == "INTAKE"
    assert confirm(trade, body).status_code == 201


def test_discontinued_sku_is_a_warning_at_intake_and_still_confirmable(trade: Any) -> None:
    """단종 SKU는 접수 시점 WARN — 접수는 허용한다(A11-2: SO 확정에서 차단)"""
    w = world()
    body = register(trade, w)
    execute("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s", s=w["sku_ids"][0])
    report = trade.get(f"{INTAKES}/{body['id']}/gates").json()
    mapping = [g for g in report["gates"] if g["gate_code"] == "ITEM_MAPPING"]
    assert mapping[0]["level"] == "WARN" and report["intake_confirmable"] is True
    assert confirm(trade, body).status_code == 201


# ── 품번 매핑: 미매핑·STALE_MAPPING·resolve ─────────────────────────────────────


def test_unmapped_items_block_confirmation_with_422_and_nothing_is_created(trade: Any) -> None:
    """미매핑 라인이 있으면 확정 422 UNMAPPED_ITEMS — SO·번호·이벤트가 생기지 않고 인테이크는 PENDING (매핑을 등록하고 resolve하면 성공)"""
    w = world()
    sku_new = create_priced_sku()
    body = register(
        trade, w, lines=[line_body(w["codes"][0]), line_body("NEW-CODE", unit_price="10")]
    )
    assert body["lines"][1]["sku_id"] is None
    r = confirm(trade, body)
    assert r.status_code == 422 and code_of(r) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    assert r.json()["error"]["detail"]["lines"][0]["line_no"] == 2
    assert scalar("SELECT count(*) FROM sales_orders") == 0
    assert get(trade, body["id"])["status"] == "PENDING"
    # 매핑 등록(마스터) → 저장본이 낡았으니 409 STALE → resolve → 확정 성공
    map_buyer_item_code(w["buyer"], sku_new, "NEW-CODE")
    stale = confirm(trade, get(trade, body["id"]))
    assert stale.status_code == 409 and code_of(stale) == "ORDER_INTAKE.LINE.STALE_MAPPING"
    resolved = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": body["version"]}, headers=idem()
    )
    assert resolved.status_code == 200
    fresh = resolved.json()
    assert fresh["lines"][1]["sku_id"] == sku_new and fresh["version"] == body["version"] + 1
    assert confirm(trade, fresh).status_code == 201


def test_stale_mapping_when_the_mapping_changes_after_review(trade: Any) -> None:
    """검토 뒤 같은 품번이 다른 SKU로 매핑되면 409 STALE_MAPPING — SO는 만들어지지 않고, resolve 후에는 새 SKU로 확정된다"""
    w = world()
    body = register(trade, w)
    other = create_priced_sku()
    execute(
        "UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p AND buyer_item_code = :c",
        p=w["buyer"],
        c=w["codes"][0],
    )
    map_buyer_item_code(w["buyer"], other, w["codes"][0])
    shown = get(trade, body["id"])
    assert shown["lines"][0]["mapping_state"] == "STALE"  # 화면이 낡음을 미리 안다
    r = confirm(trade, shown)
    assert r.status_code == 409 and code_of(r) == "ORDER_INTAKE.LINE.STALE_MAPPING"
    assert scalar("SELECT count(*) FROM sales_orders") == 0
    resolved = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": shown["version"]}, headers=idem()
    ).json()
    assert (
        resolved["lines"][0]["sku_id"] == other
        and resolved["lines"][0]["mapping_state"] == "MAPPED"
    )
    done = confirm(trade, resolved)
    assert done.status_code == 201
    so_line = rows(
        "SELECT sku_id FROM sales_order_lines WHERE so_id = :s", s=done.json()["sales_order_id"]
    )
    assert so_line[0][0] == other


def test_stale_wins_over_a_discontinued_stored_sku(trade: Any) -> None:
    """저장본 SKU가 단종(접수 WARN)이어도 매핑이 바뀌었다면 낡은 검토다 — 단종 경고가 STALE을 가리지 않는다"""
    w = world()
    body = register(trade, w)
    execute("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s", s=w["sku_ids"][0])
    other = create_priced_sku()
    execute("UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p", p=w["buyer"])
    map_buyer_item_code(w["buyer"], other, w["codes"][0])
    r = confirm(trade, get(trade, body["id"]))
    assert r.status_code == 409 and code_of(r) == "ORDER_INTAKE.LINE.STALE_MAPPING"


def test_removed_mapping_and_deleted_sku_are_unmapped_422(trade: Any) -> None:
    """검토 뒤 매핑이 사라지거나 SKU가 삭제되면 422 UNMAPPED_ITEMS — 조용히 통과하지 않는다"""
    w = world()
    body = register(trade, w)
    execute("UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p", p=w["buyer"])
    r = confirm(trade, get(trade, body["id"]))
    assert r.status_code == 422 and code_of(r) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    w2 = world()
    body2 = register(trade, w2)
    execute("UPDATE skus SET deleted_at = now() WHERE id = :s", s=w2["sku_ids"][0])
    r2 = confirm(trade, get(trade, body2["id"]))
    assert r2.status_code == 422 and code_of(r2) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"


def test_duplicate_sku_lines_are_refused_with_the_line_numbers(trade: Any) -> None:
    """서로 다른 바이어 품번이 같은 SKU를 가리키면(SO는 SKU당 유상 1줄) 확정 422 DUPLICATE_SKU — 라인 번호를 알려 준다"""
    w = world()
    map_buyer_item_code(w["buyer"], w["sku_ids"][0], "ALIAS-CODE")
    body = register(trade, w, lines=[line_body(w["codes"][0]), line_body("ALIAS-CODE")])
    r = confirm(trade, body)
    assert r.status_code == 422 and code_of(r) == "ORDER_INTAKE.LINE.DUPLICATE_SKU"
    assert r.json()["error"]["detail"]["lines"] == [[1, 2]]
    assert scalar("SELECT count(*) FROM sales_orders") == 0
    # 양성 대조: 한 줄로 고치면 통과
    fixed = trade.patch(
        f"{INTAKES}/{body['id']}",
        json={
            "version": body["version"],
            "lines": [{"id": body["lines"][0]["id"], **line_body(w["codes"][0], quantity=10)}],
        },
    ).json()
    assert confirm(trade, fixed).status_code == 201


# ── 상태 전이 3값 ───────────────────────────────────────────────────────────────


def test_terminal_states_have_no_exit(trade: Any) -> None:
    """CONFIRMED·REJECTED는 종결이다 — 편집·거부·확정·재해석 전부 409 NOT_PENDING(양성 대조: PENDING에서는 전부 성공)"""
    w = world()
    pending = register(trade, w)
    assert (
        trade.patch(
            f"{INTAKES}/{pending['id']}",
            json={"version": pending["version"], "buyer_po_date": "2026-09-01"},
        ).status_code
        == 200
    )
    pending = get(trade, pending["id"])
    done = confirm(trade, pending).json()["intake"]
    rejected = trade.post(
        f"{INTAKES}/{register(trade, world())['id']}/reject",
        json={"version": 1, "reason": "취소된 주문"},
        headers=idem(),
    ).json()
    for terminal in (done, rejected):
        base = f"{INTAKES}/{terminal['id']}"
        v = terminal["version"]
        calls = [
            trade.patch(base, json={"version": v, "buyer_po_date": "2026-09-02"}),
            trade.post(
                f"{base}/reject", json={"version": v, "reason": "다시 거부 시도"}, headers=idem()
            ),
            trade.post(f"{base}/confirm", json={"version": v}, headers=idem()),
            trade.post(f"{base}/resolve", json={"version": v}, headers=idem()),
        ]
        for response in calls:
            assert (
                response.status_code == 409
                and code_of(response) == "ORDER_INTAKE.STATE.NOT_PENDING"
            ), response.text


def test_reject_requires_a_clean_reason_and_preserves_the_record(trade: Any) -> None:
    """거부 사유 5~500자(제어문자·채움 문자 불가) — 거부 후에도 행·원본·사유·행위자가 영구 보존된다(삭제 경로 없음)"""
    w = world()
    body = register(trade, w)
    base = f"{INTAKES}/{body['id']}/reject"
    for bad in ("짧음", "     ", "줄바꿈\n포함 사유입니다", "ㅤㅤㅤㅤㅤㅤ", "가" * 501):
        r = trade.post(base, json={"version": body["version"], "reason": bad}, headers=idem())
        assert r.status_code == 422, repr(bad)
    ok = trade.post(
        base, json={"version": body["version"], "reason": "  바이어가 취소 요청  "}, headers=idem()
    )
    assert ok.status_code == 200
    got = ok.json()
    assert got["status"] == "REJECTED" and got["reject_reason"] == "바이어가 취소 요청"
    assert got["decided_by_id"] is not None and got["original"] == body["original"]
    assert scalar(
        "SELECT count(*) FROM order_intake_lines WHERE intake_id = :i", i=body["id"]
    ) == len(body["lines"])
    assert trade.delete(f"{INTAKES}/{body['id']}").status_code in (404, 405)  # 삭제 엔드포인트 없음


def test_version_is_required_and_checked(trade: Any) -> None:
    """모든 변경·확정·거부·재해석은 version이 필수이고 낡으면 409 — 라인만 바꿔도 헤더 version이 오른다"""
    w = world()
    body = register(trade, w)
    base = f"{INTAKES}/{body['id']}"
    assert (
        trade.patch(base, json={"buyer_po_date": "2026-09-01"}).status_code == 422
    )  # version 없음
    line = body["lines"][0]
    changed = trade.patch(
        base,
        json={
            "version": body["version"],
            "lines": [{"id": line["id"], **line_body(w["codes"][0], quantity=9)}],
        },
    )
    assert (
        changed.status_code == 200 and changed.json()["version"] == body["version"] + 1
    )  # 라인만 바뀌어도 +1
    stale = trade.patch(base, json={"version": body["version"], "buyer_po_date": "2026-09-01"})
    assert stale.status_code == 409 and code_of(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert confirm(trade, body).status_code == 409  # 낡은 version으로 확정 불가(양성 대조는 최신본)
    assert confirm(trade, changed.json()).status_code == 201


# ── 편집 ─────────────────────────────────────────────────────────────────────


def test_edit_keeps_the_original_snapshot_and_allows_only_the_whitelist(trade: Any) -> None:
    """편집은 헤더(PO번호·PO일자·시장·담당자)와 라인 전체만 — 거래처·통화는 필드가 없고(422), 원본(original)은 수정 후에도 그대로다"""
    w = world(lines=2)
    body = register(trade, w, po_no="PO-ED-1")
    base = f"{INTAKES}/{body['id']}"
    for forbidden in (
        {"buyer_partner_id": 1},
        {"currency": "KRW"},
        {"status": "CONFIRMED"},
        {"buyer_po_no_key": "X"},
    ):
        r = trade.patch(base, json={"version": body["version"], **forbidden})
        assert r.status_code == 422, forbidden
    keep = body["lines"][0]
    edited = trade.patch(
        base,
        json={
            "version": body["version"],
            "buyer_po_no": "PO-ED-2",
            "lines": [
                {"id": keep["id"], **line_body(w["codes"][0], quantity=11, unit_price="12.34")},
                line_body(
                    w["codes"][1], quantity=2
                ),  # id 없음 = 새 라인(번호 3 — 결번 재사용 금지)
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    got = edited.json()
    assert got["buyer_po_no"] == "PO-ED-2" and got["original"] == body["original"]
    nos = [(ln["id"] == keep["id"], ln["line_no"], ln["quantity"]) for ln in got["lines"]]
    assert nos == [(True, 1, 11), (False, 3, 2)]  # 2번 라인은 제외, 새 라인은 3번
    assert got["lines"][0]["unit_price_amount"] == 1234
    assert got["last_line_no"] == 3
    # 같은 PO로의 편집은 점유 중이면 409, 자기 자신의 키는 통과
    register(trade, w, po_no="PO-TAKEN")
    taken = trade.patch(base, json={"version": got["version"], "buyer_po_no": "po-taken"})
    assert taken.status_code == 409 and code_of(taken) == DUP
    same = trade.patch(base, json={"version": got["version"], "buyer_po_no": "po-ed-2"})
    assert same.status_code == 200


def test_line_ids_of_another_intake_are_404(trade: Any) -> None:
    """다른 인테이크의 라인 id를 실으면 404(IDOR) — 존재 여부도 알리지 않고 아무것도 바뀌지 않는다"""
    w = world()
    a = register(trade, w)
    b = register(trade, w)
    r = trade.patch(
        f"{INTAKES}/{a['id']}",
        json={
            "version": a["version"],
            "lines": [{"id": b["lines"][0]["id"], **line_body(w["codes"][0], quantity=99)}],
        },
    )
    assert r.status_code == 404
    assert (
        get(trade, b["id"])["lines"][0]["quantity"] == 5
        and get(trade, a["id"])["version"] == a["version"]
    )


def test_resolve_without_changes_does_not_bump_the_version(trade: Any) -> None:
    """재해석에 바뀐 것이 없으면 version 불변(검토 내용을 무효화하지 않는다) — 같은 키 재전송은 최초 결과 재생"""
    w = world()
    body = register(trade, w)
    key = idem()
    first = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": body["version"]}, headers=key
    )
    again = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": body["version"]}, headers=key
    )
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert first.json()["version"] == body["version"]
    other = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": body["version"] + 5}, headers=key
    )
    assert other.status_code == 409 and code_of(other) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"


def test_handover_style_assignee_change_does_not_invalidate_the_review(trade: Any) -> None:
    """담당 이관(일괄 UPDATE)은 version을 올리지 않는다 — 이관 뒤에도 같은 version으로 확정된다"""
    w = world()
    body = register(trade, w)
    new_owner = scalar("SELECT id FROM users ORDER BY id DESC LIMIT 1")
    execute("UPDATE order_intakes SET assignee_id = :u WHERE id = :i", u=new_owner, i=body["id"])
    assert confirm(trade, body).status_code == 201


# ── 복제 재접수 (X-13) ──────────────────────────────────────────────────────────


def test_copied_from_so_must_be_a_cancelled_so_of_the_same_buyer(trade: Any) -> None:
    """copied_from_so_id는 같은 바이어의 **취소** SO만 — 살아 있는 SO·다른 바이어의 SO·없는 SO는 409(양성 대조: 자격 있는 원본은 통과)"""
    w = world()
    live = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=unique("PO"))
    other = world()
    foreign = create_direct_so(other["buyer"], other["sku_ids"], buyer_po_no=unique("PO"))
    _cancel_so(trade, foreign["id"])
    for bad in (live["id"], foreign["id"], 999_999):
        r = trade.post(INTAKES, json=intake_payload(w, copied_from_so_id=bad), headers=idem())
        assert r.status_code == 409 and code_of(r) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", bad
    _cancel_so(trade, live["id"])
    ok = register(trade, w, copied_from_so_id=live["id"])
    assert ok["copied_from_so_id"] == live["id"]


def test_confirm_copies_the_lineage_and_blocks_a_second_live_copy(trade: Any) -> None:
    """확정은 copied_from_so_id를 SO의 copied_from_id로 복사한다 — 같은 원본의 PENDING 복제는 하나뿐이고, 살아 있는 복제 SO가 있으면 재복제는 409"""
    w = world()
    src = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=unique("PO"))
    _cancel_so(trade, src["id"])
    first = register(trade, w, copied_from_so_id=src["id"])
    dup = trade.post(INTAKES, json=intake_payload(w, copied_from_so_id=src["id"]), headers=idem())
    assert (
        dup.status_code == 409 and code_of(dup) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    )  # PENDING 복제 유일(DB 유니크 번역)
    done = confirm(trade, first)
    assert done.status_code == 201
    assert (
        scalar(
            "SELECT copied_from_id FROM sales_orders WHERE id = :s", s=done.json()["sales_order_id"]
        )
        == src["id"]
    )
    again = trade.post(INTAKES, json=intake_payload(w, copied_from_so_id=src["id"]), headers=idem())
    assert again.status_code == 409  # 살아 있는 복제본이 있다


# ── 조회·페이지 ───────────────────────────────────────────────────────────────


def test_list_is_paginated_with_default_50_and_filters(trade: Any) -> None:
    """목록은 페이지(기본 50·최대 200 — 초과 422)·상태·바이어 필터를 지원한다"""
    w = world()
    for _ in range(3):
        register(trade, w)
    page = trade.get(INTAKES).json()
    assert page["size"] == 50 and page["total"] == 3 and len(page["items"]) == 3
    assert page["items"][0]["line_count"] == 1 and page["items"][0]["total_amount"] == 5000
    assert trade.get(f"{INTAKES}?size=201").status_code == 422
    assert trade.get(f"{INTAKES}?status=CONFIRMED").json()["total"] == 0
    assert trade.get(f"{INTAKES}?buyer_partner_id={w['buyer']}&size=2").json()["total"] == 3
    assert trade.get(f"{INTAKES}?status=BOGUS").status_code == 422
    assert trade.get(f"{INTAKES}/999999").status_code == 404


# ── G: DB 직행 불가 ────────────────────────────────────────────────────────────


@pytest.mark.group_g
def test_there_is_no_http_route_that_lands_an_intake_as_anything_but_pending(trade: Any) -> None:
    """HTTP로 CONFIRMED·REJECTED 인테이크를 직접 만드는 길이 없다 — 생성·편집 본문에 상태 필드가 없고, 모든 착지는 PENDING이다"""
    w = world()
    for _ in range(3):
        assert register(trade, w)["status"] == "PENDING"
    assert scalar("SELECT count(*) FROM order_intakes WHERE status <> 'PENDING'") == 0
    assert scalar("SELECT count(*) FROM order_intakes WHERE sales_order_id IS NOT NULL") == 0


def test_viewer_roles_cannot_write_and_see_no_cost_fields() -> None:
    """LOGISTICS·CERT·VIEWER는 쓰기 403(서비스도 재확인), 조회는 가능하며 응답에 원가·마진 필드가 없다"""
    w = world()
    with logged_in(RoleCode.TRADE) as trade_client:
        body = register(trade_client, w)
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert client.post(INTAKES, json=intake_payload(w), headers=idem()).status_code == 403
            assert (
                client.post(
                    f"{INTAKES}/{body['id']}/confirm", json={"version": 1}, headers=idem()
                ).status_code
                == 403
            )
            got = client.get(f"{INTAKES}/{body['id']}")
            assert got.status_code == 200
            text = got.text.lower()
            assert "cost" not in text and "margin" not in text and "purchase" not in text
            gates = client.get(f"{INTAKES}/{body['id']}/gates")
            assert gates.status_code == 200
    assert scalar("SELECT count(*) FROM sales_orders") == 0


def test_the_service_layer_rechecks_roles_independently_of_the_route_gate() -> None:
    """서비스 층 역할 사전 검증 — 라우트 게이트를 거치지 않고 서비스를 직접 불러도 무역·관리자가 아니면 거부(이중 방어). 착지·편집·재해석·거부·확정 전부"""
    from app.core.errors.exceptions import ForbiddenError
    from app.modules.identity.service import AuthenticatedUser
    from app.modules.order_intake import service as intake_service
    from app.modules.trade_chain import intake_flow
    from tests.factories.intake import land
    from tests.support.factories import create_user

    w = world()
    intake = land(w)
    viewer = AuthenticatedUser(
        id=create_user(f"{unique('v')}@example.com", roles=(RoleCode.VIEWER,)),
        email="v@example.com",
        display_name="열람",
        roles=frozenset({RoleCode.VIEWER}),
        session_id=0,
    )
    calls: list[Any] = [
        lambda: intake_service.create_manual_intake(
            actor=viewer, idempotency_key="k1", payload=intake_payload(w)
        ),
        lambda: intake_service.update_intake(
            actor=viewer, intake_id=intake["id"], payload={"version": 1}
        ),
        lambda: intake_service.resolve_intake(
            actor=viewer, idempotency_key="k2", intake_id=intake["id"], version=1
        ),
        lambda: intake_service.reject_intake(
            actor=viewer,
            idempotency_key="k3",
            intake_id=intake["id"],
            version=1,
            reason="열람자 거부 시도",
        ),
        lambda: intake_flow.confirm_intake(
            actor=viewer, idempotency_key="k4", intake_id=intake["id"], version=1
        ),
    ]
    for call in calls:
        with pytest.raises(ForbiddenError):
            call()
    assert scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"]) == "PENDING"
    assert scalar("SELECT count(*) FROM sales_orders") == 0


def test_the_reject_reason_is_validated_by_the_service_too() -> None:
    """거부 사유 검증은 서비스에도 있다(스키마를 거치지 않는 호출) — 짧은 사유는 422(AppError)이지 값 오류·DB 오류(500)가 아니다"""
    from app.core.errors.exceptions import AppError
    from app.modules.order_intake import service as intake_service
    from tests.factories.intake import land, trade_actor

    w = world()
    intake = land(w)
    for bad in ("짧음", "줄바꿈\n사유입니다"):
        with pytest.raises(AppError) as caught:
            intake_service.reject_intake(
                actor=trade_actor(),
                idempotency_key=unique("k"),
                intake_id=intake["id"],
                version=intake["version"],
                reason=bad,
            )
        assert caught.value.status_code == 422
    assert scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"]) == "PENDING"


def test_a_mapping_registered_after_review_to_a_discontinued_sku_is_still_stale(trade: Any) -> None:
    """검토 때 미매핑이던 품번에 뒤늦게 (단종) SKU 매핑이 생겨도 낡은 검토다 — 단종 경고(WARN)로 통과하지 않고 409 STALE_MAPPING"""
    w = world()
    body = register(trade, w, lines=[line_body(w["codes"][0]), line_body("LATE-CODE")])
    late_sku = create_priced_sku(status="DISCONTINUED")
    map_buyer_item_code(w["buyer"], late_sku, "LATE-CODE")
    r = confirm(trade, get(trade, body["id"]))
    assert r.status_code == 409 and code_of(r) == "ORDER_INTAKE.LINE.STALE_MAPPING"
    assert scalar("SELECT count(*) FROM sales_orders") == 0


def test_creation_is_idempotent_by_key(trade: Any) -> None:
    """같은 키·같은 본문 재전송=최초 결과 재생(인테이크 1건) — 같은 키+다른 본문은 409 KEY_CONFLICT, 키 헤더 없으면 400"""
    w = world()
    key = idem()
    payload = intake_payload(w, po_no="PO-IDEM-1")
    first = trade.post(INTAKES, json=payload, headers=key)
    again = trade.post(INTAKES, json=payload, headers=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert scalar("SELECT count(*) FROM order_intakes") == 1
    other = trade.post(INTAKES, json=intake_payload(w, po_no="PO-IDEM-2"), headers=key)
    assert other.status_code == 409 and code_of(other) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    assert trade.post(INTAKES, json=payload).status_code == 400


def test_handover_moves_pending_intakes_without_touching_the_version(trade: Any) -> None:
    """실제 담당 이관(reassign_all)이 인테이크 담당자를 옮긴다 — version 불변이라 검토 내용이 무효화되지 않는다"""
    from app.modules.handover.service import reassign_all
    from tests.support.factories import create_user

    w = world()
    body = register(trade, w)
    new_owner = create_user(f"{unique('ho')}@example.com", roles=(RoleCode.TRADE,))
    admin = create_user(f"{unique('ad')}@example.com", roles=(RoleCode.ADMIN,))
    result = reassign_all(
        from_user_id=body["assignee_id"], to_user_id=new_owner, actor_user_id=admin
    )
    assert result.moved["order_intakes"] == 1
    after = get(trade, body["id"])
    assert after["assignee_id"] == new_owner and after["version"] == body["version"]
    assert confirm(trade, after).status_code == 201


def test_query_counts_do_not_grow_with_the_number_of_lines_or_rows(trade: Any) -> None:
    """상세·게이트·목록의 질의 수는 라인 수·행 수와 무관하다(N+1 없음) — 라인 1개 대 40개, 인테이크 1건 대 10건"""
    from app.core.db.uow import unit_of_work  # noqa: F401
    from app.modules.identity.models import RoleCode as _R  # noqa: F401
    from app.modules.order_intake import service as intake_service
    from app.modules.trade_chain import intake_flow
    from tests.support.sqlcount import count_statements

    w1 = world(lines=1)
    small = register(trade, w1)
    w40 = world(lines=40)
    big = register(trade, w40)
    roles = frozenset({RoleCode.TRADE})
    d1 = count_statements(lambda: intake_service.get_intake(small["id"], roles=roles))
    d40 = count_statements(lambda: intake_service.get_intake(big["id"], roles=roles))
    g1 = count_statements(lambda: intake_flow.get_intake_gates(intake_id=small["id"], roles=roles))
    g40 = count_statements(lambda: intake_flow.get_intake_gates(intake_id=big["id"], roles=roles))
    assert d1 > 0 and g1 > 0
    assert d1 == d40, (d1, d40)
    assert g1 == g40, (g1, g40)
    l1 = count_statements(lambda: intake_service.list_intakes(offset=0, limit=50, roles=roles))
    for _ in range(9):
        register(trade, world())
    l10 = count_statements(lambda: intake_service.list_intakes(offset=0, limit=50, roles=roles))
    assert l1 == l10, (l1, l10)
