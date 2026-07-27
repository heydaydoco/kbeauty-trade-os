"""태스크·알림 (DESIGN.md §3 [공통] "alert_rules, alerts, tasks" / ADR-07).

★ 담당자 컬럼(`tasks.assignee_id`, `alert_rules`의 수신 대상)이 ADR-0012가 말하는
  **구조**다. 이벤트→수신자 결정→채널 발송이라는 **동작**은 발송 채널이 서는
  S2-3의 일이며, 여기서는 "누구에게 갈지"를 적을 자리만 만든다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.identity.models import RoleCode

#: 태스크 상태 (ADR-11 "상태는 고정, 태스크는 자유" — 상태값 자체는 코드 고정).
TASK_STATUSES = ("OPEN", "IN_PROGRESS", "DONE", "CANCELLED")

#: 알림 심각도.
ALERT_SEVERITIES = ("INFO", "WARN", "CRITICAL")


class Task(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """사람이 해야 할 일 한 건."""

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="OPEN")

    #: 담당자 (ADR-0012 구조분). NULL은 "아직 아무도 안 맡음"이며,
    #: 그 상태 자체가 §5.4의 정체 알림 대상이 된다.
    assignee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: 어느 전표·인증·선적에 붙은 태스크인가(폴리모픽 — §5.4·§7.8이 같은 패턴).
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        value_in("status", TASK_STATUSES),
        Index("ix_tasks_assignee_id", "assignee_id"),
        Index("ix_tasks_status_due_date", "status", "due_date"),
        Index("ix_tasks_entity", "entity_type", "entity_id"),
    )


class AlertRule(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """어떤 사건을 누구에게 알릴 것인가 (규칙은 데이터 — ADR-03·ADR-11)."""

    __tablename__ = "alert_rules"

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, server_default="INFO")

    #: 수신 대상 (ADR-0012 구조분). 두 컬럼 다 NULL이면 "담당자에게"라는 뜻이고,
    #: 그 담당자를 어떻게 찾을지는 S2-3의 라우팅이 정한다.
    #: ★ 전사 폭포(전 사용자 발송)는 ADR-07이 금지한다 — 그래서 "전원" 값이 없다.
    recipient_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    recipient_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        value_in("severity", ALERT_SEVERITIES, name="severity_valid"),
        value_in(
            "recipient_role",
            [role.value for role in RoleCode],
            name="recipient_role_valid",
        ),
        unique_active("alert_rules", "code"),
        Index("ix_alert_rules_event_type", "event_type"),
    )


class Alert(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """실제로 발생한 알림 한 건."""

    __tablename__ = "alerts"

    alert_rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alert_rules.id", ondelete="RESTRICT"), nullable=True
    )
    recipient_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, server_default="INFO")

    #: 중복 발송 방지 키 (ADR-07 "동일 기일 중복 발송 방지 — 발송 이력 키").
    #: 부분 유니크라 §17.4대로 삭제된 알림이 같은 키의 재발생을 영구 차단하지 않는다.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)

    #: 확인(ack) 시각. ADR-07 "확인된 알림 재발송 금지"의 기준.
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        value_in("severity", ALERT_SEVERITIES, name="severity_valid"),
        unique_active("alerts", "dedup_key"),
        Index("ix_alerts_recipient_user_id", "recipient_user_id"),
        Index("ix_alerts_entity", "entity_type", "entity_id"),
    )
