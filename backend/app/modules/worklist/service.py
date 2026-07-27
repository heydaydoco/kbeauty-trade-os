"""태스크 (DESIGN.md §3 [공통] / §18.1 소유권 / §17.4 멱등).

소유권 규칙 — 태스크는 **담당자와 만든 사람의 것**이다. 관리자는 전부 본다.
남의 태스크가 남에게 보이지 않아야 §18.1의 IDOR 차단이 화면 하나에만 붙은
장식이 아니게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import ForbiddenError, NotFoundError
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.worklist.models import Task

CREATE_ENDPOINT = "POST /api/v1/tasks"


@dataclass(frozen=True, slots=True)
class TaskView:
    id: int
    title: str
    description: str | None
    status: str
    assignee_id: int | None
    due_date: date | None
    entity_type: str | None
    entity_id: int | None
    created_by_id: int | None


def _view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
        due_date=task.due_date,
        entity_type=task.entity_type,
        entity_id=task.entity_id,
        created_by_id=task.created_by_id,
    )


def _visible_to(query: Select[Any], actor: AuthenticatedUser) -> Select[Any]:
    """관리자가 아니면 자기 것(담당 또는 생성)만 보이게 좁힌다."""
    if RoleCode.ADMIN in actor.roles:
        return query
    return query.where(or_(Task.assignee_id == actor.id, Task.created_by_id == actor.id))


def create_task(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """태스크를 만든다. 같은 키의 재요청은 최초 결과를 그대로 돌려준다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        task = Task(
            title=str(payload["title"]),
            description=payload.get("description"),
            assignee_id=payload.get("assignee_id"),
            due_date=payload.get("due_date"),
            entity_type=payload.get("entity_type"),
            entity_id=payload.get("entity_id"),
            created_by_id=actor.id,
        )
        session.add(task)
        session.flush()

        # 알림 발송은 S2-3의 일이다. 여기서는 "일이 생겼다"만 적는다(§17.1).
        outbox.publish(
            session,
            event_type="worklist.task.created",
            aggregate_type="tasks",
            aggregate_id=task.id,
            payload={"task_id": task.id, "assignee_id": task.assignee_id},
        )

        body = _serialize(_view(task))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_tasks(*, actor: AuthenticatedUser, offset: int, limit: int) -> tuple[list[TaskView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            _visible_to(select(func.count()).select_from(Task), actor).where(
                Task.deleted_at.is_(None)
            )
        ).scalar_one()
        rows = session.execute(
            _visible_to(select(Task), actor)
            .where(Task.deleted_at.is_(None))
            .order_by(Task.id)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(task) for task in rows], total


def get_task(*, actor: AuthenticatedUser, task_id: int) -> TaskView:
    """소유권 검사를 통과한 태스크. 남의 것이면 403(§18.1)."""
    with unit_of_work() as uow:
        task = _load(uow.session, task_id)
        if RoleCode.ADMIN not in actor.roles and actor.id not in {
            task.assignee_id,
            task.created_by_id,
        }:
            raise ForbiddenError(log_context={"task_id": task_id, "actor_id": actor.id})
        return _view(task)


def _load(session: Session, task_id: int) -> Task:
    task = session.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError(log_context={"task_id": task_id})
    return task


def _serialize(view: TaskView) -> dict[str, Any]:
    """멱등 저장용 직렬화 — 재생 시 최초 응답과 **글자 그대로** 같아야 한다."""
    return {
        "id": view.id,
        "title": view.title,
        "description": view.description,
        "status": view.status,
        "assignee_id": view.assignee_id,
        "due_date": view.due_date.isoformat() if view.due_date else None,
        "entity_type": view.entity_type,
        "entity_id": view.entity_id,
    }
