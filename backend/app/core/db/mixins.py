"""전 테이블 공통 컬럼 (DESIGN.md §2 ADR-02: 감사 컬럼 전 테이블·soft delete·version).

여기서 정하는 것들은 첫 테이블이 생기기 전에 고정해야 한다 — S0-2가 공통 테이블
12개를 한 번에 깔기 때문이다.

담당자 컬럼(created_by_id/updated_by_id)은 users 테이블이 생기는 S0-2에서
추가한다. FK 없는 고아 BIGINT를 미리 까는 것은 §22 렌즈 2 위반이다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Integer, func, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class PkMixin:
    """BIGINT GENERATED ALWAYS AS IDENTITY 기본키.

    UUID를 쓰지 않는 이유: §18.1이 모든 엔드포인트에 역할+소유권 검증을 이미
    무조건으로 요구해 "추측 불가 ID"의 이점이 없고, 외부에 노출되는 식별자는
    §17.3의 채번 문서번호(SO-2026-0001)가 담당한다.

    ALWAYS는 id 하드코딩 INSERT를 DB가 거부한다는 뜻이다 — 시드는 자연키 기준
    ON CONFLICT upsert로 넣는다.
    """

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, sort_order=-100
    )


class TimestampMixin:
    """생성·수정 시각. TIMESTAMPTZ 고정 (§2 ADR-02 UTC 저장)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, sort_order=100
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=101,
    )


class SoftDeleteMixin:
    """soft delete (§2 ADR-02).

    멱등 UNIQUE는 반드시 `WHERE deleted_at IS NULL` 부분 인덱스여야 한다
    (§17.4) — 삭제된 행이 같은 키의 재유입을 영구히 막으면 안 되기 때문이다.
    constraints.unique_active()가 그 인덱스를 만든다.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, sort_order=110
    )


class VersionMixin:
    """낙관적 잠금 (§17.2).

    다른 사용자가 먼저 수정한 행을 덮어쓰려 하면 SQLAlchemy가
    StaleDataError를 던지고, API 계층이 409로 바꾼다.
    """

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), sort_order=120
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, object]:
        return {"version_id_col": cls.__dict__["version"]}
