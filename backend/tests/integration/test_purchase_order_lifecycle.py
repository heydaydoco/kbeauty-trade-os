"""A. PO 상태 전이 — PO 6상태 30쌍 전수·OC 경계·취소·통로 규칙 (S3-1 PR-8a / ADR-0051·0057 / design-B B1·B8 / design-F F2).

PO 6상태의 순서쌍 30개: 허용 3(사람 3·자동 0 — 공급사 확인·발행 중 취소·공급사 확인 후 취소)은 성공, 미허용 27은 409 전건. 그중 RESERVED 3상태
(PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED — 입고 후반, S4-1)는 in/out 엣지가 0이다. PO에는 **동결 액션 엣지도 자동 엣지도 없다**(생성이 곧 발행).
**OC 경계**: `doc_date ≤ oc_received_on ≤ today_kst()`이고 OC 열은 공급사 확인 전이에서만 받는다.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import permutations
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import (
    AUTO_TRANSITIONS,
    FREEZE_ACTION_EDGES,
    HUMAN_TRANSITIONS,
    RESERVED,
    STATUSES,
    public_transition_targets,
)
from app.modules.trade_docs.transition import PAYLOAD_KEYS, record_transition
from tests.factories.trade import (
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    fake_successors,
    idem,
    logged_in,
    raw_po,
)
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a

KIND = DocKind.PURCHASE_ORDER
STATES = STATUSES[KIND]
HUMAN = HUMAN_TRANSITIONS[KIND]
AUTO = AUTO_TRANSITIONS[KIND]
PO = "/api/v1/purchase-orders"
_ALL_PAIRS = list(permutations(STATES, 2))


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params).all()]


def _scalar(sql: str, **params: Any) -> Any:
    return _rows(sql, **params)[0][0]


def _log_rows(po_id: int) -> list[tuple[Any, ...]]:
    return _rows(
        "SELECT from_status, to_status, automatic, reason, actor_user_id FROM"
        " purchase_order_status_log WHERE purchase_order_id = :p ORDER BY id",
        p=po_id,
    )


def _events(po_id: int) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'purchase_orders'"
            " AND aggregate_id = :i AND event_type = 'purchase_orders.purchase_order.status_changed'",
            i=po_id,
        )
    )


def _kernel_transition(po_id: int, to: str, **kwargs: Any) -> str:
    """전이 통로(record_transition)를 직접 부른다 — 공급사 확인은 OC 열을 같은 flush에 싣는다(서비스 lifecycle과 같은 규칙)."""
    with unit_of_work() as uow:
        row = uow.session.get(PurchaseOrder, po_id)
        assert row is not None
        with uow.session.no_autoflush:
            if to == "SUPPLIER_CONFIRMED":
                row.oc_received_on = row.doc_date
            return record_transition(uow.session, row, to, **kwargs)


def _kwargs(to: str, actor: int, **over: Any) -> dict[str, Any]:
    return {
        "actor_user_id": actor,
        "reason": "테스트 사유" if to == "CANCELLED" else None,
        "automatic": False,
        **over,
    }


# ── 30쌍 전수 (상태 기계 통로) ─────────────────────────────────────────────────


@pytest.mark.parametrize("pair", _ALL_PAIRS, ids=[f"{a}->{b}" for a, b in _ALL_PAIRS])
def test_all_thirty_po_pairs(pair: tuple[str, str]) -> None:
    """PO 30쌍 — 허용 3쌍은 성공(이력 1행·automatic=false·이벤트 1건), 미허용 27쌍은 409 TRANSITION.NOT_ALLOWED이고 상태·이력·이벤트 무변"""
    frm, to = pair
    actor = create_user(f"po-actor-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    po_id = raw_po(frm)
    before_log, before_events = len(_log_rows(po_id)), _events(po_id)
    if pair in HUMAN | AUTO:
        assert _kernel_transition(po_id, to, **_kwargs(to, actor)) == frm
        assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == to
        rows = _log_rows(po_id)
        assert len(rows) == before_log + 1 and _events(po_id) == before_events + 1
        assert rows[-1][:3] == (frm, to, False)
        if to == "CANCELLED":
            assert rows[-1][3] == "테스트 사유"
    else:
        with pytest.raises(AppError) as caught:
            _kernel_transition(po_id, to, **_kwargs(to, actor))
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
        assert caught.value.status_code == 409
        assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == frm
        assert len(_log_rows(po_id)) == before_log and _events(po_id) == before_events


def test_the_pair_table_itself_is_not_vacuous() -> None:
    """공회전 방지 — 30쌍 중 허용 3(사람 3·자동 0)·미허용 27이 실제로 갈리고, RESERVED 3상태 관련 쌍은 전부 미허용이며 동결 액션 엣지가 없다"""
    assert len(_ALL_PAIRS) == 30
    assert {p for p in _ALL_PAIRS if p in HUMAN | AUTO} == {
        ("ISSUED", "SUPPLIER_CONFIRMED"),
        ("ISSUED", "CANCELLED"),
        ("SUPPLIER_CONFIRMED", "CANCELLED"),
    }
    assert (len(HUMAN), len(AUTO)) == (3, 0)
    reserved = RESERVED[KIND]
    assert reserved == {"PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CLOSED"}
    assert not [
        p for p in _ALL_PAIRS if (p[0] in reserved or p[1] in reserved) and p in HUMAN | AUTO
    ]
    assert FREEZE_ACTION_EDGES[KIND] == frozenset()  # 생성이 곧 발행이라 별도 동결 액션이 없다
    assert public_transition_targets(KIND) == {"SUPPLIER_CONFIRMED", "CANCELLED"}


@pytest.mark.parametrize("pair", sorted(HUMAN))
def test_wrong_channel_is_rejected_even_for_an_allowed_pair(pair: tuple[str, str]) -> None:
    """허용 쌍도 통로가 틀리면 거부 — 사람 쌍에 automatic=True(PO는 자동 엣지가 0) · 동결 액션 통로(via_freeze_action)로는 못 넘는다"""
    frm, to = pair
    actor = create_user(f"po-chan-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    po_id = raw_po(frm)
    good = _kwargs(to, actor)
    for flipped in ({"automatic": True}, {"via_freeze_action": True}):
        with pytest.raises(AppError) as caught:
            _kernel_transition(po_id, to, **{**good, **flipped})
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == frm
    assert _scalar("SELECT frozen_at FROM purchase_orders WHERE id = :i", i=po_id) is not None


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_cancel_requires_a_reason(reason: str | None) -> None:
    """취소 도달은 사유 필수 — 없으면(공백 포함) 422 REASON_REQUIRED이고 상태 무변"""
    po_id = raw_po("ISSUED")
    actor = create_user("po-reason@example.com", roles=(RoleCode.TRADE,))
    with pytest.raises(AppError) as caught:
        _kernel_transition(po_id, "CANCELLED", actor_user_id=actor, reason=reason, automatic=False)
    assert caught.value.code == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == "ISSUED"


# ── API: 공개 대상 × 6 출발 상태 ─────────────────────────────────────────────────


def _api_transition(client: TestClient, po_id: int, **body: Any) -> Any:
    version = _scalar("SELECT version FROM purchase_orders WHERE id = :i", i=po_id)
    return client.post(
        f"{PO}/{po_id}/transitions", json={"version": version, **body}, headers=idem()
    )


@pytest.mark.parametrize("frm", STATES)
@pytest.mark.parametrize("to", ["SUPPLIER_CONFIRMED", "CANCELLED"])
def test_api_public_targets_from_every_state(trade: TestClient, frm: str, to: str) -> None:
    """공개 대상 2개 × 출발 6상태 — 허용 3쌍만 200, 나머지(종결·예약 상태 출발·자기 상태 재요청 포함)는 409이며 상태·이력은 무변"""
    po_id = raw_po(frm)
    body: dict[str, Any] = {"to": to}
    if to == "CANCELLED":
        body["reason"] = "사유"
    else:
        body["oc_received_on"] = today_kst().isoformat()
    before = len(_log_rows(po_id))
    response = _api_transition(trade, po_id, **body)
    if (frm, to) in HUMAN:
        assert response.status_code == 200, response.text
        assert response.json()["status"] == to and len(_log_rows(po_id)) == before + 1
    else:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
        assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == frm
        assert len(_log_rows(po_id)) == before


@pytest.mark.parametrize(
    "to",
    ["ISSUED", "PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CLOSED", "EXPIRED", "DRAFT", "ON_HOLD"],
)
def test_non_public_targets_are_unrepresentable_in_the_request(trade: TestClient, to: str) -> None:
    """요청 `to` Literal에 예약 후반 상태·발행·타 전표 상태가 없다 — 어느 출발 상태에서든 422(409가 아니라 스키마에서 차단)이고 상태 무변"""
    for frm in ("ISSUED", "SUPPLIER_CONFIRMED"):
        po_id = raw_po(frm)
        response = _api_transition(trade, po_id, to=to, reason="사유")
        assert response.status_code == 422, (frm, to, response.text)
        assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == frm


# ── OC 경계 ────────────────────────────────────────────────────────────────


def test_oc_date_boundaries_issue_date_to_today_inclusive(trade: TestClient) -> None:
    """OC 일자는 발행일(증빙일) 이상·오늘(KST) 이하 — 발행일 전·미래·누락은 422, 발행일 당일·오늘은 통과. 실패하면 상태·OC 열·이력 무변"""
    doc_date = today_kst() - timedelta(days=10)
    po_id = raw_po("ISSUED", doc_date=doc_date)
    for bad in (
        (doc_date - timedelta(days=1)).isoformat(),
        (today_kst() + timedelta(days=1)).isoformat(),
        None,
    ):
        body: dict[str, Any] = {"to": "SUPPLIER_CONFIRMED"}
        if bad:
            body["oc_received_on"] = bad
        response = _api_transition(trade, po_id, **body)
        assert response.status_code == 422, (bad, response.text)
        assert "oc_received_on" in response.json()["error"]["detail"]
        row = _rows(
            "SELECT status, oc_received_on, oc_reference FROM purchase_orders WHERE id = :i",
            i=po_id,
        )[0]
        assert row == ("ISSUED", None, None) and len(_log_rows(po_id)) == 1
    same_day = raw_po("ISSUED", doc_date=doc_date)
    assert (
        _api_transition(
            trade, same_day, to="SUPPLIER_CONFIRMED", oc_received_on=doc_date.isoformat()
        ).status_code
        == 200
    )
    today = raw_po("ISSUED", doc_date=doc_date)
    ok = _api_transition(
        trade, today, to="SUPPLIER_CONFIRMED", oc_received_on=today_kst().isoformat()
    )
    assert ok.status_code == 200 and ok.json()["oc_received_on"] == today_kst().isoformat()


def test_oc_reference_is_optional_trimmed_and_capped(trade: TestClient) -> None:
    """OC 참조는 선택 — 앞뒤 공백 제거·빈 값은 NULL·100자 초과 422"""
    a, b, c = raw_po("ISSUED"), raw_po("ISSUED"), raw_po("ISSUED")
    today = today_kst().isoformat()
    ok = _api_transition(
        trade, a, to="SUPPLIER_CONFIRMED", oc_received_on=today, oc_reference="  OC-2026-9  "
    )
    assert ok.json()["oc_reference"] == "OC-2026-9"
    blank = _api_transition(
        trade, b, to="SUPPLIER_CONFIRMED", oc_received_on=today, oc_reference="   "
    )
    assert blank.status_code == 200 and blank.json()["oc_reference"] is None
    too_long = _api_transition(
        trade, c, to="SUPPLIER_CONFIRMED", oc_received_on=today, oc_reference="x" * 101
    )
    assert too_long.status_code == 422
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=c) == "ISSUED"


def test_oc_fields_are_only_accepted_on_the_supplier_confirmation(trade: TestClient) -> None:
    """취소 요청에 OC 일자·참조를 실으면 422(OC 부속은 공급사 확인 전이에서만) — 상태 무변"""
    po_id = raw_po("ISSUED")
    today = today_kst().isoformat()
    for extra in ({"oc_received_on": today}, {"oc_reference": "OC-1"}):
        response = _api_transition(trade, po_id, to="CANCELLED", reason="사유", **extra)
        assert response.status_code == 422, response.text
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == "ISSUED"


def test_the_transition_check_precedes_the_oc_validation(trade: TestClient) -> None:
    """엣지가 없는 요청은 OC 검증(422)보다 전이 409가 먼저다 — 이미 공급사 확인된 PO에 잘못된 OC를 실은 재요청은 409"""
    po_id = raw_po("SUPPLIER_CONFIRMED")
    response = _api_transition(trade, po_id, to="SUPPLIER_CONFIRMED", oc_received_on="2000-01-01")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"


# ── 취소 · 이력 · 멱등 ──────────────────────────────────────────────────────


def test_cancel_from_issued_and_from_supplier_confirmed_keep_the_document_intact(
    trade: TestClient,
) -> None:
    """발행 중 취소·공급사 확인 후 취소 모두 성공 — 사유가 이력에 남고 라인·합계·번호·OC는 그대로(삭제 경로 없음), version +1, 자동 표식 false"""
    issued = create_po_via_api(trade)
    cancelled = _api_transition(trade, issued["id"], to="CANCELLED", reason="발주 오기")
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "CANCELLED" and body["version"] == issued["version"] + 1
    assert body["doc_number"] == issued["doc_number"] and body["lines"] == issued["lines"]
    assert body["total_cost"] == issued["total_cost"]
    assert _log_rows(issued["id"])[-1][:4] == ("ISSUED", "CANCELLED", False, "발주 오기")
    confirmed = create_po_via_api(trade)
    oc = _api_transition(
        trade,
        confirmed["id"],
        to="SUPPLIER_CONFIRMED",
        oc_received_on=today_kst().isoformat(),
        oc_reference="OC-7",
    )
    after = _api_transition(trade, confirmed["id"], to="CANCELLED", reason="공급사 납기 불가")
    assert oc.status_code == after.status_code == 200
    assert after.json()["oc_reference"] == "OC-7"  # OC 기록은 취소 뒤에도 남는다(사건의 기록)
    assert _scalar("SELECT count(*) FROM purchase_orders") == 2  # 행은 삭제되지 않는다


def test_a_cancelled_po_is_terminal_a_second_cancel_is_409_and_leaves_no_trace(
    trade: TestClient,
) -> None:
    """취소된 PO는 종결 — 다시 취소·공급사 확인은 409이고 이력·이벤트 행이 늘지 않는다"""
    po = create_po_via_api(trade)
    assert _api_transition(trade, po["id"], to="CANCELLED", reason="r").status_code == 200
    log, events = len(_log_rows(po["id"])), _events(po["id"])
    assert _api_transition(trade, po["id"], to="CANCELLED", reason="r2").status_code == 409
    again = _api_transition(
        trade, po["id"], to="SUPPLIER_CONFIRMED", oc_received_on=today_kst().isoformat()
    )
    assert again.status_code == 409
    assert (len(_log_rows(po["id"])), _events(po["id"])) == (log, events)


def test_cancel_needs_a_reason_over_http_and_caps_its_length(trade: TestClient) -> None:
    """HTTP 취소는 사유 필수(없음·공백 422 REASON_REQUIRED)·500자 초과 422 — 상태 무변"""
    po_id = raw_po("ISSUED")
    missing = _api_transition(trade, po_id, to="CANCELLED")
    blank = _api_transition(trade, po_id, to="CANCELLED", reason="  ")
    assert missing.status_code == blank.status_code == 422
    assert missing.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    assert _api_transition(trade, po_id, to="CANCELLED", reason="x" * 501).status_code == 422
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po_id) == "ISSUED"


def test_transition_events_carry_only_the_whitelisted_keys_and_no_reason_text(
    trade: TestClient,
) -> None:
    """전이 이벤트 payload는 화이트리스트 키뿐 — 사유 원문·금액·통화가 없다"""
    po = create_po_via_api(trade)
    secret_reason = "사유-원문-절대-이벤트에-없어야-함"
    assert _api_transition(trade, po["id"], to="CANCELLED", reason=secret_reason).status_code == 200
    events = _rows(
        "SELECT payload::text FROM events WHERE aggregate_type = 'purchase_orders' AND aggregate_id = :i",
        i=po["id"],
    )
    assert len(events) == 2
    for (payload,) in events:
        assert (
            secret_reason not in payload
            and "total_cost" not in payload
            and "currency" not in payload
        )
    import json

    statuses = [json.loads(p) for (p,) in events]
    assert all(set(item) <= set(PAYLOAD_KEYS) for item in statuses)
    assert {item["to_status"] for item in statuses} == {"ISSUED", "CANCELLED"}


def test_a_stale_version_is_409_and_the_same_key_replays_the_first_result(
    trade: TestClient,
) -> None:
    """낡은 version 409 · 같은 키 재전송(더블클릭)은 최초 결과 그대로(이력 1행) · 같은 키 다른 본문 409 · 키 결여 400 · 없는 id 404"""
    po = create_po_via_api(trade)
    meta = trade.patch(
        f"{PO}/{po['id']}/meta", json={"version": po["version"], "internal_note": "n"}
    )
    assert meta.status_code == 200  # version이 올랐으니 옛 version은 낡았다
    stale = trade.post(
        f"{PO}/{po['id']}/transitions",
        json={"to": "CANCELLED", "version": po["version"], "reason": "r"},
        headers=idem(),
    )
    assert stale.status_code == 409
    fresh = meta.json()["version"]
    key = idem()
    body = {"to": "CANCELLED", "version": fresh, "reason": "r"}
    first = trade.post(f"{PO}/{po['id']}/transitions", json=body, headers=key)
    second = trade.post(f"{PO}/{po['id']}/transitions", json=body, headers=key)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert len(_log_rows(po["id"])) == 2  # 탄생 + 취소 1행
    assert (
        trade.post(
            f"{PO}/{po['id']}/transitions", json={**body, "reason": "다른"}, headers=key
        ).status_code
        == 409
    )
    assert trade.post(f"{PO}/{po['id']}/transitions", json=body).status_code == 400
    assert trade.post(f"{PO}/999999/transitions", json=body, headers=idem()).status_code == 404


def test_transitions_do_not_recheck_the_supplier_type(trade: TestClient) -> None:
    """OC·취소는 신규 약정이 아니라 기존 약정의 기록·해소 — 공급사 유형이 해제된 뒤에도 전이는 통과한다(재검증은 생성 1회)"""
    supplier = create_supplier(types=("SUPPLIER",))
    po = create_po_via_api(trade, supplier, [create_purchase_priced_sku()])
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p"),
            {"p": supplier},
        )
    ok = _api_transition(
        trade, po["id"], to="SUPPLIER_CONFIRMED", oc_received_on=today_kst().isoformat()
    )
    assert ok.status_code == 200


def test_a_live_successor_blocks_cancelling_a_po(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """후속(수입선적·입고 문서 대역)이 살아 있으면 취소 409 CANCEL.SUCCESSOR_ALIVE — 죽은 후속은 막지 않는다. (S3-2·S4-1이 CHILD_LINKS에 실제 후속을 등록하는 자리)"""
    with fake_successors(monkeypatch, fk_column="po_id", parent=DocKind.PURCHASE_ORDER) as fake:
        po = create_po_via_api(trade)
        child = fake.add(po["id"], status="ISSUED")
        blocked = _api_transition(trade, po["id"], to="CANCELLED", reason="r")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        fake.set_status(child, "CANCELLED")
        assert _api_transition(trade, po["id"], to="CANCELLED", reason="r").status_code == 200


# ── 메타: OC 열 편집 규칙 ────────────────────────────────────────────────────


def test_oc_columns_are_editable_only_while_supplier_confirmed(trade: TestClient) -> None:
    """OC 일자·참조는 공급사 확인 상태에서만 고친다 — 발행 중·취소 후는 422(메모·담당자는 어느 상태에서나 가능). 일자는 경계를 지키고 비울 수 없으며 참조는 비울 수 있다"""
    po = create_po_via_api(trade)
    url = f"{PO}/{po['id']}/meta"
    issued_try = trade.patch(url, json={"version": po["version"], "oc_reference": "X"})
    assert issued_try.status_code == 422
    confirmed = _api_transition(
        trade,
        po["id"],
        to="SUPPLIER_CONFIRMED",
        oc_received_on=today_kst().isoformat(),
        oc_reference="OC-1",
    ).json()
    version = confirmed["version"]
    edit = trade.patch(url, json={"version": version, "oc_reference": "OC-2"})
    assert edit.status_code == 200 and edit.json()["oc_reference"] == "OC-2"
    version = edit.json()["version"]
    assert trade.patch(url, json={"version": version, "oc_received_on": None}).status_code == 422
    future = (today_kst() + timedelta(days=1)).isoformat()
    assert trade.patch(url, json={"version": version, "oc_received_on": future}).status_code == 422
    before_doc = (today_kst() - timedelta(days=400)).isoformat()
    assert (
        trade.patch(url, json={"version": version, "oc_received_on": before_doc}).status_code == 422
    )
    cleared = trade.patch(url, json={"version": version, "oc_reference": None})
    assert cleared.status_code == 200 and cleared.json()["oc_reference"] is None
    version = cleared.json()["version"]
    cancelled = _api_transition(trade, po["id"], to="CANCELLED", reason="r").json()
    assert (
        trade.patch(url, json={"version": cancelled["version"], "oc_reference": "late"}).status_code
        == 422
    )
    note = trade.patch(url, json={"version": cancelled["version"], "internal_note": "취소 후 메모"})
    assert note.status_code == 200 and note.json()["internal_note"] == "취소 후 메모"


def test_admin_can_transition_too_and_actor_lands_in_the_history() -> None:
    """관리자도 전이한다(상시 통과) — 이력 행위자는 요청한 사용자다"""
    with logged_in(RoleCode.ADMIN) as admin:
        po_id = raw_po("ISSUED")
        response = _api_transition(admin, po_id, to="CANCELLED", reason="관리자 취소")
        assert response.status_code == 200
        actor = _log_rows(po_id)[-1][4]
        assert (
            actor is not None and _scalar("SELECT count(*) FROM users WHERE id = :i", i=actor) == 1
        )
