"""H·J·K. S3-2 입구~출구 워크스루 고정 — PR-8 렌즈 11 실 브라우저 관통과 같은 흐름을 API 수준에서 반복 가능하게
(계획서 §4 PR-8 행·§7 렌즈 11 / design-D D16 (i) / design-E E3-8 / ADR-0086 ①).

흐름(실 HTTP 스택 — TestClient·실 DB·사람 3명[관리자·무역·물류]):
  입구  관리자 CLI 생성(`cli.create_admin`) → 휴일 연도 선언(도착국 US, ETA 날짜 = 휴일) → 물류 계정 CLI 생성 → 화면 경로(API)로 '물류' 부여·ADMIN 회수
        → 무역이 SO 확정(실 확정 API)
  본류  부분선적 2건(1:N — 잔량 0·+1 = 409) → SO 선적중 → 담당 물류로 이관 → 계획 초안 → ETD·ETA·Cargo Closing 계획 → ETA 휴일 경고
        → ETD 롤오버(사유)+통보 기록 → 2번 선적 ETA = 미선언 국가 UNVERIFIED → 통관 수리 → 적재기한 산출 → 수입선적(PO 참조 — PO 잔량 불변)
  출구  기일 스캔(`scan_trade_deadlines`) → 물류가 받은 알림이 선적 화면을 가리킴(entity_type·id) → 선적 상세 열림 → 실적 입력 후 대금만기 재계산·적재기한 이행
        → 재스캔 신규 0 → 선적 1건 취소(선적중 유지) → 2건째 = 실적·통관 생존 409 → 사유 정정·삭제 → 취소 → SO 확정 복귀.
H 대표는 역할 경계(물류 생성 403)·알림 수신자·통보 = 기록(발송 0), J 대표는 같은 키 재생이 업무를 두 번 일으키지 않는 것, K는 스캔이 내는 알림 종류가 전부
화면 이동 표(`frontend/src/lib/alert-routes.ts`)에 있는 것. 테스트 데이터는 전부 익명 합성이다.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.core.time import KST, today_kst
from app.main import app as asgi_app
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import deadline_scan
from app.modules.trade_chain.deadline_scan import scan_trade_deadlines
from tests.factories.approvals import client_for, make_user
from tests.factories.confirm import complete_for_confirm, confirm
from tests.factories.gates import passing_so
from tests.factories.shipments import (
    PO,
    SHIPMENTS,
    SO,
    cancel,
    create_shipment,
    created,
    created_import,
    release,
    scalar,
    shipment_version,
    so_status,
)
from tests.factories.trade import create_po_via_api, create_supplier, idem, unique
from tests.support.factories import DEFAULT_PASSWORD
from tests.support.kst import pin_today_kst

pytestmark = [pytest.mark.group_h, pytest.mark.group_j]

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """KST '오늘'을 한 번 잡아 앱 import 지점과 이 모듈을 같은 날로 고정한다(자정 경계 — PR-4a 적대 검토 ⑩)."""
    return pin_today_kst(monkeypatch, sys.modules[__name__])


def _ok(response: Any, *codes: int) -> dict[str, Any]:
    assert response.status_code in (codes or (200, 201)), response.text
    body: dict[str, Any] = response.json()
    return body


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _day(offset: int) -> date:
    return today_kst() + timedelta(days=offset)


def _scan_now() -> datetime:
    """스캔 기준 시각 = 고정한 KST 오늘 12:00(UTC 03:00) — 스캔이 도출하는 '오늘'이 시험의 '오늘'과 같다."""
    return datetime.combine(today_kst(), time(12, 0), tzinfo=KST).astimezone(UTC)


@contextmanager
def _login(email: str) -> Iterator[TestClient]:
    """CLI로 만든 계정으로 로그인한 클라이언트(역할은 로그인 시점의 DB 값)."""
    with TestClient(asgi_app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        )
        assert response.is_success, response.text
        yield client


def _rows(client: TestClient, shipment_id: int) -> dict[str, dict[str, Any]]:
    board = _ok(client.get(f"{SHIPMENTS}/{shipment_id}/milestones"))
    return {row["milestone_type"]: row for row in board["rows"]}


def _plan(
    client: TestClient, shipment_id: int, milestone_type: str, body: dict[str, Any], **headers: str
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"{SHIPMENTS}/{shipment_id}/milestones/{milestone_type}/plan",
            json=body,
            headers=headers or idem(),
        )
    )


def _actual(
    client: TestClient, shipment_id: int, milestone_type: str, body: dict[str, Any]
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"{SHIPMENTS}/{shipment_id}/milestones/{milestone_type}/actual",
            json=body,
            headers=idem(),
        )
    )


def _open(client: TestClient, so_id: int) -> list[int]:
    return [line["shipment_open_quantity"] for line in _ok(client.get(f"{SO}/{so_id}"))["lines"]]


def _declare(
    admin: TestClient, country: str, year: int, holidays: list[tuple[date, str]], **h: str
) -> Any:
    return admin.put(
        f"/api/v1/holidays/{country}/{year}",
        json={
            "source_url": "https://example.gov/holidays",
            "verified_on": today_kst().isoformat(),
            "holidays": [{"holiday_on": d.isoformat(), "name": n} for d, n in holidays],
        },
        headers=h or idem(),
    )


@pytest.fixture
def people() -> Iterator[dict[str, Any]]:
    """관리자(CLI 생성 — 운영 개시 ①)·무역 담당·물류 담당(CLI 생성 → 관리자가 '물류' 부여·ADMIN 회수 — 운영 개시 S3-2 ②)."""
    admin_email = f"{unique('wt-admin')}@example.com"
    cli.create_admin(admin_email, DEFAULT_PASSWORD, "합성 관리자")
    logistics_email = f"{unique('wt-logi')}@example.com"
    logistics_id = cli.create_admin(logistics_email, DEFAULT_PASSWORD, "합성 물류")
    with ExitStack() as stack:
        admin = stack.enter_context(_login(admin_email))
        granted = _ok(admin.post(f"/api/v1/users/{logistics_id}/roles", json={"role": "LOGISTICS"}))
        assert sorted(granted["roles"]) == ["ADMIN", "LOGISTICS"]
        revoked = _ok(admin.delete(f"/api/v1/users/{logistics_id}/roles/ADMIN"))
        assert revoked["roles"] == ["LOGISTICS"]
        logistics = stack.enter_context(_login(logistics_email))
        assert _ok(logistics.get("/api/v1/auth/me"))["roles"] == ["LOGISTICS"]
        trade = stack.enter_context(client_for(make_user(RoleCode.TRADE)))
        yield {"admin": admin, "trade": trade, "logistics": logistics, "logistics_id": logistics_id}


def _confirmed_so(trade: TestClient) -> dict[str, Any]:
    """입구 — 확정 가능한 접수 SO(라인 10, 후불 T/T = 잔금 ETD−7)를 **실 확정 API**로 확정한다."""
    so = passing_so(quantity=10, terms="TT_DEFERRED")
    complete_for_confirm(so["id"])
    _ok(confirm(trade, so["id"]))
    assert so_status(so["id"]) == "CONFIRMED"
    so["line_id"] = int(_ok(trade.get(f"{SO}/{so['id']}"))["lines"][0]["id"])
    return so


def test_s3_2_walkthrough_entrance_to_exit(people: dict[str, Any]) -> None:
    """H — 입구(휴일 선언·역할 부여·SO 확정) → 선적 → 마일스톤·통관 → 기일 스캔 알림 → 출구(알림이 가리키는 선적 화면) → 취소 2단 복귀."""
    admin, trade, logistics = people["admin"], people["trade"], people["logistics"]
    eta = _day(40)
    _ok(_declare(admin, "US", eta.year, [(eta, "Synthetic Port Holiday")]))

    so = _confirmed_so(trade)
    line = so["line_id"]

    # 물류는 선적을 만들지 못한다(생성 = 관리자·무역 — ADR-0079)
    denied = create_shipment(logistics, so["id"], [(line, 1)])
    assert denied.status_code == 403, denied.text

    # 부분선적 1:N — 6 + 4 = 10, +1 = 409, SO 선적중
    first = created(trade, so["id"], [(line, 6)])
    second = created(trade, so["id"], [(line, 4)], dest="VN")
    assert so_status(so["id"]) == "IN_SHIPMENT" and _open(trade, so["id"]) == [0]
    over = create_shipment(trade, so["id"], [(line, 1)])
    assert over.status_code == 409 and _code(over) == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    sid, sid2 = int(first["id"]), int(second["id"])

    # 담당 이관 — 1번 선적을 물류 담당에게(이후 기일 알림 수신자)
    _ok(
        trade.patch(
            f"{SHIPMENTS}/{sid}",
            json={"version": shipment_version(sid), "assignee_id": people["logistics_id"]},
        )
    )

    # 물류 — 계획 초안 → 계획(ETD·ETA·Cargo Closing 시각형)
    _ok(logistics.post(f"{SHIPMENTS}/{sid}/milestones/plan-draft", json={}, headers=idem()))
    rows = _rows(logistics, sid)
    assert {"DOC_CUTOFF", "CARGO_CLOSING", "ETD", "ETA"} <= {
        t for t, r in rows.items() if r["version"] is not None
    }
    _plan(
        logistics,
        sid,
        "ETD",
        {"planned_on": _day(30).isoformat(), "version": rows["ETD"]["version"]},
    )
    _plan(logistics, sid, "ETA", {"planned_on": eta.isoformat(), "version": rows["ETA"]["version"]})
    closing_at = datetime.combine(_day(3), time(10, 0), tzinfo=KST)
    _plan(
        logistics,
        sid,
        "CARGO_CLOSING",
        {
            "planned_at": closing_at.isoformat(),
            "tz": "Asia/Seoul",
            "version": rows["CARGO_CLOSING"]["version"],
        },
    )
    rows = _rows(logistics, sid)
    # DoD② ETA 현지 연휴 → 경고(값은 옮기지 않는다)
    assert rows["ETA"]["holiday"] == {
        "flag": "HOLIDAY",
        "country": "US",
        "name": "Synthetic Port Holiday",
    }
    assert rows["ETA"]["effective"]["value"] == eta.isoformat()
    assert rows["PAYMENT_DUE"]["derived"]["value"] == _day(23).isoformat()  # ETD 30 − 7

    # 롤오버(사유) + 통보 기록 — 기록일 뿐 발송 0
    rolled = _plan(
        logistics,
        sid,
        "ETD",
        {
            "planned_on": _day(32).isoformat(),
            "version": rows["ETD"]["version"],
            "reason": "선사 스케줄 변경",
        },
    )
    change = rolled["change"]
    assert change["change_kind"] == "PLAN_CHANGED"
    assert _rows(logistics, sid)["ETD"]["unnotified_rollovers"] == 1
    forwarder = create_supplier(types=("FORWARDER",))
    events_before = scalar("SELECT count(*) FROM events")
    noticed = _ok(
        logistics.post(
            f"{SHIPMENTS}/{sid}/milestone-changes/{change['id']}/notices",
            json={
                "occurred_on": today_kst().isoformat(),
                "counterpart_partner_id": forwarder,
                "summary": "포워더에 메일로 ETD 변경 안내",
            },
            headers=idem(),
        ),
        201,
    )
    assert len(noticed["notices"]) == 1
    assert scalar("SELECT count(*) FROM events") == events_before  # 통보 = 기록(아웃박스 0)
    assert _rows(logistics, sid)["ETD"]["unnotified_rollovers"] == 0

    # 2번 선적 — 미선언 도착국(VN)의 ETA = UNVERIFIED('휴일 캘린더 미등록' — 평일로 읽지 않는다)
    _plan(trade, sid2, "ETA", {"planned_on": eta.isoformat()})
    assert _rows(trade, sid2)["ETA"]["holiday"] == {
        "flag": "UNVERIFIED",
        "country": "VN",
        "name": None,
    }

    # 통관 — 수리일(D−25)이 적재기한(D+5)을 낳는다
    customs = _ok(
        logistics.post(
            f"{SHIPMENTS}/{sid}/customs-records",
            json={
                "declaration_kind": "EXPORT",
                "declaration_no": unique("ED").replace("-", ""),
                "declared_on": _day(-26).isoformat(),
                "accepted_on": _day(-25).isoformat(),
            },
            headers=idem(),
        ),
        201,
    )
    rows = _rows(logistics, sid)
    assert rows["CUSTOMS_CLEARED"]["actual"] == _day(-25).isoformat()
    assert rows["CUSTOMS_CLEARED"]["customs_state"] == "CLEARED"
    assert rows["LOADING_DEADLINE"]["derived"]["value"] == _day(5).isoformat()
    assert rows["LOADING_DEADLINE"]["fulfilment"] == "OPEN"

    # 수입선적(PO 상세 참조) — PO 잔량·상태 불변, 배정 가능량만 준다
    po = create_po_via_api(trade)
    po_line = int(po["lines"][0]["id"])
    imported = created_import(trade, po["id"], [(po_line, 6)])
    assert imported["shipment_kind"] == "IMPORT"
    po_after = _ok(trade.get(f"{PO}/{po['id']}"))
    assert po_after["status"] == po["status"]
    assert po_after["lines"][0]["assignable_quantity"] == 4

    # 기일 스캔 → 물류 담당 알림: Cargo Closing D-7·D-3(지난 문턱 1회씩) + 적재기한 D-7
    counts = scan_trade_deadlines(now=_scan_now())
    assert counts["failed"] == 0 and counts["escalated"] == 0
    assert counts["threshold"] == 3, counts
    alerts = _ok(logistics.get("/api/v1/alerts"))["items"]
    assert len(alerts) == 3
    assert {(a["entity_type"], a["entity_id"]) for a in alerts} == {("shipments", sid)}
    keys = sorted(a["dedup_key"].split(":")[3].split("@")[0] for a in alerts)
    assert keys == ["CARGO_CLOSING/D-3", "CARGO_CLOSING/D-7", "LOADING_DEADLINE/D-7"]
    assert all(first["doc_number"] in a["title"] for a in alerts)

    # 출구 — 알림이 가리키는 화면(선적 상세)이 그 사람에게 열린다 → 확인(ack)
    detail = _ok(logistics.get(f"{SHIPMENTS}/{alerts[0]['entity_id']}"))
    assert detail["doc_number"] == first["doc_number"]
    _ok(logistics.post(f"/api/v1/alerts/{alerts[0]['id']}/ack"))

    # 실적 — 출고지시 → ETD 실적: 대금만기 재계산(PLANNED → ACTUAL), 적재기한 이행 MET → 재스캔 신규 0
    _ok(release(logistics, sid))
    _actual(
        logistics,
        sid,
        "ETD",
        {"actual_on": today_kst().isoformat(), "version": _rows(logistics, sid)["ETD"]["version"]},
    )
    rows = _rows(logistics, sid)
    assert rows["PAYMENT_DUE"]["derived"]["basis"] == "ACTUAL"
    assert rows["PAYMENT_DUE"]["derived"]["value"] == _day(-7).isoformat()
    assert rows["LOADING_DEADLINE"]["fulfilment"] == "MET"
    again = scan_trade_deadlines(now=_scan_now())
    assert again["threshold"] == again["overdue"] == again["escalated"] == again["failed"] == 0
    assert len(_ok(logistics.get("/api/v1/alerts"))["items"]) == 3

    # 취소 2단 — 2번 취소: 잔량 4·선적중 유지 / 1번: 실적·통관 생존 409 → 사유 정정·삭제 → 취소 → SO 확정 복귀
    _ok(cancel(trade, sid2))
    assert so_status(so["id"]) == "IN_SHIPMENT" and _open(trade, so["id"]) == [4]
    blocked = cancel(trade, sid)
    assert blocked.status_code == 409 and _code(blocked) in (
        "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED",
        "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE",
    )
    _actual(
        logistics,
        sid,
        "ETD",
        {
            "actual_on": None,
            "version": _rows(logistics, sid)["ETD"]["version"],
            "reason": "오입력 정정",
        },
    )
    deleted = logistics.request(
        "DELETE",
        f"{SHIPMENTS}/{sid}/customs-records/{customs['id']}",
        json={"version": customs["version"], "reason": "신고 취하"},
    )
    assert deleted.status_code == 204, deleted.text
    _ok(cancel(trade, sid))
    assert so_status(so["id"]) == "CONFIRMED" and _open(trade, so["id"]) == [10]


def test_s3_2_walkthrough_same_key_replays_do_not_repeat_business(people: dict[str, Any]) -> None:
    """J — 같은 Idempotency-Key 재생: 휴일 선언·선적 생성(더블클릭)·롤오버·통보·통관 기록이 각각 한 번만 일어나고 응답이 같다. 재스캔 신규 0."""
    admin, trade, logistics = people["admin"], people["trade"], people["logistics"]
    key = {"Idempotency-Key": unique("wt-hol")}
    year = _day(40).year
    first_put = _ok(_declare(admin, "US", year, [(_day(40), "Synthetic")], **key))
    assert _ok(_declare(admin, "US", year, [(_day(40), "Synthetic")], **key)) == first_put
    assert scalar("SELECT count(*) FROM holidays WHERE deleted_at IS NULL") == 1

    so = _confirmed_so(trade)
    key = {"Idempotency-Key": unique("wt-sh")}
    one = _ok(create_shipment(trade, so["id"], [(so["line_id"], 5)], headers=key), 201)
    two = _ok(create_shipment(trade, so["id"], [(so["line_id"], 5)], headers=key), 201)
    assert one["id"] == two["id"]
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 1
    assert _open(trade, so["id"]) == [5]
    sid = int(one["id"])

    _plan(logistics, sid, "ETD", {"planned_on": _day(30).isoformat()})
    roll_key = unique("wt-roll")
    body = {
        "planned_on": _day(31).isoformat(),
        "version": _rows(logistics, sid)["ETD"]["version"],
        "reason": "선사 사정",
    }
    rolled = _plan(logistics, sid, "ETD", body, **{"Idempotency-Key": roll_key})
    replayed = _plan(logistics, sid, "ETD", body, **{"Idempotency-Key": roll_key})
    assert replayed["change"]["id"] == rolled["change"]["id"]
    assert _rows(logistics, sid)["ETD"]["rollover_count"] == 1

    notice_key = {"Idempotency-Key": unique("wt-notice")}
    notice_url = f"{SHIPMENTS}/{sid}/milestone-changes/{rolled['change']['id']}/notices"
    notice = {"occurred_on": today_kst().isoformat(), "summary": "전화로 안내"}
    first_notice = _ok(logistics.post(notice_url, json=notice, headers=notice_key), 201)
    assert _ok(logistics.post(notice_url, json=notice, headers=notice_key), 201) == first_notice
    assert scalar("SELECT count(*) FROM milestone_change_notices") == 1

    customs_key = {"Idempotency-Key": unique("wt-cus")}
    customs = {
        "declaration_kind": "EXPORT",
        "declaration_no": unique("ED").replace("-", ""),
        "declared_on": _day(-2).isoformat(),
        "accepted_on": _day(-1).isoformat(),
    }
    url = f"{SHIPMENTS}/{sid}/customs-records"
    made = _ok(logistics.post(url, json=customs, headers=customs_key), 201)
    assert _ok(logistics.post(url, json=customs, headers=customs_key), 201)["id"] == made["id"]
    assert scalar("SELECT count(*) FROM customs_records WHERE shipment_id = :s", s=sid) == 1

    scan_trade_deadlines(now=_scan_now())
    assert scan_trade_deadlines(now=_scan_now())["threshold"] == 0


@pytest.mark.group_k
def test_every_entity_type_the_trade_scan_alerts_on_has_a_screen_route() -> None:
    """K — 출구 계약: 무역 기일 스캔이 만드는 알림의 entity_type(선적·견적·PI)이 전부 프런트 알림 이동 표에 있다(없으면 알림이 글자로만 보여 화면 이동이 끊긴다)."""
    routes = REPO / "frontend" / "src" / "lib" / "alert-routes.ts"
    if not routes.is_file():
        pytest.skip("프런트 소스가 보이지 않는다")
    table = set(re.findall(r"^\s*(\w+): \(id\) =>", routes.read_text(encoding="utf-8"), re.M))
    emitted = {"shipments", *deadline_scan._VALIDITY_TARGETS}
    assert emitted == {"shipments", "quotations", "proforma_invoices"}  # 대상 핀(공회전 방지)
    assert emitted <= table, emitted - table
