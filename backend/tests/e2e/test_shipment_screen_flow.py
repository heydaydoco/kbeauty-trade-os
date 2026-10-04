"""A(모듈 마커 `group_a` — 시험 3건 전부)·K(역할별 동작 시험 1건만 `group_k` 추가). 선적 화면 경로 (S3-2 PR-3b — 프런트 소비 계약, 백엔드 코드 무변경).

SO 상세 '선적 만들기' 2단 대화상자가 보내는 **본문 그대로**(빈칸 라인 제외·원천 값 필드 0) 미리보기 → 생성을 하고,
부분선적 2건 → 선적 잔량 0, +1 → 409 `EXCEEDS_OPEN`의 `detail.open_quantity` 키가 **SO 라인 id**인지(화면이 그 라인 칸 아래에
'서버 확인: 남은 수량 N'을 붙이는 근거) 실 HTTP로 고정한다. 이어서 화면이 같은 흐름에서 부르는 조회 — SO 목록 '선적중' 필터(R-21)·
SO 상세 선적 섹션(`GET /shipments?so_id=`)·보드 '선적중' 열·문서 흐름 SHIPMENT 노드(PR-3c)·역할별 `allowed_actions`(버튼 노출의
유일한 근거) — 의 응답 모양을 같은 순서로 확인한다. 화면 쪽 단언은 vitest(`frontend/src/routes/*shipment*.test.tsx`)가 맡는다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.models import RoleCode
from tests.factories.shipments import (
    SHIPMENTS,
    SO,
    cancel,
    confirmed_so,
    scalar,
    so_status,
)
from tests.factories.trade import create_supplier, idem, logged_in, unique

pytestmark = pytest.mark.group_a

BOARD = "/api/v1/order-board"
FLOW = "/api/v1/document-flow"

#: 생성 대화상자가 아는 본문 키 — 이 밖의 키는 화면이 보내지 않는다(원천 값 재입력 0, 서버 extra=forbid).
DIALOG_BODY_KEYS = {"lines", "origin_country_code", "dest_country_code", "parties", "internal_note"}
#: 무역 계정이 계획 중 선적에서 받는 동작 — 화면 버튼은 이 문자열로만 열린다(lib/shipment.ts `ShipmentAction`).
TRADE_PLANNED_ACTIONS = [
    "RELEASE_ORDER",
    "CANCEL",
    "EDIT_LINES",
    "EDIT_COUNTRIES",
    "EDIT_META",
    "EDIT_PARTIES",
]


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _dialog_body(
    picks: dict[int, str], *, origin: str = "KR", dest: str = "US", **extra: Any
) -> dict[str, Any]:
    """대화상자 `buildBody`와 같은 규칙 — 칸 값이 빈칸·'0'인 라인은 빼고, 나머지는 정수로."""
    body: dict[str, Any] = {
        "lines": [
            {"so_line_id": line_id, "quantity": int(raw)}
            for line_id, raw in picks.items()
            if raw.strip() not in ("", "0")
        ],
        "origin_country_code": origin,
        "dest_country_code": dest,
        **extra,
    }
    assert set(body) <= DIALOG_BODY_KEYS
    return body


def _open(client: TestClient, so_id: int) -> list[int]:
    return [line["shipment_open_quantity"] for line in client.get(f"{SO}/{so_id}").json()["lines"]]


def _so_ids(client: TestClient, query: str) -> set[int]:
    body = client.get(f"{SO}?{query}&size=200").json()
    return {item["id"] for item in body["items"]}


def test_screen_flow_two_partial_shipments_reach_zero_and_the_next_409_is_keyed_by_so_line(
    trade: TestClient,
) -> None:
    """A — SO 라인 [10, 5]: 대화상자 본문(빈칸 라인 제외)으로 미리보기(저장 0) → 생성 6 → 같은 키 재전송 = 같은 선적 →
    둘째 4+5 → 잔량 [0, 0] → +1 미리보기·생성 모두 409 `{open_quantity: {"<so_line_id>": 0}}`(선적 수 불변)"""
    so = confirmed_so((10, 5))
    first_line, second_line = so["line_ids"]
    assert _open(trade, so["id"]) == [10, 5]

    body = _dialog_body({first_line: "6", second_line: ""})
    assert body["lines"] == [{"so_line_id": first_line, "quantity": 6}]
    preview = trade.post(f"{SO}/{so['id']}/shipments/preview", json=body)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    # 미리보기 단계가 그리는 값 — 원천 사본·잔량 전후·자동 수하인.
    assert shown["so_doc_number"] == so["doc_number"] and shown["fx_rate"] == "1350"
    assert shown["incoterm"] == {"code": "FOB", "place": "Busan", "year": 2020}
    assert [(ln["open_quantity_before"], ln["remaining_after"]) for ln in shown["lines"]] == [
        (10, 4)
    ]
    assert set(shown["lines"][0]["dg"]) == {"flag", "un_number", "dg_class"}
    assert [(p["role"], p["auto"]) for p in shown["parties"]] == [("CONSIGNEE", True)]
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 0

    key = idem()
    created = trade.post(f"{SO}/{so['id']}/shipments", json=body, headers=key)
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["allowed_actions"] == TRADE_PLANNED_ACTIONS
    assert detail["lines"][0]["availability"] == {"status": "NOT_IMPLEMENTED"}
    assert detail["lines"][0]["source_line"] == {
        "id": first_line,
        "line_no": 1,
        "quantity": 10,
        "remaining_after": 4,
    }
    replay = trade.post(f"{SO}/{so['id']}/shipments", json=body, headers=key)  # 더블클릭·재시도
    assert replay.status_code in (200, 201) and replay.json()["id"] == detail["id"]
    assert so_status(so["id"]) == "IN_SHIPMENT"
    assert _open(trade, so["id"]) == [4, 5]

    second = trade.post(
        f"{SO}/{so['id']}/shipments",
        json=_dialog_body({first_line: "4", second_line: "5"}, dest="JP"),
        headers=idem(),
    )
    assert second.status_code == 201, second.text
    assert _open(trade, so["id"]) == [0, 0]

    over = _dialog_body({first_line: "1", second_line: "0"})
    for path, headers in (
        (f"{SO}/{so['id']}/shipments/preview", None),
        (f"{SO}/{so['id']}/shipments", idem()),
    ):
        response = trade.post(path, json=over, headers=headers)
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
        assert error["detail"] == {"open_quantity": {str(first_line): 0}}
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 2


def test_screen_reads_follow_the_so_into_and_out_of_shipping(trade: TestClient) -> None:
    """A — 화면 조회 경로: SO 목록 '선적중' 필터·SO 상세 선적 섹션(Page·최신순)·보드 '선적중' 열·선적에서 연 문서 흐름(SO 아래 노드)
    → 선적 2건 취소 → SO 확정 복귀·필터에서 빠짐·잔량 복원"""
    so = confirmed_so((3,))
    line = so["line_ids"][0]
    a = trade.post(
        f"{SO}/{so['id']}/shipments", json=_dialog_body({line: "1"}), headers=idem()
    ).json()
    b = trade.post(
        f"{SO}/{so['id']}/shipments", json=_dialog_body({line: "2"}, dest="CN"), headers=idem()
    ).json()

    assert so["id"] in _so_ids(trade, "status=IN_SHIPMENT")
    assert so["id"] not in _so_ids(trade, "status=CONFIRMED")

    section = trade.get(f"{SHIPMENTS}?so_id={so['id']}").json()
    assert section["total"] == 2 and section["size"] == 50
    assert [item["id"] for item in section["items"]] == [b["id"], a["id"]]
    assert {item["dest_country_code"] for item in section["items"]} == {"US", "CN"}
    assert all(item["source"]["id"] == so["id"] for item in section["items"])

    board = trade.get(f"{BOARD}?q={so['doc_number']}").json()
    columns = {c["stage"]: c for c in board["columns"]}
    assert [c["stage"] for c in board["columns"]][-1] == "SO_IN_SHIPMENT"
    assert columns["SO_IN_SHIPMENT"]["label_ko"] == "선적중"
    assert ("SO", so["id"]) in {(i["kind"], i["id"]) for i in columns["SO_IN_SHIPMENT"]["items"]}
    assert so["id"] not in {i["id"] for i in columns["SO_CONFIRMED"]["items"]}

    flow = trade.get(f"{FLOW}/SHIPMENT/{a['id']}").json()
    nodes = {(n["kind"], n["id"]): n for n in flow["nodes"]}
    assert nodes[("SHIPMENT", a["id"])]["is_current"] is True
    assert nodes[("SHIPMENT", a["id"])]["parent_kind"] == "SALES_ORDER"
    assert nodes[("SHIPMENT", a["id"])]["parent_id"] == so["id"]
    assert nodes[("SHIPMENT", b["id"])]["is_current"] is False

    assert cancel(trade, b["id"]).status_code == 200
    assert so_status(so["id"]) == "IN_SHIPMENT"
    assert cancel(trade, a["id"]).status_code == 200
    assert so_status(so["id"]) == "CONFIRMED"
    assert so["id"] not in _so_ids(trade, "status=IN_SHIPMENT")
    assert _open(trade, so["id"]) == [3]
    statuses = [i["status"] for i in trade.get(f"{SHIPMENTS}?so_id={so['id']}").json()["items"]]
    assert statuses == ["CANCELLED", "CANCELLED"]  # 취소 선적도 섹션에 남는다(이력)


@pytest.mark.group_k
def test_allowed_actions_are_the_only_button_source_for_each_role() -> None:
    """K — 같은 계획 중 선적을 역할별로 열면 버튼 근거가 갈린다: 무역 6종 / 물류 = 출고지시·국가·메모·당사자 / 조회 = 없음.
    물류가 출고지시하면 무역은 취소·메모·당사자만(라인·국가 동결) — 화면은 이 목록 밖의 버튼을 그리지 않는다."""
    so = confirmed_so((4,))
    with logged_in(RoleCode.TRADE) as trade:
        shipment = trade.post(
            f"{SO}/{so['id']}/shipments",
            json=_dialog_body(
                {so["line_ids"][0]: "2"},
                parties=[
                    {"role": "FORWARDER", "partner_id": create_supplier(types=("FORWARDER",))}
                ],
                internal_note=unique("메모"),
            ),
            headers=idem(),
        ).json()
        assert shipment["allowed_actions"] == TRADE_PLANNED_ACTIONS
    with logged_in(RoleCode.LOGISTICS) as logistics:
        seen = logistics.get(f"{SHIPMENTS}/{shipment['id']}").json()
        assert seen["allowed_actions"] == [
            "RELEASE_ORDER",
            "EDIT_COUNTRIES",
            "EDIT_META",
            "EDIT_PARTIES",
        ]
        # 물류는 담당자 선택 목록(`/users/lookup`)을 못 부른다 — 화면이 담당자를 읽기로 보이는 근거.
        assert logistics.get("/api/v1/users/lookup?size=200").status_code == 403
        released = logistics.post(
            f"{SHIPMENTS}/{shipment['id']}/release-order",
            json={"version": seen["version"]},
            headers=idem(),
        )
        assert released.status_code == 200, released.text
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get(f"{SHIPMENTS}/{shipment['id']}").json()["allowed_actions"] == []
    with logged_in(RoleCode.TRADE) as trade:
        assert trade.get(f"{SHIPMENTS}/{shipment['id']}").json()["allowed_actions"] == [
            "CANCEL",
            "EDIT_META",
            "EDIT_PARTIES",
        ]
