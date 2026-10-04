"""H·I·J·K. 승인 무결성 대사 잡 `approval-integrity-check` (S3-2 PR-1b / ADR-0087 / design-integrated §9 R-17 / PROGRESS PR-9a 부채 ①).

■ K `scan_all` — `check_integrity`를 `after_id` 페이지로 **전건** 순회한다(페이지 경계에서 빠지거나 겹치는 승인 0).
■ H 불일치 = ADMIN 인앱 알림, dedup `approval-integrity:{approval_id}:{problem}`(일자 제외) — 재실행 재발송 0, 문제가 바뀌면 새 알림.
■ I 레지스트리 daily@05:40 · 불일치는 잡 OK · 실행 중 예외만 잡 FAILED(+§15 실패 알림).
■ J 읽기 전용 — 대사 트랜잭션은 `READ ONLY`(쓰기 시도를 DB가 거부)·승인/이력 행은 xmin까지 그대로.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import cli
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.approvals import integrity
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler
from tests.factories.approvals import add_line, count, credit_so, make_user, request
from tests.support.factories import create_user

JOB = "approval-integrity-check"

pytestmark = pytest.mark.group_h


def _pending(n: int) -> list[int]:
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    return [
        int(request(credit_so()["id"], make_user(RoleCode.TRADE)).approval.id) for _ in range(n)
    ]


def _forge(sql: str, **params: Any) -> None:
    """소유자 권한 위조 시뮬레이션 — 앱 경로를 거치지 않는 직접 변경."""
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def _rows(sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def _admin() -> int:
    return create_user(f"integrity-admin-{count('users')}@example.com", roles=(RoleCode.ADMIN,))


def _integrity_alerts() -> list[tuple[Any, ...]]:
    return _rows(
        "SELECT dedup_key, recipient_user_id, entity_type, entity_id, severity, title, coalesce(body, '') "
        "FROM alerts WHERE dedup_key LIKE 'approval-integrity:%' ORDER BY id"
    )


def _snapshot() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """승인·이력 전 행 + xmin(행 버전) — 같은 값으로 덮어쓰는 UPDATE도 xmin을 바꾼다."""
    return (
        _rows("SELECT xmin::text, * FROM approvals ORDER BY id"),
        _rows("SELECT xmin::text, * FROM approval_events ORDER BY id"),
    )


# ── 알림·dedup (H) ───────────────────────────────────────────────────────────


def test_a_clean_db_sends_nothing() -> None:
    _admin()
    _pending(2)
    assert integrity.run_integrity_check() == {"approvals": 2, "mismatches": 0, "notified": 0}
    assert _integrity_alerts() == []


def test_one_mismatch_alerts_each_admin_once_and_a_rerun_resends_nothing() -> None:
    """불일치 1건 → ADMIN 인앱 알림 1회(키 = 승인 id·문제, 일자 없음) · 재실행 재발송 0 · 자동 정정 없음"""
    admin = _admin()
    ids = _pending(2)
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[1])

    assert integrity.run_integrity_check() == {"approvals": 2, "mismatches": 1, "notified": 1}
    alerts = _integrity_alerts()
    assert len(alerts) == 1
    key, recipient, entity_type, entity_id, severity, title, body = alerts[0]
    assert key == f"approval-integrity:{ids[1]}:NO_EVENTS:{admin}"
    assert (recipient, entity_type, entity_id, severity) == (admin, "approvals", ids[1], "CRITICAL")
    assert f"#{ids[1]}" in title and "NO_EVENTS" in body and "이력 이벤트가 하나도 없습니다" in body

    assert integrity.run_integrity_check() == {"approvals": 2, "mismatches": 1, "notified": 0}
    assert scheduler.JOBS_BY_CODE[JOB].run() == {"approvals": 2, "mismatches": 1, "notified": 0}
    assert len(_integrity_alerts()) == 1
    assert count("approval_events", "approval_id = :i", i=ids[1]) == 0  # 조용한 정정 금지


def test_a_new_problem_on_the_same_approval_is_a_new_alert() -> None:
    """같은 승인이라도 문제가 바뀌면 새 알림 — 이미 알린 문제는 다시 보내지 않는다(문제별 1회)"""
    admin = _admin()
    (approval_id,) = _pending(1)
    other = make_user(RoleCode.TRADE)
    _forge(
        "UPDATE approvals SET status = 'APPROVED', decided_by_id = :u, decided_at = now() WHERE id = :i",
        u=other.id,
        i=approval_id,
    )
    with unit_of_work() as uow:
        first = {p["problem"] for p in integrity.check_integrity(uow.session)}
    assert "STATUS_MISMATCH" in first
    assert integrity.run_integrity_check()["notified"] == len(first)

    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=approval_id)
    counts = integrity.run_integrity_check()
    assert counts == {"approvals": 1, "mismatches": 1, "notified": 1}
    keys = [row[0] for row in _integrity_alerts()]
    assert keys[-1] == f"approval-integrity:{approval_id}:NO_EVENTS:{admin}"
    assert len(keys) == len(first) + 1 == len(set(keys))


def test_the_dedup_subject_has_no_date() -> None:
    assert integrity.dedup_subject(7, "NO_EVENTS") == "approval-integrity:7:NO_EVENTS"


# ── 레지스트리·실패 의미 (I) ────────────────────────────────────────────────


def _job_status() -> tuple[Any, ...]:
    return _rows(
        f"SELECT last_status, coalesce(last_error, '') FROM scheduled_jobs WHERE code = '{JOB}'"
    )[0]


def test_the_job_is_registered_daily_at_0540_and_a_mismatch_is_not_a_failure() -> None:
    """레지스트리 daily@05:40 · 불일치가 있어도 잡은 OK(알림만) — FAILED는 실행 예외 전용"""
    assert scheduler.JOBS_BY_CODE[JOB].schedule == "daily@05:40"
    _admin()
    ids = _pending(1)
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[0])
    scheduler.register_jobs()
    scheduler.run_due_jobs()
    assert _job_status() == ("OK", "")
    assert len(_integrity_alerts()) == 1
    assert _rows("SELECT count(*) FROM alerts WHERE dedup_key LIKE 'jobs.failed:%'") == [(0,)]


def test_an_exception_during_the_scan_fails_the_job_and_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실행 중 예외는 삼키지 않는다 — 잡 FAILED + §15 실패 알림(jobs.failed)"""
    _admin()
    scheduler.register_jobs()

    def boom(_session: Session, *, page_size: int) -> Any:
        raise RuntimeError("대사 실패 주입")

    monkeypatch.setattr(integrity, "scan_all", boom)
    with pytest.raises(RuntimeError):
        scheduler.JOBS_BY_CODE[JOB].run()
    scheduler.run_due_jobs()
    status, error = _job_status()
    assert status == "FAILED" and "대사 실패 주입" in error
    assert _rows(f"SELECT count(*) FROM alerts WHERE dedup_key LIKE 'jobs.failed:{JOB}:%'") == [
        (1,)
    ]


def test_the_cli_runs_the_same_check(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 수동 실행 = 같은 함수 — 깨끗하면 0, 불일치가 있으면 1(알림은 문제별 1회)"""
    _admin()
    ids = _pending(1)
    assert cli.main([JOB]) == 0
    assert "불일치 0건" in capsys.readouterr().out
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[0])
    assert cli.main([JOB]) == 1
    out = capsys.readouterr().out
    assert "불일치 1건" in out and "신규 알림 1건" in out
    assert cli.main([JOB]) == 1
    assert "신규 알림 0건" in capsys.readouterr().out


# ── 읽기 전용 (J) ───────────────────────────────────────────────────────────


@pytest.mark.group_j
def test_the_job_leaves_every_approval_and_event_row_untouched() -> None:
    """불일치가 있어도 잡 실행 전후 승인·이력 행이 xmin(행 버전)까지 같다 — 승인 행 수정 0"""
    _admin()
    ids = _pending(3)
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[0])
    before = _snapshot()
    assert integrity.run_integrity_check()["mismatches"] == 1
    scheduler.register_jobs()
    scheduler.run_due_jobs()
    assert _snapshot() == before


@pytest.mark.group_j
def test_the_scan_transaction_is_read_only_at_the_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """대사 트랜잭션에서는 DB가 쓰기를 거부한다 — 대사 코드가 승인 행을 고치려 해도 실패하고 행은 그대로다(읽기 전용의 DB 증명)"""
    (approval_id,) = _pending(1)
    before = _snapshot()
    real_scan = integrity.scan_all

    def scan_then_try_to_write(session: Session, *, page_size: int) -> Any:
        result = real_scan(session, page_size=page_size)
        session.execute(
            text("UPDATE approvals SET status = status WHERE id = :i"), {"i": approval_id}
        )
        return result

    monkeypatch.setattr(integrity, "scan_all", scan_then_try_to_write)
    with pytest.raises(DBAPIError, match="read-only transaction"):
        integrity.run_integrity_check()
    assert _snapshot() == before


@pytest.mark.group_j
def test_it_refuses_to_join_an_open_transaction() -> None:
    """바깥 트랜잭션에 합류하면 READ ONLY가 첫 문장이 못 된다 — 프로그래밍 오류로 멈춘다"""
    with unit_of_work(), pytest.raises(RuntimeError, match="독립 트랜잭션"):
        integrity.run_integrity_check()


@pytest.mark.group_j
def test_the_module_has_no_write_path_to_approvals() -> None:
    """대사 모듈은 승인 상태를 바꾸는 통로를 임포트·언급하지 않는다(서비스·UPDATE·add·setattr 0) — 쓰기는 notify(alerts)뿐"""
    source = inspect.getsource(integrity)
    for forbidden in (
        "approvals import service",
        "approvals.service",
        "UPDATE ",
        "DELETE ",
        "INSERT ",
        "session.add",
        "setattr",
        ".status =",
    ):
        assert forbidden not in source, forbidden


# ── 전건 순회 (K) ────────────────────────────────────────────────────────────


@pytest.mark.group_k
@pytest.mark.parametrize("page_size", [1, 2, 4, 5, 1000])
def test_scan_all_visits_every_approval_across_page_boundaries(page_size: int) -> None:
    """페이지 크기(1·경계 정확히 맞음·경계 넘음·한 페이지)와 무관하게 대사 수 = 승인 전건, 마지막 페이지의 위조까지 찾는다"""
    ids = _pending(4)
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[-1])
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[0])
    with unit_of_work() as uow:
        scanned, problems = integrity.scan_all(uow.session, page_size=page_size)
    assert scanned == 4
    assert problems == [
        {"approval_id": ids[0], "problem": "NO_EVENTS"},
        {"approval_id": ids[-1], "problem": "NO_EVENTS"},
    ]


@pytest.mark.group_k
def test_scan_all_on_an_empty_table_is_clean_and_rejects_a_zero_page() -> None:
    with unit_of_work() as uow:
        assert integrity.scan_all(uow.session) == (0, [])
        with pytest.raises(ValueError):
            integrity.scan_all(uow.session, page_size=0)
