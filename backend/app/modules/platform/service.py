"""배치 레지스트리 조회·토글 (§15 "모든 배치를 scheduled_jobs로 관리 화면 노출").

★ 화면에서 할 수 있는 것은 **목록 열람과 켜기·끄기뿐**이다(판정 요청 14).
  스케줄 문자열 편집을 화면에 열지 않는 이유: 문법이 폐쇄 2형이라 잘못 적으면
  그 잡이 조용히 안 돌고, 그 침묵은 다음 도과 때까지 드러나지 않는다. 스케줄
  변경은 코드(레지스트리)와 함께 움직인다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import NotFoundError, VersionConflictError
from app.modules.identity.service import AuthenticatedUser
from app.modules.platform.models import ScheduledJob
from app.modules.platform.scheduler import JOBS_BY_CODE


@dataclass(frozen=True, slots=True)
class ScheduledJobView:
    id: int
    code: str
    name_ko: str
    schedule: str
    is_enabled: bool
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    #: 코드에 실행 함수가 매핑돼 있는가 — 없으면 등록만 되고 돌지 않는다.
    is_mapped: bool
    version: int


def _view(row: ScheduledJob) -> ScheduledJobView:
    return ScheduledJobView(
        id=row.id,
        code=row.code,
        name_ko=row.name_ko,
        schedule=row.schedule,
        is_enabled=row.is_enabled,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        last_status=row.last_status,
        last_error=row.last_error,
        is_mapped=row.code in JOBS_BY_CODE,
        version=row.version,
    )


def list_jobs(*, offset: int, limit: int) -> tuple[list[ScheduledJobView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total: int = session.execute(
            select(func.count()).select_from(ScheduledJob).where(ScheduledJob.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(ScheduledJob)
            .where(ScheduledJob.deleted_at.is_(None))
            .order_by(ScheduledJob.id)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(row) for row in rows], total


def set_enabled(
    *, actor: AuthenticatedUser, job_id: int, version: int, is_enabled: bool
) -> ScheduledJobView:
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.deleted_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"scheduled_job_id": job_id})
        if row.version != version:
            raise VersionConflictError(
                log_context={"scheduled_job_id": job_id, "expected": version, "actual": row.version}
            )
        row.is_enabled = is_enabled
        row.updated_by_id = actor.id
        session.flush()
        return _view(row)
