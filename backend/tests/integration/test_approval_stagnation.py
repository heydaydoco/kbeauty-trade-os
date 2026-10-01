"""H. 결재 대기 정체 독촉 — N일 기준·k 몫 dedup·규칙 덮어쓰기·수신자·상태 불변·건별 실패 (S3-1 PR-9a / ADR-0061 / design-C C7).

잡은 **알림만** 만든다 — 승인 상태·이벤트·전표를 바꾸지 않는다(만료·자동 결정 없음). 기안자 퇴사·자격자 부재 같은 정체를 fail-visible로 올리는 것이 목적이다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.time import today_kst
from app.modules.approvals import alerts, stagnation
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler
from app.modules.worklist.models import AlertRule
from tests.factories.approvals import (
    add_line,
    approval_row,
    count,
    credit_so,
    decide,
    events_of,
    make_user,
    request,
)

pytestmark = pytest.mark.group_h


def _pending(role: str = "TRADE") -> tuple[int, Any, Any]:
    so = credit_so()
    if count("approval_lines") == 0:
        add_line(0, role=role)
    requester, approver = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    return int(request(so["id"], requester).approval.id), requester, approver


def _trade_holders_except(user_id: int) -> set[int]:
    """TRADE 역할 보유 활성 사용자(기안자 제외) — `credit_so()`가 만드는 SO 담당자도 TRADE라 수신자에 포함된다."""
    with owner_engine.connect() as connection:
        return {
            int(r[0])
            for r in connection.execute(
                text(
                    "SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id AND ur.deleted_at IS NULL"
                    " JOIN roles r ON r.id = ur.role_id WHERE r.code = 'TRADE' AND u.is_active AND u.id <> :x"
                ),
                {"x": user_id},
            )
        }


def _alerts(approval_id: int) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text("SELECT * FROM alerts WHERE dedup_key LIKE :p ORDER BY id"),
                {"p": f"approval:{approval_id}:stagnation#%"},
            ).mappings()
        ]


def _rule(config: dict[str, Any] | None) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            AlertRule(
                code="RULE-APPROVALS-STAGNATION",
                name_ko="결재 정체",
                event_type=alerts.STAGNATION_EVENT,
                config=config or {},
            )
        )


def _scan(base: date) -> dict[str, int]:
    return stagnation.scan_approval_stagnation(base_date=base)


def test_default_threshold_is_two_days_and_the_period_k_repeats_every_n_days() -> None:
    """기본 N=2: 1일 경과는 0건, 2일째 k=1 신규, 같은 날 재실행 0건, 3일째도 같은 k라 0건, 4일째 k=2 신규 — N일마다 1건(수신자별)"""
    approval_id, requester, _ = _pending()
    n = len(_trade_holders_except(requester.id))  # 수신자 수 = TRADE 결재 자격자(기안자 제외)
    assert n >= 1
    today = today_kst()
    assert _scan(today + timedelta(days=1))["stagnant"] == 0
    assert _scan(today + timedelta(days=2))["stagnant"] == n
    assert _scan(today + timedelta(days=2))["stagnant"] == 0  # 멱등
    assert _scan(today + timedelta(days=3))["stagnant"] == 0  # 같은 k=1
    assert _scan(today + timedelta(days=4))["stagnant"] == n  # k=2
    keys = sorted({a["dedup_key"].rsplit(":", 1)[0] for a in _alerts(approval_id)})
    assert keys == [f"approval:{approval_id}:stagnation#1", f"approval:{approval_id}:stagnation#2"]


def test_an_alert_rule_overrides_n_and_a_malformed_config_falls_back_to_the_default() -> None:
    """alert_rules(approvals.stagnation).config.days가 N을 덮어쓴다 — 형이 흐린 값(문자·0·음수·불리언)은 기본 2일"""
    approval_id, requester, _ = _pending()
    n = len(_trade_holders_except(requester.id))
    today = today_kst()
    _rule({"days": 5})
    assert _scan(today + timedelta(days=4))["stagnant"] == 0
    assert _scan(today + timedelta(days=5))["stagnant"] == n
    for bad in ("3", 0, -1, True, None, [3]):
        assert stagnation.normalize_days(bad) == stagnation.DEFAULT_STAGNATION_DAYS
    assert stagnation.normalize_days(7) == 7
    assert len(_alerts(approval_id)) == n


def test_the_elapsed_days_use_the_korean_calendar_date_not_utc() -> None:
    """요청 시각이 UTC 14일 15:30(= KST 15일 00:30)이면 17일(KST) 기준 경과는 2일이다 — UTC 날짜 차(3일)로 세지 않는다"""
    approval_id, _, _ = _pending()
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE approvals SET created_at = '2026-10-14T15:30:00+00:00' WHERE id = :i"),
            {"i": approval_id},
        )
    assert _scan(date(2026, 10, 16))["stagnant"] == 0  # KST로 1일(UTC 날짜 차였다면 이미 2일)
    assert _scan(date(2026, 10, 17))["stagnant"] >= 1  # KST로 2일


def test_recipients_are_eligible_approvers_only_and_never_the_requester() -> None:
    """수신자는 결재 역할 보유자(기안자 제외)뿐이다 — ADMIN은 역할 보유자가 있으면 받지 않고 다른 역할은 받지 않는다"""
    approval_id, requester, approver = _pending()
    other_trade = make_user(RoleCode.TRADE)
    admin, logistics = make_user(RoleCode.ADMIN), make_user(RoleCode.LOGISTICS)
    expected = _trade_holders_except(requester.id)
    assert {approver.id, other_trade.id} <= expected
    assert _scan(today_kst() + timedelta(days=2))["stagnant"] == len(expected)
    got = {a["recipient_user_id"] for a in _alerts(approval_id)}
    assert got == expected
    assert requester.id not in got and admin.id not in got and logistics.id not in got


def test_with_no_role_holder_the_admins_get_it_with_the_fallback_note() -> None:
    """결재 역할 보유자가 0명이면 활성 ADMIN 전원에게(기안자 제외) 가고 본문에 '관리자에게 전달' 안내가 붙는다"""
    so = credit_so()
    add_line(0, role="CERT")  # CERT 보유자 없음
    requester = make_user(RoleCode.TRADE)
    admin = make_user(RoleCode.ADMIN)
    approval_id = int(request(so["id"], requester).approval.id)
    assert _scan(today_kst() + timedelta(days=2))["stagnant"] == 1
    [alert] = _alerts(approval_id)
    assert alert["recipient_user_id"] == admin.id
    assert alerts.ROLE_FALLBACK_NOTE in alert["body"]


def test_the_alert_carries_no_amounts_and_points_at_the_approval() -> None:
    """독촉 알림의 제목·본문에 금액·이메일이 없고 entity_type='approvals'로 결재함 이동이 가능하다"""
    approval_id, _, _ = _pending()
    _scan(today_kst() + timedelta(days=2))
    alert = _alerts(approval_id)[0]
    text_blob = f"{alert['title']} {alert['body']}"
    assert alert["entity_type"] == "approvals" and alert["entity_id"] == approval_id
    assert "100000" not in text_blob and "100,000" not in text_blob and "@" not in text_blob
    assert alert["severity"] == "WARN"


def test_the_job_never_changes_an_approval() -> None:
    """잡 전후로 approvals 상태·이벤트·버전이 동일하다 — 알림만 만든다"""
    approval_id, _, _ = _pending()
    before = (approval_row(approval_id), len(events_of(approval_id)), count("approval_events"))
    _scan(today_kst() + timedelta(days=9))
    after = (approval_row(approval_id), len(events_of(approval_id)), count("approval_events"))
    assert before == after


def test_decided_approvals_are_not_nagged() -> None:
    """이미 결정(승인·반려)된 승인은 독촉 대상이 아니다"""
    approval_id, _, approver = _pending()
    decide(approval_id, approver, "APPROVE")
    result = _scan(today_kst() + timedelta(days=9))
    assert result == {"approvals": 0, "stagnant": 0, "failed": 0}


def test_one_failing_approval_fails_the_job_but_the_others_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """건별 독립 트랜잭션 — 한 건이 실패해도 나머지는 커밋되고, 실행기 잡은 FAILED(예외)로 올라간다"""
    first, _, _ = _pending()
    second, _, _ = _pending()
    real = alerts.notify_stagnation

    def flaky(session: Any, approval: Any, **kwargs: Any) -> int:
        if approval.id == first:
            raise RuntimeError("boom")
        return real(session, approval, **kwargs)

    monkeypatch.setattr(alerts, "notify_stagnation", flaky)
    monkeypatch.setattr(stagnation, "kst_today", lambda: today_kst() + timedelta(days=2))
    counts = stagnation.scan_approval_stagnation()
    assert counts["failed"] == 1 and counts["stagnant"] >= 1
    assert _alerts(first) == [] and len(_alerts(second)) >= 1
    with pytest.raises(RuntimeError, match="1건 실패"):
        scheduler._run_approval_stagnation_scan()


def test_the_job_is_registered_daily_at_0710() -> None:
    """레지스트리에 daily@07:10으로 등록돼 있다 — 인증 정체 스캔(07:00) 뒤·브리핑(09:00) 앞"""
    spec = scheduler.JOBS_BY_CODE["approval-stagnation-scan"]
    assert spec.schedule == "daily@07:10"
    assert (
        scheduler.JOBS_BY_CODE["stagnation-scan"].schedule
        < spec.schedule
        < (scheduler.JOBS_BY_CODE["daily-briefing"].schedule)
    )


def test_the_title_limit_matches_the_alert_column_and_the_deadline_constant() -> None:
    """알림 제목 길이 상수가 alerts.title 컬럼 길이·기일 엔진 상수와 같다(복사본이 어긋나지 않게)"""
    from app.modules.deadlines.service import ALERT_TITLE_MAX
    from app.modules.worklist.models import Alert

    assert alerts.ALERT_TITLE_MAX == ALERT_TITLE_MAX == Alert.title.type.length  # type: ignore[attr-defined]
