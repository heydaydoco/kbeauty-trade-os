"""태스크 엔드포인트 (DESIGN.md §18.1 인가·소유권 / §18.4 페이지네이션)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.worklist import service
from app.modules.worklist.schemas import TaskCreateRequest, TaskSummary

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: 조회 역할(VIEWER)은 만들 수 없다 — §2가 조회를 읽기 전용으로 규정한다.
CAN_CREATE = (RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT)


@router.post(
    "",
    summary="태스크 생성",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_CREATE)],
)
def create_task(
    payload: TaskCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> TaskSummary:
    status_code, body = service.create_task(
        actor=current,
        idempotency_key=key,
        payload=payload.model_dump(mode="json"),
    )
    # 재요청이면 최초와 같은 상태코드로 돌려준다(GC-A3 "최초 결과 그대로").
    response.status_code = status_code
    return TaskSummary.model_validate(body)


@router.get("", summary="태스크 목록")
def list_tasks(current: CurrentUser, params: Annotated[PageParams, Depends()]) -> Page[TaskSummary]:
    views, total = service.list_tasks(actor=current, offset=params.offset, limit=params.limit)
    return Page.of([TaskSummary.of(view) for view in views], total, params)


@router.get("/{task_id}", summary="태스크 상세 (담당자·생성자 또는 관리자)")
def get_task(task_id: int, current: CurrentUser) -> TaskSummary:
    return TaskSummary.of(service.get_task(actor=current, task_id=task_id))
