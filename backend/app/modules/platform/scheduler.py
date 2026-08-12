"""배치 실행기 (DESIGN.md §15 스케줄 레지스트리 / §17.6 / 부채 #12 소비).

S0-2부터 scheduled_jobs는 빈 테이블이었다 — 목록은 있고 돌리는 주체가 없었다.
이 파일이 그 주체다.

★ **잡 코드 → 함수 매핑은 코드 고정 폐쇄 열거다**(ADR-11 "핵심 골격은 코드
  고정"). 행을 넣는 것만으로 새 동작이 생기면, scheduled_jobs에 한 줄 적는
  행위가 곧 코드 배포가 된다 — 자동화 4금(§15 L3)을 데이터로 우회하는 통로가
  열린다는 뜻이다. 등록되지 않은 코드는 실행 거부 + 실패 기록이다.

★ **schedule 문법은 폐쇄 2형이다**(판정 요청 13·조건 F):
      `daily@HH:MM`   — KST 기준 매일 1회
      `interval@N`    — N분 주기(N≥1 정수)
  cron 표현식·파서 의존성을 들이지 않는다. §1 규모(잡 몇 개)에 cron 파서는
  과설계이고, 표현력이 늘면 "그 표현이 정확히 언제 도는가"를 사람이 검산할 수
  없게 된다. 문법이 좁으면 오해가 없다.

★ **다운 중 지나간 스케줄은 재기동 시 딱 1회 실행한다**(미스파이어 수렴).
  지난 횟수만큼 몰아 실행하면 하루 다운 뒤 24회가 한꺼번에 돈다.

★ **비정상 종료로 남은 RUNNING은 기동 시 정리한다.** worker가 잡 실행 중
  죽으면 last_status='RUNNING'이 영원히 남아 ⓐ 관리 화면이 돌지 않는 잡을
  실행 중으로 표시하고 ⓑ FAILED 기록이 없어 §15 "실패 시 관리자 알림"이
  크래시 경로에서 미발화한다. 기동 시 FAILED로 내리고 관리자에게 알린다.

★ **중복 실행 방지는 잡별 트랜잭션 advisory lock이다**(`pg_try_advisory_xact_lock`).
  프로세스 단위 세션 잠금을 쓰지 않는 이유가 실측으로 드러났다 — 앱 계정에
  `idle_in_transaction_session_timeout = '60s'`가 걸려 있어(infra/postgres/init/
  00-roles.sql), 잠금만 잡고 노는 세션은 60초 뒤 PG가 끊는다. **세션 수명에 묶인
  잠금은 그때 함께 풀리는데 프로세스는 아무 오류 없이 계속 돈다** — 기동 60초
  후부터 중복 기동 방지가 존재하지 않는 상태가 된다(자기 적대 검증 확정 발견).
  잡별 트랜잭션 잠금은 클레임 트랜잭션과 수명이 같아 이 창이 없고, 두 worker가
  동시에 떠도 같은 잡을 동시에 집지 못한다.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.logging.redaction import scrub_text
from app.core.time import KST, utcnow
from app.modules.certifications import service as certifications
from app.modules.notifications import dispatcher
from app.modules.notifications import service as notifications
from app.modules.platform.models import ScheduledJob

#: 동시 기동 방지용 advisory lock 키 (임의 상수 — 이 앱의 실행기 전용).
SCHEDULER_LOCK_KEY = 8_231_057

#: worker 루프의 잠 자는 간격(초).
TICK_SECONDS = 20

_DAILY = re.compile(r"^daily@(\d{2}):(\d{2})$")
_INTERVAL = re.compile(r"^interval@(\d+)$")


class ScheduleError(ValueError):
    """schedule 문자열이 폐쇄 2형 밖이다."""


@dataclass(frozen=True, slots=True)
class Schedule:
    kind: str  # "daily" | "interval"
    #: daily — KST 시각. interval — 주기(분).
    hour: int = 0
    minute: int = 0
    minutes: int = 0


def parse_schedule(value: str) -> Schedule:
    daily = _DAILY.match(value)
    if daily is not None:
        hour, minute = int(daily.group(1)), int(daily.group(2))
        if hour > 23 or minute > 59:
            raise ScheduleError(f"시각이 범위를 벗어났습니다: {value!r}")
        return Schedule("daily", hour=hour, minute=minute)

    interval = _INTERVAL.match(value)
    if interval is not None:
        minutes = int(interval.group(1))
        if minutes < 1:
            raise ScheduleError(f"주기는 1분 이상이어야 합니다: {value!r}")
        return Schedule("interval", minutes=minutes)

    raise ScheduleError(
        f"알 수 없는 스케줄 형식입니다: {value!r}. 'daily@HH:MM' 또는 'interval@N'만 씁니다."
    )


def is_due(schedule: Schedule, *, last_run_at: datetime | None, now: datetime) -> bool:
    """지금 돌 차례인가.

    daily는 **KST 기준**이다(§22 렌즈 6 — 업무 시각은 KST로 읽는다). 마지막
    실행이 없으면 첫 기동에서 곧바로 한 번 돈다.
    """
    if last_run_at is None:
        return True
    if schedule.kind == "interval":
        return now - last_run_at >= timedelta(minutes=schedule.minutes)

    local_now = now.astimezone(KST)
    scheduled_today = local_now.replace(
        hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
    )
    if local_now < scheduled_today:
        return False
    # 오늘 예정 시각이 지났고, 그 시각 이후로 아직 안 돌았으면 지금 1회 돈다
    # (다운 중 밀린 회차를 몰아 실행하지 않는다 — 미스파이어 수렴).
    return last_run_at.astimezone(KST) < scheduled_today


# ── 잡 레지스트리 (코드 고정 폐쇄 열거) ──────────────────────────────────────


def _run_certification_sweep() -> dict[str, int]:
    return certifications.sweep_date_transitions()


def _run_outbox_dispatch() -> dict[str, int]:
    return dispatcher.dispatch_pending()


@dataclass(frozen=True, slots=True)
class JobSpec:
    code: str
    name_ko: str
    schedule: str
    run: Callable[[], dict[str, int]]


#: 이 튜플이 "존재하는 배치"의 전부다. 여기 없는 code는 실행되지 않는다.
JOB_REGISTRY: tuple[JobSpec, ...] = (
    JobSpec(
        code="certification-sweep",
        name_ko="인증 만료임박·만료 날짜 스윕",
        # 업무 시작 전에 그날의 상태를 맞춰 둔다(ADR-0039 스윕 함수 재사용).
        schedule="daily@06:00",
        run=_run_certification_sweep,
    ),
    JobSpec(
        code="outbox-dispatch",
        name_ko="아웃박스 알림 디스패치",
        schedule="interval@1",
        run=_run_outbox_dispatch,
    ),
)

JOBS_BY_CODE: dict[str, JobSpec] = {spec.code: spec for spec in JOB_REGISTRY}


def register_jobs() -> list[str]:
    """레지스트리의 잡 중 아직 등록되지 않은 것을 scheduled_jobs에 넣는다.

    ★ **마이그레이션 시드가 아니라 앱 경로다**(함정 ⑩ — scheduled_jobs는
      ActorMixin의 users FK를 달고 있어 시드로 넣으면 테스트 정리의 TRUNCATE
      CASCADE가 PRESERVED_TABLES를 무력화한다).
    ★ 멱등이다 — 이미 있는 code는 건드리지 않는다(스케줄을 손으로 바꿔 둔 것을
      덮어쓰지 않는다).

    Returns:
        이번에 새로 등록된 code 목록.
    """
    created: list[str] = []
    with unit_of_work() as uow:
        session = uow.session
        existing = set(
            session.execute(
                select(ScheduledJob.code).where(ScheduledJob.deleted_at.is_(None))
            ).scalars()
        )
        for spec in JOB_REGISTRY:
            if spec.code in existing:
                continue
            parse_schedule(spec.schedule)  # 등록 전에 문법을 검증한다
            session.add(ScheduledJob(code=spec.code, name_ko=spec.name_ko, schedule=spec.schedule))
            created.append(spec.code)
    return created


# ── 실행 ────────────────────────────────────────────────────────────────────


def recover_stale_running() -> list[str]:
    """비정상 종료로 남은 RUNNING을 FAILED로 내리고 관리자에게 알린다.

    Returns:
        정리된 잡 code 목록.
    """
    recovered: list[str] = []
    with unit_of_work() as uow:
        session = uow.session
        rows = session.execute(
            select(ScheduledJob).where(
                ScheduledJob.last_status == "RUNNING", ScheduledJob.deleted_at.is_(None)
            )
        ).scalars()
        for row in rows:
            row.last_status = "FAILED"
            row.last_error = "비정상 종료 — 실행 중 프로세스가 내려갔습니다(기동 시 정리)."
            recovered.append(row.code)
            notifications.notify(
                session,
                subject_key=f"jobs.crashed:{row.code}:{row.last_run_at:%Y%m%dT%H%M%S}"
                if row.last_run_at
                else f"jobs.crashed:{row.code}:unknown",
                title=f"배치가 비정상 종료됐습니다 — {row.name_ko}",
                body=(
                    f"'{row.code}' 실행 중 프로세스가 내려가 상태가 RUNNING으로 남아 "
                    "있었습니다. 기동 시 FAILED로 정리했으니 원인을 확인해 주세요."
                ),
                severity="CRITICAL",
                routing=notifications.Routing.ADMIN,
                entity_type="scheduled_jobs",
                entity_id=row.id,
            )
    return recovered


def run_due_jobs(*, now: datetime | None = None) -> dict[str, int]:
    """지금 돌 차례인 잡을 전부 실행한다.

    Returns:
        {"checked", "ran", "failed", "skipped"} 집계.
    """
    moment = now or utcnow()
    counts = {"checked": 0, "ran": 0, "failed": 0, "skipped": 0}

    for job_id in _due_job_ids(now=moment):
        counts["checked"] += 1
        outcome = _run_one(job_id, now=moment)
        counts[outcome] += 1
    return counts


def _due_job_ids(*, now: datetime) -> list[int]:
    with unit_of_work() as uow:
        rows = uow.session.execute(
            select(ScheduledJob)
            .where(ScheduledJob.is_enabled.is_(True), ScheduledJob.deleted_at.is_(None))
            .order_by(ScheduledJob.id)
        ).scalars()
        due: list[int] = []
        for row in rows:
            defect = _defect_of(row)
            if defect is not None:
                # 매핑 없는 코드·문법 밖 schedule은 **실행 대상이 아니다**.
                # 이미 그 사유로 FAILED가 찍혀 있으면 다시 집지 않는다 — 안 그러면
                # 매 틱 실패를 덮어쓰며 관리자 알림을 분당 1건씩 무한 생성한다
                # (자기 적대 검증 확정 발견).
                if row.last_status != "FAILED":
                    due.append(row.id)
                continue
            if is_due(parse_schedule(row.schedule), last_run_at=row.last_run_at, now=now):
                due.append(row.id)
        return due


def _defect_of(row: ScheduledJob) -> str | None:
    """실행할 수 없는 행인가 — 사유 문자열, 정상이면 None.

    ★ 등록 행은 사람이 손댈 수 있고(코드 오타·주기 오기), 그 행을 **실행하지
      않는 것**이 계약이다(ADR-0044 ①). 판정 없이 실행하면 오타 난 주기가
      is_due를 건너뛰어 매 틱 도는 정반대 결과가 된다.
    """
    if row.code not in JOBS_BY_CODE:
        return f"등록되지 않은 잡 코드입니다: {row.code!r}"
    try:
        parse_schedule(row.schedule)
    except ScheduleError as error:
        return str(error)
    return None


def _run_one(job_id: int, *, now: datetime) -> str:
    """잡 하나를 실행한다 — 실행 자체는 트랜잭션 밖이다.

    ★ 잡 본체(스윕·디스패치)는 **자기 트랜잭션을 스스로 연다**(건별 독립 —
      §17.6). 실행기가 감싸면 그 안에서 잡의 커밋이 합류해 버려, 한 건의 실패가
      그날 처리분 전체를 되돌린다.

    ★ 클레임 트랜잭션이 **잡별 트랜잭션 advisory lock**을 잡는다 — 두 worker가
      동시에 떠도 같은 잡을 동시에 집지 못한다. 잠금 수명이 이 트랜잭션이라
      세션 타임아웃(60초)에 조용히 풀리는 창이 없다.
    """
    with unit_of_work() as uow:
        session = uow.session
        if not _try_lock_job(session, job_id):
            return "skipped"  # 다른 실행기가 이 잡을 집었다 — 경합의 정상 결과
        row = session.get(ScheduledJob, job_id)
        if row is None or not row.is_enabled or row.deleted_at is not None:
            return "skipped"
        defect = _defect_of(row)
        code, name_ko = row.code, row.name_ko
        if defect is None:
            # ★ 잠근 뒤 **다시 판정한다**(§17.2 "확인→기록"). 잠금 수명은 이
            #   트랜잭션이라 커밋과 함께 풀리고, 잡 본체는 그 밖에서 돈다 —
            #   재판정이 없으면 후보 목록을 먼저 뜬 다른 실행기가 방금 실행된
            #   잡을 한 번 더 집는다(실측: 동시 실행 테스트가 잡아냈다).
            #   last_run_at을 여기서 찍으므로 뒤이은 재판정은 자연히 False다.
            if not is_due(parse_schedule(row.schedule), last_run_at=row.last_run_at, now=now):
                return "skipped"
            row.last_run_at = now
            row.last_status = "RUNNING"
            row.last_error = None

    if defect is not None:
        _finish(job_id, status="FAILED", error=defect)
        _alert_failure(job_id, code=code, name_ko=name_ko, now=now)
        return "failed"

    try:
        JOBS_BY_CODE[code].run()
    except Exception as error:
        # 원본 예외 문자열에는 DB가 만든 행 값이 실릴 수 있다(함정 ④) — 관리
        # 화면으로 되돌아가는 필드이므로 마스킹 프로세서를 통과시킨다.
        _finish(
            job_id, status="FAILED", error=scrub_text(f"{type(error).__name__}: {error}")[:2000]
        )
        _alert_failure(job_id, code=code, name_ko=name_ko, now=now)
        return "failed"

    _finish(job_id, status="OK", error=None)
    return "ran"


def _try_lock_job(session: Session, job_id: int) -> bool:
    """이 트랜잭션이 그 잡을 집을 수 있는가 (트랜잭션 수명 advisory lock)."""
    acquired: bool = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :id)"),
        {"ns": SCHEDULER_LOCK_KEY, "id": job_id},
    ).scalar_one()
    return acquired


def _finish(job_id: int, *, status: str, error: str | None) -> None:
    with unit_of_work() as uow:
        row = uow.session.get(ScheduledJob, job_id)
        if row is None:
            return
        row.last_status = status
        row.last_error = error


def _alert_failure(job_id: int, *, code: str, name_ko: str, now: datetime) -> None:
    """§15 "실패 시 관리자 알림". 실패 기록과 별도 트랜잭션이라 함께 사라지지 않는다."""
    with unit_of_work() as uow:
        notifications.notify(
            uow.session,
            # 키는 **날짜** 단위다. 분 단위로 두면 1분 주기 잡이 지속 실패할 때
            # 관리자 알림함에 하루 1,440건이 쌓인다 — 알림이 많으면 안 읽히고,
            # 안 읽히는 알림은 없는 알림이다(자기 적대 검증 확정 발견).
            subject_key=f"jobs.failed:{code}:{now.astimezone(KST):%Y%m%d}",
            title=f"배치 실행이 실패했습니다 — {name_ko}",
            body=f"'{code}' 실행이 실패했습니다. 관리 화면에서 오류 내용을 확인해 주세요.",
            severity="CRITICAL",
            routing=notifications.Routing.ADMIN,
            entity_type="scheduled_jobs",
            entity_id=job_id,
        )


def run_forever(*, tick_seconds: int = TICK_SECONDS) -> int:  # pragma: no cover — 무한 루프
    """worker 컨테이너의 진입점.

    ★ 프로세스 단위 잠금을 잡지 않는다. 두 worker가 동시에 떠도 **잡별 트랜잭션
      잠금**이 같은 잡의 동시 실행을 막고(_run_one), 못 집은 쪽은 그 틱을 건너뛴다.
      프로세스를 조기 종료시키던 종전 방식은 `restart: unless-stopped`와 만나
      무한 재기동이 되기도 했다 — 지금은 두 번째 프로세스도 정상 대기 상태다.
    """
    recovered = recover_stale_running()
    if recovered:
        print(f"비정상 종료 정리: {', '.join(recovered)}")
    print(f"실행기 기동 — 등록 잡 {len(JOB_REGISTRY)}종, {tick_seconds}초 간격 확인.")

    while True:
        counts = run_due_jobs()
        if counts["ran"] or counts["failed"]:
            print(f"실행 {counts['ran']}건·실패 {counts['failed']}건")
        time.sleep(tick_seconds)
