"""J·H. 배치 실행기 (§15 스케줄 레지스트리 / §17.6 / 부채 #12 소비).

★ 이 파일이 고정하는 것:
  ① **schedule 문법이 폐쇄 2형**이다(판정 요청 13·조건 F) — 그 밖은 거부.
  ② **미스파이어는 1회로 수렴한다** — 다운 중 밀린 회차를 몰아 실행하지 않는다.
  ③ **등록되지 않은 코드는 실행되지 않는다** — 행을 넣는 것만으로 새 동작이
    생기면 scheduled_jobs 한 줄이 곧 코드 배포가 된다(4금 우회 통로 차단).
  ④ **실패·크래시가 조용히 지나가지 않는다**(§15 "실패 시 관리자 알림").
  ⑤ **중복 기동 방지**(advisory lock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.db.session import SessionFactory
from app.core.db.uow import unit_of_work
from app.core.time import KST
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler
from app.modules.platform.models import ScheduledJob
from app.modules.platform.scheduler import Schedule, ScheduleError, is_due, parse_schedule
from app.modules.worklist.models import Alert
from tests.support.factories import create_user

pytestmark = pytest.mark.group_j


# ── ① 문법 (조건 F) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("daily@06:00", Schedule("daily", hour=6, minute=0)),
        ("daily@23:59", Schedule("daily", hour=23, minute=59)),
        ("interval@1", Schedule("interval", minutes=1)),
        ("interval@1440", Schedule("interval", minutes=1440)),
    ],
)
def test_the_two_accepted_forms(value: str, expected: Schedule) -> None:
    assert parse_schedule(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "0 6 * * *",  # cron — 의존성을 들이지 않는다
        "daily@6:00",
        "daily@24:00",
        "daily@06:60",
        "interval@0",
        "interval@-5",
        "hourly",
        "",
    ],
)
def test_everything_else_is_refused(value: str) -> None:
    with pytest.raises(ScheduleError):
        parse_schedule(value)


# ── ② due 판정·미스파이어 ───────────────────────────────────────────────────


def _kst(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST).astimezone(UTC)


def test_a_job_that_never_ran_is_due() -> None:
    assert is_due(parse_schedule("daily@06:00"), last_run_at=None, now=_kst(2026, 8, 12, 3, 0))


def test_daily_waits_for_its_kst_time() -> None:
    """업무 시각은 KST다 — UTC로 판단하면 9시간 어긋난다(§22 렌즈 6)"""
    schedule = parse_schedule("daily@06:00")
    yesterday = _kst(2026, 8, 11, 6, 0)
    assert not is_due(schedule, last_run_at=yesterday, now=_kst(2026, 8, 12, 5, 59))
    assert is_due(schedule, last_run_at=yesterday, now=_kst(2026, 8, 12, 6, 0))


def test_daily_runs_once_after_a_long_outage() -> None:
    """3일 다운 뒤 재기동 — 3회가 아니라 1회다(미스파이어 수렴)"""
    schedule = parse_schedule("daily@06:00")
    long_ago = _kst(2026, 8, 9, 6, 0)
    now = _kst(2026, 8, 12, 9, 0)
    assert is_due(schedule, last_run_at=long_ago, now=now)
    # 방금 돌았으면 오늘 몫은 끝이다.
    assert not is_due(schedule, last_run_at=now, now=now)


def test_interval_counts_from_the_last_run() -> None:
    schedule = parse_schedule("interval@5")
    last = _kst(2026, 8, 12, 9, 0)
    assert not is_due(schedule, last_run_at=last, now=last + timedelta(minutes=4))
    assert is_due(schedule, last_run_at=last, now=last + timedelta(minutes=5))


# ── ③ 등록·매핑 ─────────────────────────────────────────────────────────────


def _jobs() -> list[ScheduledJob]:
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(ScheduledJob).where(ScheduledJob.deleted_at.is_(None))
            ).scalars()
        )
        for row in rows:
            uow.session.expunge(row)
        return rows


def test_registering_is_idempotent() -> None:
    """CLI 등록은 멱등 — 두 번 불러도 행이 늘지 않는다"""
    first = scheduler.register_jobs()
    assert sorted(first) == ["certification-sweep", "outbox-dispatch"]
    assert scheduler.register_jobs() == []
    assert len(_jobs()) == 2


def test_every_registered_code_has_a_mapping() -> None:
    """등록 잡 전건이 코드에 매핑돼 있다 — 등록만 되고 안 도는 잡 0"""
    scheduler.register_jobs()
    for row in _jobs():
        assert row.code in scheduler.JOBS_BY_CODE, row.code


def test_every_registry_schedule_parses() -> None:
    for spec in scheduler.JOB_REGISTRY:
        parse_schedule(spec.schedule)


def test_an_unmapped_code_is_refused_and_recorded() -> None:
    """행만 있고 매핑 없는 잡은 실행 거부 + 실패 기록 — 조용히 무시하지 않는다"""
    create_user("jobs-admin@example.com", roles=(RoleCode.ADMIN,))
    with unit_of_work() as uow:
        uow.session.add(ScheduledJob(code="rogue-job", name_ko="정체 불명", schedule="interval@1"))

    counts = scheduler.run_due_jobs()
    assert counts["failed"] == 1

    row = _jobs()[0]
    assert row.last_status == "FAILED"
    assert row.last_error is not None and "등록되지 않은" in row.last_error


# ── ④ 실행·실패·크래시 정리 ─────────────────────────────────────────────────


def test_a_due_job_runs_and_records_ok() -> None:
    scheduler.register_jobs()
    counts = scheduler.run_due_jobs()
    assert counts["ran"] == 2
    assert {row.last_status for row in _jobs()} == {"OK"}
    assert all(row.last_run_at is not None for row in _jobs())


def test_a_failing_job_alerts_an_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = create_user("failing-admin@example.com", roles=(RoleCode.ADMIN,))
    scheduler.register_jobs()

    def _boom() -> dict[str, int]:
        raise RuntimeError("스윕이 터졌습니다")

    broken = scheduler.JobSpec(
        code="certification-sweep", name_ko="인증 스윕", schedule="daily@06:00", run=_boom
    )
    monkeypatch.setitem(scheduler.JOBS_BY_CODE, "certification-sweep", broken)

    counts = scheduler.run_due_jobs()
    assert counts["failed"] == 1

    failed = next(row for row in _jobs() if row.code == "certification-sweep")
    assert failed.last_status == "FAILED"
    assert failed.last_error is not None and "RuntimeError" in failed.last_error

    with unit_of_work() as uow:
        alerts = uow.session.execute(
            select(Alert).where(
                Alert.recipient_user_id == admin, Alert.entity_type == "scheduled_jobs"
            )
        ).scalars()
        assert [alert.severity for alert in alerts] == ["CRITICAL"]


def test_a_disabled_job_does_not_run() -> None:
    scheduler.register_jobs()
    with unit_of_work() as uow:
        for row in uow.session.execute(select(ScheduledJob)).scalars():
            row.is_enabled = False
    assert scheduler.run_due_jobs()["checked"] == 0


def test_a_crashed_run_is_recovered_on_startup() -> None:
    """worker가 실행 중 죽어 남은 RUNNING을 기동 시 FAILED로 내리고 알린다.

    이것이 없으면 ⓐ 관리 화면이 돌지 않는 잡을 '실행 중'으로 표시하고
    ⓑ FAILED 기록이 없어 §15 "실패 시 관리자 알림"이 크래시 경로에서 미발화한다.
    """
    admin = create_user("crash-admin@example.com", roles=(RoleCode.ADMIN,))
    scheduler.register_jobs()
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(ScheduledJob).where(ScheduledJob.code == "outbox-dispatch")
        ).scalar_one()
        row.last_status = "RUNNING"
        row.last_run_at = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)

    recovered = scheduler.recover_stale_running()
    assert recovered == ["outbox-dispatch"]

    row = next(job for job in _jobs() if job.code == "outbox-dispatch")
    assert row.last_status == "FAILED"
    assert row.last_error is not None and "비정상 종료" in row.last_error

    with unit_of_work() as uow:
        total: int = uow.session.execute(
            select(func.count()).select_from(Alert).where(Alert.recipient_user_id == admin)
        ).scalar_one()
    assert total == 1


# ── ⑤ 중복 기동 방지 ────────────────────────────────────────────────────────


def test_only_one_scheduler_can_hold_the_lock() -> None:
    """두 번째 실행기는 잠금을 못 잡는다 — scale up 해도 하나만 돈다"""
    first = SessionFactory()
    second = SessionFactory()
    try:
        assert scheduler.try_acquire_lock(first) is True
        assert scheduler.try_acquire_lock(second) is False
    finally:
        first.close()
        second.close()
