"""태스크 요청·응답 (DESIGN.md §3 [공통] tasks)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.modules.worklist.models import TASK_STATUSES
from app.modules.worklist.service import TaskView


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assignee_id: int | None = None
    due_date: date | None = None
    entity_type: str | None = Field(default=None, max_length=60)
    entity_id: int | None = None


class TaskSummary(BaseModel):
    id: int
    title: str
    description: str | None
    status: str
    assignee_id: int | None
    due_date: date | None
    entity_type: str | None
    entity_id: int | None

    @classmethod
    def of(cls, view: TaskView) -> TaskSummary:
        return cls(
            id=view.id,
            title=view.title,
            description=view.description,
            status=view.status,
            assignee_id=view.assignee_id,
            due_date=view.due_date,
            entity_type=view.entity_type,
            entity_id=view.entity_id,
        )


__all__ = ["TASK_STATUSES", "TaskCreateRequest", "TaskSummary"]
