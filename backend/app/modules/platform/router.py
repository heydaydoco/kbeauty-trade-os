"""배치 레지스트리 엔드포인트 (§15 관리 화면 노출 — 관리자 전용, 판정 요청 14)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.platform import service
from app.modules.platform.schemas import ScheduledJobSummary, ScheduledJobToggleRequest

router = APIRouter(prefix="/scheduled-jobs", tags=["scheduled-jobs"])


@router.get("", summary="배치 목록", dependencies=[require_roles(RoleCode.ADMIN)])
def list_scheduled_jobs(params: Annotated[PageParams, Depends()]) -> Page[ScheduledJobSummary]:
    views, total = service.list_jobs(offset=params.offset, limit=params.limit)
    return Page.of([ScheduledJobSummary(**asdict(view)) for view in views], total, params)


@router.patch("/{job_id}", summary="배치 켜기·끄기")
def toggle_scheduled_job(
    job_id: int, payload: ScheduledJobToggleRequest, current: AdminUser
) -> ScheduledJobSummary:
    view = service.set_enabled(actor=current, job_id=job_id, is_enabled=payload.is_enabled)
    return ScheduledJobSummary(**asdict(view))
