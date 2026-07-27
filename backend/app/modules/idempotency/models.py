"""쓰기 API 멱등 키 (DESIGN.md §17.4 / GC-A3 / ADR-0014).

같은 요청이 두 번 도착해도 업무가 두 번 일어나지 않게 하고, 두 번째 요청에는
**에러가 아니라 최초 결과를 그대로** 돌려준다. 네트워크 재시도와 더블클릭은
사용자 입장에서 실수가 아니라 정상이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin, TimestampMixin


class IdempotencyKey(PkMixin, TimestampMixin, Base):
    """(행위자 × 엔드포인트 × 키) 하나당 한 행 — 스코프 근거는 ADR-0014.

    soft delete를 쓰지 않는다. 이 행은 업무 데이터가 아니라 TTL이 지나면 사라져야
    하는 재시도 흡수 장치다. deleted_at을 두면 "지워졌지만 남아 있는 키"가 생겨
    같은 키의 재유입이 최초 결과를 재생할지 새로 실행할지 모호해진다.
    """

    __tablename__ = "idempotency_keys"

    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: "POST /api/v1/skus"처럼 메서드+경로 템플릿. 같은 키로 다른 업무를 부른
    #: 요청이 엉뚱한 결과를 재생하지 않게 스코프에 포함한다.
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: 요청 본문의 sha256. 같은 키로 **다른 내용**이 오면 409로 거절한다 —
    #: 조용히 최초 결과로 흡수하면 사용자는 바뀐 값이 저장됐다고 믿는다.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 완료 전에는 NULL. 동시 요청은 이 행의 잠금에서 대기하다가 커밋 후 읽는다.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: 보존 기한(ADR-0014 — 24시간). 지난 행은 배치가 지운다.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_keys_actor_endpoint_key",
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )
