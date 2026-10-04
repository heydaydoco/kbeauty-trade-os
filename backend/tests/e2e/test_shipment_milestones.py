"""A·H·J·K. 선적 마일스톤 API — 계획·롤오버·실적·파생 재계산·휴일 경고·계획 초안·통보 기록·실적 생존 취소 가드
(S3-2 PR-4a / WBS DoD② / GC-A14·A18·A19·A20 / ADR-0080·0082·0083 / design-B B3·B7·B8·B9·B12·B16 / design-integrated §9 R-01·R-05·R-06·R-18·R-19·R-20).

DoD② "ETA 현지 연휴 → 경고"를 **API 층**에서 닫는다(화면 배지는 PR-4b). 파생값은 저장하지 않으므로 '재계산'은 읽기 결과의 변화다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import milestone_flow, milestone_view
from tests.factories.shipments import (
    SHIPMENTS,
    cancel,
    confirmed_so,
    created,
    release,
    rows,
    scalar,
    shipment_version,
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


def _shipment(
    client: TestClient, *, terms: str = "TT_DEFERRED", released: bool = False
) -> dict[str, Any]:
    so = confirmed_so((10,), terms=terms)
    body = created(client, so["id"], [(so["line_ids"][0], 4)])
    body["so_id"] = so["id"]
    if released:
        assert release(client, body["id"]).status_code == 200
    return body


def _plan(
    client: TestClient,
    shipment_id: int,
    milestone_type: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/{milestone_type}/plan",
        json=body,
        headers={"Idempotency-Key": key or unique("mp")},
    )


def _actual(
    client: TestClient,
    shipment_id: int,
    milestone_type: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return client.post(
        f"{SHIPMENTS}/{shipment_id}/milestones/{milestone_type}/actual",
        json=body,
        headers={"Idempotency-Key": key or unique("ma")},
    )


def _rows(client: TestClient, shipment_id: int) -> dict[str, dict[str, Any]]:
    response = client.get(f"{SHIPMENTS}/{shipment_id}/milestones")
    assert response.status_code == 200, response.text
    return {row["milestone_type"]: row for row in response.json()["rows"]}


def _version(client: TestClient, shipment_id: int, milestone_type: str) -> int | None:
    version = _rows(client, shipment_id)[milestone_type]["version"]
    return int(version) if version is not None else None


def _changes(shipment_id: int) -> int:
    return int(
        scalar(
            "SELECT count(*) FROM milestone_changes mc JOIN milestones m ON m.id = mc.milestone_id"
            " WHERE m.shipment_id = :s",
            s=shipment_id,
        )
    )


def _day(offset: int) -> str:
    return (today_kst() + timedelta(days=offset)).isoformat()


# ── 보드 모양·적용 표 ───────────────────────────────────────────────────────────


def test_the_board_has_eleven_rows_in_flow_order_with_applicability(trade: TestClient) -> None:
    """보드 = 저장형 8 + 파생 3 = 11행(업무 흐름 순서) — 수출선적: PSI 적용·수입 세금 비적용, T/T라 제시기한 비적용, 신고수리 실적 입력처 = 통관 기록.
    선적 상세에도 같은 보드가 내장되고 통관 요약이 붙는다"""
    shipment = _shipment(trade)
    response = trade.get(f"{SHIPMENTS}/{shipment['id']}/milestones")
    board = response.json()
    assert [row["milestone_type"] for row in board["rows"]] == [
        "DOC_CUTOFF",
        "CARGO_CLOSING",
        "PSI",
        "CUSTOMS_CLEARED",
        "LOADING_DEADLINE",
        "ETD",
        "BL_ISSUED",
        "PRESENTATION_DEADLINE",
        "ETA",
        "IMPORT_TAX_DUE",
        "PAYMENT_DUE",
    ]
    rows_by = {row["milestone_type"]: row for row in board["rows"]}
    assert rows_by["PSI"]["applicable"] and not rows_by["IMPORT_TAX_DUE"]["applicable"]
    assert not rows_by["PRESENTATION_DEADLINE"]["applicable"]
    assert (
        rows_by["DOC_CUTOFF"]["value_shape"] == "DATETIME"
        and rows_by["ETD"]["value_shape"] == "DATE"
    )
    assert {
        r["kind"]
        for r in board["rows"]
        if r["milestone_type"] in {"LOADING_DEADLINE", "PAYMENT_DUE", "PRESENTATION_DEADLINE"}
    } == {"DERIVED"}
    assert rows_by["CUSTOMS_CLEARED"]["input_source"] == "CUSTOMS_RECORD"
    assert rows_by["CUSTOMS_CLEARED"]["customs_state"] == "NONE"
    # 기산점(ETD) 미정 → 대금만기 UNKNOWN(대체 금지 — 0·오늘로 그리지 않는다), 적재기한 수리 전 UNKNOWN
    assert rows_by["PAYMENT_DUE"]["derived"] == {
        "status": "UNKNOWN",
        "value": None,
        "basis": None,
        "reason_code": "ANCHOR_PENDING",
    }
    assert rows_by["LOADING_DEADLINE"]["derived"]["reason_code"] == "NOT_CLEARED"
    assert board["today_kst"] == today_kst().isoformat()
    detail = trade.get(f"{SHIPMENTS}/{shipment['id']}").json()
    assert detail["milestones"]["rows"] == board["rows"]
    assert detail["customs_summary"] == {
        "live_count": 0,
        "pending_count": 0,
        "latest_accepted_on": None,
    }
    assert {"EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"} <= set(detail["allowed_actions"])


def test_lc_shipments_show_the_presentation_deadline_as_unknown_not_hidden(
    trade: TestClient,
) -> None:
    """L/C 결제 — 제시기한은 숨기지 않고 UNKNOWN `LC_TERMS_NOT_REGISTERED`(운영 L/C = S3-3 lc_terms 미공급, B/L+21 대체 금지), 대금만기도 같은 사유"""
    shipment = _shipment(trade, terms="LC")
    rows_by = _rows(trade, shipment["id"])
    assert rows_by["PRESENTATION_DEADLINE"]["applicable"] is True
    assert rows_by["PRESENTATION_DEADLINE"]["derived"]["reason_code"] == "LC_TERMS_NOT_REGISTERED"
    assert rows_by["PAYMENT_DUE"]["derived"]["reason_code"] == "LC_TERMS_NOT_REGISTERED"


# ── 계획·롤오버 (A20) ─────────────────────────────────────────────────────────


@pytest.mark.golden
def test_gc_a20_rollover_history_reason_and_same_key_same_change(trade: TestClient) -> None:
    """GC-A20 — ETD 계획 11-05(PLAN_SET) → 11-12 사유 없음 422 → 사유와 함께 롤오버(PLAN_CHANGED) → **같은 키 재요청 = 같은 change.id·이력 1행**
    → 이력 2행(최신순)·롤오버 1회·통보 미연결 1. 같은 값 재입력은 no-op(change = null, 이력 0), 이전 값으로의 재유입 = 새 이력 행(J)"""
    shipment = _shipment(trade)
    sid = shipment["id"]
    first = _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    assert first.status_code == 200, first.text
    assert first.json()["change"]["change_kind"] == "PLAN_SET"
    version = _version(trade, sid, "ETD")
    missing = _plan(trade, sid, "ETD", {"planned_on": "2026-11-12", "version": version})
    assert missing.status_code == 422 and _code(missing) == "SHIPMENTS.MILESTONE.REASON_REQUIRED"
    # 서비스 1차 검사(필드 안내 동반) — DB CHECK 번역(2차 방어선, detail 없음)에 기대지 않는다
    assert "reason" in missing.json()["error"]["detail"]
    key = unique("roll")
    body = {"planned_on": "2026-11-12", "version": version, "reason": "선사 스케줄 변경"}
    rolled = _plan(trade, sid, "ETD", body, key=key)
    assert rolled.status_code == 200 and rolled.json()["change"]["change_kind"] == "PLAN_CHANGED"
    again = _plan(trade, sid, "ETD", body, key=key)
    assert again.json()["change"]["id"] == rolled.json()["change"]["id"]
    assert _changes(sid) == 2
    etd = _rows(trade, sid)["ETD"]
    assert etd["planned"] == "2026-11-12" and etd["rollover_count"] == 1
    assert etd["unnotified_rollovers"] == 1
    page = trade.get(f"{SHIPMENTS}/{sid}/milestone-changes").json()
    assert page["total"] == 2 and page["size"] == 50
    assert [item["change_kind"] for item in page["items"]] == ["PLAN_CHANGED", "PLAN_SET"]
    assert page["items"][0]["old"] == "2026-11-05" and page["items"][0]["new"] == "2026-11-12"
    assert page["items"][0]["reason"] == "선사 스케줄 변경" and page["items"][0]["notices"] == []
    noop = _plan(
        trade, sid, "ETD", {"planned_on": "2026-11-12", "version": _version(trade, sid, "ETD")}
    )
    assert noop.status_code == 200 and noop.json()["change"] is None and _changes(sid) == 2
    events = rows(
        "SELECT payload FROM events WHERE event_type = 'shipments.milestone.changed'"
        " AND aggregate_id = :i ORDER BY id",
        i=sid,
    )
    assert [e["payload"]["change_kind"] for e in events] == ["PLAN_SET", "PLAN_CHANGED"]
    assert events[1]["payload"]["old"]["on"] == "2026-11-05"
    assert not {k for e in events for k in e["payload"] if "amount" in k or "cost" in k}
    # 재유입 = 신규(J) — 이전 값(11-05)으로 되돌리는 롤오버도 새 키면 새 이력 행·새 change.id(과거 행 재사용·중복 제거 없음)
    back = _plan(
        trade,
        sid,
        "ETD",
        {"planned_on": "2026-11-05", "version": _version(trade, sid, "ETD"), "reason": "선사 원복"},
    )
    assert back.status_code == 200 and back.json()["change"]["change_kind"] == "PLAN_CHANGED"
    earlier = {first.json()["change"]["id"], rolled.json()["change"]["id"]}
    assert back.json()["change"]["id"] not in earlier
    assert _changes(sid) == 3 and _rows(trade, sid)["ETD"]["rollover_count"] == 2


def test_change_history_filters_and_404_for_unknown_shipment(trade: TestClient) -> None:
    """K — 변경 이력 Page 필터(종류·변경 종류)·없는 선적 404"""
    sid = _shipment(trade)["id"]
    _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    _plan(trade, sid, "ETA", {"planned_on": "2026-12-05"})
    page = trade.get(
        f"{SHIPMENTS}/{sid}/milestone-changes", params={"milestone_type": "ETA"}
    ).json()
    assert page["total"] == 1 and page["items"][0]["milestone_type"] == "ETA"
    page = trade.get(
        f"{SHIPMENTS}/{sid}/milestone-changes", params={"change_kind": "PLAN_CHANGED"}
    ).json()
    assert page["total"] == 0
    assert trade.get(f"{SHIPMENTS}/999999/milestone-changes").status_code == 404
    assert trade.get(f"{SHIPMENTS}/999999/milestones").status_code == 404


@pytest.mark.golden
def test_gc_a20_derived_values_are_never_editable_api_and_db(trade: TestClient) -> None:
    """GC-21(A20) 덮어쓰기 금지 2중 — 파생 3종 계획·실적 쓰기 = 422 DERIVED_NOT_EDITABLE(API) + DB 직접 INSERT도 CHECK 거부"""
    sid = _shipment(trade)["id"]
    for derived in ("LOADING_DEADLINE", "PAYMENT_DUE", "PRESENTATION_DEADLINE"):
        response = _plan(trade, sid, derived, {"planned_on": "2026-11-05"})
        assert response.status_code == 422, derived
        assert _code(response) == "SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE"
        assert _code(_actual(trade, sid, derived, {"actual_on": _day(0)})) == (
            "SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE"
        )
    with pytest.raises(IntegrityError), owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO milestones (shipment_id, milestone_type, planned_on)"
                " VALUES (:s, 'PAYMENT_DUE', '2026-11-05')"
            ),
            {"s": sid},
        )
    assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=sid) == 0


def test_types_outside_the_shipment_kind_are_not_applicable(trade: TestClient) -> None:
    """수출선적의 수입 세금 납부기한·선적의 OEM 생산 종류 = 422 TYPE_NOT_APPLICABLE, 모르는 종류 = 스키마 422"""
    sid = _shipment(trade)["id"]
    for milestone_type in ("IMPORT_TAX_DUE", "FILLING"):
        response = _plan(trade, sid, milestone_type, {"planned_on": "2026-11-05"})
        assert _code(response) == "SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE", milestone_type
    assert _plan(trade, sid, "ATD", {"planned_on": "2026-11-05"}).status_code == 422


def test_value_shapes_and_time_zones(trade: TestClient) -> None:
    """N-04 — 날짜형에 시각·시간대 422 VALUE_SHAPE_MISMATCH, 시각형에 날짜 422, 시간대 누락·비IANA 422 TIMEZONE_INVALID, UTC 오프셋 없는 시각 = 스키마 422.
    시각형은 UTC로 저장하고 tz를 함께 낸다"""
    sid = _shipment(trade)["id"]
    shape = "SHIPMENTS.MILESTONE.VALUE_SHAPE_MISMATCH"
    tz_code = "SHIPMENTS.MILESTONE.TIMEZONE_INVALID"
    assert (
        _code(_plan(trade, sid, "ETD", {"planned_at": "2026-11-05T00:00:00Z", "tz": "UTC"}))
        == shape
    )
    assert _code(_plan(trade, sid, "ETD", {"planned_on": "2026-11-05", "tz": "UTC"})) == shape
    assert _code(_plan(trade, sid, "DOC_CUTOFF", {"planned_on": "2026-11-05"})) == shape
    assert _code(_plan(trade, sid, "DOC_CUTOFF", {"planned_at": "2026-11-05T00:00:00Z"})) == tz_code
    for bad in ("Mars/Olympus", "localtime", " Asia/Seoul"):
        response = _plan(
            trade, sid, "DOC_CUTOFF", {"planned_at": "2026-11-05T00:00:00Z", "tz": bad}
        )
        assert _code(response) == tz_code, bad
    assert (
        _plan(
            trade, sid, "DOC_CUTOFF", {"planned_at": "2026-11-05T09:00:00", "tz": "UTC"}
        ).status_code
        == 422
    )
    ok = _plan(
        trade, sid, "DOC_CUTOFF", {"planned_at": "2026-11-05T18:00:00+09:00", "tz": "Asia/Seoul"}
    )
    assert ok.status_code == 200, ok.text
    row = _rows(trade, sid)["DOC_CUTOFF"]
    assert row["planned"] == {"at_utc": "2026-11-05T09:00:00Z", "tz": "Asia/Seoul"}
    assert row["scan_date"] == "2026-11-05" and row["local_date"] == "2026-11-05"
    assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=sid) == 1


def test_alias_time_zones_are_stored_under_their_canonical_name(trade: TestClient) -> None:
    """적대 검토 반영 ① — 별칭·폐지 이름은 정규 이름으로 저장(Asia/Saigon → Asia/Ho_Chi_Minh), 국가 대표 이름은 그대로(Europe/Amsterdam)"""
    sid = _shipment(trade)["id"]
    at = "2026-11-05T02:00:00Z"
    assert (
        _plan(trade, sid, "DOC_CUTOFF", {"planned_at": at, "tz": "Asia/Saigon"}).status_code == 200
    )
    assert _rows(trade, sid)["DOC_CUTOFF"]["planned"]["tz"] == "Asia/Ho_Chi_Minh"
    assert (
        scalar(
            "SELECT tz FROM milestones WHERE shipment_id = :s AND milestone_type = 'DOC_CUTOFF'",
            s=sid,
        )
        == "Asia/Ho_Chi_Minh"
    )
    assert (
        _plan(trade, sid, "CARGO_CLOSING", {"planned_at": at, "tz": "Europe/Amsterdam"}).status_code
        == 200
    )
    assert _rows(trade, sid)["CARGO_CLOSING"]["planned"]["tz"] == "Europe/Amsterdam"


def _set_stored_tz(shipment_id: int, milestone_type: str, tz: str) -> None:
    """서비스 우회 — 이미지 tzdata에서 이름이 빠진 상황을 흉내 낸다(`tz_format` CHECK는 통과하는 모르는 이름)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE milestones SET tz = :z WHERE shipment_id = :s AND milestone_type = :t"),
            {"z": tz, "s": shipment_id, "t": milestone_type},
        )


@pytest.mark.group_a
def test_an_unresolvable_stored_time_zone_never_breaks_reads_writes_or_cancel(
    trade: TestClient,
) -> None:
    """적대 검토 반영 ① — 저장된 tz를 앱 tzdata가 모르면 그 행만 UNKNOWN `TZ_UNRESOLVED`(scan_date·local_date·days_left·is_overdue null —
    KST 추정 0), 상세·보드·다른 쓰기·취소는 200. 새 tz로 계획을 다시 쓰면(롤오버 사유) 해석된다 — 탈출로"""
    sid = _shipment(trade)["id"]
    assert (
        _plan(
            trade,
            sid,
            "DOC_CUTOFF",
            {"planned_at": "2026-11-05T09:00:00+09:00", "tz": "Asia/Seoul"},
        ).status_code
        == 200
    )
    _set_stored_tz(sid, "DOC_CUTOFF", "Mars/Olympus_Mons")
    row = _rows(trade, sid)["DOC_CUTOFF"]
    assert row["unknown_reason"] == "TZ_UNRESOLVED"
    assert row["planned"] == {"at_utc": "2026-11-05T00:00:00Z", "tz": "Mars/Olympus_Mons"}
    assert row["effective"]["value"].startswith("2026-11-05T00:00:00")
    assert (row["scan_date"], row["local_date"], row["days_left"], row["is_overdue"]) == (
        None,
        None,
        None,
        None,
    )
    detail = trade.get(f"{SHIPMENTS}/{sid}")
    assert detail.status_code == 200, detail.text
    assert _plan(trade, sid, "ETD", {"planned_on": "2026-11-10"}).status_code == 200
    # 탈출로 — 같은 시각을 해석되는 tz로 다시 쓰면(계획 변경 = 사유) 행이 정상 판정으로 돌아온다
    fixed = _plan(
        trade,
        sid,
        "DOC_CUTOFF",
        {
            "planned_at": "2026-11-05T09:00:00+09:00",
            "tz": "Asia/Seoul",
            "version": _version(trade, sid, "DOC_CUTOFF"),
            "reason": "시간대 정정",
        },
    )
    assert fixed.status_code == 200, fixed.text
    assert _rows(trade, sid)["DOC_CUTOFF"]["unknown_reason"] is None
    # 해석 불가 행이 있어도 취소는 된다(취소 응답도 상세 = 보드 조립)
    other = _shipment(trade)["id"]
    _plan(trade, other, "CARGO_CLOSING", {"planned_at": "2026-11-05T00:00:00Z", "tz": "Asia/Seoul"})
    _set_stored_tz(other, "CARGO_CLOSING", "Atlantis/Lost_City")
    cancelled = cancel(trade, other)
    assert cancelled.status_code == 200, cancelled.text
    assert {r["milestone_type"]: r for r in cancelled.json()["milestones"]["rows"]}[
        "CARGO_CLOSING"
    ]["unknown_reason"] == "TZ_UNRESOLVED"


def test_a_stale_or_missing_row_version_is_a_conflict(trade: TestClient) -> None:
    """J-10 — 행이 있으면 그 version을 보내야 한다(누락·옛 version = 409), 없는 행에 version을 보내도 409(화면이 본 상태와 다름)"""
    sid = _shipment(trade)["id"]
    assert _plan(trade, sid, "ETA", {"planned_on": "2026-12-01", "version": 1}).status_code == 409
    assert _plan(trade, sid, "ETA", {"planned_on": "2026-12-01"}).status_code == 200
    body = {"planned_on": "2026-12-02", "reason": "변경"}
    assert _plan(trade, sid, "ETA", body).status_code == 409
    current = _version(trade, sid, "ETA")
    assert current is not None
    assert _plan(trade, sid, "ETA", {**body, "version": current + 1}).status_code == 409
    assert _plan(trade, sid, "ETA", {**body, "version": current}).status_code == 200
    assert _rows(trade, sid)["ETA"]["planned"] == "2026-12-02"


# ── 실적 (R-01·R-18·A20) ──────────────────────────────────────────────────────


def test_release_bound_actuals_wait_for_the_release_order(trade: TestClient) -> None:
    """R-01 — ETD·B/L 발행·ETA 실적은 출고지시 뒤에만(계획 단계 422 ACTUAL_BEFORE_RELEASE), 서류마감·PSI 실적은 계획 단계에도 받는다"""
    sid = _shipment(trade)["id"]
    for milestone_type in ("ETD", "BL_ISSUED", "ETA"):
        response = _actual(trade, sid, milestone_type, {"actual_on": _day(0)})
        assert response.status_code == 422, milestone_type
        assert _code(response) == "SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE"
    assert _actual(trade, sid, "PSI", {"actual_on": _day(-1)}).status_code == 200
    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat()
    assert (
        _actual(trade, sid, "DOC_CUTOFF", {"actual_at": now_iso, "tz": "Asia/Seoul"}).status_code
        == 200
    )
    assert release(trade, sid).status_code == 200
    recorded = _actual(trade, sid, "ETD", {"actual_on": _day(0)})
    assert (
        recorded.status_code == 200
        and recorded.json()["change"]["change_kind"] == "ACTUAL_RECORDED"
    )


def test_customs_cleared_actual_comes_only_from_customs_records(trade: TestClient) -> None:
    """X-02 — 신고수리 실적 직접 입력 = 422 ACTUAL_FROM_CUSTOMS_RECORD(계획은 마일스톤 행에 받는다)"""
    sid = _shipment(trade)["id"]
    response = _actual(trade, sid, "CUSTOMS_CLEARED", {"actual_on": _day(0)})
    assert _code(response) == "SHIPMENTS.MILESTONE.ACTUAL_FROM_CUSTOMS_RECORD"
    # 서비스 1차 검사(입력처 안내 동반) — DB CHECK `customs_actual_from_records` 번역(2차 방어선, detail 없음)에 기대지 않는다
    assert "milestone_type" in response.json()["error"]["detail"]
    assert _plan(trade, sid, "CUSTOMS_CLEARED", {"planned_on": _day(3)}).status_code == 200


def test_actual_body_must_name_the_value_field(trade: TestClient) -> None:
    """빈 본문은 실적을 지우지 않는다 — 값 필드를 명시해야 한다(지우기 = 명시적 null + 사유)"""
    sid = _shipment(trade)["id"]
    response = _actual(trade, sid, "PSI", {})
    assert response.status_code == 422 and "actual_on" in response.json()["error"]["detail"]
    assert (
        _actual(trade, sid, "PSI", {"actual_on": None}).json()["change"] is None
    )  # 없는 실적 지우기 = no-op


@pytest.mark.golden
def test_gc_a20_actual_dates_allow_one_day_slack_but_instants_none(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GC-20·GC-35(A20 — R-18) — 날짜형 실적: KST 오늘 10-03이면 10-04 성공·10-05 422 ACTUAL_IN_FUTURE / 시각형: 현재 05:00Z면 05:00Z 성공·05:00:01Z 422"""
    sid = _shipment(trade, released=True)["id"]
    monkeypatch.setattr(milestone_flow, "today_kst", lambda: date(2026, 10, 3))
    future = _actual(trade, sid, "ETD", {"actual_on": "2026-10-05"})
    assert future.status_code == 422 and _code(future) == "SHIPMENTS.MILESTONE.ACTUAL_IN_FUTURE"
    assert _actual(trade, sid, "ETD", {"actual_on": "2026-10-04"}).status_code == 200
    now = datetime(2026, 10, 10, 5, 0, tzinfo=UTC)
    monkeypatch.setattr(milestone_flow, "utcnow", lambda: now)
    late = _actual(
        trade, sid, "CARGO_CLOSING", {"actual_at": "2026-10-10T05:00:01Z", "tz": "Asia/Seoul"}
    )
    assert late.status_code == 422 and _code(late) == "SHIPMENTS.MILESTONE.ACTUAL_IN_FUTURE"
    exact = _actual(
        trade, sid, "CARGO_CLOSING", {"actual_at": "2026-10-10T05:00:00Z", "tz": "Asia/Seoul"}
    )
    assert exact.status_code == 200, exact.text


@pytest.mark.golden
def test_gc_a20_an_actual_recalculates_the_payment_due(trade: TestClient) -> None:
    """GC-02·03(A20 — '실적 입력 시 후속 재계산') — T/T 잔금 ETD−7: ETD 계획 → 대금만기 PLANNED 기준 / ETD 실적 → ACTUAL 기준으로 다시 계산(저장 0 — 읽기 결과의 변화)"""
    sid = _shipment(trade, released=True)["id"]
    planned = (today_kst() + timedelta(days=20)).isoformat()
    _plan(trade, sid, "ETD", {"planned_on": planned})
    due = _rows(trade, sid)["PAYMENT_DUE"]["derived"]
    assert due["status"] == "OK" and due["basis"] == "PLANNED"
    assert due["value"] == (today_kst() + timedelta(days=13)).isoformat()
    recorded = _actual(
        trade, sid, "ETD", {"actual_on": _day(-1), "version": _version(trade, sid, "ETD")}
    )
    assert recorded.status_code == 200, recorded.text
    due = _rows(trade, sid)["PAYMENT_DUE"]["derived"]
    assert due["basis"] == "ACTUAL" and due["value"] == _day(-8)
    assert scalar("SELECT count(*) FROM milestones WHERE milestone_type = 'PAYMENT_DUE'") == 0


@pytest.mark.golden
def test_gc_a14_live_actual_blocks_cancel_until_corrected_with_a_reason(trade: TestClient) -> None:
    """GC-A14(4a 가산 — R-01) — ETD 실적이 살아 있는 선적 취소 = 409 ACTUAL_RECORDED(`detail.milestone_types`) → 사유 없는 정정 422 →
    사유와 함께 실적 삭제(ACTUAL_CORRECTED, 이력 남음) → 취소 200 → SO 확정 복귀"""
    shipment = _shipment(trade, released=True)
    sid = shipment["id"]
    _actual(trade, sid, "ETD", {"actual_on": _day(0)})
    _actual(trade, sid, "ETA", {"actual_on": _day(0)})
    blocked = cancel(trade, sid)
    assert blocked.status_code == 409 and _code(blocked) == "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED"
    assert blocked.json()["error"]["detail"]["milestone_types"] == ["ETA", "ETD"]
    for milestone_type in ("ETD", "ETA"):
        version = _version(trade, sid, milestone_type)
        no_reason = _actual(trade, sid, milestone_type, {"actual_on": None, "version": version})
        assert _code(no_reason) == "SHIPMENTS.MILESTONE.REASON_REQUIRED"
        removed = _actual(
            trade, sid, milestone_type, {"actual_on": None, "version": version, "reason": "오입력"}
        )
        assert removed.json()["change"]["change_kind"] == "ACTUAL_CORRECTED"
    done = cancel(trade, sid)
    assert done.status_code == 200, done.text
    assert so_status(shipment["so_id"]) == "CONFIRMED"
    assert _changes(sid) == 4  # 기록 2 + 정정 2 — 이력은 남는다(IMMUTABLE)


def test_a_cancelled_shipment_takes_no_milestone_writes(trade: TestClient) -> None:
    """N-05 — 취소된 선적: 계획·실적·초안·통보 = 409 OWNER_NOT_ACTIVE(값 검증 422보다 먼저 — ADR-0079 ⑧), 조회는 된다"""
    sid = _shipment(trade)["id"]
    change = _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"}).json()["change"]
    assert cancel(trade, sid).status_code == 200
    code = "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
    assert _code(_plan(trade, sid, "ETA", {"planned_on": "2026-12-01"})) == code
    assert (
        _code(_plan(trade, sid, "PAYMENT_DUE", {"planned_at": "bad"})) != code
    )  # 스키마 422가 먼저(FastAPI 검증)
    assert _code(_actual(trade, sid, "PSI", {"actual_on": "2999-01-01"})) == code
    draft = trade.post(f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={}, headers=idem())
    assert _code(draft) == code
    notice = trade.post(
        f"{SHIPMENTS}/{sid}/milestone-changes/{change['id']}/notices",
        json={"occurred_on": _day(1), "summary": " "},
        headers=idem(),
    )
    assert notice.status_code == 409 and _code(notice) == code
    assert trade.get(f"{SHIPMENTS}/{sid}/milestones").status_code == 200


# ── 휴일 경고 (DoD② — API 층, R-09 ETA·도착국만) ─────────────────────────────────────


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


@pytest.mark.golden
def test_dod2_gc_a19_eta_local_holiday_warns_both_ways_and_unverified(trade: TestClient) -> None:
    """DoD② "ETA 현지 연휴 → 경고"(GC-A19 배선 층): 도착국 US 2027 선언 + ETA = 휴일 → HOLIDAY+이름 / 평일로 롤오버 → CLEAR /
    도착국을 미선언 JP로 → UNVERIFIED(평일 아님). ETA 값은 옮기지 않는다(경고만 — 자동 순연 0). 다른 종류(ETD)는 휴일 판정 0(R-09)"""
    shipment = _shipment(trade)
    sid = shipment["id"]
    _declare("US", 2027, [("2027-01-04", "Observed New Year")])
    _plan(trade, sid, "ETD", {"planned_on": "2027-01-04"})  # 출발국 휴일 판정 없음(R-09)
    _plan(trade, sid, "ETA", {"planned_on": "2027-01-04"})
    board = trade.get(f"{SHIPMENTS}/{sid}/milestones").json()
    eta = {r["milestone_type"]: r for r in board["rows"]}["ETA"]
    assert eta["holiday"] == {"flag": "HOLIDAY", "country": "US", "name": "Observed New Year"}
    assert eta["planned"] == "2027-01-04" and eta["effective"]["value"] == "2027-01-04"
    assert board["holiday_summary"] == {"holiday": 1, "unverified": 0}
    assert {r["milestone_type"]: r for r in board["rows"]}["ETD"]["holiday"] is None
    _plan(
        trade,
        sid,
        "ETA",
        {"planned_on": "2027-01-05", "reason": "도착항 휴일 회피", "version": eta["version"]},
    )
    assert _rows(trade, sid)["ETA"]["holiday"] == {"flag": "CLEAR", "country": "US", "name": None}
    patched = trade.patch(
        f"{SHIPMENTS}/{sid}",
        json={"version": shipment_version(sid), "dest_country_code": "JP"},
    )
    assert patched.status_code == 200
    board = patched.json()["milestones"]
    eta = {r["milestone_type"]: r for r in board["rows"]}["ETA"]
    assert eta["holiday"] == {"flag": "UNVERIFIED", "country": "JP", "name": None}
    assert board["holiday_summary"] == {"holiday": 0, "unverified": 1}


@pytest.mark.golden
def test_gc_a19_a_holiday_on_the_payment_due_date_does_not_move_it(trade: TestClient) -> None:
    """GC-18(A19) — 대금만기가 도착국 휴일과 같은 날이어도 값 불변(자동 순연 0)·휴일 판정 대상 아님(대금만기·제시기한·적재기한은 휴일 미반영)"""
    sid = _shipment(trade)["id"]
    _declare("US", 2027, [("2027-02-08", "Synthetic Holiday")])
    _plan(trade, sid, "ETD", {"planned_on": "2027-02-15"})  # 잔금 = ETD − 7 = 02-08(휴일)
    due = _rows(trade, sid)["PAYMENT_DUE"]
    assert due["derived"]["value"] == "2027-02-08" and due["holiday"] is None


# ── 시각형 도과 (R-20·R-25) ─────────────────────────────────────────────────────


def test_datetime_overdue_compares_utc_instants_not_dates(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GC-36(R-20) — Cargo Closing 2026-10-11T00:00Z(LA): 2026-10-10T21:40Z(KST 10-11 06:40)에 조회 → 도과 아님(기준일 10-10은 지났지만
    기한 시각 전 → D-day), scan_date 10-10·local_date 10-10(LA 10-10 17:00) / 10-11T00:00:01Z → 도과"""
    sid = _shipment(trade)["id"]
    _plan(
        trade,
        sid,
        "CARGO_CLOSING",
        {"planned_at": "2026-10-11T00:00:00Z", "tz": "America/Los_Angeles"},
    )
    monkeypatch.setattr(milestone_view, "today_kst", lambda: date(2026, 10, 11))
    monkeypatch.setattr(
        milestone_view, "utcnow", lambda: datetime(2026, 10, 10, 21, 40, tzinfo=UTC)
    )
    row = _rows(trade, sid)["CARGO_CLOSING"]
    assert row["scan_date"] == "2026-10-10" and row["local_date"] == "2026-10-10"
    assert row["is_overdue"] is False and row["days_left"] == 0
    monkeypatch.setattr(
        milestone_view, "utcnow", lambda: datetime(2026, 10, 11, 0, 0, 1, tzinfo=UTC)
    )
    row = _rows(trade, sid)["CARGO_CLOSING"]
    assert row["is_overdue"] is True and row["days_left"] == -1


def test_date_rows_count_days_from_the_kst_today(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """날짜형 D-N = 유효일 − KST 오늘, 도과 = KST 오늘 > 유효일, 실적이 있으면 D-N·도과 null(완료)"""
    sid = _shipment(trade, released=True)["id"]
    _plan(trade, sid, "ETA", {"planned_on": "2026-10-10"})
    monkeypatch.setattr(milestone_view, "today_kst", lambda: date(2026, 10, 7))
    eta = _rows(trade, sid)["ETA"]
    assert (eta["days_left"], eta["is_overdue"]) == (3, False)
    monkeypatch.setattr(milestone_view, "today_kst", lambda: date(2026, 10, 11))
    eta = _rows(trade, sid)["ETA"]
    assert (eta["days_left"], eta["is_overdue"]) == (-1, True)
    monkeypatch.undo()
    _actual(trade, sid, "ETA", {"actual_on": _day(0), "version": eta["version"]})
    eta = _rows(trade, sid)["ETA"]
    assert eta["days_left"] is None and eta["is_overdue"] is None
    assert eta["effective"] == {"value": _day(0), "basis": "ACTUAL"}


def test_eta_before_etd_is_a_warning_only(trade: TestClient) -> None:
    """B8 ⑤ — ETA < ETD(날짜변경선)는 저장 성공 + `order_warning`(차단 0)"""
    sid = _shipment(trade)["id"]
    _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    assert _plan(trade, sid, "ETA", {"planned_on": "2026-11-04"}).status_code == 200
    assert _rows(trade, sid)["ETA"]["order_warning"] == "ETA_BEFORE_ETD"


# ── 계획 초안 (M4 — B16) ─────────────────────────────────────────────────────


def test_plan_draft_creates_empty_rows_once_and_skips_existing(trade: TestClient) -> None:
    """계획 초안 1클릭 — 품목군 세트가 없으면 구분별 적용 종류 전부(수출 7종)의 빈 계획 행, 이미 있는 종류는 건너뜀, 같은 키 1회,
    값이 없으므로 이력·이벤트 0. 자동 생성 0(SO 확정·선적 생성 TX는 마일스톤 행을 만들지 않는다 — X-11)"""
    sid = _shipment(trade)["id"]
    assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=sid) == 0
    _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    key = unique("draft")
    first = trade.post(
        f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={}, headers={"Idempotency-Key": key}
    )
    assert first.status_code == 200, first.text
    again = trade.post(
        f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={}, headers={"Idempotency-Key": key}
    )
    assert again.json() == first.json()
    types = {
        r["milestone_type"]
        for r in rows("SELECT milestone_type FROM milestones WHERE shipment_id = :s", s=sid)
    }
    assert types == {
        "DOC_CUTOFF",
        "CARGO_CLOSING",
        "PSI",
        "CUSTOMS_CLEARED",
        "ETD",
        "BL_ISSUED",
        "ETA",
    }
    assert _rows(trade, sid)["ETD"]["planned"] == "2026-11-05"  # 기존 값 보존
    assert _changes(sid) == 1  # ETD PLAN_SET 1행뿐(초안은 값 0 → 이력 0)
    assert (
        trade.post(
            f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={"x": 1}, headers=idem()
        ).status_code
        == 422
    )


def test_plan_draft_intersects_item_profile_sets_when_every_sku_has_one(trade: TestClient) -> None:
    """B16 — 라인 SKU 전부에 품목군 세트가 있으면 적용 종류 ∩ 세트 합집합(수출선적에 IMPORT_TAX_DUE 세트는 비적용이라 빠짐),
    세트 미정의 품목군이 하나라도 있으면 구분별 전부(누락보다 과다 — fail-closed)"""
    from tests.support.factories import create_item_profile

    so = confirmed_so((5, 5))
    profile_a = create_item_profile("PRF-A")
    profile_b = create_item_profile("PRF-B")
    with owner_engine.begin() as connection:
        for sku, profile in zip(so["sku_ids"], (profile_a, profile_b), strict=True):
            connection.execute(
                text("UPDATE skus SET item_profile_id = :p WHERE id = :s"), {"p": profile, "s": sku}
            )
        for profile, kinds in ((profile_a, ("ETD", "ETA")), (profile_b, ("PSI", "IMPORT_TAX_DUE"))):
            for kind in kinds:
                connection.execute(
                    text(
                        "INSERT INTO item_profile_milestone_types (profile_id, milestone_type)"
                        " VALUES (:p, :k)"
                    ),
                    {"p": profile, "k": kind},
                )
    shipment = created(trade, so["id"], [(line, 1) for line in so["line_ids"]])
    trade.post(f"{SHIPMENTS}/{shipment['id']}/milestones/plan-draft", json={}, headers=idem())
    assert {
        r["milestone_type"]
        for r in rows(
            "SELECT milestone_type FROM milestones WHERE shipment_id = :s", s=shipment["id"]
        )
    } == {"ETD", "ETA", "PSI"}
    other = confirmed_so((5,))
    unprofiled = created(trade, other["id"], [(other["line_ids"][0], 1)])
    trade.post(f"{SHIPMENTS}/{unprofiled['id']}/milestones/plan-draft", json={}, headers=idem())
    assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=unprofiled["id"]) == 7


# ── 통보 기록 (H — 기록, 발송 0) ───────────────────────────────────────────────


@pytest.mark.group_h
def test_a_rollover_notice_is_a_record_not_a_send(trade: TestClient) -> None:
    """H — 롤오버 통보 = comm_logs(SHIPMENT) 1행 + 연결 1행(1TX), **아웃박스·알림 0**(발송 코드 0), 같은 키 재요청 = 같은 결과·행 1,
    미통보 롤오버 배지 해소. 상대 거래처 유형(포워더 등)·오간 날 미래·빈 요지·보이지 않는 글자 = 422"""
    sid = _shipment(trade)["id"]
    _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    rolled = _plan(
        trade,
        sid,
        "ETD",
        {"planned_on": "2026-11-12", "reason": "선사 사정", "version": _version(trade, sid, "ETD")},
    ).json()["change"]
    forwarder = create_supplier(types=("FORWARDER",))
    url = f"{SHIPMENTS}/{sid}/milestone-changes/{rolled['id']}/notices"
    events_before = scalar("SELECT count(*) FROM events")
    alerts_before = scalar("SELECT count(*) FROM alerts")
    key = unique("notice")
    body = {
        "occurred_on": _day(0),
        "counterpart_partner_id": forwarder,
        "summary": "포워더 담당자에게 메일로 ETD 11-12 변경 안내\n회신 확인",
    }
    response = trade.post(url, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    assert trade.post(url, json=body, headers={"Idempotency-Key": key}).json() == response.json()
    notice = response.json()["notices"]
    assert len(notice) == 1 and notice[0]["partner_id"] == forwarder
    assert notice[0]["summary"].startswith("포워더 담당자에게")
    assert (
        scalar(
            "SELECT count(*) FROM comm_logs WHERE subject_type = 'SHIPMENT' AND subject_id = :s",
            s=sid,
        )
        == 1
    )
    assert (
        scalar("SELECT count(*) FROM milestone_change_notices WHERE change_id = :c", c=rolled["id"])
        == 1
    )
    assert scalar("SELECT count(*) FROM events") == events_before
    assert scalar("SELECT count(*) FROM alerts") == alerts_before
    assert _rows(trade, sid)["ETD"]["unnotified_rollovers"] == 0
    cert_agency = create_supplier(types=("CERT_AGENCY",))
    for bad, field in (
        ({**body, "counterpart_partner_id": cert_agency}, "counterpart_partner_id"),
        ({**body, "occurred_on": _day(1)}, "occurred_on"),
        ({**body, "summary": "   "}, "summary"),
        ({**body, "summary": "요지​"}, "summary"),
    ):
        rejected = trade.post(url, json=bad, headers=idem())
        assert rejected.status_code == 422 and field in rejected.json()["error"]["detail"], bad
    assert scalar("SELECT count(*) FROM comm_logs WHERE subject_type = 'SHIPMENT'") == 1


@pytest.mark.group_k
def test_notices_and_changes_are_scoped_to_their_shipment(trade: TestClient) -> None:
    """K 부모-자식 — 다른 선적의 변경 id로 통보 = 404·부작용 0(comm_logs 0)"""
    one = _shipment(trade)["id"]
    other = _shipment(trade)["id"]
    change = _plan(trade, one, "ETD", {"planned_on": "2026-11-05"}).json()["change"]
    response = trade.post(
        f"{SHIPMENTS}/{other}/milestone-changes/{change['id']}/notices",
        json={"occurred_on": _day(0), "summary": "통보"},
        headers=idem(),
    )
    assert response.status_code == 404
    assert scalar("SELECT count(*) FROM comm_logs WHERE subject_type = 'SHIPMENT'") == 0


@pytest.mark.group_k
def test_an_unknown_shipment_is_404_before_any_input_error(trade: TestClient) -> None:
    """ADR-0079 ⑧(401→403→404→409→422) — 없는 선적이면 파생 종류·미래 실적·빈 요지·구분 불일치·공백 든 신고번호 본문이어도
    전부 404(입력 422가 존재 판정을 앞지르지 않는다)"""
    missing = 999999
    assert _plan(trade, missing, "PAYMENT_DUE", {"planned_on": "2026-11-05"}).status_code == 404
    assert _actual(trade, missing, "ETD", {"actual_on": "2999-01-01"}).status_code == 404
    assert _actual(trade, missing, "CUSTOMS_CLEARED", {"actual_on": _day(0)}).status_code == 404
    draft = trade.post(f"{SHIPMENTS}/{missing}/milestones/plan-draft", json={}, headers=idem())
    assert draft.status_code == 404
    notice = trade.post(
        f"{SHIPMENTS}/{missing}/milestone-changes/1/notices",
        json={"occurred_on": "2999-01-01", "summary": " "},
        headers=idem(),
    )
    assert notice.status_code == 404
    customs = trade.post(
        f"{SHIPMENTS}/{missing}/customs-records",
        json={"declaration_kind": "IMPORT", "declaration_no": "a b", "declared_on": "2999-01-01"},
        headers=idem(),
    )
    assert customs.status_code == 404


@pytest.mark.group_k
def test_the_generic_comm_log_paths_never_see_shipment_notices(trade: TestClient) -> None:
    """K(R-05) — 범용 `/comm-logs`: SHIPMENT 주제 POST·목록 필터 = 스키마 422, 목록 기본 결과에서 제외, id 상세·PATCH·DELETE = 404(부작용 0),
    문서 COMM_LOG 첨부 = 422. 선적 통보는 선적 전용 통로로만 오간다"""
    sid = _shipment(trade)["id"]
    change = _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"}).json()["change"]
    trade.post(
        f"{SHIPMENTS}/{sid}/milestone-changes/{change['id']}/notices",
        json={"occurred_on": _day(0), "summary": "포워더 통보"},
        headers=idem(),
    )
    log_id = int(scalar("SELECT id FROM comm_logs WHERE subject_type = 'SHIPMENT'"))
    with logged_in(RoleCode.CERT) as cert:
        posted = cert.post(
            "/api/v1/comm-logs",
            json={
                "subject_type": "SHIPMENT",
                "subject_id": sid,
                "occurred_on": _day(0),
                "summary": "x",
            },
            headers=idem(),
        )
        assert posted.status_code == 422
        assert cert.get("/api/v1/comm-logs", params={"subject_type": "SHIPMENT"}).status_code == 422
        listed = cert.get("/api/v1/comm-logs").json()
        assert log_id not in {item["id"] for item in listed["items"]}
        assert cert.get(f"/api/v1/comm-logs/{log_id}").status_code == 404
        assert (
            cert.patch(
                f"/api/v1/comm-logs/{log_id}", json={"version": 1, "summary": "변조"}
            ).status_code
            == 404
        )
        assert cert.delete(f"/api/v1/comm-logs/{log_id}").status_code == 404
        attach = cert.post(
            "/api/v1/documents/links",
            json={
                "owner_type": "COMM_LOG",
                "owner_id": log_id,
                "document_type": "CFS",
                "url": "https://example.com/x.pdf",
            },
            headers=idem(),
        )
        assert attach.status_code == 422, attach.text
    assert scalar("SELECT summary FROM comm_logs WHERE id = :i", i=log_id) == "포워더 통보"
    assert scalar("SELECT deleted_at IS NULL FROM comm_logs WHERE id = :i", i=log_id) is True


# ── 권한·쿼리 수 ──────────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_logistics_records_schedules_but_viewers_and_cert_cannot() -> None:
    """ADR-0079 — 물류는 계획·실적·초안을 쓴다(일정·실적 = 물류 실무), 인증·조회 전용은 403·부작용 0"""
    with logged_in(RoleCode.TRADE) as trade:
        sid = _shipment(trade)["id"]
    with logged_in(RoleCode.LOGISTICS) as logistics:
        assert _plan(logistics, sid, "ETD", {"planned_on": "2026-11-05"}).status_code == 200
        assert _actual(logistics, sid, "PSI", {"actual_on": _day(0)}).status_code == 200
    for role in (RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert _plan(client, sid, "ETA", {"planned_on": "2026-12-01"}).status_code == 403
            assert client.get(f"{SHIPMENTS}/{sid}/milestones").status_code == 200
    assert _changes(sid) == 2


@pytest.mark.group_k
def test_the_board_query_count_does_not_grow_with_rows_or_history(trade: TestClient) -> None:
    """K(렌즈 7) — 보드 조회 질의 수는 마일스톤·변경 이력 수와 무관한 상수(N+1 0)"""
    from tests.support.sqlcount import count_statements

    small = _shipment(trade, released=True)["id"]
    big = _shipment(trade, released=True)["id"]
    # 같은 종류 구성(ETD·ETA — 휴일 판정 질의 포함)에서 행 수·이력 수만 다르게: 질의 수가 같아야 한다
    _plan(trade, small, "ETD", {"planned_on": "2026-11-05"})
    _plan(trade, small, "ETA", {"planned_on": "2026-12-05"})
    for milestone_type, planned in (
        ("ETD", "2026-11-05"),
        ("ETA", "2026-12-05"),
        ("PSI", "2026-10-20"),
    ):
        _plan(trade, big, milestone_type, {"planned_on": planned})
    for day in ("2026-11-06", "2026-11-07", "2026-11-08"):
        _plan(
            trade,
            big,
            "ETD",
            {"planned_on": day, "reason": "롤오버", "version": _version(trade, big, "ETD")},
        )
    counts = [count_statements(lambda s=sid: milestone_view.get_board(s)) for sid in (small, big)]
    assert counts[0] == counts[1] and 0 < counts[0] <= 8, counts


@pytest.mark.group_k
def test_the_shipment_list_carries_etd_and_eta_with_a_fixed_query_count(trade: TestClient) -> None:
    """적대 검토 반영 ②(design-D D3 `ShipmentListItem.etd/eta` — PR-3b 부채 R-3b-3) — 목록 행의 ETD·ETA = 유효값(실적 우선)·없으면 null,
    질의 수는 페이지 행 수·마일스톤 수와 무관(현재 페이지 id들로 1회 — N+1 0)"""
    from app.modules.trade_chain import shipment_view
    from tests.support.sqlcount import count_statements

    dated = _shipment(trade, released=True)
    sid = dated["id"]
    _plan(trade, sid, "ETD", {"planned_on": "2026-11-05"})
    _plan(trade, sid, "ETA", {"planned_on": "2026-12-05"})
    etd_version = _version(trade, sid, "ETD")
    assert (
        _actual(trade, sid, "ETD", {"actual_on": _day(0), "version": etd_version}).status_code
        == 200
    )
    bare = _shipment(trade)
    item = trade.get(SHIPMENTS, params={"so_id": dated["so_id"]}).json()["items"][0]
    assert item["etd"] == {"value": _day(0), "basis": "ACTUAL"}
    assert item["eta"] == {"value": "2026-12-05", "basis": "PLANNED"}
    empty = trade.get(SHIPMENTS, params={"so_id": bare["so_id"]}).json()["items"][0]
    assert empty["etd"] is None and empty["eta"] is None
    counts = [
        count_statements(lambda n=size: shipment_view.list_shipments(offset=0, limit=n))
        for size in (1, 50)
    ]
    assert counts[0] == counts[1] and counts[0] > 0, counts
