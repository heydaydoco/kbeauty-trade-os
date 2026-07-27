"""전 테이블 공통 컬럼 (DESIGN.md §2 ADR-02: 감사 컬럼 전 테이블·soft delete·version).

여기서 정하는 것들은 첫 테이블이 생기기 전에 고정해야 한다 — S0-2가 공통 테이블
12개를 한 번에 깔기 때문이다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, func, text
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


class ActorMixin:
    """누가 만들고 누가 고쳤는가 (§2 ADR-02 "감사 컬럼 전 테이블").

    ★ NULL을 허용한다. 마이그레이션 시드·배치·아웃박스 디스패처가 만드는 행에는
      사람 행위자가 없다. NOT NULL로 두면 그런 행마다 "시스템" 더미 계정을 쓰게
      되고, 그 계정은 곧 audit_log에서 진짜 사람과 구분되지 않는 잡음이 된다.
      "누가 했는지 모른다"와 "시스템이 했다"는 다른 사실이고, 감사에서는 그
      차이가 전부다.

    ★ audit_log는 이 믹스인을 쓰지 않는다. 불변 테이블이라 updated_by가 존재할
      수 없고(§17.5), 행위자는 actor_user_id 한 컬럼으로 명시한다.

    declared_attr을 쓰는 이유: ForeignKey 객체는 여러 매핑 클래스가 공유할 수
    없다. 평범한 클래스 속성으로 두면 두 번째 모델에서 SQLAlchemy가 터진다.
    """

    @declared_attr
    def created_by_id(cls) -> Mapped[int | None]:
        return mapped_column(
            BigInteger,
            # 사용자는 soft delete만 하므로(§2 ADR-02) 이 경로는 실제로 발동하지
            # 않는다. RESTRICT는 "실수로 hard delete하면 막힌다"는 안전망이다.
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
            sort_order=130,
        )

    @declared_attr
    def updated_by_id(cls) -> Mapped[int | None]:
        return mapped_column(
            BigInteger,
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
            sort_order=131,
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
