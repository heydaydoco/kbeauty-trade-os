"""플랫폼 공통 — 배치 레지스트리·기능 플래그·외부참조·커스텀 필드.

DESIGN.md §3 [공통] / §15 "스케줄 레지스트리" / ADR-10 외부참조 / ADR-11 커스텀 필드.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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

#: 배치 마지막 실행 결과.
JOB_STATUSES = ("RUNNING", "OK", "FAILED")

#: 커스텀 필드 값 종류 (ADR-11 "임시 수납장" — 검색·집계가 필요해지면 정식 컬럼으로 승격).
CUSTOM_FIELD_TYPES = ("TEXT", "NUMBER", "DATE", "BOOLEAN", "SELECT")


class ScheduledJob(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """배치 레지스트리 (§15 "모든 배치를 scheduled_jobs로 관리 화면 노출").

    ★ 등록되지 않은 배치는 존재하지 않는 배치다. 코드 어딘가에서 조용히 도는
      스케줄은 실패해도 아무도 모르고, 실패를 모르면 §15의 "실패 시 관리자 알림"이
      성립하지 않는다. 실행기 자체는 S2-3이고, 여기서는 목록과 상태 자리를 만든다.
    """

    __tablename__ = "scheduled_jobs"

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    #: cron 표현 또는 사람이 읽는 주기 설명. 실행기 도입 시 형식을 고정한다.
    schedule: Mapped[str] = mapped_column(String(60), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("last_status", JOB_STATUSES, name="last_status_valid"),
        unique_active("scheduled_jobs", "code"),
    )


class FeatureFlag(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """기능 플래그 (§20 H "기능 플래그 오프 완전 비활성")."""

    __tablename__ = "feature_flags"

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (unique_active("feature_flags", "code"),)


class ExternalRef(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """내부 ID ↔ 외부 시스템 코드 (ADR-10 — Phase 0부터 깐다).

    §17.4의 멱등 키 목록에 있는 "(시스템, 외부코드)"가 여기 부분 유니크다.
    같은 외부 코드가 두 번 들어와도 내부 엔터티가 둘로 갈라지지 않는다.
    """

    __tablename__ = "external_refs"

    #: "ECOUNT" / "SAP" / "AMAZON" 등.
    system: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_code: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        unique_active("external_refs", "system", "entity_type", "external_code"),
        Index("ix_external_refs_entity", "entity_type", "entity_id"),
    )


class CustomFieldDef(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """커스텀 필드 정의 (ADR-11 "임시 수납장")."""

    __tablename__ = "custom_field_defs"

    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label_ko: Mapped[str] = mapped_column(String(60), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: SELECT 타입의 선택지 등.
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        value_in("data_type", CUSTOM_FIELD_TYPES, name="data_type_valid"),
        unique_active("custom_field_defs", "entity_type", "code"),
    )


class CustomFieldValue(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """커스텀 필드 값.

    값을 전부 텍스트로 담는다. 형 변환·검증은 정의(data_type)가 맡는다 —
    ADR-11대로 검색·집계·검증이 필요해진 필드는 정식 컬럼으로 승격하는 것이
    정답이고, 그전까지의 임시 저장에 타입별 컬럼을 5개 파 두면 승격할 이유가
    사라져 영구 임시가 된다.
    """

    __tablename__ = "custom_field_values"

    definition_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("custom_field_defs.id", ondelete="RESTRICT"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (unique_active("custom_field_values", "definition_id", "entity_id"),)
