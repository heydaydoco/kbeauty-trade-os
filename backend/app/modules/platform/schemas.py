"""배치 레지스트리 요청·응답 (S2-3 PR-1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScheduledJobToggleRequest(BaseModel):
    """켜기·끄기만 받는다 — 스케줄 문자열은 코드(레지스트리)와 함께 움직인다."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    is_enabled: bool


class ScheduledJobSummary(BaseModel):
    id: int
    code: str
    name_ko: str
    schedule: str
    is_enabled: bool
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    is_mapped: bool
    version: int
