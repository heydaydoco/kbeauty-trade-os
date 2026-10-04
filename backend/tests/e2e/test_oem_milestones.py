"""A·K. OEM 생산 일정 API — PO 소유 4종(원료수급·충진·포장·출하검사)의 보드·계획·롤오버·실적·정정·변경 이력
(S3-2 PR-4c / design-D M7~M9·D7 / design-B B15 / design-integrated N-05·N-07·X-16 / ADR-0079 ④·0080·0085 ①).

■ 검증 A(OEM 4종 ⇔ PO 소유): OEM 종류는 OEM 생산 PO에만(일반 구매 PO 422 OWNER_NOT_OEM), PO 경로에 선적 종류 422·파생 422, 선적 경로에 OEM
  종류 422 — 행은 `po_id` 소유로만 생긴다. PO 자체는 바뀌지 않는다(T13 = PO `FOR SHARE`, 무수정).
■ 검증 K: 쓰기 = 무역·관리자(물류·인증·조회 403 — 물류의 PO 쓰기 0), 조회 = 전 역할, 오류 우선순위 404 → 409 → 422(ADR-0079 ⑧),
  Page 기본 50, 보드 질의 수 상수(N+1 0).
계획·실적 본체는 선적과 같은 함수라 값 형태·사유·version 규율은 선적 시험(test_shipment_milestones)이 넓게 덮고, 여기서는 OEM 고유 경로를 본다.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import milestone_view
from tests.factories.shipments import SHIPMENTS, cancel, confirmed_so, created, rows, scalar
from tests.factories.trade import (
    create_po_via_api,
    create_supplier,
    idem,
    logged_in,
    raw_po,
    unique,
)
from tests.support.kst import pin_today_kst

pytestmark = pytest.mark.group_a

PO = "/api/v1/purchase-orders"
OEM_ORDER = ["RAW_MATERIAL_READY", "FILLING", "PACKING", "OUTGOING_INSPECTION"]


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정(4a 적대 검토 반영 ⑩ 승계 — PR-4c 적대 검토 반영 ①) — 시험마다 base 날짜를 한 번 잡아 앱 import 지점(마일스톤·
    통관·선적 흐름·보드)과 이 시험 모듈의 `today_kst`를 같은 날로 맞춘다(`_day(n)`·D-N·미래 실적 판정이 자정을 넘겨도 같은 날 기준)."""
    pin_today_kst(monkeypatch, sys.modules[__name__])


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _oem_po(client: TestClient) -> dict[str, Any]:
    """API로 만든 OEM 생산 PO(공급사 = OEM 유형 — 생성 규칙 F3)."""
    supplier = create_supplier(types=("OEM",))
    return create_po_via_api(client, supplier, po_kind="OEM_PRODUCTION")


def _plan(
    client: TestClient, po_id: int, milestone_type: str, body: dict[str, Any], *, key: str = ""
) -> Any:
    return client.post(
        f"{PO}/{po_id}/milestones/{milestone_type}/plan",
        json=body,
        headers={"Idempotency-Key": key or unique("op")},
    )


def _actual(client: TestClient, po_id: int, milestone_type: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"{PO}/{po_id}/milestones/{milestone_type}/actual", json=body, headers=idem()
    )


def _board(client: TestClient, po_id: int) -> dict[str, Any]:
    response = client.get(f"{PO}/{po_id}/milestones")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _row(client: TestClient, po_id: int, milestone_type: str) -> dict[str, Any]:
    return next(r for r in _board(client, po_id)["rows"] if r["milestone_type"] == milestone_type)


def _day(offset: int) -> str:
    return (today_kst() + timedelta(days=offset)).isoformat()


def _cancel_po(client: TestClient, po_id: int) -> None:
    version = int(scalar("SELECT version FROM purchase_orders WHERE id = :i", i=po_id))
    response = client.post(
        f"{PO}/{po_id}/transitions",
        json={"to": "CANCELLED", "version": version, "reason": "생산 취소"},
        headers=idem(),
    )
    assert response.status_code == 200, response.text


# ── 보드 모양 ──────────────────────────────────────────────────────────────────


def test_the_oem_board_has_four_production_rows_in_flow_order(trade: TestClient) -> None:
    """M7 — OEM 생산 PO의 보드 = 원료수급 → 충진 → 포장 → 출하검사 4행(저장형·날짜형·적용), 휴일 요약 0·파생 0·통관 0,
    무역 = EDIT_MILESTONES, 조회 전용 = 버튼 0. 원가·금액 키 없음"""
    po = _oem_po(trade)
    board = _board(trade, po["id"])
    assert [r["milestone_type"] for r in board["rows"]] == OEM_ORDER
    assert board["po_id"] == po["id"]
    assert board["holiday_summary"] == {"holiday": 0, "unverified": 0}
    assert board["allowed_actions"] == ["EDIT_MILESTONES"]
    for row in board["rows"]:
        assert (row["kind"], row["value_shape"], row["applicable"]) == ("STORED", "DATE", True)
        assert row["planned"] is None and row["actual"] is None and row["milestone_id"] is None
        assert row["derived"] is None and row["holiday"] is None and row["customs_state"] is None
        assert row["input_source"] == "MILESTONE"
    text = str(board)
    assert "cost" not in text and "amount" not in text and "price" not in text
    with logged_in(RoleCode.VIEWER) as viewer:
        assert _board(viewer, po["id"])["allowed_actions"] == []


# ── 계획·롤오버·실적·정정 (T13) ────────────────────────────────────────────────────


def test_plan_rollover_actual_and_correction_on_an_oem_po(trade: TestClient) -> None:
    """A — 계획 PLAN_SET → 롤오버(사유 없으면 422, 있으면 PLAN_CHANGED — 배지 대상 아님: 롤오버 횟수·미통보 0) → 미래 실적 422 → 실적 → 정정(사유 필수).
    행은 PO 소유(po_id, shipment_id NULL)이고 이력은 M9 Page(기본 50, 최신순), 아웃박스 payload owner_type = PURCHASE_ORDER"""
    po_id = _oem_po(trade)["id"]
    first = _plan(trade, po_id, "FILLING", {"planned_on": _day(10)})
    assert first.status_code == 200, first.text
    assert first.json()["change"]["change_kind"] == "PLAN_SET"
    row = next(r for r in first.json()["board"]["rows"] if r["milestone_type"] == "FILLING")
    assert row["planned"] == _day(10) and row["days_left"] == 10 and row["is_overdue"] is False
    stored = rows(
        "SELECT po_id, shipment_id, milestone_type FROM milestones WHERE po_id = :p", p=po_id
    )
    assert stored == [{"po_id": po_id, "shipment_id": None, "milestone_type": "FILLING"}]

    version = row["version"]
    no_reason = _plan(trade, po_id, "FILLING", {"planned_on": _day(12), "version": version})
    assert (no_reason.status_code, _code(no_reason)) == (
        422,
        "SHIPMENTS.MILESTONE.REASON_REQUIRED",
    )
    rolled = _plan(
        trade,
        po_id,
        "FILLING",
        {"planned_on": _day(12), "version": version, "reason": "원료 입고 지연"},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["change"]["change_kind"] == "PLAN_CHANGED"
    row = _row(trade, po_id, "FILLING")
    # 롤오버 배지는 ROLLOVER_TYPES(ETD·ETA·CARGO_CLOSING — B9)만 — OEM 종류는 이력(PLAN_CHANGED·사유)만 남고 횟수·미통보 0
    assert (row["rollover_count"], row["unnotified_rollovers"]) == (0, 0)

    future = _actual(trade, po_id, "FILLING", {"actual_on": _day(2), "version": row["version"]})
    assert (future.status_code, _code(future)) == (422, "SHIPMENTS.MILESTONE.ACTUAL_IN_FUTURE")
    done = _actual(trade, po_id, "FILLING", {"actual_on": _day(0), "version": row["version"]})
    assert done.status_code == 200, done.text
    assert done.json()["change"]["change_kind"] == "ACTUAL_RECORDED"
    row = _row(trade, po_id, "FILLING")
    assert row["actual"] == _day(0) and row["effective"]["basis"] == "ACTUAL"
    assert row["days_left"] is None and row["is_overdue"] is None

    blank = _actual(trade, po_id, "FILLING", {"actual_on": _day(-1), "version": row["version"]})
    assert _code(blank) == "SHIPMENTS.MILESTONE.REASON_REQUIRED"
    fixed = _actual(
        trade,
        po_id,
        "FILLING",
        {"actual_on": _day(-1), "version": row["version"], "reason": "충진 완료일 오기"},
    )
    assert fixed.json()["change"]["change_kind"] == "ACTUAL_CORRECTED"
    same = _actual(
        trade,
        po_id,
        "FILLING",
        {"actual_on": _day(-1), "version": fixed.json()["board"]["rows"][1]["version"]},
    )
    assert same.status_code == 200 and same.json()["change"] is None  # 같은 값 = no-op(이력 0)

    history = trade.get(f"{PO}/{po_id}/milestone-changes")
    assert history.status_code == 200
    page = history.json()
    assert page["size"] == 50 and page["total"] == 4
    assert [item["change_kind"] for item in page["items"]] == [
        "ACTUAL_CORRECTED",
        "ACTUAL_RECORDED",
        "PLAN_CHANGED",
        "PLAN_SET",
    ]
    assert page["items"][2]["reason"] == "원료 입고 지연" and page["items"][2]["notices"] == []
    filtered = trade.get(f"{PO}/{po_id}/milestone-changes", params={"change_kind": "PLAN_SET"})
    assert filtered.json()["total"] == 1
    events = rows(
        "SELECT aggregate_type, aggregate_id, payload FROM events"
        " WHERE event_type = 'shipments.milestone.changed' AND aggregate_id = :p"
        " AND aggregate_type = 'purchase_orders' ORDER BY id",
        p=po_id,
    )
    assert len(events) == 4
    assert {e["payload"]["owner_type"] for e in events} == {"PURCHASE_ORDER"}
    assert {e["payload"]["owner_id"] for e in events} == {po_id}
    assert not {k for e in events for k in e["payload"] if "cost" in k or "amount" in k}


def test_the_same_key_replays_the_same_change(trade: TestClient) -> None:
    """J(R-19 승계) — 같은 Idempotency-Key 재요청 = 같은 change.id·이력 1행. 재생 응답의 보드는 **지금** 상태로 다시 조립한다(4a 개정 규율 —
    그사이 다른 종류 계획·PO 취소가 보드·버튼에 보인다)"""
    po_id = _oem_po(trade)["id"]
    key = unique("same")
    first = _plan(trade, po_id, "PACKING", {"planned_on": _day(20)}, key=key)
    assert _plan(trade, po_id, "FILLING", {"planned_on": _day(15)}).status_code == 200
    _cancel_po(trade, po_id)
    again = _plan(trade, po_id, "PACKING", {"planned_on": _day(20)}, key=key)
    assert first.status_code == again.status_code == 200
    assert first.json()["change"]["id"] == again.json()["change"]["id"]
    assert first.json()["board"]["allowed_actions"] == ["EDIT_MILESTONES"]
    assert again.json()["board"]["allowed_actions"] == []  # 재생 = 지금 보드(취소 반영)
    assert again.json()["board"]["rows"][1]["planned"] == _day(15)
    assert (
        scalar(
            "SELECT count(*) FROM milestone_changes mc JOIN milestones m ON m.id = mc.milestone_id"
            " WHERE m.po_id = :p",
            p=po_id,
        )
        == 2
    )


def test_oem_writes_never_modify_the_purchase_order(trade: TestClient) -> None:
    """T13 — PO는 `FOR SHARE`로 상태만 확인한다: 계획·실적이 PO의 version·상태·수정 시각을 바꾸지 않는다(PO 무수정 — 동결 전표)"""
    po_id = _oem_po(trade)["id"]
    before = rows("SELECT version, status, updated_at FROM purchase_orders WHERE id = :p", p=po_id)
    assert _plan(trade, po_id, "RAW_MATERIAL_READY", {"planned_on": _day(3)}).status_code == 200
    version = _row(trade, po_id, "RAW_MATERIAL_READY")["version"]
    assert (
        _actual(trade, po_id, "RAW_MATERIAL_READY", {"actual_on": _day(0), "version": version})
    ).status_code == 200
    after = rows("SELECT version, status, updated_at FROM purchase_orders WHERE id = :p", p=po_id)
    assert before == after


# ── OEM 4종 ⇔ PO 소유 (검증 A) ─────────────────────────────────────────────────────


def test_oem_types_live_only_on_oem_purchase_orders(trade: TestClient) -> None:
    """A — 일반 구매 PO: 보드·계획·실적·이력 = 422 OWNER_NOT_OEM(빈 보드로 숨기지 않는다) / OEM PO: 선적 종류 422 TYPE_NOT_APPLICABLE(detail
    po_kind)·파생 422 DERIVED_NOT_EDITABLE·모르는 종류 스키마 422 / 선적: OEM 종류 422 TYPE_NOT_APPLICABLE. 어느 거부도 행을 남기지 않는다"""
    general = raw_po(po_kind="PURCHASE")
    for response in (
        trade.get(f"{PO}/{general}/milestones"),
        _plan(trade, general, "FILLING", {"planned_on": _day(5)}),
        _actual(trade, general, "FILLING", {"actual_on": _day(0)}),
        trade.get(f"{PO}/{general}/milestone-changes"),
    ):
        assert (response.status_code, _code(response)) == (
            422,
            "SHIPMENTS.MILESTONE.OWNER_NOT_OEM",
        ), response.text
    oem = _oem_po(trade)["id"]
    for milestone_type in ("ETD", "ETA", "CUSTOMS_CLEARED", "IMPORT_TAX_DUE"):
        response = _plan(trade, oem, milestone_type, {"planned_on": _day(5)})
        assert _code(response) == "SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE", milestone_type
        assert response.json()["error"]["detail"]["po_kind"] == "OEM_PRODUCTION"
    for milestone_type in ("LOADING_DEADLINE", "PAYMENT_DUE", "PRESENTATION_DEADLINE"):
        response = _actual(trade, oem, milestone_type, {"actual_on": _day(0)})
        assert _code(response) == "SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE", milestone_type
    assert _plan(trade, oem, "MOLDING", {"planned_on": _day(5)}).status_code == 422
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 1)])
    for milestone_type in OEM_ORDER:
        response = trade.post(
            f"{SHIPMENTS}/{shipment['id']}/milestones/{milestone_type}/plan",
            json={"planned_on": _day(5)},
            headers=idem(),
        )
        assert _code(response) == "SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE", milestone_type
    assert scalar("SELECT count(*) FROM milestones WHERE po_id IN (:a, :b)", a=general, b=oem) == 0
    assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=shipment["id"]) == 0


def test_a_cancelled_oem_po_takes_no_writes_but_still_reads(trade: TestClient) -> None:
    """N-05 — 취소된 OEM PO의 계획·실적 = 409 OWNER_NOT_ACTIVE(선적과 같은 코드), 보드·이력 읽기는 그대로(버튼 0). 공급사 확인(OC) 상태는 쓰기 가능"""
    po_id = _oem_po(trade)["id"]
    assert _plan(trade, po_id, "PACKING", {"planned_on": _day(7)}).status_code == 200
    _cancel_po(trade, po_id)
    version = _row(trade, po_id, "PACKING")["version"]
    for response in (
        _plan(trade, po_id, "PACKING", {"planned_on": _day(9), "version": version, "reason": "x"}),
        _actual(trade, po_id, "PACKING", {"actual_on": _day(0), "version": version}),
        _plan(trade, po_id, "FILLING", {"planned_on": _day(9)}),
    ):
        assert (response.status_code, _code(response)) == (
            409,
            "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE",
        )
    board = _board(trade, po_id)
    assert board["allowed_actions"] == []
    assert board["rows"][2]["planned"] == _day(7)
    assert trade.get(f"{PO}/{po_id}/milestone-changes").json()["total"] == 1
    confirmed = raw_po("SUPPLIER_CONFIRMED", po_kind="OEM_PRODUCTION")
    assert _plan(trade, confirmed, "FILLING", {"planned_on": _day(4)}).status_code == 200
    assert _board(trade, confirmed)["allowed_actions"] == ["EDIT_MILESTONES"]


def test_owner_not_active_names_the_right_remedy_per_owner(trade: TestClient) -> None:
    """PR-4c 적대 검토 반영 ③ — 같은 409 OWNER_NOT_ACTIVE라도 조치 문구는 소유자별로 정확하다: 취소된 **선적**의 마일스톤 쓰기 = "수주에서 새 선적"
    (발주 안내 0, detail.owner_type = SHIPMENT), 취소된 **발주**의 생산 일정 쓰기 = "새 발주"(선적 안내 0, detail.owner_type = PURCHASE_ORDER).
    카탈로그 기본 문구는 소유자 중립이다(경로가 덮는다)"""
    from app.core.errors.catalog import spec_for
    from app.core.errors.codes import ErrorCode

    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 1)])
    assert cancel(trade, shipment["id"]).status_code == 200
    on_shipment = trade.post(
        f"{SHIPMENTS}/{shipment['id']}/milestones/ETD/plan",
        json={"planned_on": _day(3)},
        headers=idem(),
    )
    error = on_shipment.json()["error"]
    assert (on_shipment.status_code, error["code"]) == (409, "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE")
    assert "수주에서 새 선적" in error["message"] and "발주" not in error["message"]
    assert error["detail"] == {"owner_type": "SHIPMENT"}
    po_id = _oem_po(trade)["id"]
    _cancel_po(trade, po_id)
    on_po = _plan(trade, po_id, "FILLING", {"planned_on": _day(3)})
    error = on_po.json()["error"]
    assert (on_po.status_code, error["code"]) == (409, "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE")
    assert "새 발주" in error["message"] and "선적" not in error["message"]
    assert error["detail"] == {"owner_type": "PURCHASE_ORDER"}
    neutral = spec_for(ErrorCode.SHIPMENTS_MILESTONE_OWNER_NOT_ACTIVE).message_ko
    assert "새 선적" not in neutral and "새 발주" not in neutral


# ── 권한·오류 우선순위 (검증 K) ──────────────────────────────────────────────────────


@pytest.mark.group_k
def test_only_trade_and_admin_write_oem_schedules_everyone_reads() -> None:
    """K(X-16) — 물류·인증·조회 전용의 계획·실적 = 403(존재·입력 검사보다 먼저 — 없는 PO여도 403), 전 역할 보드·이력 200, 관리자 쓰기 200"""
    with logged_in(RoleCode.TRADE) as trade:
        po_id = _oem_po(trade)["id"]
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert _plan(client, po_id, "FILLING", {"planned_on": _day(3)}).status_code == 403
            assert _actual(client, po_id, "FILLING", {"actual_on": _day(0)}).status_code == 403
            assert _plan(client, 99_999_999, "ETD", {"planned_on": "x"}).status_code == 403
            assert _board(client, po_id)["allowed_actions"] == []
            assert client.get(f"{PO}/{po_id}/milestone-changes").status_code == 200
    with logged_in(RoleCode.ADMIN) as admin:
        assert _plan(admin, po_id, "FILLING", {"planned_on": _day(3)}).status_code == 200
        assert _board(admin, po_id)["allowed_actions"] == ["EDIT_MILESTONES"]
    assert scalar("SELECT count(*) FROM milestones WHERE po_id = :p", p=po_id) == 1


@pytest.mark.group_k
def test_error_priority_is_404_then_409_then_422(trade: TestClient) -> None:
    """ADR-0079 ⑧ — 없는 PO + 파생 종류 = 404 / 취소된 일반 구매 PO = 409(OWNER_NOT_OEM 422보다 먼저) / 취소된 OEM PO + 파생 = 409 /
    일반 구매 PO + 파생 = 422 OWNER_NOT_OEM(소유자 판정이 종류 판정보다 먼저) / **행 version 409가 소유자·종류 422보다 먼저**(4a 개정 규율 —
    일반 구매 PO·파생·선적 종류에 version을 보내면 409) / 행 version 어긋남 + 미래 실적 = 409(값 422보다 먼저)"""
    missing = _plan(trade, 99_999_999, "PAYMENT_DUE", {"planned_on": _day(1)})
    assert missing.status_code == 404
    assert trade.get(f"{PO}/99999999/milestones").status_code == 404
    assert trade.get(f"{PO}/99999999/milestone-changes").status_code == 404
    general = raw_po("CANCELLED", po_kind="PURCHASE")
    assert _code(_plan(trade, general, "FILLING", {"planned_on": _day(1)})) == (
        "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
    )
    cancelled_oem = raw_po("CANCELLED", po_kind="OEM_PRODUCTION")
    assert _code(_actual(trade, cancelled_oem, "PAYMENT_DUE", {"actual_on": _day(9)})) == (
        "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
    )
    live_general = raw_po(po_kind="PURCHASE")
    assert _code(_plan(trade, live_general, "PAYMENT_DUE", {"planned_on": _day(1)})) == (
        "SHIPMENTS.MILESTONE.OWNER_NOT_OEM"
    )
    stale_general = _plan(trade, live_general, "FILLING", {"planned_on": _day(1), "version": 3})
    assert (stale_general.status_code, _code(stale_general)) == (
        409,
        "COMMON.CONCURRENCY.VERSION_CONFLICT",
    )
    po_id = _oem_po(trade)["id"]
    for milestone_type in ("PAYMENT_DUE", "ETD"):
        stale_kind = _plan(trade, po_id, milestone_type, {"planned_on": _day(1), "version": 2})
        assert _code(stale_kind) == "COMMON.CONCURRENCY.VERSION_CONFLICT", milestone_type
    assert _plan(trade, po_id, "FILLING", {"planned_on": _day(5)}).status_code == 200
    stale = _actual(trade, po_id, "FILLING", {"actual_on": _day(9), "version": 99})
    assert (stale.status_code, _code(stale)) == (409, "COMMON.CONCURRENCY.VERSION_CONFLICT")
    missing_version = _actual(trade, po_id, "FILLING", {"actual_on": _day(0)})
    assert _code(missing_version) == "COMMON.CONCURRENCY.VERSION_CONFLICT"


@pytest.mark.group_k
def test_oem_types_take_dates_only(trade: TestClient) -> None:
    """N-04 — OEM 4종은 날짜형: 시각·시간대를 보내면 422 VALUE_SHAPE_MISMATCH, 값 키 없는 실적 본문 422, 사유의 보이지 않는 글자 422"""
    po_id = _oem_po(trade)["id"]
    shaped = _plan(
        trade, po_id, "FILLING", {"planned_at": "2026-11-05T09:00:00+09:00", "tz": "Asia/Seoul"}
    )
    assert _code(shaped) == "SHIPMENTS.MILESTONE.VALUE_SHAPE_MISMATCH"
    assert _actual(trade, po_id, "FILLING", {}).status_code == 422
    assert _plan(trade, po_id, "FILLING", {"planned_on": _day(5)}).status_code == 200
    version = _row(trade, po_id, "FILLING")["version"]
    hidden = _plan(
        trade, po_id, "FILLING", {"planned_on": _day(6), "version": version, "reason": "지연​"}
    )
    assert hidden.status_code == 422
    assert (
        scalar(
            "SELECT count(*) FROM milestone_changes mc JOIN milestones m ON m.id = mc.milestone_id"
            " WHERE m.po_id = :p",
            p=po_id,
        )
        == 1
    )


@pytest.mark.group_k
def test_the_oem_board_takes_exactly_two_queries_whatever_the_row_count(trade: TestClient) -> None:
    """K(렌즈 7) — OEM 보드 질의 수 = **정확히 2**(PO 판정 열 1·마일스톤 행 1), 행 0·1·4개 모두 같다. OEM 4종은 ROLLOVER_TYPES 밖이라
    롤오버 통계 질의는 없다(대상 0이면 생략 — N+1 0). 롤오버가 OEM 종류에 생겨도 질의 수는 그대로"""
    from tests.support.sqlcount import count_statements

    empty = _oem_po(trade)["id"]
    one = _oem_po(trade)["id"]
    four = _oem_po(trade)["id"]
    _plan(trade, one, "FILLING", {"planned_on": _day(5)})
    for milestone_type in OEM_ORDER:
        _plan(trade, four, milestone_type, {"planned_on": _day(5)})
    version = _row(trade, four, "FILLING")["version"]
    rolled = _plan(
        trade, four, "FILLING", {"planned_on": _day(6), "version": version, "reason": "롤오버"}
    )
    assert rolled.json()["change"]["change_kind"] == "PLAN_CHANGED"
    roles = frozenset({RoleCode.TRADE})
    counts = [
        count_statements(lambda p=po: milestone_view.get_oem_board(p, roles))
        for po in (empty, one, four)
    ]
    assert counts == [2, 2, 2], counts
