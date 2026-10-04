"""A(모듈 마커 `group_a` — 3건 전부)·K(역할별 버튼 근거 시험 2건 `group_k` 추가 — 선적·OEM/세트). 마일스톤·통관·OEM 생산 일정·품목군 세트 화면 경로
(S3-2 PR-4b — 프런트 소비 계약, 백엔드 앱 코드 무변경).

선적 상세 마일스톤 타임라인·대화상자·통관 섹션·변경 이력·발주 상세 '생산 일정'·품목군 '마일스톤 세트'가 보내는 **본문 그대로**
(행이 있으면 보드 행의 version, 없으면 생략 / 롤오버 사유 / 실적 지우기 = 명시적 null / 통관 추가는 빈칸 키 생략 / 삭제 본문 {version, reason})
를 실 HTTP로 흘려, 화면이 읽는 응답 칸(보드 행 `milestone_id`·`version`·`holiday`·`customs_state`·`rollover_count`·`unnotified_rollovers`·
시각형 `scan_date`·`local_date`, 목록 `etd`·`eta`, 취소 409 detail, OEM 보드 `allowed_actions`)을 같은 순서로 확인한다.
화면 쪽 단언은 vitest(`frontend/src/**/milestone-*.test.tsx`·`shipment-detail.milestones/customs.test.tsx`·OEM·세트 시험)가 맡는다.
KST 자정 경계는 `pin_today_kst`로 고정한다(PR-4a 적대 검토 ⑩ 승계).
"""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from tests.factories.shipments import SHIPMENTS, confirmed_so, created, release, scalar
from tests.factories.trade import create_po_via_api, create_supplier, idem, logged_in, unique
from tests.support.factories import create_item_profile
from tests.support.kst import pin_today_kst

pytestmark = pytest.mark.group_a

PO = "/api/v1/purchase-orders"
PROFILES = "/api/v1/item-profiles"
#: 화면(lib/milestone.ts MilestoneRow)이 읽는 보드 행 칸 — 서버 응답에 전부 있어야 한다(추측 필드 0).
ROW_KEYS = {
    "milestone_type",
    "kind",
    "value_shape",
    "applicable",
    "planned",
    "actual",
    "effective",
    "derived",
    "scan_date",
    "local_date",
    "customs_state",
    "customs_pending_count",
    "days_left",
    "is_overdue",
    "unknown_reason",
    "fulfilment",
    "holiday",
    "rollover_count",
    "unnotified_rollovers",
    "order_warning",
    "milestone_id",
    "version",
    "input_source",
}


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_today_kst(monkeypatch, sys.modules[__name__])


def _day(offset: int) -> str:
    return (today_kst() + timedelta(days=offset)).isoformat()


def _rows(client: TestClient, shipment_id: int) -> dict[str, dict[str, Any]]:
    response = client.get(f"{SHIPMENTS}/{shipment_id}/milestones")
    assert response.status_code == 200, response.text
    return {row["milestone_type"]: row for row in response.json()["rows"]}


def _version_part(row: dict[str, Any]) -> dict[str, int]:
    """화면 `versionPart` — 행이 있으면 그 version, 없으면 생략."""
    return {"version": row["version"]} if row["milestone_id"] is not None else {}


def _write(
    client: TestClient, shipment_id: int, milestone_type: str, mode: str, body: dict[str, Any]
) -> Any:
    return client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/{milestone_type}/{mode}",
        json=body,
        headers={"Idempotency-Key": unique("scr")},
    )


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _declare(country: str, year: int, holidays: list[tuple[str, str]]) -> None:
    with logged_in(RoleCode.ADMIN) as admin:
        response = admin.put(
            f"/api/v1/holidays/{country}/{year}",
            json={
                "source_url": "https://example.gov/holidays",
                "verified_on": "2026-09-01",
                "holidays": [{"holiday_on": d, "name": n} for d, n in holidays],
            },
            headers=idem(),
        )
        assert response.status_code == 200, response.text


@pytest.mark.group_k
def test_screen_flow_shipment_timeline_rollover_notice_holiday_customs_and_cancel_guards() -> None:
    """A·K — 물류 계정이 화면 순서대로: 상세 allowed_actions(마일스톤 3종) → 계획 초안 {} → ETA 계획(행 version) → 롤오버 사유 없음 422 →
    사유 200(롤오버·미통보 1) → 통보 기록(미통보 0·이력 notices) → 휴일 HOLIDAY+이름 / 미선언 UNVERIFIED → 목록 etd·eta → 출고지시·ETD 실적
    → 통관 추가(빈칸 키 생략) PARTIAL → 취소 409 통관 생존(detail) → 첫 수리일(사유 없음) CLEARED → 삭제 {version, reason} →
    취소 409 실적 생존(detail) → 실적 지우기(명시적 null) → 취소 200. 조회 역할 allowed_actions = 마일스톤 동작 0."""
    with logged_in(RoleCode.TRADE) as trade:
        so = confirmed_so((10, 5))
        shipment = created(trade, so["id"], [(so["line_ids"][0], 4)])  # KR → US
        other = created(trade, so["id"], [(so["line_ids"][1], 2)], dest="JP")
    sid = shipment["id"]
    with logged_in(RoleCode.VIEWER) as viewer:
        assert not {"EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"} & set(
            viewer.get(f"{SHIPMENTS}/{sid}").json()["allowed_actions"]
        )
    with logged_in(RoleCode.LOGISTICS) as logi:
        detail = logi.get(f"{SHIPMENTS}/{sid}").json()
        assert {"EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"} <= set(detail["allowed_actions"])
        board = detail["milestones"]
        assert {row["milestone_type"] for row in board["rows"]} >= {"ETA", "PAYMENT_DUE"}
        for row in board["rows"]:
            assert set(row) == ROW_KEYS, row["milestone_type"]
        assert set(detail["customs_summary"]) == {
            "live_count",
            "pending_count",
            "latest_accepted_on",
        }

        # 계획 초안(본문 {}) → 저장형 행이 생겨 version이 붙는다(값 없음)
        draft = logi.post(f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={}, headers=idem())
        assert draft.status_code == 200, draft.text
        eta = {r["milestone_type"]: r for r in draft.json()["rows"]}["ETA"]
        assert eta["milestone_id"] is not None and eta["planned"] is None

        # 계획 입력(행 version) → {board, change}
        first = _write(logi, sid, "ETA", "plan", {"planned_on": "2027-01-04", **_version_part(eta)})
        assert first.status_code == 200, first.text
        assert first.json()["change"]["change_kind"] == "PLAN_SET"
        eta = {r["milestone_type"]: r for r in first.json()["board"]["rows"]}["ETA"]

        # 롤오버 — 사유 없음 422(화면은 빈칸 제출 불가) → 사유 200
        no_reason = _write(
            logi, sid, "ETA", "plan", {"planned_on": "2027-01-05", **_version_part(eta)}
        )
        assert (
            no_reason.status_code == 422
            and _code(no_reason) == "SHIPMENTS.MILESTONE.REASON_REQUIRED"
        )
        rolled = _write(
            logi,
            sid,
            "ETA",
            "plan",
            {"planned_on": "2027-01-05", **_version_part(eta), "reason": "선사 스케줄 변경"},
        )
        assert rolled.status_code == 200, rolled.text
        change = rolled.json()["change"]
        eta = {r["milestone_type"]: r for r in rolled.json()["board"]["rows"]}["ETA"]
        assert (eta["rollover_count"], eta["unnotified_rollovers"]) == (1, 1)
        # 낡은 version(대화상자를 연 순간의 값) → 409, 화면은 '최신 내용 불러오기'
        stale = _write(
            logi,
            sid,
            "ETA",
            "plan",
            {"planned_on": "2027-01-06", "version": eta["version"] - 1, "reason": "x"},
        )
        assert stale.status_code == 409 and _code(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"

        # 통보 기록(화면 noticeBody — 상대 미지정이면 키 생략, 여러 줄 요지)
        notice = logi.post(
            f"{SHIPMENTS}/{sid}/milestone-changes/{change['id']}/notices",
            json={"occurred_on": _day(0), "summary": "포워더에 메일로 알림\n회신 대기"},
            headers=idem(),
        )
        assert notice.status_code == 201, notice.text
        assert _rows(logi, sid)["ETA"]["unnotified_rollovers"] == 0
        history = logi.get(f"{SHIPMENTS}/{sid}/milestone-changes").json()
        assert history["size"] == 50
        top = history["items"][0]
        assert (
            top["change_kind"] == "PLAN_CHANGED"
            and top["old"] == "2027-01-04"
            and top["new"] == "2027-01-05"
        )
        assert [n["summary"] for n in top["notices"]] == ["포워더에 메일로 알림\n회신 대기"]

    # 휴일 배지 3분기의 원천 — 도착국(US) 선언·휴일 = HOLIDAY+이름 / 평일 = CLEAR / 미선언(JP) = UNVERIFIED
    _declare("US", 2027, [("2027-01-05", "Synthetic Holiday")])
    with logged_in(RoleCode.LOGISTICS) as logi:
        assert _rows(logi, sid)["ETA"]["holiday"] == {
            "flag": "HOLIDAY",
            "country": "US",
            "name": "Synthetic Holiday",
        }
        board = logi.get(f"{SHIPMENTS}/{sid}/milestones").json()
        assert board["holiday_summary"] == {"holiday": 1, "unverified": 0}
        oid = other["id"]
        o_eta = _rows(logi, oid)["ETA"]
        assert (
            _write(
                logi, oid, "ETA", "plan", {"planned_on": "2027-01-05", **_version_part(o_eta)}
            ).status_code
            == 200
        )
        assert _rows(logi, oid)["ETA"]["holiday"] == {
            "flag": "UNVERIFIED",
            "country": "JP",
            "name": None,
        }

        # 목록 ETD·ETA 열(R-3b-3) — 유효값 문자열 + basis
        listed = {
            item["id"]: item for item in logi.get(f"{SHIPMENTS}?so_id={so['id']}").json()["items"]
        }
        assert listed[sid]["eta"] == {"value": "2027-01-05", "basis": "PLANNED"}
        assert listed[sid]["etd"] is None

        # 출고지시 → ETD 실적(행 없음 = version 생략)
        assert release(logi, sid).status_code == 200
        etd = _rows(logi, sid)["ETD"]
        recorded = _write(logi, sid, "ETD", "actual", {"actual_on": _day(0), **_version_part(etd)})
        assert recorded.status_code == 200, recorded.text
        listed = {
            item["id"]: item for item in logi.get(f"{SHIPMENTS}?so_id={so['id']}").json()["items"]
        }
        assert listed[sid]["etd"] == {"value": _day(0), "basis": "ACTUAL"}

        # 통관 추가(화면 본문 — 구분 = 선적 구분, 빈칸 키 생략) → 신고수리 PARTIAL·미수리 1
        added = logi.post(
            f"{SHIPMENTS}/{sid}/customs-records",
            json={
                "declaration_kind": "EXPORT",
                "declaration_no": " ab-2001 ",
                "declared_on": _day(-1),
            },
            headers=idem(),
        )
        assert added.status_code == 201, added.text
        record = added.json()
        assert record["declaration_no"] == "AB-2001" and record["accepted_on"] is None
        customs_row = _rows(logi, sid)["CUSTOMS_CLEARED"]
        assert customs_row["input_source"] == "CUSTOMS_RECORD"
        # 미수리 기록 1건뿐 — 부분 수리 배지 근거(R-06)·실적 없음(MIN은 NULL 무시 — '완료' 표시 근거 0, 적대 검토 low ⑫)
        assert (customs_row["customs_state"], customs_row["customs_pending_count"]) == (
            "PARTIAL",
            1,
        )
        assert customs_row["actual"] is None

    with logged_in(RoleCode.TRADE) as trade:
        blocked = trade.post(
            f"{SHIPMENTS}/{sid}/transitions",
            json={
                "to_status": "CANCELLED",
                "version": scalar("SELECT version FROM shipments WHERE id = :s", s=sid),
                "reason": "고객 요청",
            },
            headers=idem(),
        )
        assert (
            blocked.status_code == 409
            and _code(blocked) == "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE"
        )
        assert blocked.json()["error"]["detail"]["declaration_nos"] == ["AB-2001"]

    with logged_in(RoleCode.LOGISTICS) as logi:
        # 수리일 첫 입력 = 사유 없이 {version, accepted_on}
        patched = logi.patch(
            f"{SHIPMENTS}/{sid}/customs-records/{record['id']}",
            json={"version": record["version"], "accepted_on": _day(0)},
        )
        assert patched.status_code == 200, patched.text
        customs_row = _rows(logi, sid)["CUSTOMS_CLEARED"]
        assert (customs_row["customs_state"], customs_row["actual"]) == ("CLEARED", _day(0))
        # 기존 수리일 지우기 = 사유 필수
        no_reason = logi.patch(
            f"{SHIPMENTS}/{sid}/customs-records/{record['id']}",
            json={"version": patched.json()["version"], "accepted_on": None},
        )
        assert (
            no_reason.status_code == 422 and _code(no_reason) == "SHIPMENTS.CUSTOMS.REASON_REQUIRED"
        )
        # 삭제 = DELETE 본문 {version, reason}(화면 apiDelete body)
        deleted = logi.request(
            "DELETE",
            f"{SHIPMENTS}/{sid}/customs-records/{record['id']}",
            json={"version": patched.json()["version"], "reason": "중복 입력"},
        )
        assert deleted.status_code == 204, deleted.text

    with logged_in(RoleCode.TRADE) as trade:
        version = scalar("SELECT version FROM shipments WHERE id = :s", s=sid)
        blocked = trade.post(
            f"{SHIPMENTS}/{sid}/transitions",
            json={"to_status": "CANCELLED", "version": version, "reason": "고객 요청"},
            headers=idem(),
        )
        assert blocked.status_code == 409 and _code(blocked) == "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED"
        assert blocked.json()["error"]["detail"]["milestone_types"] == ["ETD"]
        # 실적 지우기 = {actual_on: null, version, reason}
        etd = _rows(trade, sid)["ETD"]
        cleared = _write(
            trade,
            sid,
            "ETD",
            "actual",
            {"actual_on": None, **_version_part(etd), "reason": "오입력"},
        )
        assert (
            cleared.status_code == 200
            and cleared.json()["change"]["change_kind"] == "ACTUAL_CORRECTED"
        )
        done = trade.post(
            f"{SHIPMENTS}/{sid}/transitions",
            json={"to_status": "CANCELLED", "version": version, "reason": "고객 요청"},
            headers=idem(),
        )
        assert done.status_code == 200, done.text
        assert "EDIT_MILESTONES" not in done.json()["allowed_actions"]


def test_screen_flow_datetime_cutoff_sends_utc_instant_and_tz_and_reads_scan_and_local_dates() -> (
    None
):
    """A — 시각형(서류마감): 화면 변환 결과 `{planned_at: UTC ISO('Z'), tz}`(KR 출발 기본 Asia/Seoul) → 행 planned `{at_utc, tz}`·
    scan_date·local_date·D-N, 별칭 tz는 서버 정규 이름으로 돌아온다(화면은 서버 값을 그대로 보인다)."""
    with logged_in(RoleCode.TRADE) as trade:
        so = confirmed_so((10,))
        sid = created(trade, so["id"], [(so["line_ids"][0], 4)])["id"]
        at = f"{_day(5)}T08:00:00.000Z"  # 벽시계 17:00 KST
        written = _write(trade, sid, "DOC_CUTOFF", "plan", {"planned_at": at, "tz": "Asia/Seoul"})
        assert written.status_code == 200, written.text
        row = {r["milestone_type"]: r for r in written.json()["board"]["rows"]}["DOC_CUTOFF"]
        assert row["value_shape"] == "DATETIME"
        assert row["planned"]["tz"] == "Asia/Seoul" and row["planned"]["at_utc"].startswith(
            f"{_day(5)}T08:00:00"
        )
        assert (row["scan_date"], row["local_date"], row["days_left"], row["is_overdue"]) == (
            _day(5),
            _day(5),
            5,
            False,
        )
        closing = _write(
            trade,
            sid,
            "CARGO_CLOSING",
            "plan",
            {"planned_at": f"{_day(6)}T01:00:00.000Z", "tz": "Asia/Saigon"},
        )
        assert closing.status_code == 200, closing.text
        row = {r["milestone_type"]: r for r in closing.json()["board"]["rows"]}["CARGO_CLOSING"]
        assert row["planned"]["tz"] == "Asia/Ho_Chi_Minh"
        assert row["local_date"] == _day(6) and row["unknown_reason"] is None


@pytest.mark.group_k
def test_screen_flow_oem_schedule_section_and_item_profile_milestone_set() -> None:
    """A — 발주 상세 '생산 일정': OEM 보드 4행·allowed_actions(무역 = EDIT_MILESTONES / 조회 = 없음 — 버튼 근거), 계획 → {board, change},
    일반 구매 발주 보드 422(화면은 섹션을 그리지 않는다). 품목군 '마일스톤 세트': 관리자 추가 201·제거 204, 무역 추가 403(화면은 버튼 0)."""
    with logged_in(RoleCode.TRADE) as trade:
        po = create_po_via_api(trade, create_supplier(types=("OEM",)), po_kind="OEM_PRODUCTION")
        board = trade.get(f"{PO}/{po['id']}/milestones").json()
        assert [r["milestone_type"] for r in board["rows"]] == [
            "RAW_MATERIAL_READY",
            "FILLING",
            "PACKING",
            "OUTGOING_INSPECTION",
        ]
        assert board["allowed_actions"] == ["EDIT_MILESTONES"]
        assert board["holiday_summary"] == {"holiday": 0, "unverified": 0}
        written = trade.post(
            f"{PO}/{po['id']}/milestones/FILLING/plan",
            json={"planned_on": _day(10)},
            headers=idem(),
        )
        assert written.status_code == 200, written.text
        assert set(written.json()) == {"board", "change"}
        assert written.json()["board"]["allowed_actions"] == ["EDIT_MILESTONES"]
        purchase = create_po_via_api(trade, create_supplier())
        plain = trade.get(f"{PO}/{purchase['id']}/milestones")
        assert plain.status_code == 422 and _code(plain) == "SHIPMENTS.MILESTONE.OWNER_NOT_OEM"
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.get(f"{PO}/{po['id']}/milestones").json()["allowed_actions"] == []
    # 물류는 선적 일정은 쓰지만 PO(OEM) 쓰기는 0(X-16) — 보드 버튼 근거 [] + 직접 요청 403(적대 검토 low ⑬)
    with logged_in(RoleCode.LOGISTICS) as logi:
        assert logi.get(f"{PO}/{po['id']}/milestones").json()["allowed_actions"] == []
        denied = logi.post(
            f"{PO}/{po['id']}/milestones/FILLING/plan",
            json={"planned_on": _day(11), "version": 1},
            headers=idem(),
        )
        assert denied.status_code == 403

    profile = create_item_profile(unique("PRF"))
    with logged_in(RoleCode.TRADE) as trade:
        denied = trade.post(
            f"{PROFILES}/{profile}/milestone-types", json={"milestone_type": "ETD"}, headers=idem()
        )
        assert denied.status_code == 403
        assert trade.get(f"{PROFILES}/{profile}/milestone-types").json()["items"] == []
    with logged_in(RoleCode.ADMIN) as admin:
        added = admin.post(
            f"{PROFILES}/{profile}/milestone-types", json={"milestone_type": "ETA"}, headers=idem()
        )
        assert added.status_code == 201, added.text
        assert set(added.json()) == {"id", "item_profile_id", "milestone_type", "created_at"}
        removed = admin.delete(f"{PROFILES}/{profile}/milestone-types/{added.json()['id']}")
        assert removed.status_code == 204
        assert admin.get(f"{PROFILES}/{profile}/milestone-types").json()["items"] == []
