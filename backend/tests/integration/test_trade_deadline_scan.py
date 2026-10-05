"""H·I·A·J·K. 무역 기일 스캔 `trade-deadline-scan` (S3-2 PR-6 / ADR-0084 / design-B B13·B18 / design-C C11·H-01~H-07·I-02·I-03 / GC-A21).

■ H 운영 — ack 재발송 0 · D-3 에스컬레이션 · 롤오버 = 새 기일 새 알림 · 담당 이관 즉시 반영 · ADMIN 폴백 · L/C 오프 알림 0 · OEM 알림 0 ·
  견적/PI D-N 후보 = 만료 스윕 후보.
■ A(GC-A21 golden) — 롤오버 dedup · KST 경계 · **시각형 도과 = `now_utc > effective_at`**(날짜 비교 아님).
■ I — 레지스트리 daily@06:40 · 건별 독립 TX · 1건 실패 → 나머지 처리 + 잡 FAILED + 관리자 알림.
■ J — 알림만(이벤트·전표 행 불변) · 열린 트랜잭션 합류 거부 · keyset 페이지 경계.

시각은 전부 주입한다(`scan_trade_deadlines(now=…)` — 스캔이 KST 오늘을 그 시각에서 도출). 날짜는 고정 상수라 시험 실행 시각·KST 자정과 무관하다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app import cli
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.handover import service as handover
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.notifications import service as notifications
from app.modules.platform import scheduler
from app.modules.trade_chain import deadline_scan, milestone_view
from app.modules.trade_chain.deadline_scan import scan_trade_deadlines
from app.modules.trade_chain.expiry_sweep import sweep_expired_documents
from app.modules.worklist.models import AlertRule
from tests.factories.shipments import confirmed_so, raw_shipment
from tests.factories.trade import raw_pi, raw_po, raw_quotation, raw_so, unique
from tests.support.factories import create_user

pytestmark = pytest.mark.group_h

JOB = "trade-deadline-scan"

#: 기준 시각 — KST 2026-10-03 09:40(UTC 00:40). 이 시험의 '오늘'은 2026-10-03이다(실행 시각과 무관).
NOW = datetime(2026, 10, 3, 0, 40, tzinfo=UTC)
TODAY = date(2026, 10, 3)


# ── 준비 ─────────────────────────────────────────────────────────────────────


def _rows(sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def _exec(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


@pytest.fixture
def admins() -> Iterator[tuple[int, int]]:
    yield (
        create_user(f"{unique('tds-admin')}@example.com", roles=(RoleCode.ADMIN,)),
        create_user(f"{unique('tds-admin')}@example.com", roles=(RoleCode.ADMIN,)),
    )


def _shipment(
    *,
    assignee: int | None = None,
    inactive_assignee: bool = False,
    kind: str = "EXPORT",
    terms: str = "TT_DEFERRED",
) -> int:
    """살아 있는 선적(PLANNED) — 담당자는 SO 담당자 사본(NOT NULL), `assignee`면 그 사용자.

    `inactive_assignee` = 담당자 계정 비활성(퇴사자) — 선적 담당자는 NOT NULL이라 '담당자 없음'은 이 모양으로만 온다(수신자 아님 → 규칙·폴백).
    """
    so = confirmed_so(terms=terms)
    if kind == "IMPORT":
        shipment_id = raw_shipment(so["id"], kind="IMPORT", po_id=raw_po())
    else:
        shipment_id = raw_shipment(so["id"])
    if inactive_assignee:
        assignee = create_user(f"{unique('tds-gone')}@example.com", roles=(RoleCode.LOGISTICS,))
        _exec("UPDATE users SET is_active = false WHERE id = :u", u=assignee)
    if assignee is not None:
        _exec("UPDATE shipments SET assignee_id = :a WHERE id = :i", a=assignee, i=shipment_id)
    return shipment_id


def _assignee(shipment_id: int) -> int:
    return int(_rows("SELECT assignee_id FROM shipments WHERE id = :i", i=shipment_id)[0][0])


def _plan_on(
    shipment_id: int, milestone_type: str, planned: date, actual: date | None = None
) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO milestones (shipment_id, milestone_type, planned_on, actual_on)"
                    " VALUES (:s, :t, :p, :a) RETURNING id"
                ),
                {"s": shipment_id, "t": milestone_type, "p": planned, "a": actual},
            ).scalar_one()
        )


def _plan_at(
    shipment_id: int,
    milestone_type: str,
    planned: datetime,
    tz: str = "Asia/Seoul",
    actual: datetime | None = None,
) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO milestones (shipment_id, milestone_type, planned_at, actual_at, tz)"
                    " VALUES (:s, :t, :p, :a, :z) RETURNING id"
                ),
                {"s": shipment_id, "t": milestone_type, "p": planned, "a": actual, "z": tz},
            ).scalar_one()
        )


def _customs_accepted(shipment_id: int, accepted: date, kind: str = "EXPORT") -> None:
    _exec(
        "INSERT INTO customs_records (shipment_id, declaration_kind, declaration_no, declared_on, accepted_on)"
        " VALUES (:s, :k, :n, :d, :a)",
        s=shipment_id,
        k=kind,
        n=unique("DCL").upper().replace("-", ""),
        d=accepted,
        a=accepted,
    )


def _alerts(entity_type: str | None = None, entity_id: int | None = None) -> list[tuple[Any, ...]]:
    """(dedup_key, recipient, severity, title, body, acknowledged?) — 기일·에스컬레이션 키만(jobs.failed 등 제외)."""
    clauses = [
        "(dedup_key LIKE 'deadline:%' OR dedup_key LIKE 'esc:%' OR dedup_key LIKE 'deadline-unresolved:%')"
    ]
    params: dict[str, Any] = {}
    if entity_type is not None:
        clauses.append("entity_type = :et")
        params["et"] = entity_type
    if entity_id is not None:
        clauses.append("entity_id = :ei")
        params["ei"] = entity_id
    return _rows(
        "SELECT dedup_key, recipient_user_id, severity, title, coalesce(body, ''), acknowledged_at IS NOT NULL"
        f" FROM alerts WHERE {' AND '.join(clauses)} ORDER BY id",
        **params,
    )


def _keys(entity_type: str | None = None, entity_id: int | None = None) -> list[str]:
    return [row[0] for row in _alerts(entity_type, entity_id)]


def _ack_all(user_id: int) -> None:
    actor = AuthenticatedUser(
        id=user_id, email="x@example.com", display_name="x", roles=frozenset(), session_id=0
    )
    for (alert_id,) in _rows(
        "SELECT id FROM alerts WHERE recipient_user_id = :u AND acknowledged_at IS NULL", u=user_id
    ):
        notifications.acknowledge(actor=actor, alert_id=int(alert_id))


def _scan(now: datetime = NOW, **kwargs: Any) -> dict[str, int]:
    """주입 시각으로 스캔하고, 방금 만든 알림의 `created_at`을 그 시각으로 맞춘다 — 알림 생성 시각(DB now())이 시험의 가상 시계와
    같아야 에스컬레이션의 '스캔일 KST 0시 이전 알림만' 판정이 실행 날짜와 무관하게 재현된다."""
    before = int(_rows("SELECT coalesce(max(id), 0) FROM alerts")[0][0])
    counts = scan_trade_deadlines(now=now, **kwargs)
    # 이번 스캔이 만든 알림만(id > before) 양방향으로 맞춘다 — 가상 시계가 실제 시각보다 미래여도 과거여도 같다(적대 검토 ⑧)
    _exec("UPDATE alerts SET created_at = :t WHERE id > :b", t=now, b=before)
    return counts


def _at(days: int, hour: int = 9) -> datetime:
    """기준 '오늘'(KST 10-03)에서 days일 뒤 KST hour시의 UTC 시각."""
    return datetime(2026, 10, 3, hour, 0, tzinfo=UTC) - timedelta(hours=9) + timedelta(days=days)


# ── H-01 문턱·지각·멱등·ack ───────────────────────────────────────────────────


def test_passed_thresholds_fire_once_to_the_assignee_and_an_acked_alert_is_never_resent(
    admins: tuple[int, int],
) -> None:
    """D-5 Cargo Closing → D-7 1건(지각 발송, D-3은 아직) · 담당자에게만 · 재실행 신규 0 · 확인 뒤 재실행도 0(H-01)"""
    shipment = _shipment()
    owner = _assignee(shipment)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    counts = _scan()
    assert counts["threshold"] == 1 and counts["failed"] == 0
    rows = _alerts("shipments", shipment)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (
            f"deadline:shipments:{shipment}:CARGO_CLOSING/D-7@{deadline_scan.instant_stamp(_at(5))}:{owner}",
            owner,
            "WARN",
        )
    ]
    assert "Cargo Closing" in rows[0][3] and "KST" in rows[0][4]
    assert _scan()["threshold"] == 0
    _ack_all(owner)
    again = _scan()
    assert again["threshold"] == again["escalated"] == 0
    next_day = _scan(
        NOW + timedelta(days=1)
    )  # 다음 날 D-4 — 확인한 D-7은 다시 나가지 않는다(키에 실행일 없음)
    assert next_day["threshold"] == next_day["escalated"] == 0
    assert len(_alerts("shipments", shipment)) == 1


def test_a_document_cutoff_at_d1_sends_every_passed_threshold_as_critical_below_d3() -> None:
    """D-1 서류마감 → D-7(주의)·D-3(긴급)·D-1(긴급) 3건 — 문턱은 '지났다' 판정, 같은 키는 다시 나가지 않는다"""
    shipment = _shipment()
    _plan_at(shipment, "DOC_CUTOFF", _at(1))
    assert _scan()["threshold"] == 3
    severities = {
        key.split("/")[1].split("@")[0]: sev for key, _, sev, *_ in _alerts("shipments", shipment)
    }
    assert severities == {"D-7": "WARN", "D-3": "CRITICAL", "D-1": "CRITICAL"}


def test_overdue_sends_one_alert_and_never_backfills_thresholds() -> None:
    """이미 지난 수입 세금 납부기한 → 도과 1건뿐(D-7·3·1 소급 0)"""
    shipment = _shipment(kind="IMPORT")
    _plan_on(shipment, "IMPORT_TAX_DUE", TODAY - timedelta(days=2))
    counts = _scan()
    assert counts["overdue"] == 1 and counts["threshold"] == 0
    (key,) = _keys("shipments", shipment)
    assert f":IMPORT_TAX_DUE/overdue@{TODAY - timedelta(days=2)}:" in key


# ── H-02 에스컬레이션 ─────────────────────────────────────────────────────────


def test_d3_escalation_goes_to_admins_only_while_the_same_deadline_alert_is_unacknowledged(
    admins: tuple[int, int],
) -> None:
    """D-5에 D-7 → 미확인 상태로 D-2 도달 → ADMIN 전원 에스컬레이션 1회(+D-3) · 재실행 추가 0. 확인한 건은 에스컬레이션 0(H-02)"""
    unread, read = _shipment(), _shipment()
    for shipment in (unread, read):
        _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    _ack_all(_assignee(read))
    later = NOW + timedelta(days=3)  # D-2
    counts = _scan(later)
    assert counts["escalated"] == 2  # 관리자 2명 — unread 건만
    esc = [r for r in _alerts("shipments", unread) if r[0].startswith("esc:")]
    assert sorted(r[1] for r in esc) == sorted(admins)
    assert all(r[2] == "CRITICAL" and ":CARGO_CLOSING/D-3@" in r[0] for r in esc)
    assert not [r for r in _alerts("shipments", read) if r[0].startswith("esc:")]
    _ack_all(
        _assignee(read)
    )  # 오늘 받은 D-3도 확인 — 재실행에서 확인한 건은 에스컬레이션 대상이 아니다
    assert _scan(later)["escalated"] == 0  # 같은 기일의 에스컬레이션은 1회(키 dedup)


def test_a_same_day_rerun_does_not_escalate_alerts_it_just_created(admins: tuple[int, int]) -> None:
    """D-2에 처음 스캔(D-7·D-3 생성) → 같은 날 재실행 에스컬레이션 0('받을 틈이 없었다' — 실기동 재실행에서 확인) → 다음 날 미확인이면 1회"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", _at(2))
    first = _scan()
    assert first["threshold"] == 2 and first["escalated"] == 0
    assert _scan(NOW + timedelta(hours=3))["escalated"] == 0
    assert (
        _scan(NOW + timedelta(days=1))["escalated"] == 0
    )  # 정확히 24시간 — 근거는 24시간보다 오래된 알림만(created_at < now−24h)
    assert _scan(NOW + timedelta(days=1, minutes=1))["escalated"] == len(admins)


def test_escalation_does_not_mix_milestone_types_on_the_same_shipment(
    admins: tuple[int, int],
) -> None:
    """같은 선적·같은 기일의 다른 종류(서류마감) 미확인 알림이 Cargo Closing 에스컬레이션을 일으키지 않는다(prefix에 TYPE 포함 — X-25)"""
    shipment = _shipment()
    owner = _assignee(shipment)
    same_instant = _at(5)
    _plan_at(shipment, "DOC_CUTOFF", same_instant)
    _plan_at(shipment, "CARGO_CLOSING", same_instant)
    _scan()
    # 서류마감 알림만 미확인으로 남기고 Cargo Closing 알림은 확인
    for alert_id, key in _rows(
        "SELECT id, dedup_key FROM alerts WHERE recipient_user_id = :u", u=owner
    ):
        if ":CARGO_CLOSING/" in key:
            notifications.acknowledge(
                actor=AuthenticatedUser(
                    id=owner,
                    email="o@example.com",
                    display_name="o",
                    roles=frozenset(),
                    session_id=0,
                ),
                alert_id=int(alert_id),
            )
    _scan(NOW + timedelta(days=3))
    esc = [k for k in _keys("shipments", shipment) if k.startswith("esc:")]
    assert esc and all(":DOC_CUTOFF/" in k for k in esc)


def test_a_direct_assignee_change_does_not_make_the_new_assignee_escalate(
    admins: tuple[int, int],
) -> None:
    """A가 D-7을 받은 뒤 선적 담당을 C로 직접 바꿈(일괄 이관 아님 — 옛 알림은 A 앞에 남음) → D-2: 에스컬레이션 0(근거 = 지금 수신자 C의
    미확인뿐 — 적대 검토 ④), D-3은 C에게"""
    a = create_user(f"{unique('tds-a')}@example.com", roles=(RoleCode.LOGISTICS,))
    c = create_user(f"{unique('tds-c')}@example.com", roles=(RoleCode.LOGISTICS,))
    shipment = _shipment(assignee=a)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    _exec("UPDATE shipments SET assignee_id = :c WHERE id = :i", c=c, i=shipment)
    later = _scan(NOW + timedelta(days=3))
    assert later["escalated"] == 0
    assert {r[1] for r in _alerts("shipments", shipment) if "/D-3@" in r[0]} == {c}


def test_an_inactive_assignee_does_not_cause_a_false_escalation(admins: tuple[int, int]) -> None:
    """A가 D-7을 받고 비활성화 → D-2: 기일 알림은 ADMIN 폴백으로 가지만 A의 옛 미확인은 근거가 아니다(에스컬레이션 0 — 적대 검토 ④)"""
    a = create_user(f"{unique('tds-a')}@example.com", roles=(RoleCode.LOGISTICS,))
    shipment = _shipment(assignee=a)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    _exec("UPDATE users SET is_active = false WHERE id = :u", u=a)
    later = _scan(NOW + timedelta(days=3))
    assert later["escalated"] == 0
    assert {r[1] for r in _alerts("shipments", shipment) if "/D-3@" in r[0]} == set(admins)


def test_a_fallback_escalation_names_the_rule_or_admin_recipients(admins: tuple[int, int]) -> None:
    """폴백 수신자(관리자)가 D-7을 하루 넘게 안 읽으면 에스컬레이션 — 문구는 '수신자(규칙·관리자)가'(담당자가 아님)"""
    shipment = _shipment(inactive_assignee=True)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    assert _scan(NOW + timedelta(days=3))["escalated"] == len(admins)
    esc = [r for r in _alerts("shipments", shipment) if r[0].startswith("esc:")]
    assert esc and all("수신자(규칙·관리자)가" in r[4] for r in esc)


def test_a_misfired_late_night_run_does_not_escalate_the_next_morning(
    admins: tuple[int, int],
) -> None:
    """미스파이어 — 전날 KST 23:00에 처음 돈 스캔이 D-7·D-3을 만들고 다음 날 06:40 정규 실행: 7시간 40분 전 알림은 근거가 아니다
    (기준 = min(KST 0시, now−24h) — 적대 검토 ⑦). 그다음 날에는 하루 넘게 미확인이라 에스컬레이션"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", datetime(2026, 10, 6, 0, 0, tzinfo=UTC), tz="Asia/Seoul")
    first = _scan(datetime(2026, 10, 3, 14, 0, tzinfo=UTC))  # KST 10-03 23:00 — D-3
    assert first["threshold"] == 2
    morning = _scan(datetime(2026, 10, 3, 21, 40, tzinfo=UTC))  # KST 10-04 06:40 — D-2
    assert morning["escalated"] == 0
    assert _scan(datetime(2026, 10, 4, 21, 40, tzinfo=UTC))["escalated"] == len(admins)


def _inject_between_verdicts(monkeypatch: pytest.MonkeyPatch, change: Any) -> None:
    """첫 판정 직후(발송 직전 재확인 전)에 다른 트랜잭션의 커밋을 끼워 넣는다 — 경합 재현."""
    real = deadline_scan._shipment_verdict
    calls = {"n": 0}

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            change()
        return result

    monkeypatch.setattr(deadline_scan, "_shipment_verdict", wrapped)


@pytest.mark.group_j
def test_an_actual_recorded_mid_scan_is_not_alerted(monkeypatch: pytest.MonkeyPatch) -> None:
    """스캔-실적 입력 경합 — 첫 판정 뒤 실적이 커밋되면 발송 직전 재확인에서 빠진다(알림 0, deferred 1 — 적대 검토 ⑥)"""
    shipment = _shipment()
    milestone = _plan_at(shipment, "CARGO_CLOSING", _at(1))
    _inject_between_verdicts(
        monkeypatch,
        lambda: _exec("UPDATE milestones SET actual_at = :a WHERE id = :i", a=_at(-1), i=milestone),
    )
    counts = _scan()
    assert counts["threshold"] == 0 and counts["deferred"] == 1
    assert _keys("shipments", shipment) == []


@pytest.mark.group_j
def test_a_loading_fulfilment_recorded_mid_scan_is_not_alerted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """적재기한 — 첫 판정 뒤 ETD 실적이 커밋되면(이행) 통관·실적 원천을 다시 읽어 발송하지 않는다"""
    shipment = _shipment()
    _customs_accepted(shipment, date(2026, 9, 8))
    _inject_between_verdicts(
        monkeypatch, lambda: _plan_on(shipment, "ETD", date(2026, 10, 1), actual=date(2026, 10, 1))
    )
    counts = _scan()
    assert counts["threshold"] == 0 and counts["deferred"] == 1
    assert _keys("shipments", shipment) == []


@pytest.mark.group_j
def test_a_handover_committed_mid_scan_defers_the_shipment(monkeypatch: pytest.MonkeyPatch) -> None:
    """스캔-이관 경합 — 첫 판정 뒤 담당자가 바뀌면 그 건은 보내지 않고 다음 실행에 맡긴다(옛 담당자에게 새 알림 0 — 적대 검토 ⑤)"""
    shipment = _shipment()
    c = create_user(f"{unique('tds-c')}@example.com", roles=(RoleCode.LOGISTICS,))
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _inject_between_verdicts(
        monkeypatch,
        lambda: _exec("UPDATE shipments SET assignee_id = :c WHERE id = :i", c=c, i=shipment),
    )
    counts = _scan()
    assert counts["threshold"] == 0 and counts["deferred"] == 1
    monkeypatch.undo()
    _scan()
    assert {r[1] for r in _alerts("shipments", shipment)} == {c}


# ── H-03 · GC-A21 롤오버 ─────────────────────────────────────────────────────


@pytest.mark.golden
def test_gc_a21_a_rollover_is_a_new_deadline_and_the_old_key_is_never_resent() -> None:
    """GC-A21 ① — Cargo Closing D-3 알림 후 롤오버로 기일 +7 → 옛 기일 재발송 0, 새 기일 문턱이 오면 **새 키**로 새 알림(H-03)"""
    shipment = _shipment()
    old = _at(3)
    milestone = _plan_at(shipment, "CARGO_CLOSING", old)
    _scan()
    old_keys = _keys("shipments", shipment)
    assert {k.split("/")[1].split("@")[0] for k in old_keys} == {"D-7", "D-3"}
    new = old + timedelta(days=7)
    _exec("UPDATE milestones SET planned_at = :p WHERE id = :i", p=new, i=milestone)
    assert _scan()["threshold"] == 0  # 새 기일 D-10 — 아직 문턱 전, 옛 키 재발송 0
    assert _keys("shipments", shipment) == old_keys
    counts = _scan(NOW + timedelta(days=3))  # 새 기일 D-7
    assert counts["threshold"] == 1
    (fresh,) = [k for k in _keys("shipments", shipment) if k not in old_keys]
    assert f"/D-7@{deadline_scan.instant_stamp(new)}:" in fresh
    assert all(f"@{deadline_scan.instant_stamp(old)}:" in k for k in old_keys)


def test_a_same_day_time_rollover_is_also_a_new_deadline() -> None:
    """시각형은 시각까지 기일이다 — 같은 날 안에서 09:00 → 15:00으로 바꿔도 새 키(자율 확정 — 기일 표기 = UTC 초)"""
    shipment = _shipment()
    milestone = _plan_at(shipment, "CARGO_CLOSING", _at(2, hour=9))
    _scan()
    before = len(_keys("shipments", shipment))
    _exec("UPDATE milestones SET planned_at = :p WHERE id = :i", p=_at(2, hour=15), i=milestone)
    assert _scan()["threshold"] == before


# ── GC-A21 시각형 도과·KST 경계 ──────────────────────────────────────────────


@pytest.mark.golden
def test_gc_a21_datetime_overdue_compares_utc_instants_not_dates() -> None:
    """GC-A21 ④ — Cargo Closing 2026-10-11T00:00Z(LA 현지 10-10 17:00), 스캔 10-10T21:40Z(KST 10-11 06:40) → **도과 아님**(D-N 기준일
    10-10 — D-0이라 문턱 3개) / 정각 00:00:00Z → 도과 아님 / 00:00:01Z → 도과"""
    shipment = _shipment()
    deadline = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)
    _plan_at(shipment, "CARGO_CLOSING", deadline, tz="America/Los_Angeles")
    early = _scan(datetime(2026, 10, 10, 21, 40, tzinfo=UTC))
    assert early["overdue"] == 0 and early["threshold"] == 3
    at_deadline = _scan(deadline)
    assert (
        at_deadline["overdue"] == 0 and at_deadline["escalated"] == 0
    )  # 2시간 20분 전 알림 — 24시간 미만은 근거가 아니다
    late = _scan(deadline + timedelta(seconds=1))
    assert late["overdue"] == 1 and late["escalated"] == 0
    overdue = [r for r in _alerts("shipments", shipment) if "/overdue@" in r[0]]
    assert overdue and "@2026-10-11T000000Z:" in overdue[0][0]
    assert "현지 2026-10-10 17:00 America/Los_Angeles" in overdue[0][4]


@pytest.mark.golden
def test_gc_a21_the_scan_day_is_the_kst_date_of_the_instant() -> None:
    """GC-A21 ③ — 문서 값 그대로: 2026-10-03T14:59Z 스캔 = KST **10-03**, 15:00Z = KST **10-04**. 날짜형 기일 10-11이면 14:59Z는
    D-8(알림 0)·15:00Z는 D-7(알림 1)"""
    shipment = _shipment(kind="IMPORT")
    _plan_on(shipment, "IMPORT_TAX_DUE", date(2026, 10, 11))
    assert _scan(datetime(2026, 10, 3, 14, 59, tzinfo=UTC))["threshold"] == 0
    assert _scan(datetime(2026, 10, 3, 15, 0, tzinfo=UTC))["threshold"] == 1
    assert [k.split(":")[3] for k in _keys("shipments", shipment)] == [
        "IMPORT_TAX_DUE/D-7@2026-10-11"
    ]


# ── 충족 신호·적재기한 ───────────────────────────────────────────────────────


def test_an_actual_satisfies_the_deadline_and_stops_alerts() -> None:
    """실적이 있으면 대상이 아니다(GC-25 ①) — Cargo Closing·서류마감 실적, 수입 세금 납부 실적"""
    export = _shipment()
    _plan_at(export, "CARGO_CLOSING", _at(1), actual=_at(-1))
    _plan_at(export, "DOC_CUTOFF", _at(1), actual=_at(-2))
    imported = _shipment(kind="IMPORT")
    _plan_on(imported, "IMPORT_TAX_DUE", TODAY, actual=TODAY - timedelta(days=1))
    _scan()
    assert _keys("shipments", export) == [] and _keys("shipments", imported) == []


def test_loading_deadline_alerts_until_etd_or_bl_actual_fulfils_it() -> None:
    """적재기한 = 수리일+30(통관 기록 MIN): 수리 09-08 → 기한 10-08(D-5 → D-7 알림) / ETD 실적이 있으면(MET) 0 / 지나면 도과 1건(GC-25 ②)"""
    open_ = _shipment()
    _customs_accepted(open_, date(2026, 9, 8))
    met = _shipment()
    _customs_accepted(met, date(2026, 9, 8))
    _plan_on(met, "ETD", date(2026, 10, 1), actual=date(2026, 10, 1))
    late = _shipment()
    _customs_accepted(late, date(2026, 8, 30))  # 기한 09-29 — 도과
    _scan()
    assert [k.split(":")[3] for k in _keys("shipments", open_)] == [
        "LOADING_DEADLINE/D-7@2026-10-08"
    ]
    assert _keys("shipments", met) == []
    assert [k.split(":")[3] for k in _keys("shipments", late)] == [
        "LOADING_DEADLINE/overdue@2026-09-29"
    ]


def test_loading_deadline_is_not_scanned_without_a_customs_acceptance() -> None:
    """수리 실적이 없으면 기한 자체가 없다(NOT_CLEARED — 계획 수리일로 계산하지 않는다) — 알림 0"""
    shipment = _shipment()
    _plan_on(shipment, "CUSTOMS_CLEARED", date(2026, 9, 8))
    _scan()
    assert _keys("shipments", shipment) == []


# ── 대상 밖(L/C·대금만기·ETD/ETA·OEM·취소) ───────────────────────────────────


def test_lc_payment_and_presentation_deadlines_never_alert_with_the_flag_off() -> None:
    """L/C 기능 플래그 오프(기본) — L/C 선적의 대금만기·제시기한은 UNKNOWN이고 알림 0, T/T 대금만기도 S3-2 알림 0(충족 신호 S3-3 — H-06)"""
    lc = _shipment(terms="LC")
    tt = _shipment()
    for shipment in (lc, tt):
        _plan_at(
            shipment, "DOC_CUTOFF", _at(30)
        )  # 스캔 후보로 만든다(먼 기일 — 문턱 전) — 후보 거르기에 기대 0건이 되지 않게
        _plan_on(shipment, "ETD", TODAY + timedelta(days=1))
        _plan_on(shipment, "BL_ISSUED", TODAY + timedelta(days=1))
        _plan_on(shipment, "ETA", TODAY + timedelta(days=2))
    board = milestone_view.get_board(lc)
    assert (
        next(r for r in board["rows"] if r["milestone_type"] == "PRESENTATION_DEADLINE")["derived"][
            "status"
        ]
        == "UNKNOWN"
    )
    counts = _scan()
    assert (
        counts["shipments"] == 2
    )  # 두 선적 모두 스캔했다(보드 조립 — 대금만기는 T/T라 산정 OK, L/C는 UNKNOWN)
    assert counts["threshold"] == counts["overdue"] == 0
    keys = _keys("shipments")
    assert not [
        k
        for k in keys
        if any(t in k for t in ("PAYMENT_DUE", "PRESENTATION", ":ETD/", ":ETA/", "BL_ISSUED"))
    ]


def test_oem_production_milestones_never_alert() -> None:
    """OEM 생산 일정(PO 소유 4종)은 대상이 아니다(B15 '알림 없음') — OEM PO를 원천으로 한 수입선적을 **실제 스캔 후보**(서류마감 +30)로
    만들고 OEM 4종 기일을 모두 지나게 해도 OEM 키 0(적대 검토 ⑨ — 후보가 아니라 0건인 공회전 방지)"""
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    with owner_engine.begin() as connection:
        for milestone_type in ("RAW_MATERIAL_READY", "FILLING", "PACKING", "OUTGOING_INSPECTION"):
            connection.execute(
                text(
                    "INSERT INTO milestones (po_id, milestone_type, planned_on) VALUES (:p, :t, :d)"
                ),
                {"p": po_id, "t": milestone_type, "d": TODAY - timedelta(days=1)},
            )
    shipment = raw_shipment(confirmed_so()["id"], kind="IMPORT", po_id=po_id)
    _plan_at(shipment, "DOC_CUTOFF", _at(30))
    counts = _scan()
    assert counts["shipments"] == 1
    assert counts["threshold"] == counts["overdue"] == 0
    assert _alerts("purchase_orders") == []
    oem = ("RAW_MATERIAL_READY", "FILLING", "PACKING", "OUTGOING_INSPECTION")
    assert not [k for k in _keys() if any(t in k for t in oem)]


def test_cancelled_and_deleted_shipments_are_not_scanned() -> None:
    cancelled, deleted = _shipment(), _shipment()
    for shipment in (cancelled, deleted):
        _plan_at(shipment, "CARGO_CLOSING", _at(1))
    _exec("UPDATE shipments SET status = 'CANCELLED' WHERE id = :i", i=cancelled)
    _exec("UPDATE shipments SET deleted_at = now() WHERE id = :i", i=deleted)
    assert _scan()["shipments"] == 0
    assert _keys() == []


# ── 수신자(H-04·H-05) ────────────────────────────────────────────────────────


def test_without_assignee_or_rule_the_alert_falls_back_to_every_admin(
    admins: tuple[int, int],
) -> None:
    """담당자가 수신자가 못 됨(비활성 — 선적 담당자는 NOT NULL) + 규칙 없음 → ADMIN 전원 폴백(Routing.DEADLINE — 본문 꼬리말로 이유를 말한다, H-05)"""
    shipment = _shipment(inactive_assignee=True)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    rows = _alerts("shipments", shipment)
    assert sorted(r[1] for r in rows) == sorted(admins)
    assert all(notifications.FALLBACK_NOTE in r[4] for r in rows)


def test_a_rule_sets_thresholds_and_recipient_when_there_is_no_assignee(
    admins: tuple[int, int],
) -> None:
    """규칙(`shipments.milestone.approaching`)의 config.thresholds가 기본 D-7/3/1을 덮고, 담당자가 수신자가 못 되면(비활성) 규칙 수신자가 받는다(폴백 0)"""
    reader = create_user(f"{unique('tds-rule')}@example.com", roles=(RoleCode.LOGISTICS,))
    with unit_of_work() as uow:
        uow.session.add(
            AlertRule(
                code="RULE-TRADE-DEADLINE",
                name_ko="무역 기일 규칙",
                event_type=deadline_scan.SHIPMENT_EVENT,
                recipient_user_id=reader,
                config={"thresholds": [10]},
            )
        )
    shipment = _shipment(inactive_assignee=True)
    _plan_at(shipment, "CARGO_CLOSING", _at(9))
    _scan()
    rows = _alerts("shipments", shipment)
    assert [(r[0].split(":")[3], r[1]) for r in rows] == [
        (f"CARGO_CLOSING/D-10@{deadline_scan.instant_stamp(_at(9))}", reader)
    ]
    assert notifications.FALLBACK_NOTE not in rows[0][4]


@pytest.mark.group_j
def test_a_handover_is_reflected_at_once_without_resending(admins: tuple[int, int]) -> None:
    """담당 A의 D-7 알림 → 일괄 이관 A→B → 다음 스캔: 같은 키 재발송 0(이관 키 재작성), 새 문턱(D-3)은 B에게(H-04)"""
    a = create_user(f"{unique('tds-a')}@example.com", roles=(RoleCode.LOGISTICS,))
    b = create_user(f"{unique('tds-b')}@example.com", roles=(RoleCode.LOGISTICS,))
    shipment = _shipment(assignee=a)
    _plan_at(shipment, "CARGO_CLOSING", _at(5))
    _scan()
    handover.reassign_all(from_user_id=a, to_user_id=b, actor_user_id=admins[0])
    assert _assignee(shipment) == b
    assert _scan()["threshold"] == 0  # D-7은 이미 B 앞으로 옮겨진 키 — 재발송 0
    _scan(NOW + timedelta(days=2))  # D-3
    rows = [r for r in _alerts("shipments", shipment) if r[0].startswith("deadline:")]
    assert {r[1] for r in rows} == {b}
    assert len(rows) == 2 and all(r[0].endswith(f":{b}") for r in rows)
    # 옮겨진 D-7이 미확인이라 D-3에서 관리자 에스컬레이션(이관 뒤에도 같은 기일 판정이 이어진다)
    assert {r[1] for r in _alerts("shipments", shipment) if r[0].startswith("esc:")} == set(admins)


# ── 판정 불가(TZ_UNRESOLVED) ─────────────────────────────────────────────────


def test_an_unknown_timezone_is_alerted_once_as_unresolved_not_guessed() -> None:
    """tzdata가 모르는 시간대 → D-N은 KST로 추정하지 않고 '판정 불가' 1건(재실행 0), 기일 전이면 문턱 알림 0 — 조용한 누락 금지"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", _at(1), tz="Mars/Olympus_Mons")
    counts = _scan()
    assert counts["unresolved"] == 1 and counts["overdue"] == counts["threshold"] == 0
    (row,) = _alerts("shipments", shipment)
    assert row[0].startswith("deadline-unresolved:") and "/UNRESOLVED@" in row[0]
    assert row[2] == "CRITICAL" and "시간대" in row[4]
    assert _scan()["unresolved"] == 0


def test_an_unknown_timezone_deadline_is_still_overdue_by_utc_and_escalates(
    admins: tuple[int, int],
) -> None:
    """시간대를 몰라도 도과는 UTC 비교로 확정이다(적대 검토 ②) — 지난 기일이면 판정 불가 + 도과 알림, 24시간 넘게 미확인이면 에스컬레이션"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", _at(-1), tz="Mars/Olympus_Mons")
    counts = _scan()
    assert counts["unresolved"] == 1 and counts["overdue"] == 1 and counts["threshold"] == 0
    overdue = [k for k in _keys("shipments", shipment) if "/overdue@" in k]
    assert overdue and overdue[0].startswith("deadline:shipments:")
    assert _scan(NOW + timedelta(days=2))["escalated"] == len(admins)


def test_an_unresolved_alert_is_never_escalation_evidence(admins: tuple[int, int]) -> None:
    """판정 불가 알림(미확인)만 남아 있으면 기일이 지나도 에스컬레이션 근거가 아니다(키 종류 분리 — 적대 검토 ③). 그날 만든 도과 알림도 근거 아님"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", _at(1), tz="Mars/Olympus_Mons")
    _scan()  # 판정 불가 1건(미확인)
    later = _scan(NOW + timedelta(days=2))  # 이제 도과 — 판정 불가 알림은 하루 넘게 미확인
    assert later["overdue"] == 1 and later["escalated"] == 0
    assert not [k for k in _keys("shipments", shipment) if k.startswith("esc:")]


# ── 견적·PI 만료 임박(H-07 · GC-A21 ②) ───────────────────────────────────────


@pytest.mark.golden
def test_gc_a21_quotation_and_pi_candidates_are_the_expiry_sweep_candidates() -> None:
    """GC-A21 ② — ISSUED QT valid_until 10-10·오늘 10-03 → D-7 / 살아 있는 후속(PI) 있는 QT·CONVERTED QT·EXPIRED QT → 0 /
    valid_until = 오늘 → D-0 대상(문턱 3개) / ISSUED PI D-3 → 알림 / 일부입금 PI → 0"""
    d7 = raw_quotation("ISSUED", valid_until=date(2026, 10, 10))
    held = raw_quotation("ISSUED", valid_until=date(2026, 10, 10))
    raw_pi(held, "ISSUED", valid_until=date(2026, 12, 31))  # 후속(PI)이 부모를 붙잡는다
    so_held = raw_quotation("ISSUED", valid_until=date(2026, 10, 10))
    raw_so("RECEIVED", qt_id=so_held)  # 문서 값: 살아 있는 SO가 있는 QT
    converted = raw_quotation("CONVERTED", valid_until=date(2026, 10, 10))
    today_qt = raw_quotation("ISSUED", valid_until=TODAY)
    pi_d3 = raw_pi(
        raw_quotation("CONVERTED", valid_until=date(2026, 12, 31)),
        "ISSUED",
        valid_until=date(2026, 10, 6),
    )
    paid_pi = raw_pi(
        raw_quotation("CONVERTED", valid_until=date(2026, 12, 31)),
        "PARTIALLY_PAID",
        valid_until=date(2026, 10, 6),
    )
    _scan()
    assert [k.split(":")[3] for k in _keys("quotations", d7)] == ["VALIDITY/D-7@2026-10-10"]
    assert _keys("quotations", held) == [] and _keys("quotations", converted) == []
    assert _keys("quotations", so_held) == []
    assert {k.split(":")[3] for k in _keys("quotations", today_qt)} == {
        f"VALIDITY/D-{n}@{TODAY}" for n in (7, 3, 1)
    }
    assert {k.split(":")[3] for k in _keys("proforma_invoices", pi_d3)} == {
        "VALIDITY/D-7@2026-10-06",
        "VALIDITY/D-3@2026-10-06",
    }
    assert _keys("proforma_invoices", paid_pi) == []
    # 문서 값: valid_until = 오늘인 QT는 다음 날 스윕이 EXPIRED로 닫는다(D-0까지 알림 대상이던 그 문서)
    assert sweep_expired_documents(base_date=TODAY + timedelta(days=1))["expired_qt"] >= 1
    assert _rows("SELECT status FROM quotations WHERE id = :i", i=today_qt) == [("EXPIRED",)]


def test_a_lapsed_quotation_is_the_sweeps_business_not_an_overdue_alert() -> None:
    """경과(valid_until < 오늘)는 스윕이 EXPIRED로 닫는다 — 스캔은 도과 알림을 내지 않는다. 같은 문서가 두 경로 중 정확히 한 곳의 후보"""
    lapsed = raw_quotation("ISSUED", valid_until=TODAY - timedelta(days=1))
    alive = raw_quotation("ISSUED", valid_until=TODAY)
    _scan()
    assert _keys("quotations", lapsed) == [] and _keys("quotations", alive)
    swept = sweep_expired_documents(base_date=TODAY)
    assert swept["expired_qt"] == 1
    assert _rows("SELECT status FROM quotations WHERE id = :i", i=alive) == [("ISSUED",)]


@pytest.mark.group_k
def test_the_scan_targets_are_pinned() -> None:
    """대상 종류는 정확히 4종 — 대금만기·제시기한(충족 신호 S3-3)·OEM 생산 4종(B15)·ETD·ETA·B/L·PSI·신고수리는 넣지 않는다(ADR-0084 ②⑥).
    종류를 늘리려면 이 집합과 ADR을 같이 고친다(dedup 키 계보 — 가산만)."""
    from app.modules.trade_docs.constants import OEM_MILESTONES

    assert {
        "DOC_CUTOFF",
        "CARGO_CLOSING",
        "IMPORT_TAX_DUE",
        "LOADING_DEADLINE",
    } == deadline_scan.SCAN_TYPES
    assert not deadline_scan.SCAN_TYPES & OEM_MILESTONES
    assert deadline_scan.DEFAULT_THRESHOLDS == (7, 3, 1)


@pytest.mark.group_k
def test_a_board_row_without_a_verdict_fails_the_shipment_instead_of_skipping() -> None:
    """대상 행인데 보드가 D-N·도과를 내지 않으면(도달 불가 방어) 조용히 건너뛰지 않고 예외 — 건별 실패 집계 → 잡 FAILED(fail-visible)"""
    row = {
        "milestone_type": "DOC_CUTOFF",
        "applicable": True,
        "actual": None,
        "planned": {"at_utc": "2026-10-05T00:00:00+00:00", "tz": "Asia/Seoul"},
        "unknown_reason": None,
        "days_left": None,
        "is_overdue": None,
    }
    with pytest.raises(RuntimeError, match="판정"):
        deadline_scan.shipment_dues(
            {"rows": [row]}, shipment_id=1, doc_number="SH-X", assignee_id=1, now=NOW
        )


# ── 문턱 해석 공용화 회귀(deadlines.policy 공개 승격) ─────────────────────────


def test_policy_promotion_keeps_the_certification_defaults_and_takes_trade_defaults() -> None:
    """`deadlines.policy` 공개 승격(B13) — 인자 없는 인증·문서 축은 D-180/90/30 그대로, 무역 축은 D-7/3/1, 흐린 config는 그 축의 기본값"""
    from app.modules.deadlines import service as deadlines

    with unit_of_work() as uow:
        assert deadlines.policy(uow.session, deadlines.CERTIFICATION_EVENT).thresholds == (
            180,
            90,
            30,
        )
        trade = deadlines.policy(
            uow.session, deadline_scan.SHIPMENT_EVENT, defaults=deadline_scan.DEFAULT_THRESHOLDS
        )
        assert trade.thresholds == (7, 3, 1) and trade.rule is None
        uow.session.add(
            AlertRule(
                code="RULE-BAD-CONFIG",
                name_ko="흐린 규칙",
                event_type=deadline_scan.QUOTATION_EVENT,
                config={"thresholds": ["7", 0]},
            )
        )
        uow.session.flush()
        assert deadlines.policy(
            uow.session, deadline_scan.QUOTATION_EVENT, defaults=deadline_scan.DEFAULT_THRESHOLDS
        ).thresholds == (7, 3, 1)


# ── J 알림만·트랜잭션 규율 ───────────────────────────────────────────────────


@pytest.mark.group_j
def test_the_scan_writes_alerts_only_no_events_and_no_document_rows() -> None:
    """알림 말고는 아무것도 바꾸지 않는다 — 이벤트 0·선적/마일스톤/견적 행은 xmin까지 그대로(4금 — ADR-0084)"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", _at(1))
    qt = raw_quotation("ISSUED", valid_until=TODAY + timedelta(days=2))
    pi = raw_pi(
        raw_quotation("CONVERTED", valid_until=date(2026, 12, 31)),
        "ISSUED",
        valid_until=TODAY + timedelta(days=2),
    )
    _customs_accepted(shipment, TODAY - timedelta(days=27))

    def snapshot() -> tuple[Any, ...]:
        return (
            _rows("SELECT count(*) FROM events"),
            _rows("SELECT xmin::text, * FROM shipments WHERE id = :i", i=shipment),
            _rows("SELECT xmin::text, * FROM milestones WHERE shipment_id = :i", i=shipment),
            _rows("SELECT xmin::text, * FROM quotations WHERE id = :i", i=qt),
            _rows("SELECT xmin::text, * FROM proforma_invoices WHERE id = :i", i=pi),
            _rows("SELECT xmin::text, * FROM customs_records WHERE shipment_id = :i", i=shipment),
            _rows("SELECT xmin::text, * FROM sales_orders ORDER BY id"),
            _rows("SELECT count(*) FROM milestone_changes"),
        )

    before = snapshot()
    counts = _scan()
    assert counts["threshold"] > 0
    assert snapshot() == before


@pytest.mark.group_j
def test_it_refuses_to_join_an_open_transaction() -> None:
    with unit_of_work(), pytest.raises(RuntimeError, match="unit_of_work 밖"):
        scan_trade_deadlines(now=NOW)


@pytest.mark.group_k
@pytest.mark.parametrize("page_size", [1, 2, 3, 500])
def test_keyset_pages_visit_every_candidate(page_size: int) -> None:
    """후보 페이지 크기와 무관하게 전 건을 정확히 1회 본다(경계 정확·경계 넘음·한 페이지)"""
    shipments = [_shipment() for _ in range(3)]
    for shipment in shipments:
        _plan_at(shipment, "CARGO_CLOSING", _at(5))
    counts = _scan(page_size=page_size)
    assert counts["shipments"] == 3 and counts["threshold"] == 3
    with pytest.raises(ValueError):
        _scan(page_size=0)


# ── I 레지스트리·실패 격리·CLI ───────────────────────────────────────────────


@pytest.mark.group_i
def test_one_failing_shipment_does_not_stop_the_others_and_fails_the_job(
    monkeypatch: pytest.MonkeyPatch, admins: tuple[int, int]
) -> None:
    """건별 독립 TX — 3건 중 1건의 보드 조립에 예외 주입 → 나머지 2건 알림 커밋·failed=1, 잡은 FAILED + 관리자 실패 알림(I-03)"""
    shipments = [_shipment() for _ in range(3)]
    for shipment in shipments:
        _plan_at(shipment, "CARGO_CLOSING", _at(5))
    broken = shipments[1]
    real = milestone_view.assemble

    def assemble(session: Any, row: Any, **kwargs: Any) -> Any:
        if row.id == broken:
            raise RuntimeError("조립 실패 주입")
        return real(session, row, **kwargs)

    monkeypatch.setattr(milestone_view, "assemble", assemble)
    counts = _scan()
    assert counts["failed"] == 1 and counts["shipments"] == 2 and counts["threshold"] == 2
    assert _keys("shipments", broken) == []
    assert all(_keys("shipments", s) for s in shipments if s != broken)

    scheduler.register_jobs()
    with pytest.raises(RuntimeError, match="무역 기일 스캔 1건 실패"):
        scheduler.JOBS_BY_CODE[JOB].run()
    _exec("UPDATE scheduled_jobs SET last_run_at = NULL WHERE code = :c", c=JOB)
    scheduler.run_due_jobs(now=utcnow())
    status, error = _rows(
        "SELECT last_status, coalesce(last_error, '') FROM scheduled_jobs WHERE code = :c", c=JOB
    )[0]
    assert status == "FAILED" and "무역 기일 스캔" in error
    failed = _rows(
        f"SELECT recipient_user_id FROM alerts WHERE dedup_key LIKE 'jobs.failed:{JOB}:%'"
    )
    assert sorted(r[0] for r in failed) == sorted(admins)


@pytest.mark.group_i
def test_the_job_is_registered_daily_at_0640_and_runs_ok_when_clean() -> None:
    """레지스트리 daily@06:40 — 실패 0이면 잡 OK(I-02)"""
    assert scheduler.JOBS_BY_CODE[JOB].schedule == "daily@06:40"
    scheduler.register_jobs()
    scheduler.run_due_jobs(now=utcnow())
    assert _rows("SELECT last_status FROM scheduled_jobs WHERE code = :c", c=JOB) == [("OK",)]


@pytest.mark.group_i
def test_the_cli_runs_the_same_scan(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 수동 실행 = 같은 함수(지금 시각) — 실패 0이면 종료 코드 0·건수 출력, 재실행 신규 0. 기준일 인자는 받지 않는다"""
    shipment = _shipment()
    _plan_at(shipment, "CARGO_CLOSING", utcnow() + timedelta(days=2))
    assert cli.main([JOB]) == 0
    out = capsys.readouterr().out
    assert "무역 기일 스캔 완료" in out and "실패 0건" in out and "문턱 2" in out
    assert cli.main([JOB]) == 0
    assert "문턱 0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main([JOB, "--base-date", "2026-10-03"])


@pytest.mark.group_j
def test_a_successor_created_mid_scan_stops_the_quotation_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """견적 D-N 경합 — 첫 판정 뒤 살아 있는 SO가 커밋되면(후속이 부모를 붙잡음) 발송 직전 재확인에서 빠진다(알림 0, deferred 1)"""
    qt = raw_quotation("ISSUED", valid_until=TODAY + timedelta(days=2))
    so_owner = create_user(
        f"{unique('tds-so')}@example.com", roles=(RoleCode.TRADE,)
    )  # 끼워 넣는 쪽은 스캔 TX 밖에서 커밋
    real = deadline_scan.has_live_children
    calls = {"n": 0}

    def wrapped(*args: Any, **kwargs: Any) -> bool:
        result = real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            raw_so("RECEIVED", qt_id=qt, assignee_id=so_owner)
        return result

    monkeypatch.setattr(deadline_scan, "has_live_children", wrapped)
    counts = _scan()
    assert counts["quotations"] == 1 and counts["deferred"] == 1 and counts["threshold"] == 0
    assert _keys("quotations", qt) == []
