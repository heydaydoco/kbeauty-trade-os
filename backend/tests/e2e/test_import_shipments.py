"""A·G·K. 수입선적 API — PO 참조 생성·배정 가능량·PO 잔량/상태 불변·원가 비복사·응답 갈래 (S3-2 PR-5a / GC-A15·G3 / ADR-0024·0074·0077·0079 /
design-A A3·A4 / design-C C8·J-06·K-07 / design-D D3·S5·S6 / R-3c-2).

수입선적은 PO 원가를 복사하지 않는다(단가 NULL·금액 0 — M14 CHECK). 응답은 그 0을 '0.00'으로 그리지 않고 **금액·통화 계열 키를 아예 싣지 않는다**
(null이 아니라 미포함 — design-D D3). PO 통화·환율은 PO CostHidden이 원가 비열람 역할에게 가리는 필드라 전 역할 같은 모양이다(원가 열람 역할로도 0).
수입선적은 PO 라인을 **IN_TRANSIT**로 소비한다 — PO 잔량(입고에서만 준다)·PO 상태는 그대로이고 **배정 가능량**만 준다(초과 409 EXCEEDS_ASSIGNABLE).
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.purchase_orders import service as po_service
from app.modules.trade_docs.quantities import ASSIGNABLE_KINDS, open_quantity
from tests.factories.shipments import (
    PO,
    SHIPMENTS,
    cancel,
    confirmed_so,
    create_import_shipment,
    created,
    created_import,
    import_body,
    raw_shipment,
    release,
    rows,
    scalar,
    shipment_version,
)
from tests.factories.trade import (
    SENTINEL_UNIT_COST,
    SENTINEL_UNIT_COST_TEXT,
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    idem,
    logged_in,
)
from tests.support.kst import pin_today_kst

pytestmark = pytest.mark.group_a

#: PO 원가 마스킹 정규식(설계 F5(f) — PO CostHidden 스캔과 같은 식).
COST_KEY = re.compile(r"(_cost$|^cost$|^currency$|^price_)")
#: 수입선적 응답에 **있으면 안 되는** 금액·통화 계열 키(PO CostHidden 추가 목록 + 선적 라인 판매가 축 키).
IMPORT_ABSENT_KEYS = frozenset(
    {
        "currency",
        "minor_units",
        "fx_rate",
        "fx_rate_date",
        "total_amount",
        "total_text",
        "unit_price_amount",
        "unit_price_text",
        "line_amount",
        "line_amount_text",
        "is_free",
    }
)
#: 수출선적 응답에는 **있어야 하는** 키(갈래가 수출을 잘라 먹지 않음 — 판별자 합집합의 양성 대조).
EXPORT_PRESENT_KEYS = frozenset(
    {"currency", "minor_units", "fx_rate", "total_amount", "total_text"}
)
SENTINELS = (str(SENTINEL_UNIT_COST), SENTINEL_UNIT_COST_TEXT)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정 — 선적 흐름·마일스톤·통관 import 지점 + PO 서비스(증빙일 기본값)·이 모듈의 `today_kst`를 같은 날로 맞춘다
    (PO 환율일 ≤ 선적 증빙일 CHECK가 실행 중 자정을 넘겨도 어긋나지 않게)."""
    pin_today_kst(monkeypatch, sys.modules[__name__], po_service)


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def scan_keys(node: Any) -> set[str]:
    """JSON을 재귀로 훑어 모든 키를 모은다(중첩 라인·출처·보드 포함)."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= scan_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= scan_keys(item)
    return keys


def leaked_keys(body: Any) -> set[str]:
    keys = scan_keys(body)
    return {k for k in keys if COST_KEY.search(k)} | (keys & IMPORT_ABSENT_KEYS)


def sentinel_po(
    client: TestClient,
    *,
    quantities: tuple[int, ...] = (100,),
    supplier: int | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """발행 PO — 라인마다 센티널 매입 단가(원가 열람 역할에게만 보이는 값)·지정 수량. 응답 본문(무역 = 원가 포함)."""
    lines = [
        {
            "sku_id": create_purchase_priced_sku(),
            "quantity": qty,
            "unit_cost": SENTINEL_UNIT_COST_TEXT,
        }
        for qty in quantities
    ]
    return create_po_via_api(client, supplier or create_supplier(), [], lines=lines, **overrides)


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _po_row(po_id: int) -> dict[str, Any]:
    return rows(
        "SELECT status, version, updated_at, (SELECT count(*) FROM purchase_order_status_log"
        " WHERE purchase_order_id = :i) AS log_count FROM purchase_orders WHERE id = :i",
        i=po_id,
    )[0]


def _po_open(line_id: int) -> tuple[int, int]:
    """(PO 잔량[기본 FULFILL], 배정 가능량[IN_TRANSIT]) — 같은 함수(`open_quantity`)의 두 kind."""
    with unit_of_work() as uow:
        fulfil = open_quantity(uow.session, "PO_LINE", [line_id])[line_id].open
        assignable = open_quantity(uow.session, "PO_LINE", [line_id], kinds=ASSIGNABLE_KINDS)[
            line_id
        ].open
    return fulfil, assignable


def _raw_import_line(shipment_id: int, po_line: dict[str, Any], quantity: int) -> None:
    """원시 수입선적 라인(원가 비복사 — 단가 NULL·금액 0·무상 false, M14 CHECK `import_no_price`)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO shipment_lines (shipment_id, line_no, po_line_id, sku_id, sku_code, sku_name_ko,"
                " sku_name_en, sku_kind, currency, quantity, unit_price_amount, is_free, line_amount)"
                " SELECT :s, 1, :p, sku_id, sku_code, sku_name_ko, sku_name_en, sku_kind,"
                " (SELECT currency FROM shipments WHERE id = :s), :q, NULL, false, 0"
                " FROM purchase_order_lines WHERE id = :p"
            ),
            {"s": shipment_id, "p": po_line["id"], "q": quantity},
        )
        connection.execute(
            text("UPDATE shipments SET last_line_no = 1 WHERE id = :s"), {"s": shipment_id}
        )


# ── A. GC-A15 — PO 잔량·상태 불변 / 배정 가능량 초과 409 ─────────────────────────────


@pytest.mark.golden
def test_gc_a15_import_shipment_never_changes_po_open_quantity_or_status(
    trade: TestClient,
) -> None:
    """GC-A15 — PO 라인 100: 수입선적 60 → PO 잔량(FULFILL) **100 그대로**·PO 상태·version·이력·updated_at 불변·배정 가능량 40 /
    같은 라인 41 → 409 `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE`(PO 잔량 코드 EXCEEDS_OPEN과 다름 — detail 배정 가능량 40) / 40은 통과(0) /
    수입선적 취소 → 배정 가능량 복원 / 전 과정에서 PO 행·이력 무변경"""
    po = sentinel_po(trade, quantities=(100,))
    line = po["lines"][0]["id"]
    before = _po_row(po["id"])
    first = created_import(trade, po["id"], [(line, 60)])
    assert first["shipment_kind"] == "IMPORT" and first["status"] == "PLANNED"
    assert first["source"] == {
        "kind": "PURCHASE_ORDER",
        "id": po["id"],
        "doc_number": po["doc_number"],
        "status": "ISSUED",
    }
    assert first["lines"][0]["source_line"]["remaining_after"] == 40
    assert _po_open(line) == (100, 40)  # PO 잔량은 입고에서만 준다 — 배정 가능량만 40
    assert _po_row(po["id"]) == before
    over = create_import_shipment(trade, po["id"], [(line, 41)])
    assert over.status_code == 409 and _code(over) == "SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE"
    assert over.json()["error"]["detail"] == {"assignable_quantity": {str(line): 40}}
    second = created_import(trade, po["id"], [(line, 40)])  # 정확히 배정 가능량 — 통과
    assert _po_open(line) == (100, 0)
    assert _code(create_import_shipment(trade, po["id"], [(line, 1)])) == (
        "SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE"
    )
    assert cancel(trade, first["id"]).status_code == 200
    assert _po_open(line) == (100, 60)  # 취소 → 배정 가능량 복원(파생)
    assert cancel(trade, second["id"]).status_code == 200
    assert _po_open(line) == (100, 100)
    assert (
        _po_row(po["id"]) == before
    )  # 생성·초과·취소 내내 PO 행·이력 무변경(4금 ① — 발주 확정 무접촉)
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'purchase_orders' AND aggregate_id = :i",
            i=po["id"],
        )
        == 1  # 발행(created) 1건뿐 — 수입선적이 PO 이벤트를 만들지 않는다
    )


def test_import_shipment_copies_the_po_header_but_never_its_cost(trade: TestClient) -> None:
    """원천 사본 — 통화(DB)·고정 환율·결제조건·Incoterms·거래 상대(공급사)·담당자 = PO 값, 증빙일 = 오늘(KST — R-15), 송하인 = 공급사 영문 자동 스냅샷,
    라인 = SKU·수량만(단가 NULL·금액 0·무상 false — 원가 비복사), 헤더 합계 0(CHECK import_has_no_amount)"""
    supplier = create_supplier(name_en="Guangzhou Synthetic Packaging Co.")
    po = sentinel_po(trade, quantities=(30, 20), supplier=supplier)
    po_row = rows("SELECT * FROM purchase_orders WHERE id = :i", i=po["id"])[0]
    body = created_import(
        trade,
        po["id"],
        [(po["lines"][1]["id"], 5), (po["lines"][0]["id"], 7)],
        internal_note=" 1차 ",
    )
    assert body["doc_date"] == today_kst().isoformat()
    assert body["counterparty"] == {"partner_id": supplier, "name": po_row["supplier_name"]}
    assert body["payment_terms"]["payment_type"] == po_row["payment_type"]
    assert body["payment_terms"]["balance_anchor"] == po_row["balance_anchor"]
    assert body["incoterm"] == {"code": "EXW", "place": "Seoul", "year": 2020}
    assert body["assignee"]["id"] == po_row["assignee_id"]
    assert body["internal_note"] == "1차"
    assert (
        [(line["po_line_id"], line["quantity"]) for line in body["lines"]]
        == [
            (po["lines"][0]["id"], 7),
            (po["lines"][1]["id"], 5),
        ]
    )  # 요청 순서가 아니라 원천(PO) 라인 번호 순으로 선적 라인 번호가 매겨진다(수출 SO 라인과 같은 규칙)
    shipper = [p for p in body["parties"] if p["auto"]]
    assert shipper == [
        {
            "id": shipper[0]["id"],
            "role": "SHIPPER",
            "partner_id": supplier,
            "name_en": "Guangzhou Synthetic Packaging Co.",
            "address_en": None,
            "auto": True,
            "version": 1,
        }
    ]
    stored = rows("SELECT * FROM shipments WHERE id = :i", i=body["id"])[0]
    assert stored["currency"] == po_row["currency"] and stored["fx_rate"] == po_row["fx_rate"]
    assert stored["fx_rate_date"] == po_row["fx_rate_date"] and stored["total_amount"] == 0
    assert stored["po_id"] == po["id"] and stored["so_id"] is None
    lines = rows(
        "SELECT unit_price_amount, line_amount, is_free, currency FROM shipment_lines WHERE shipment_id = :i",
        i=body["id"],
    )
    assert (
        lines
        == [{"unit_price_amount": None, "line_amount": 0, "is_free": False, "currency": "USD"}] * 2
    )


# ── G. GC-G3 — 원가 비복사(원가 열람 역할로도 응답·CSV·outbox·멱등 저장 본문 0) ────────────────────────


@pytest.mark.golden
@pytest.mark.group_g
def test_gc_g3_no_cost_key_or_sentinel_reaches_any_import_surface() -> None:
    """GC-G3 — 센티널 원가 PO로 수입선적을 만들고 **원가 열람 역할(관리자·무역·물류·인증)**과 조회 역할로 상세·목록·CSV를, 무역·관리자로 미리보기·
    생성 응답을 본다 → 원가 키(`*_cost`·`currency`·`price_*`)·금액·통화 계열 키 0, 센티널 값 0회. outbox payload·멱등 저장 본문(at-rest)도 같다.
    선적 라인은 단가 NULL·금액 0(복사 자체가 없다 — 마스킹 분기가 아님, ADR-0024 10번째 채널 미개설)"""
    with logged_in(RoleCode.TRADE) as trade:
        po = sentinel_po(trade, quantities=(3, 6))
        assert (
            po["lines"][0]["unit_cost"] == SENTINEL_UNIT_COST
        )  # 센티널 실재(0회 단언 공회전 방지)
        lines = [(po["lines"][0]["id"], 2), (po["lines"][1]["id"], 6)]
        preview = trade.post(f"{PO}/{po['id']}/shipments/preview", json=import_body(lines))
        assert preview.status_code == 200, preview.text
        assert leaked_keys(preview.json()) == set()
        assert [ln["assignable_before"] for ln in preview.json()["lines"]] == [3, 6]
        assert [ln["remaining_after"] for ln in preview.json()["lines"]] == [1, 0]
        key = idem()
        created_response = trade.post(
            f"{PO}/{po['id']}/shipments", json=import_body(lines), headers=key
        )
        assert created_response.status_code == 201, created_response.text
        body = created_response.json()
        replay = trade.post(f"{PO}/{po['id']}/shipments", json=import_body(lines), headers=key)
        assert replay.status_code == 201 and replay.json()["id"] == body["id"]
        surfaces = [preview.text, created_response.text, replay.text]
        assert leaked_keys(body) == set() and leaked_keys(replay.json()) == set()
    for role in RoleCode:
        with logged_in(role) as client:
            detail = client.get(f"{SHIPMENTS}/{body['id']}")
            listing = client.get(SHIPMENTS, params={"po_id": po["id"]})
            csv = client.get(f"{SHIPMENTS}/export.csv", params={"po_id": po["id"]})
            assert detail.status_code == listing.status_code == csv.status_code == 200, role
            assert leaked_keys(detail.json()) == set(), (role, leaked_keys(detail.json()))
            assert all(leaked_keys(item) == set() for item in listing.json()["items"]), role
            surfaces += [detail.text, listing.text, csv.text]
    for blob in surfaces:
        for sentinel in SENTINELS:
            assert sentinel not in blob
    events = rows(
        "SELECT payload FROM events WHERE aggregate_type = 'shipments' AND aggregate_id = :i",
        i=body["id"],
    )
    assert events and all(not leaked_keys(e["payload"]) for e in events)
    assert {k for e in events for k in e["payload"]} <= {
        "doc_type",
        "doc_id",
        "doc_number",
        "from_status",
        "to_status",
        "automatic",
        "assignee_id",
        "partner_id",
        "shipment_kind",
        "so_id",
        "po_id",
    }
    stored = rows(
        "SELECT response_body FROM idempotency_keys WHERE endpoint = 'POST /api/v1/purchase-orders/{id}/shipments'"
        " AND response_body ->> 'id' = :i",
        i=str(body["id"]),
    )
    assert len(stored) == 1 and leaked_keys(stored[0]["response_body"]) == set()
    for sentinel in SENTINELS:
        assert sentinel not in json.dumps(stored[0]["response_body"])
    assert rows(
        "SELECT count(*) AS n FROM shipment_lines WHERE shipment_id = :i"
        " AND (unit_price_amount IS NOT NULL OR line_amount <> 0 OR is_free)",
        i=body["id"],
    ) == [{"n": 0}]


@pytest.mark.group_g
def test_the_service_layer_omits_import_amount_keys_before_any_schema_applies(
    trade: TestClient,
) -> None:
    """G3 2중 — 응답 모델(합집합)이 걸러 주기 **전에** 서비스 dict부터 수입선적 금액·통화 키를 만들지 않는다(만든 뒤 지우지 않는다 — PO
    `include_cost` 관례): 상세 조립·목록 행·미리보기 dict를 직접 본다(스키마가 조용히 버리는 키도 여기서 잡힌다)"""
    from app.modules.trade_chain import shipment_flow, shipment_view
    from tests.factories.approvals import make_user

    po = sentinel_po(trade, quantities=(6,))
    shipment = created_import(trade, po["id"], [(po["lines"][0]["id"], 2)])
    actor = make_user(RoleCode.ADMIN)  # 원가 열람 역할 — 역할 분기가 없음을 함께 본다
    preview = shipment_flow.preview_shipment_from_purchase_order(
        actor=actor, po_id=po["id"], payload=import_body([(po["lines"][0]["id"], 1)])
    )
    detail = shipment_view.get_shipment(shipment["id"], actor.roles)
    items, _total = shipment_view.list_shipments(offset=0, limit=50, po_id=po["id"])
    for label, payload in (("preview", preview), ("detail", detail), ("list", items)):
        assert leaked_keys(payload) == set(), (label, leaked_keys(payload))


@pytest.mark.group_k
@pytest.mark.group_g
def test_import_rows_carry_no_amount_or_currency_keys_for_any_role(trade: TestClient) -> None:
    """R-3c-2 해소·G3 — 원시 수입선적 행(생성 경로를 거치지 않은 행)도 상세·목록 응답에 통화·소수 자릿수·환율·합계·라인 단가/금액/무상 키가 **없다**
    (조립 갈래가 구분으로 정해진다 — 생성 경로에 기대지 않는다). 같은 목록의 수출 행은 금액 키를 그대로 싣는다(양성 대조)"""
    po = sentinel_po(trade, quantities=(9,))
    holder = confirmed_so((3,))
    export = created(trade, holder["id"], [(holder["line_ids"][0], 1)])
    imported = raw_shipment(holder["id"], kind="IMPORT", po_id=po["id"])
    _raw_import_line(imported, po["lines"][0], 4)
    for role in RoleCode:
        with logged_in(role) as client:
            detail = client.get(f"{SHIPMENTS}/{imported}")
            assert detail.status_code == 200, (role, detail.text)
            body = detail.json()
            assert body["shipment_kind"] == "IMPORT" and len(body["lines"]) == 1
            assert leaked_keys(body) == set(), (role, leaked_keys(body))
            assert body["lines"][0]["source_line"] == {
                "id": po["lines"][0]["id"],
                "line_no": 1,
                "quantity": 9,
                "remaining_after": 5,  # 배정 가능량 = 9 − 살아 있는 수입선적 4
            }
            listing = client.get(SHIPMENTS, params={"shipment_kind": "IMPORT"})
            [item] = [i for i in listing.json()["items"] if i["id"] == imported]
            assert leaked_keys(item) == set(), (role, item)
            for sentinel in SENTINELS:
                assert sentinel not in detail.text and sentinel not in listing.text, role
            exported = client.get(f"{SHIPMENTS}/{export['id']}").json()
            assert set(exported) >= EXPORT_PRESENT_KEYS, role  # 수출은 판매가 축 그대로
            assert {"unit_price_amount", "line_amount", "currency"} <= set(exported["lines"][0])
            [export_item] = [
                i
                for i in client.get(SHIPMENTS, params={"so_id": holder["id"]}).json()["items"]
                if i["id"] == export["id"]
            ]
            assert {"currency", "total_amount", "total_text"} <= set(export_item), role


# ── A. 부분 수입선적·라인 편집·멱등·미리보기 ─────────────────────────────────────────


def test_partial_import_shipments_split_the_assignable_quantity_and_lines_edit_within_it(
    trade: TestClient,
) -> None:
    """부분 수입선적 1:N — 같은 PO의 두 라인을 두 선적이 나눠 싣고, 라인 추가·수량 수정은 배정 가능량(+ 이 라인 현재 수량) 안에서,
    초과는 409 EXCEEDS_ASSIGNABLE, 다른 PO의 라인은 422 LINE_MISMATCH('발주'), 같은 원천 라인은 409 DUPLICATE_SOURCE, 라인 제외는 배정 가능량을 복원한다"""
    po = sentinel_po(trade, quantities=(10, 8))
    a, b = (line["id"] for line in po["lines"])
    other = sentinel_po(trade, quantities=(5,))
    shipment = created_import(trade, po["id"], [(a, 4)])
    created_import(trade, po["id"], [(a, 3), (b, 2)])
    assert _po_open(a) == (10, 3) and _po_open(b) == (8, 6)

    def add(source: int, quantity: int) -> Any:
        return trade.post(
            f"{SHIPMENTS}/{shipment['id']}/lines",
            json={
                "version": shipment_version(shipment["id"]),
                "source_line_id": source,
                "quantity": quantity,
            },
            headers=idem(),
        )

    over = add(b, 7)
    assert over.status_code == 409 and over.json()["error"]["detail"] == {
        "assignable_quantity": {str(b): 6}
    }
    mismatch = add(other["lines"][0]["id"], 1)
    assert mismatch.status_code == 422 and _code(mismatch) == "SHIPMENTS.SOURCE.LINE_MISMATCH"
    assert mismatch.json()["error"]["detail"] == {"source_line_id": "이 발주의 라인이 아닙니다."}
    added = add(b, 6)
    assert added.status_code == 201, added.text
    assert _po_open(b) == (8, 0)
    assert _code(add(a, 1)) == "SHIPMENTS.LINE.DUPLICATE_SOURCE"
    line_a = next(ln for ln in added.json()["lines"] if ln["po_line_id"] == a)
    line_b = next(ln for ln in added.json()["lines"] if ln["po_line_id"] == b)

    def patch(line_id: int, quantity: int) -> Any:
        return trade.patch(
            f"{SHIPMENTS}/{shipment['id']}/lines/{line_id}",
            json={"version": shipment_version(shipment["id"]), "quantity": quantity},
        )

    over_edit = patch(line_a["id"], 8)  # 상한 = 배정 가능량 3 + 이 라인 4 = 7
    assert over_edit.status_code == 409 and over_edit.json()["error"]["detail"] == {
        "assignable_quantity": {str(a): 7}
    }
    assert patch(line_a["id"], 7).status_code == 200 and _po_open(a) == (10, 0)
    removed = trade.delete(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line_b['id']}",
        params={"version": shipment_version(shipment["id"])},
    )
    assert removed.status_code == 200 and _po_open(b) == (8, 6)
    assert _po_row(po["id"])["status"] == "ISSUED"


def test_preview_saves_nothing_and_matches_creation_checks(trade: TestClient) -> None:
    """미리보기 — 저장·채번·이벤트·멱등 키 0, 생성과 같은 검증(초과 409·소속 422)·같은 값(배정 가능량 전/후·자동 송하인)"""
    po = sentinel_po(trade, quantities=(12,))
    line = po["lines"][0]["id"]
    counts = (
        "SELECT (SELECT count(*) FROM shipments) AS s, (SELECT count(*) FROM events) AS e,"
        " (SELECT count(*) FROM idempotency_keys) AS k"
    )
    before = rows(counts)
    preview = trade.post(f"{PO}/{po['id']}/shipments/preview", json=import_body([(line, 5)]))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["po_id"] == po["id"] and body["po_doc_number"] == po["doc_number"]
    assert body["shipment_kind"] == "IMPORT" and body["doc_date"] == today_kst().isoformat()
    assert body["lines"][0]["assignable_before"] == 12 and body["lines"][0]["remaining_after"] == 7
    assert [p["role"] for p in body["parties"] if p["auto"]] == ["SHIPPER"]
    over = trade.post(f"{PO}/{po['id']}/shipments/preview", json=import_body([(line, 13)]))
    assert over.status_code == 409 and _code(over) == "SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE"
    assert rows(counts) == before


# ── A·K. 오류 우선순위·입력 검증·상태 ──────────────────────────────────────────────


def test_import_errors_follow_404_409_422_and_reject_bad_inputs(trade: TestClient) -> None:
    """ADR-0079 ⑧ 404 → 409 → 422 — 없는 PO + 잘못된 역할 = 404 / 취소 PO + 잘못된 역할 = 409 DOCUMENT_NOT_CONSUMABLE /
    수하인·송하인 직접 지정 = 422 ROLE_NOT_ALLOWED(수입 문구) / 다른 PO 라인 = 422 LINE_MISMATCH / 같은 라인 2번 = 422 /
    공급사 영문명 결측 = 422 ENGLISH_NAME_MISSING(po_id) / 공급사 유형 해제 = 422 INVALID_FIELD(po_id) / 메모의 보이지 않는 글자 = 422 —
    전부 저장 0"""
    po = sentinel_po(trade, quantities=(5,))
    line = po["lines"][0]["id"]
    bad_role = [{"role": "CONSIGNEE", "partner_id": create_supplier(types=("FORWARDER",))}]
    missing = create_import_shipment(trade, 999_999_999, [(line, 1)], parties=bad_role)
    assert missing.status_code == 404
    cancelled = sentinel_po(trade, quantities=(5,))
    assert (
        trade.post(
            f"{PO}/{cancelled['id']}/transitions",
            json={"to": "CANCELLED", "version": cancelled["version"], "reason": "발주 철회"},
            headers=idem(),
        ).status_code
        == 200
    )
    blocked = create_import_shipment(
        trade, cancelled["id"], [(cancelled["lines"][0]["id"], 1)], parties=bad_role
    )
    assert blocked.status_code == 409 and _code(blocked) == (
        "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
    )
    for role in ("CONSIGNEE", "SHIPPER"):
        response = create_import_shipment(
            trade,
            po["id"],
            [(line, 1)],
            parties=[{"role": role, "partner_id": bad_role[0]["partner_id"]}],
        )
        assert response.status_code == 422 and _code(response) == "SHIPMENTS.PARTY.ROLE_NOT_ALLOWED"
        assert "발주 공급사 자동" in response.json()["error"]["detail"]["parties[0].role"]
    other = sentinel_po(trade, quantities=(5,))
    mismatch = create_import_shipment(trade, po["id"], [(other["lines"][0]["id"], 1)])
    assert mismatch.status_code == 422 and mismatch.json()["error"]["detail"] == {
        "lines[0].po_line_id": "이 발주의 라인이 아닙니다."
    }
    twice = create_import_shipment(trade, po["id"], [(line, 1), (line, 1)])
    assert twice.status_code == 422 and twice.json()["error"]["detail"].keys() == {"lines"}
    nameless = sentinel_po(trade, quantities=(5,), supplier=create_supplier(name_en=None))
    no_name = create_import_shipment(trade, nameless["id"], [(nameless["lines"][0]["id"], 1)])
    assert no_name.status_code == 422 and _code(no_name) == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"
    assert no_name.json()["error"]["detail"] == {"po_id": "거래처 영문명을 확인해 주세요."}
    with (
        owner_engine.begin() as connection
    ):  # 공급사 유형 해제 + 다른 유형(바이어)만 남김 — '유형 무관 통과' 회귀를 잡는다
        connection.execute(
            text("UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p"),
            {"p": other["supplier_partner_id"]},
        )
        connection.execute(
            text("INSERT INTO partner_type_links (partner_id, type_code) VALUES (:p, 'BUYER')"),
            {"p": other["supplier_partner_id"]},
        )
    revoked = create_import_shipment(trade, other["id"], [(other["lines"][0]["id"], 1)])
    assert revoked.status_code == 422 and _code(revoked) == "COMMON.VALIDATION.INVALID_FIELD"
    assert list(revoked.json()["error"]["detail"]) == ["po_id"]
    for note in ("메모\u200b", "a\x00b", "\u3164"):
        bad_note = create_import_shipment(trade, po["id"], [(line, 1)], internal_note=note)
        assert bad_note.status_code == 422 and list(bad_note.json()["error"]["detail"]) == [
            "internal_note"
        ], note
    multi_line = created_import(trade, po["id"], [(line, 1)], internal_note="첫 줄\n둘째\t줄")
    assert multi_line["internal_note"] == "첫 줄\n둘째\t줄"  # 줄바꿈·탭은 메모에 허용
    assert (
        int(
            scalar(
                "SELECT count(*) FROM shipments WHERE po_id IN (:a, :b, :c)",
                a=cancelled["id"],
                b=other["id"],
                c=nameless["id"],
            )
        )
        == 0
    )


def test_supplier_confirmed_po_is_consumable_and_import_milestones_and_customs_work(
    trade: TestClient,
) -> None:
    """공급사 확인(OC) PO도 소비 가능 — 수입선적 마일스톤(ETA 계획 → 목록 eta)·통관(수입신고만 — 수출신고 422 KIND_MISMATCH, R-4a-4 재판정 증거)·
    출고지시·취소가 수출과 같은 통로로 동작하고 PO는 그대로다"""
    po = sentinel_po(trade, quantities=(20,))
    confirmed = trade.post(
        f"{PO}/{po['id']}/transitions",
        json={
            "to": "SUPPLIER_CONFIRMED",
            "version": po["version"],
            "oc_received_on": today_kst().isoformat(),
        },
        headers=idem(),
    )
    assert confirmed.status_code == 200, confirmed.text
    shipment = created_import(trade, po["id"], [(po["lines"][0]["id"], 20)])
    board = {row["milestone_type"]: row for row in shipment["milestones"]["rows"]}
    assert board["PSI"]["applicable"] is False and board["IMPORT_TAX_DUE"]["applicable"] is True
    eta = (today_kst()).isoformat()
    planned = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/milestones/ETA/plan",
        json={"planned_on": eta},
        headers=idem(),
    )
    assert planned.status_code == 200, planned.text
    [item] = trade.get(SHIPMENTS, params={"po_id": po["id"]}).json()["items"]
    assert item["eta"] == {"value": eta, "basis": "PLANNED"}
    wrong_kind = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/customs-records",
        json={"declaration_kind": "EXPORT", "declaration_no": "EX-1", "declared_on": eta},
        headers=idem(),
    )
    assert wrong_kind.status_code == 422 and _code(wrong_kind) == "SHIPMENTS.CUSTOMS.KIND_MISMATCH"
    right_kind = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/customs-records",
        json={"declaration_kind": "IMPORT", "declaration_no": "IM-1", "declared_on": eta},
        headers=idem(),
    )
    assert right_kind.status_code == 201, right_kind.text
    assert release(trade, shipment["id"]).status_code == 200
    blocked = cancel(trade, shipment["id"])
    assert blocked.status_code == 409 and _code(blocked) == (
        "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE"
    )
    assert _po_row(po["id"])["status"] == "SUPPLIER_CONFIRMED"


@pytest.mark.group_k
def test_logistics_cannot_create_import_shipments_but_reads_and_operates_them(
    trade: TestClient,
) -> None:
    """ADR-0079 — 수입선적 생성·미리보기는 무역(배정 가능량 소비 = 상업 사실) — 물류·인증·조회 403(부작용 0), 물류는 조회·출고지시 가능"""
    po = sentinel_po(trade, quantities=(4,))
    line = po["lines"][0]["id"]
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert create_import_shipment(client, po["id"], [(line, 1)]).status_code == 403
            preview = client.post(
                f"{PO}/{po['id']}/shipments/preview", json=import_body([(line, 1)])
            )
            assert preview.status_code == 403
    assert int(scalar("SELECT count(*) FROM shipments WHERE po_id = :p", p=po["id"])) == 0
    shipment = created_import(trade, po["id"], [(line, 4)])
    with logged_in(RoleCode.LOGISTICS) as logistics:
        detail = logistics.get(f"{SHIPMENTS}/{shipment['id']}").json()
        assert (
            "RELEASE_ORDER" in detail["allowed_actions"]
            and "EDIT_LINES" not in detail["allowed_actions"]
        )
        assert release(logistics, shipment["id"]).status_code == 200


def test_a_live_import_shipment_blocks_cancelling_the_po_until_it_is_cancelled(
    trade: TestClient,
) -> None:
    """역순 취소(CHILD_LINKS PO→선적) — 살아 있는 수입선적이 있는 PO 취소 = 409 SUCCESSOR_ALIVE(detail 선적 번호), 선적 취소 뒤 PO 취소 통과"""
    po = sentinel_po(trade, quantities=(4,))
    shipment = created_import(trade, po["id"], [(po["lines"][0]["id"], 4)])

    def cancel_po() -> Any:
        version = int(scalar("SELECT version FROM purchase_orders WHERE id = :i", i=po["id"]))
        return trade.post(
            f"{PO}/{po['id']}/transitions",
            json={"to": "CANCELLED", "version": version, "reason": "발주 철회"},
            headers=idem(),
        )

    blocked = cancel_po()
    assert blocked.status_code == 409 and _code(blocked) == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert blocked.json()["error"]["detail"] == {"successors": [shipment["doc_number"]]}
    assert cancel(trade, shipment["id"]).status_code == 200
    assert cancel_po().status_code == 200


# ── PO 상세 — 배정 가능량·입고예정(계산값, 열 없음 — design-D X4 / design-B B17 / ADR-0085) ─────────────


def _po_lines(client: TestClient, po_id: int) -> dict[int, dict[str, Any]]:
    response = client.get(f"{PO}/{po_id}")
    assert response.status_code == 200, response.text
    return {line["id"]: line for line in response.json()["lines"]}


def _receipt(
    status: str, value: str | None, basis: str | None, count: int, unscheduled: int
) -> dict[str, Any]:
    return {
        "status": status,
        "value": value,
        "basis": basis,
        "shipment_count": count,
        "unscheduled_count": unscheduled,
    }


def _plan_eta(client: TestClient, shipment_id: int, on: str, *, reason: str | None = None) -> None:
    board = client.get(f"{SHIPMENTS}/{shipment_id}/milestones").json()
    row = next(r for r in board["rows"] if r["milestone_type"] == "ETA")
    body: dict[str, Any] = {"planned_on": on}
    if row["version"] is not None:
        body["version"] = row["version"]
    if reason is not None:
        body["reason"] = reason
    response = client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/ETA/plan", json=body, headers=idem()
    )
    assert response.status_code == 200, response.text


def _actual_eta(client: TestClient, shipment_id: int, on: str) -> None:
    board = client.get(f"{SHIPMENTS}/{shipment_id}/milestones").json()
    row = next(r for r in board["rows"] if r["milestone_type"] == "ETA")
    response = client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/ETA/actual",
        json={"actual_on": on, "version": row["version"]},
        headers=idem(),
    )
    assert response.status_code == 200, response.text


def test_po_detail_lines_carry_the_assignable_quantity_and_the_latest_eta_receipt(
    trade: TestClient,
) -> None:
    """design-D X4·B17 — PO 상세 라인마다 `assignable_quantity`(배정 가능량 — 409 판정과 같은 함수)·`expected_receipt`(살아 있는 수입선적 ETA 중
    **가장 늦은 날**, ETA 없는 선적이 있으면 UNSCHEDULED·값 없음, 선적 없으면 NONE, 전 선적 실적이면 basis ACTUAL). 취소 선적은 빠진다"""
    from datetime import timedelta

    today = today_kst()
    po = sentinel_po(trade, quantities=(10, 5))
    a, b = (line["id"] for line in po["lines"])
    assert {
        k: (v["assignable_quantity"], v["expected_receipt"])
        for k, v in _po_lines(trade, po["id"]).items()
    } == {
        a: (10, _receipt("NONE", None, None, 0, 0)),
        b: (5, _receipt("NONE", None, None, 0, 0)),
    }  # 생성 응답·상세 모두 같은 계산(생성 직후는 선적 없음)
    assert {ln["id"]: ln["assignable_quantity"] for ln in po["lines"]} == {a: 10, b: 5}
    s1 = created_import(trade, po["id"], [(a, 4)])
    lines = _po_lines(trade, po["id"])
    assert lines[a]["assignable_quantity"] == 6
    assert lines[a]["expected_receipt"] == _receipt("UNSCHEDULED", None, None, 1, 1)
    assert lines[b]["expected_receipt"] == _receipt("NONE", None, None, 0, 0)
    _plan_eta(trade, s1["id"], (today + timedelta(days=10)).isoformat())
    assert _po_lines(trade, po["id"])[a]["expected_receipt"] == _receipt(
        "SCHEDULED", (today + timedelta(days=10)).isoformat(), "PLANNED", 1, 0
    )
    s2 = created_import(trade, po["id"], [(a, 3), (b, 5)])
    _plan_eta(trade, s2["id"], (today + timedelta(days=20)).isoformat())
    lines = _po_lines(trade, po["id"])
    late = (today + timedelta(days=20)).isoformat()
    assert (lines[a]["assignable_quantity"], lines[b]["assignable_quantity"]) == (3, 0)
    assert lines[a]["expected_receipt"] == _receipt("SCHEDULED", late, "PLANNED", 2, 0)
    assert lines[b]["expected_receipt"] == _receipt("SCHEDULED", late, "PLANNED", 1, 0)
    s3 = created_import(trade, po["id"], [(a, 1)])  # ETA 없는 셋째 선적 — 가장 늦은 날을 알 수 없다
    assert _po_lines(trade, po["id"])[a]["expected_receipt"] == _receipt(
        "UNSCHEDULED", None, None, 3, 1
    )
    assert cancel(trade, s3["id"]).status_code == 200  # 취소 선적은 빠진다
    assert _po_lines(trade, po["id"])[a]["expected_receipt"] == _receipt(
        "SCHEDULED", late, "PLANNED", 2, 0
    )
    for shipment in (s1, s2):
        assert release(trade, shipment["id"]).status_code == 200
    _actual_eta(trade, s1["id"], (today - timedelta(days=1)).isoformat())
    assert _po_lines(trade, po["id"])[a]["expected_receipt"] == _receipt(
        "SCHEDULED", late, "PLANNED", 2, 0
    )  # 하나만 도착 — 늦은 쪽(계획)이 대표, 기준은 PLANNED
    _actual_eta(trade, s2["id"], today.isoformat())
    assert _po_lines(trade, po["id"])[a]["expected_receipt"] == _receipt(
        "SCHEDULED", today.isoformat(), "ACTUAL", 2, 0
    )  # 전 선적 도착 실적 — 실적 중 가장 늦은 날·ACTUAL
    assert _po_row(po["id"])["status"] == "ISSUED"


@pytest.mark.group_g
def test_po_detail_receipt_fields_are_the_same_for_cost_hidden_roles(trade: TestClient) -> None:
    """G — 배정 가능량·입고예정은 원가와 무관한 수량·날짜라 원가 비열람 역할(조회)도 같은 값을 본다(CostHidden 라인에도 실린다), 원가 키는 여전히 0"""
    po = sentinel_po(trade, quantities=(8,))
    shipment = created_import(trade, po["id"], [(po["lines"][0]["id"], 3)])
    _plan_eta(trade, shipment["id"], today_kst().isoformat())
    full = _po_lines(trade, po["id"])[po["lines"][0]["id"]]
    with logged_in(RoleCode.VIEWER) as viewer:
        detail = viewer.get(f"{PO}/{po['id']}")
        hidden = detail.json()["lines"][0]
        assert {k for k in scan_keys(detail.json()) if COST_KEY.search(k)} == set()
        for sentinel in SENTINELS:
            assert sentinel not in detail.text
    assert (
        (hidden["assignable_quantity"], hidden["expected_receipt"])
        == (
            full["assignable_quantity"],
            full["expected_receipt"],
        )
        == (5, _receipt("SCHEDULED", today_kst().isoformat(), "PLANNED", 1, 0))
    )


@pytest.mark.group_k
def test_po_detail_query_count_does_not_grow_with_lines_or_import_shipments(
    trade: TestClient,
) -> None:
    """K(렌즈 7) — PO 상세의 배정 가능량·입고예정은 질의 수가 라인·수입선적 수와 무관한 상수다(N+1 0 — 배정 가능량 2·입고예정 1)"""
    from sqlalchemy import event

    from app.core.db.session import engine

    small = sentinel_po(trade, quantities=(5,))
    big = sentinel_po(trade, quantities=(5, 5, 5, 5))
    for line in big["lines"]:
        shipment = created_import(trade, big["id"], [(line["id"], 2)])
        _plan_eta(trade, shipment["id"], today_kst().isoformat())
    created_import(trade, big["id"], [(big["lines"][0]["id"], 1)])
    counts: list[int] = []
    for po_id in (small["id"], big["id"]):
        statements: list[str] = []

        def _count(*_args: Any, sink: list[str] = statements) -> None:
            sink.append("q")

        event.listen(engine, "before_cursor_execute", _count)
        try:
            assert trade.get(f"{PO}/{po_id}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        counts.append(len(statements))
    assert counts[0] == counts[1], counts
