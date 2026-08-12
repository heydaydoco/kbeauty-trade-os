"""배치 레지스트리 요청·응답 (S2-3 PR-1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ScheduledJobToggleRequest(BaseModel):
    """켜기·끄기만 받는다 — 스케줄 문자열은 코드(레지스트리)와 함께 움직인다.

    version이 없는 이유는 service.set_enabled의 독스트링에 있다(시스템이 매
    실행마다 같은 행을 갱신해 낙관 잠금이 성립하지 않는다).
    """

    model_config = ConfigDict(extra="forbid")

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
