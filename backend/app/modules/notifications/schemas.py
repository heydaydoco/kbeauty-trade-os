"""알림센터·알림 규칙 요청·응답 (S2-3 PR-1).

쓰기 스키마는 전부 `extra="forbid"`다 — 모르는 필드는 조용한 무시가 아니라
422다(판정 요청 18에서 인증 태스크까지 전면화한 규율을 신규 모듈이 처음부터
따른다).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.notifications.service import AlertRuleView, AlertView

Severity = Literal["INFO", "WARN", "CRITICAL"]
RoleName = Literal["ADMIN", "TRADE", "LOGISTICS", "CERT", "VIEWER"]


class AlertRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=60)
    name_ko: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=60)
    severity: Severity = "INFO"
    #: 수신자 2필드를 다 비우면 "담당자에게"라는 뜻이다(모델 문면). 기일 계열은
    #: 담당자도 없을 때 관리자 폴백으로 간다(판정 요청 7).
    recipient_user_id: int | None = Field(default=None, ge=1)
    recipient_role: RoleName | None = None


class AlertRuleUpdateRequest(BaseModel):
    """exclude_unset 의미론 — 안 보낸 필드는 안 바뀐다."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name_ko: str | None = Field(default=None, min_length=1, max_length=100)
    event_type: str | None = Field(default=None, min_length=1, max_length=60)
    severity: Severity | None = None
    is_enabled: bool | None = None
    #: 3값 의미론: 생략=미변경 / null=해제(담당자 경로 복귀) / 값=지정.
    recipient_user_id: int | None = Field(default=None, ge=1)
    recipient_role: RoleName | None = None


class AlertRuleSummary(BaseModel):
    id: int
    code: str
    name_ko: str
    event_type: str
    severity: str
    recipient_user_id: int | None
    recipient_role: str | None
    is_enabled: bool
    version: int

    @classmethod
    def of(cls, view: AlertRuleView) -> AlertRuleSummary:
        return cls(**asdict(view))


class AlertSummary(BaseModel):
    id: int
    alert_rule_id: int | None
    recipient_user_id: int
    title: str
    body: str | None
    severity: str
    dedup_key: str
    acknowledged_at: str | None
    entity_type: str | None
    entity_id: int | None

    @classmethod
    def of(cls, view: AlertView) -> AlertSummary:
        return cls(**asdict(view))


class UnreadCount(BaseModel):
    count: int
