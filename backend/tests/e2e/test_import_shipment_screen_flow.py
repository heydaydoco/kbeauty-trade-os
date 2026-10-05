"""A(모듈 마커 `group_a` — 시험 전부)·G(금액 칸 0 시험에 `group_g`)·K(역할별 버튼 시험에 `group_k`). 수입선적 화면 경로
(S3-2 PR-5b — 프런트 소비 계약, 백엔드 앱 코드 무변경).

PO 상세 '수입선적 만들기' 2단 대화상자(`ImportShipmentCreateDialog`)가 보내는 **본문 그대로**(빈칸 라인 제외·`po_line_id`·원천 값 필드 0)
미리보기 → 생성을 하고, 화면 TS 타입(`lib/shipment.ts` `ImportShipmentPreview`·`ImportShipmentDetail`·`ImportShipmentLine`·
`ImportShipmentListItem`, `lib/purchase-order.ts` `PoLine.assignable_quantity`·`ExpectedReceipt`)이 기대하는 **키 집합 그대로**인지
(금액·통화 키 0 — 화면이 그 칸을 그리지 않는 근거) 실 HTTP로 고정한다. 이어서 409 `EXCEEDS_ASSIGNABLE`의 `detail.assignable_quantity` 키가
PO 라인 id인지(칸별 '배정 가능량 n 초과'의 근거)·422 `lines[i].po_line_id`가 보낸 본문의 i번째인지·PO 상세 입고예정 3분기(NONE →
UNSCHEDULED → SCHEDULED 'YYYY-MM-DD' 문자열)·노출 조건 입력(상태·Σ 배정 가능량)·'선적 확정'(RELEASE_ORDER 경로 그대로)·역순 취소 409 를
같은 순서로 확인한다. 화면 쪽 단언은 vitest(`purchase-order-detail.import.test.tsx`·`shipment-detail.import.test.tsx`)가 맡는다.
"""

from __future__ import annotations

import re
import sys
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.purchase_orders import service as po_service
from tests.factories.shipments import PO, SHIPMENTS, release
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

#: 대화상자가 아는 본문 키 — 이 밖의 키는 화면이 보내지 않는다(원천 값·금액 재입력 0, 서버 extra=forbid).
DIALOG_BODY_KEYS = {"lines", "origin_country_code", "dest_country_code", "parties", "internal_note"}
#: 화면 TS 타입의 키 집합(lib/shipment.ts) — 백엔드 응답과 1:1(하나라도 늘거나 줄면 화면 타입·표시 규칙을 다시 본다).
PREVIEW_KEYS = {
    "po_id",
    "po_doc_number",
    "po_status",
    "doc_date",
    "shipment_kind",
    "counterparty",
    "origin_country_code",
    "dest_country_code",
    "payment_terms",
    "incoterm",
    "lines",
    "parties",
}
PREVIEW_LINE_KEYS = {
    "po_line_id",
    "line_no",
    "sku",
    "quantity",
    "assignable_before",
    "remaining_after",
    "dg",
}
DETAIL_KEYS = {
    "id",
    "doc_number",
    "doc_date",
    "status",
    "shipment_kind",
    "version",
    "frozen_at",
    "source",
    "counterparty",
    "origin_country_code",
    "dest_country_code",
    "payment_terms",
    "incoterm",
    "internal_note",
    "assignee",
    "last_line_no",
    "dg_line_count",
    "lines",
    "parties",
    "milestones",
    "customs_summary",
    "allowed_actions",
    "created_at",
    "updated_at",
}
LINE_KEYS = {"id", "line_no", "po_line_id", "sku", "quantity", "source_line", "dg", "availability"}
LIST_KEYS = {
    "id",
    "doc_number",
    "doc_date",
    "status",
    "shipment_kind",
    "source",
    "counterparty_name",
    "origin_country_code",
    "dest_country_code",
    "line_count",
    "etd",
    "eta",
    "assignee",
    "created_at",
    "updated_at",
}
RECEIPT_KEYS = {"status", "value", "basis", "shipment_count", "unscheduled_count"}
#: 화면이 금액·통화 칸으로 그릴 수 있는 키 — 수입 응답 어디에도(중첩 포함) 없어야 한다.
AMOUNT_KEY = re.compile(r"(currency|minor_units|fx_rate|total|amount|price|cost|is_free)")
SENTINELS = (str(SENTINEL_UNIT_COST), SENTINEL_UNIT_COST_TEXT)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정 — 선적 흐름·PO 서비스(증빙일 기본값)·이 모듈의 `today_kst`를 같은 날로."""
    pin_today_kst(monkeypatch, sys.modules[__name__], po_service)


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _all_keys(node: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= _all_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _all_keys(item)
    return keys


def _amount_keys(body: Any) -> set[str]:
    return {key for key in _all_keys(body) if AMOUNT_KEY.search(key)}


def _sentinel_po(client: TestClient, quantities: tuple[int, ...]) -> dict[str, Any]:
    lines = [
        {
            "sku_id": create_purchase_priced_sku(),
            "quantity": qty,
            "unit_cost": SENTINEL_UNIT_COST_TEXT,
        }
        for qty in quantities
    ]
    return create_po_via_api(client, create_supplier(), [], lines=lines)


def _dialog_body(
    picks: dict[int, str], *, origin: str = "CN", dest: str = "KR", **extra: Any
) -> dict[str, Any]:
    """대화상자 `buildBody`와 같은 규칙 — 칸 값이 빈칸·'0'인 라인은 빼고, 나머지는 정수로(라인 키 = po_line_id)."""
    body: dict[str, Any] = {
        "lines": [
            {"po_line_id": line_id, "quantity": int(raw)}
            for line_id, raw in picks.items()
            if raw.strip() not in ("", "0")
        ],
        "origin_country_code": origin,
        "dest_country_code": dest,
        **extra,
    }
    assert set(body) <= DIALOG_BODY_KEYS
    return body


def _po_lines(client: TestClient, po_id: int) -> dict[int, dict[str, Any]]:
    response = client.get(f"{PO}/{po_id}")
    assert response.status_code == 200, response.text
    return {line["id"]: line for line in response.json()["lines"]}


def _shows_create_button(client: TestClient, po_id: int) -> bool:
    """화면 노출 조건의 서버 입력 — 상태 ∈ {발행, 공급사 확인} + Σ 배정 가능량 > 0(역할은 호출자 계정)."""
    body = client.get(f"{PO}/{po_id}").json()
    total = sum(max(line["assignable_quantity"], 0) for line in body["lines"])
    return body["status"] in {"ISSUED", "SUPPLIER_CONFIRMED"} and total > 0


def _plan_eta(client: TestClient, shipment_id: int, on: str) -> None:
    board = client.get(f"{SHIPMENTS}/{shipment_id}/milestones").json()
    row = next(r for r in board["rows"] if r["milestone_type"] == "ETA")
    body: dict[str, Any] = {"planned_on": on}
    if row["version"] is not None:
        body["version"] = row["version"]
    response = client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/ETA/plan", json=body, headers=idem()
    )
    assert response.status_code == 200, response.text


@pytest.mark.group_g
def test_dialog_round_trip_matches_the_screen_types_with_zero_amount_keys(
    trade: TestClient,
) -> None:
    """2단 대화상자 본문 그대로 미리보기 → 생성 — 응답 키 집합 = 화면 타입, 금액·통화 키·센티널 0(원가 열람 역할인 무역으로도)."""
    po = _sentinel_po(trade, (100, 50))
    a, b = (line["id"] for line in po["lines"])
    lines = _po_lines(trade, po["id"])
    # PO 상세 — 화면 열 '수입선적 배정 가능'·'입고예정'(NONE)의 입력.
    assert lines[a]["assignable_quantity"] == 100 and lines[b]["assignable_quantity"] == 50
    assert set(lines[a]["expected_receipt"]) == RECEIPT_KEYS
    assert lines[a]["expected_receipt"]["status"] == "NONE"
    assert _shows_create_button(trade, po["id"])

    body = _dialog_body({a: "60", b: ""}, internal_note="1차 선적")
    preview = trade.post(f"{PO}/{po['id']}/shipments/preview", json=body)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert set(data) == PREVIEW_KEYS
    assert [set(line) for line in data["lines"]] == [PREVIEW_LINE_KEYS]
    assert (data["lines"][0]["assignable_before"], data["lines"][0]["remaining_after"]) == (100, 40)
    assert [(p["role"], p["auto"]) for p in data["parties"]] == [
        ("SHIPPER", True)
    ]  # '(자동 — 발주 공급사)'
    assert _amount_keys(data) == set()

    response = trade.post(f"{PO}/{po['id']}/shipments", json=body, headers=idem())
    assert response.status_code == 201, response.text
    detail = response.json()
    assert set(detail) == DETAIL_KEYS
    assert detail["shipment_kind"] == "IMPORT"
    assert [set(line) for line in detail["lines"]] == [LINE_KEYS]
    source_line = detail["lines"][0]["source_line"]
    # 화면 '#n · 발주 q'·'배정 가능 n'의 입력(PR-3b 과도기의 line_no 0·quantity 0 아님).
    assert (
        source_line["id"],
        source_line["line_no"],
        source_line["quantity"],
        source_line["remaining_after"],
    ) == (a, 1, 100, 40)
    assert detail["source"]["kind"] == "PURCHASE_ORDER" and detail["source"]["status"] == "ISSUED"
    assert _amount_keys(detail) == set()

    listed = trade.get(f"{SHIPMENTS}?po_id={po['id']}").json()["items"]
    assert [item["id"] for item in listed] == [detail["id"]]
    assert set(listed[0]) == LIST_KEYS
    assert _amount_keys(listed) == set()
    for text in (
        preview.text,
        response.text,
        trade.get(f"{SHIPMENTS}/{detail['id']}").text,
        str(listed),
    ):
        for sentinel in SENTINELS:
            assert sentinel not in text


def test_conflicts_land_on_the_right_line_fields(trade: TestClient) -> None:
    """409 EXCEEDS_ASSIGNABLE `detail.assignable_quantity`의 키 = PO 라인 id(칸별 안내) · 422 `lines[i].po_line_id` = 보낸 본문의 i번째
    · 생성 확정 409도 같은 모양 · 라인 추가 경로(`source_line_id = po_line_id`)도 같은 코드·키."""
    po = _sentinel_po(trade, (10, 8))
    a, b = (line["id"] for line in po["lines"])
    other = _sentinel_po(trade, (5,))["lines"][0]["id"]

    over = trade.post(f"{PO}/{po['id']}/shipments/preview", json=_dialog_body({a: "11", b: "8"}))
    assert over.status_code == 409, over.text
    error = over.json()["error"]
    assert error["code"] == "SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE"
    assert error["detail"] == {
        "assignable_quantity": {str(a): 10}
    }  # 넘은 라인만 — 화면은 그 칸에만 붙인다

    mismatch = trade.post(
        f"{PO}/{po['id']}/shipments/preview", json=_dialog_body({a: "1", other: "1"})
    )
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["error"]["code"] == "SHIPMENTS.SOURCE.LINE_MISMATCH"
    assert mismatch.json()["error"]["detail"] == {
        "lines[1].po_line_id": "이 발주의 라인이 아닙니다."
    }

    first = trade.post(f"{PO}/{po['id']}/shipments", json=_dialog_body({a: "7"}), headers=idem())
    assert first.status_code == 201, first.text
    late = trade.post(f"{PO}/{po['id']}/shipments", json=_dialog_body({a: "4"}), headers=idem())
    assert late.status_code == 409
    assert late.json()["error"]["detail"] == {"assignable_quantity": {str(a): 3}}

    shipment = first.json()
    added = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/lines",
        json={"version": shipment["version"], "source_line_id": b, "quantity": 9},
        headers=idem(),
    )
    assert added.status_code == 409, added.text
    assert added.json()["error"]["detail"] == {"assignable_quantity": {str(b): 8}}
    # 수정 상한 = 배정 가능 + 현재(화면 안내 '상한 = 배정 가능 3 + 현재 7') — 넘으면 detail = 이 라인 최대 수량.
    line = shipment["lines"][0]
    assert (line["source_line"]["remaining_after"], line["quantity"]) == (3, 7)
    edit = trade.patch(
        f"{SHIPMENTS}/{shipment['id']}/lines/{line['id']}",
        json={"version": shipment["version"], "quantity": 11},
    )
    assert edit.status_code == 409
    assert edit.json()["error"]["detail"] == {"assignable_quantity": {str(a): 10}}


def test_po_line_expected_receipt_walks_none_unscheduled_scheduled_as_plain_date_strings(
    trade: TestClient,
) -> None:
    """입고예정 3분기 — 수입선적 없음 NONE → ETA 없는 선적 UNSCHEDULED(값 null·건수) → ETA 계획 SCHEDULED('YYYY-MM-DD' 문자열·PLANNED).
    배정 가능량이 남아 있으면 화면은 '미배정 n'을 함께 적는다(값은 서버가 준 그대로)."""
    po = _sentinel_po(trade, (20,))
    a = po["lines"][0]["id"]
    assert _po_lines(trade, po["id"])[a]["expected_receipt"]["status"] == "NONE"
    shipment = trade.post(
        f"{PO}/{po['id']}/shipments", json=_dialog_body({a: "5"}), headers=idem()
    ).json()
    line = _po_lines(trade, po["id"])[a]
    assert line["assignable_quantity"] == 15
    assert line["expected_receipt"] == {
        "status": "UNSCHEDULED",
        "value": None,
        "basis": None,
        "shipment_count": 1,
        "unscheduled_count": 1,
    }
    eta = (today_kst() + timedelta(days=12)).isoformat()
    _plan_eta(trade, shipment["id"], eta)
    receipt = _po_lines(trade, po["id"])[a]["expected_receipt"]
    assert receipt == {
        "status": "SCHEDULED",
        "value": eta,
        "basis": "PLANNED",
        "shipment_count": 1,
        "unscheduled_count": 0,
    }
    assert DATE.match(receipt["value"])  # 시각 문자열 아님 — 화면은 그대로 쓴다(new Date 0)


@pytest.mark.group_k
def test_roles_and_freeze_wording_inputs_and_reverse_cancel(trade: TestClient) -> None:
    """버튼 노출의 서버 근거 — 물류·조회는 만들기 403(화면 버튼 0), 물류 RELEASE_ORDER(화면 '선적 확정') → RELEASE_ORDERED,
    무역 상세 allowed_actions·노출 조건(배정 가능 0이면 닫힘)·살아 있는 수입선적이 있는 PO 취소 409 SUCCESSOR_ALIVE(선적 번호)."""
    po = _sentinel_po(trade, (6,))
    a = po["lines"][0]["id"]
    for role in (RoleCode.LOGISTICS, RoleCode.VIEWER):
        with logged_in(role) as other:
            denied = other.post(f"{PO}/{po['id']}/shipments/preview", json=_dialog_body({a: "1"}))
            assert denied.status_code == 403, (role, denied.text)
    created = trade.post(
        f"{PO}/{po['id']}/shipments", json=_dialog_body({a: "6"}), headers=idem()
    ).json()
    assert "RELEASE_ORDER" in created["allowed_actions"]
    assert not _shows_create_button(trade, po["id"])  # Σ 배정 가능 0 → 버튼 닫힘

    with logged_in(RoleCode.LOGISTICS) as logistics:
        detail = logistics.get(f"{SHIPMENTS}/{created['id']}").json()
        assert "RELEASE_ORDER" in detail["allowed_actions"]
        released = release(logistics, created["id"])
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "RELEASE_ORDERED"
        assert released.json()["frozen_at"] is not None  # 화면 '선적 확정 시각'
        assert _amount_keys(released.json()) == set()

    cancel = trade.post(
        f"{PO}/{po['id']}/transitions",
        json={"to": "CANCELLED", "version": po["version"], "reason": "공급 중단"},
        headers=idem(),
    )
    assert cancel.status_code == 409, cancel.text
    error = cancel.json()["error"]
    assert error["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    assert error["detail"]["successors"] == [created["doc_number"]]
