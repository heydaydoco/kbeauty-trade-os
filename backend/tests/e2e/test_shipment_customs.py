"""A·K. 통관 기록 API — 사실 기록·수리일 유일 원천·파생(적재기한·PARTIAL)·미래일 422·정정 사유·삭제·통관 생존 취소 가드
(S3-2 PR-4a / GC-A14·A18 / ADR-0074·0080 / design-A A8 / design-C C9 / design-integrated X-02·X-03·R-06·R-10·R-16·R-18·R-26).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import customs_flow
from tests.factories.shipments import (
    SHIPMENTS,
    cancel,
    confirmed_so,
    created,
    release,
    rows,
    scalar,
    so_status,
)
from tests.factories.trade import create_supplier, idem, logged_in, unique

pytestmark = pytest.mark.group_a


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _day(offset: int) -> str:
    return (today_kst() + timedelta(days=offset)).isoformat()


def _shipment(client: TestClient) -> dict[str, Any]:
    so = confirmed_so((10,))
    body = created(client, so["id"], [(so["line_ids"][0], 4)])
    body["so_id"] = so["id"]
    return body


def _record(client: TestClient, shipment_id: int, **override: Any) -> Any:
    body: dict[str, Any] = {
        "declaration_kind": "EXPORT",
        "declaration_no": unique("ED").replace("-", ""),
        "declared_on": _day(-5),
    }
    body.update(override)
    return client.post(f"{SHIPMENTS}/{shipment_id}/customs-records", json=body, headers=idem())


def _patch(client: TestClient, shipment_id: int, record: dict[str, Any], **changes: Any) -> Any:
    return client.patch(
        f"{SHIPMENTS}/{shipment_id}/customs-records/{record['id']}",
        json={"version": record["version"], **changes},
    )


def _delete(client: TestClient, shipment_id: int, record: dict[str, Any], **body: Any) -> Any:
    return client.request(
        "DELETE",
        f"{SHIPMENTS}/{shipment_id}/customs-records/{record['id']}",
        json={"version": record["version"], **body},
    )


def _rows(client: TestClient, shipment_id: int) -> dict[str, dict[str, Any]]:
    board = client.get(f"{SHIPMENTS}/{shipment_id}/milestones").json()
    return {row["milestone_type"]: row for row in board["rows"]}


def test_a_customs_record_is_a_fact_record_with_a_normalized_number(trade: TestClient) -> None:
    """추가 201 — 신고번호 앞뒤 공백 제거·대문자 정규화, 관세사 이름 동반, 목록 Page·상세 통관 요약, 이벤트 `shipments.customs.recorded`(금액 0).
    세율·세액·HS 필드는 응답에도 요청에도 없다"""
    sid = _shipment(trade)["id"]
    broker = create_supplier(types=("CUSTOMS_BROKER",), name_ko="합성 관세사")
    response = _record(
        trade,
        sid,
        declaration_no="  ab-12-345x ",
        accepted_on=_day(-4),
        customs_broker_partner_id=broker,
        note="1차 분할 신고\n잔량은 2차",
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["declaration_no"] == "AB-12-345X"
    assert record["customs_broker"] == {"partner_id": broker, "name": "합성 관세사"}
    assert not {k for k in record if "tax" in k or "duty" in k or "hs" in k or "rate" in k}
    page = trade.get(f"{SHIPMENTS}/{sid}/customs-records").json()
    assert page["total"] == 1 and page["size"] == 50 and page["items"][0]["id"] == record["id"]
    summary = trade.get(f"{SHIPMENTS}/{sid}").json()["customs_summary"]
    assert summary == {"live_count": 1, "pending_count": 0, "latest_accepted_on": _day(-4)}
    events = rows(
        "SELECT payload FROM events WHERE event_type = 'shipments.customs.recorded' AND aggregate_id = :i",
        i=sid,
    )
    assert events == [
        {
            "payload": {
                "shipment_id": sid,
                "customs_record_id": record["id"],
                "declaration_kind": "EXPORT",
                "accepted": True,
            }
        }
    ]
    assert (
        trade.post(
            f"{SHIPMENTS}/{sid}/customs-records",
            json={
                "declaration_kind": "EXPORT",
                "declaration_no": "X1",
                "declared_on": _day(-1),
                "tax_amount": 100,
            },
            headers=idem(),
        ).status_code
        == 422
    )  # extra=forbid — 세액 필드는 구조적으로 없다


def test_input_rules_are_422_and_store_nothing(trade: TestClient) -> None:
    """구분 불일치 422 KIND_MISMATCH · 수리일 < 신고일 422 ACCEPT_BEFORE_DECLARE · 신고번호 안쪽 공백·보이지 않는 글자 422 · 관세사 아닌 거래처 422 ·
    메모 제어문자 422 — 저장 0"""
    sid = _shipment(trade)["id"]
    cases = [
        ({"declaration_kind": "IMPORT"}, "SHIPMENTS.CUSTOMS.KIND_MISMATCH"),
        ({"accepted_on": _day(-6)}, "SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE"),
        ({"declaration_no": "AB 12"}, "COMMON.VALIDATION.INVALID_FIELD"),
        ({"declaration_no": "AB​12"}, "COMMON.VALIDATION.INVALID_FIELD"),
        ({"declaration_no": "AB\u008512"}, "COMMON.VALIDATION.INVALID_FIELD"),
        ({"customs_broker_partner_id": create_supplier()}, "COMMON.VALIDATION.INVALID_FIELD"),
        ({"note": "메모\u0007"}, "COMMON.VALIDATION.INVALID_FIELD"),
    ]
    for override, code in cases:
        response = _record(trade, sid, **override)
        assert response.status_code == 422 and _code(response) == code, override
    # 수리일 < 신고일은 서비스 1차 검사(필드 안내 동반)에서 나온다 — DB CHECK 번역(2차 방어선, detail 없음)에 기대지 않는다
    before = _record(trade, sid, accepted_on=_day(-6))
    assert "accepted_on" in before.json()["error"]["detail"]
    assert scalar("SELECT count(*) FROM customs_records") == 0


@pytest.mark.golden
def test_gc_a18_future_customs_dates_are_rejected_without_slack(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GC-37(A18 — R-18) — KST 오늘 10-03: 수리일 10-04 = 422 DATE_IN_FUTURE(여유 0), 신고일 10-04도 422, 10-03은 성공(경계 포함)"""
    sid = _shipment(trade)["id"]
    monkeypatch.setattr(customs_flow, "today_kst", lambda: date(2026, 10, 3))
    for override in (
        {"declared_on": "2026-10-02", "accepted_on": "2026-10-04"},
        {"declared_on": "2026-10-04"},
    ):
        response = _record(trade, sid, **override)
        assert response.status_code == 422, override
        assert _code(response) == "SHIPMENTS.CUSTOMS.DATE_IN_FUTURE"
    ok = _record(trade, sid, declared_on="2026-10-03", accepted_on="2026-10-03")
    assert ok.status_code == 201, ok.text


def test_a_duplicate_declaration_is_409_even_with_other_casing(trade: TestClient) -> None:
    """(구분, 신고번호) 살아 있는 기록 유일 — 대소문자만 다른 번호도 같은 번호(정규화) → 409 DECLARATION_DUPLICATE(부분 유니크 번역, 500 아님)"""
    sid = _shipment(trade)["id"]
    assert _record(trade, sid, declaration_no="ABC-1").status_code == 201
    other = _shipment(trade)["id"]
    duplicate = _record(trade, other, declaration_no="abc-1")
    assert (
        duplicate.status_code == 409
        and _code(duplicate) == "SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE"
    )


@pytest.mark.golden
def test_gc_a18_acceptance_drives_the_loading_deadline_with_partial_and_max_fulfilment(
    trade: TestClient,
) -> None:
    """GC-A18(배선 층 — X-02·R-06·R-10): 통관 2건(수리 D−20·미수리) → 신고수리 실적 = MIN D−20·PARTIAL(미수리 1)·적재기한 = D+10(MIN 유지) /
    미수리 기록이 수리되면 CLEARED / ETD 실적 D−1·B/L 실적 D 둘 다면 이행일 = MAX → MET / 수리일 정정(사유)으로 적재기한 재계산(저장 0)"""
    shipment = _shipment(trade)
    sid = shipment["id"]
    first = _record(trade, sid, declared_on=_day(-21), accepted_on=_day(-20)).json()
    pending = _record(trade, sid, declared_on=_day(-21)).json()
    rows_by = _rows(trade, sid)
    cleared = rows_by["CUSTOMS_CLEARED"]
    assert cleared["actual"] == _day(-20) and cleared["customs_state"] == "PARTIAL"
    assert cleared["customs_pending_count"] == 1
    loading = rows_by["LOADING_DEADLINE"]
    assert loading["derived"]["value"] == _day(10) and loading["fulfilment"] == "OPEN"
    assert loading["days_left"] == 10 and loading["is_overdue"] is False
    assert (
        _patch(trade, sid, pending, accepted_on=_day(-2)).status_code == 200
    )  # 첫 수리 입력 = 기록(사유 불요)
    assert _rows(trade, sid)["CUSTOMS_CLEARED"]["customs_state"] == "CLEARED"
    assert release(trade, sid).status_code == 200
    for milestone_type, actual in (("ETD", _day(-1)), ("BL_ISSUED", _day(0))):
        response = trade.post(
            f"{SHIPMENTS}/{sid}/milestones/{milestone_type}/actual",
            json={"actual_on": actual},
            headers=idem(),
        )
        assert response.status_code == 200, response.text
    assert _rows(trade, sid)["LOADING_DEADLINE"]["fulfilment"] == "MET"
    corrected = _patch(
        trade, sid, first, declared_on=_day(-41), accepted_on=_day(-40), reason="수리필증 재확인"
    )
    assert corrected.status_code == 200, corrected.text
    loading = _rows(trade, sid)["LOADING_DEADLINE"]
    assert loading["derived"]["value"] == _day(-10)  # MIN 재계산(정정된 수리일 + 30)
    assert loading["fulfilment"] == "MET_LATE"  # 이행일 MAX(D) > 기한(D−10)
    assert scalar("SELECT count(*) FROM milestones WHERE milestone_type = 'LOADING_DEADLINE'") == 0


def test_corrections_need_a_reason_and_leave_an_audit_trail(trade: TestClient) -> None:
    """C9 — 기존 수리일 변경·신고번호·신고일 정정은 사유 필수(422 REASON_REQUIRED), 사유가 있으면 audit `shipments.customs.corrected`(전후 날짜·번호·사유).
    수리일 첫 입력은 사유 불요(기록 — audit은 남는다). 메모만 바꾸면 사유 불요·이벤트 0. 같은 값만 보내면 무변경(version 그대로)"""
    sid = _shipment(trade)["id"]
    record = _record(trade, sid, declaration_no="CR-1").json()
    first_accept = _patch(trade, sid, record, accepted_on=_day(-3))
    assert (
        first_accept.status_code == 200 and first_accept.json()["version"] == record["version"] + 1
    )
    record = first_accept.json()
    for change in (
        {"accepted_on": _day(-2)},
        {"accepted_on": None},
        {"declaration_no": "CR-2"},
        {"declared_on": _day(-4)},
    ):
        response = _patch(trade, sid, record, **change)
        assert _code(response) == "SHIPMENTS.CUSTOMS.REASON_REQUIRED", change
    fixed = _patch(trade, sid, record, accepted_on=_day(-2), reason="수리일 오기 정정")
    assert fixed.status_code == 200
    record = fixed.json()
    noted = _patch(trade, sid, record, note="관세사 확인 완료")
    assert noted.status_code == 200 and noted.json()["note"] == "관세사 확인 완료"
    record = noted.json()
    same = _patch(trade, sid, record, note="관세사 확인 완료", declaration_no="cr-1")
    assert same.status_code == 200 and same.json()["version"] == record["version"]
    audits = rows(
        "SELECT detail FROM audit_log WHERE action = 'shipments.customs.corrected' AND entity_id = :i"
        " ORDER BY id",
        i=sid,
    )
    assert len(audits) == 3
    assert audits[1]["detail"]["facts"] == {"accepted_on": {"before": _day(-3), "after": _day(-2)}}
    assert audits[1]["detail"]["reason"] == "수리일 오기 정정"
    assert audits[2]["detail"]["changed"] == ["note"] and audits[2]["detail"]["facts"] == {}
    events = scalar(
        "SELECT count(*) FROM events WHERE event_type = 'shipments.customs.recorded' AND aggregate_id = :i",
        i=sid,
    )
    assert events == 3  # 추가 1 + 사실 정정 2(메모 정정은 이벤트 0)


@pytest.mark.group_j
def test_a_stale_version_is_409_and_other_shipments_records_are_404(trade: TestClient) -> None:
    """J-10·K 부모-자식 — 옛 version 정정·삭제 409, 다른 선적 경로로 기록 접근 404(부작용 0)"""
    sid = _shipment(trade)["id"]
    other = _shipment(trade)["id"]
    record = _record(trade, sid).json()
    assert _patch(trade, sid, record, note="첫 메모").status_code == 200
    assert _patch(trade, sid, record, note="옛 화면").status_code == 409
    assert _delete(trade, sid, record, reason="옛 화면").status_code == 409
    assert _patch(trade, other, record, note="남의 선적").status_code == 404
    assert (
        _delete(trade, other, {**record, "version": record["version"] + 1}, reason="x").status_code
        == 404
    )
    assert scalar("SELECT note FROM customs_records WHERE id = :i", i=record["id"]) == "첫 메모"


@pytest.mark.golden
def test_gc_a14_live_customs_record_blocks_cancel_until_deleted_with_a_reason(
    trade: TestClient,
) -> None:
    """GC-A14(4a 가산 — R-16): 살아 있는 통관 기록 → 선적 취소 409 CUSTOMS_RECORD_ALIVE(`detail.declaration_nos`) → 사유 없는 삭제 422 →
    사유와 함께 삭제 204(audit) → 취소 200·SO 확정 복귀 → 취소된 선적에 통관 추가 409 NOT_ACTIVE"""
    shipment = _shipment(trade)
    sid = shipment["id"]
    record = _record(trade, sid, declaration_no="LIVE-1").json()
    assert _rows(trade, sid)["CUSTOMS_CLEARED"]["customs_state"] == "PARTIAL"  # 미수리 1건
    blocked = cancel(trade, sid)
    assert (
        blocked.status_code == 409 and _code(blocked) == "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE"
    )
    assert blocked.json()["error"]["detail"]["declaration_nos"] == ["LIVE-1"]
    no_reason = _delete(trade, sid, record)
    assert no_reason.status_code == 422 and _code(no_reason) == "SHIPMENTS.CUSTOMS.REASON_REQUIRED"
    assert _delete(trade, sid, record, reason="신고 취하").status_code == 204
    audit = rows(
        "SELECT detail FROM audit_log WHERE action = 'shipments.customs.deleted' AND entity_id = :i",
        i=sid,
    )
    assert (
        audit[0]["detail"]["reason"] == "신고 취하"
        and audit[0]["detail"]["declaration_no"] == "LIVE-1"
    )
    assert trade.get(f"{SHIPMENTS}/{sid}/customs-records").json()["total"] == 0
    # 삭제된 기록은 수리 파생에서 빠진다(살아 있는 기록만 — X-02)
    assert _rows(trade, sid)["CUSTOMS_CLEARED"]["customs_state"] == "NONE"
    done = cancel(trade, sid)
    assert done.status_code == 200, done.text
    assert so_status(shipment["so_id"]) == "CONFIRMED"
    after = _record(trade, sid)
    assert after.status_code == 409 and _code(after) == "SHIPMENTS.SHIPMENT.NOT_ACTIVE"
    assert _record(trade, 999999).status_code == 404


@pytest.mark.group_j
def test_a_deleted_declaration_number_reenters_as_a_new_record(trade: TestClient) -> None:
    """J-13 — 삭제(soft delete)한 (구분, 신고번호)를 다시 기록하면 부활이 아니라 신규 행(삭제 행은 그대로)"""
    sid = _shipment(trade)["id"]
    record = _record(trade, sid, declaration_no="RE-1").json()
    assert _delete(trade, sid, record, reason="오기").status_code == 204
    again = _record(trade, sid, declaration_no="RE-1")
    assert again.status_code == 201 and again.json()["id"] != record["id"]
    assert scalar("SELECT count(*) FROM customs_records WHERE declaration_no = 'RE-1'") == 2


@pytest.mark.group_k
def test_logistics_records_customs_and_viewers_cannot() -> None:
    """ADR-0079 — 통관 기록 쓰기 = 무역·물류(관세사 응대는 물류 실무), 인증·조회 전용 = 403·부작용 0, 조회는 전 역할"""
    with logged_in(RoleCode.TRADE) as trade:
        sid = _shipment(trade)["id"]
    with logged_in(RoleCode.LOGISTICS) as logistics:
        assert _record(logistics, sid).status_code == 201
    for role in (RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert _record(client, sid).status_code == 403
            assert client.get(f"{SHIPMENTS}/{sid}/customs-records").status_code == 200
    assert scalar("SELECT count(*) FROM customs_records") == 1
