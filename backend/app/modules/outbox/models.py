"""아웃박스 (DESIGN.md §17.1 / §16 "도메인 이벤트 발행 → 커넥터").

★ 트랜잭션 안에서 외부(메일·슬랙·AI·웹훅)를 부르면, 롤백된 일을 대외에 통보하게
  된다. 그래서 발송할 일도 **업무와 같은 트랜잭션으로 이 테이블에 적기만** 하고,
  커밋된 뒤 디스패처가 읽어서 보낸다. "보낼 일"과 "일어난 일"이 같이 커밋되거나
  같이 사라진다.

디스패처와 채널 어댑터는 S2-3(알림 엔진)이다. S0-2는 기록까지다 —
그래야 §20 J의 "아웃박스: 커밋 실패 시 발송 0"을 지금 검증할 수 있다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin, TimestampMixin


class Event(PkMixin, TimestampMixin, Base):
    """발행 대기 중이거나 발행된 도메인 이벤트."""

    __tablename__ = "events"

    #: "identity.role.granted"처럼 <도메인>.<대상>.<사건>. audit_log의 action과
    #: 형식을 맞춰 둔다 — 같은 사건을 두 곳에서 다른 이름으로 부르지 않게.
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    aggregate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    #: NULL이면 아직 안 보냈다. 디스패처(S2-3)가 이 컬럼으로 대기열을 만든다.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 다음 재시도 시각 (S2-3 판정 요청 12 — 지수 백오프의 저장형).
    #: NULL이면 "지금 바로 대상"이다. 실패한 건에만 값이 찍히므로, 대기열
    #: 조회가 실패 건을 매번 다시 집어 드는 스핀을 막는다. 산식 계산
    #: (updated_at+attempts)으로 두지 않은 이유는 그 식이 인덱스를 못 타서
    #: 대기열 조회가 실패 건 수에 비례해 느려지기 때문이다.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 대기열 조회는 "아직 안 보낸 것"만 훑는다 — 부분 인덱스로 발송 완료분을
        # 인덱스에서 빼면 시간이 지나도 대기열 조회 비용이 늘지 않는다.
        # ★ 선두 컬럼이 next_retry_at인 이유: 디스패처의 클레임 질의가
        #   "재시도 시각이 지났거나 없는 것"을 시각 순으로 집기 때문이다.
        #   NULLS FIRST가 PG 오름차순의 기본이 아니므로 질의에서 명시한다.
        Index(
            "ix_events_pending",
            "next_retry_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index("ix_events_aggregate", "aggregate_type", "aggregate_id"),
    )
