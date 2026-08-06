"""엑셀 왕복 임포트 스테이징 (DESIGN.md §12.2 / §2 ADR-09 / §3 [M9] import_staging).

■ 모든 임포트는 스테이징을 거친다 (ADR-09)

  파일 → 파싱 → **import_staging(+행)** → 사람 검토 → 확정. DB 직행은 없다.
  행 테이블은 "변경분 diff만"을 담는다(§12.2) — 파일과 같은 행(무변경)은
  세지기만 하고 저장하지 않고, 파일에 없는 행은 무시한다(삭제가 아니다).

■ 스테이징 행은 등록 후 불변이다 — 확정은 이 행들을 그대로 반영한다

  검토한 내용과 반영되는 내용이 다르면 검토가 무의미해진다. 행에는
  VersionMixin이 없다(수정 경로 자체가 없다 — UserRole 등 이력성 테이블과
  같은 꼴). 헤더는 상태 전이(PENDING→CONFIRMED)가 있어 표준 5종을 다 단다.

■ 같은 파일은 검토 대기 중 한 번만 (ADR-09 "파일 해시 멱등")

  (레지스트리, sha256) 부분 유니크를 PENDING·미삭제 행으로 좁힌다 — 확정이
  끝난 파일을 다시 올리는 것은 막지 않는다(그때는 diff가 0건이 되어
  "동일 임포트 재실행 변화 0"이 자연스럽게 성립한다 — §20 J).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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

#: 임포트 레지스트리 코드 — 실체는 modules/imports/registry.py의 어댑터다.
#: 어댑터는 대상 모델에 결합된 코드라 데이터로 옮길 수 없다(ADR-11의
#: "검증 골격은 코드 고정" 쪽). 대상이 늘면 여기와 registry.py가 같이 는다.
IMPORT_REGISTRY_CODES = ("materials", "partners")

#: 스테이징 상태 — 검토 대기 / 확정 완료. 취소는 soft delete다(별도 상태 없음).
IMPORT_STAGING_STATUSES = ("PENDING", "CONFIRMED")

#: 행 분류 (§12.2 diff): 빈 ID=신규 / ID 있는 행=변경분만 / 훼손·검증 실패=오류.
IMPORT_ROW_KINDS = ("NEW", "CHANGED", "ERROR")


class ImportStaging(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """임포트 스테이징 헤더 — 업로드 파일 1건 = 1행 (§3 [M9] import_staging)."""

    __tablename__ = "import_staging"

    registry_code: Mapped[str] = mapped_column(String(20), nullable=False)
    #: 사용자 파일명은 표시 전용이다(ADR-0029와 같은 규율 — 경로에 쓰지 않는다).
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 업로드 원문 바이트의 해시 — PENDING 중복 차단의 멱등 키(ADR-09).
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'PENDING'")
    )
    #: 파일의 데이터 행 수(빈 행 제외). 행 테이블에는 변경분·오류만 남으므로
    #: "몇 행 중 몇 행이 그대로였나"는 여기서만 알 수 있다(파일은 보관하지 않는다).
    total_data_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        value_in("registry_code", IMPORT_REGISTRY_CODES),
        value_in("status", IMPORT_STAGING_STATUSES),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        CheckConstraint("total_data_rows >= 0", name="total_data_rows_nonnegative"),
        CheckConstraint("unchanged_rows >= 0", name="unchanged_rows_nonnegative"),
        CheckConstraint("unchanged_rows <= total_data_rows", name="unchanged_within_total"),
        # 확정 상태와 확정 흔적은 한 쌍이다 — 한쪽만 남은 행이 생기지 않는다.
        CheckConstraint(
            "(status = 'CONFIRMED') = (confirmed_at IS NOT NULL)", name="confirmed_at_pair"
        ),
        CheckConstraint(
            "(status = 'CONFIRMED') = (confirmed_by_id IS NOT NULL)", name="confirmed_by_pair"
        ),
        # 같은 파일은 검토 대기 중 한 번만 — 확정·삭제된 건은 다시 올릴 수 있다.
        Index(
            "uq_import_staging_registry_code_sha256_pending",
            "registry_code",
            "sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status = 'PENDING'"),
        ),
    )


class ImportStagingRow(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """스테이징 행 — 신규/변경/오류만 담는다(무변경 행은 저장하지 않는다 — §12.2)."""

    __tablename__ = "import_staging_rows"

    staging_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_staging.id", ondelete="RESTRICT"), nullable=False
    )
    #: 파일(엑셀)에서 사람이 보는 행 번호 — 헤더가 1행이라 데이터는 2부터.
    #: 오류 행 리포트(§12.2 "행번호+사유")가 이 번호로 왕복한다.
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 변경 대상 행의 PK. 레지스트리별 대상 테이블이 달라 FK는 걸지 않는다
    #: (documents.owner_id와 같은 폴리모픽 관례).
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: diff 시점의 대상 version — 확정이 이 값과 현재 version을 대조한다(§17.2).
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 파싱된 정규형 값(내부 필드명 → 값). 오류 행은 읽힌 만큼만 담는다.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: 변경 행의 표시용 diff — {CSV 헤더: [이전 표기, 이후 표기]}.
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("kind", IMPORT_ROW_KINDS),
        CheckConstraint("row_no >= 2", name="row_no_after_header"),
        CheckConstraint(
            "kind <> 'NEW' OR (target_id IS NULL AND expected_version IS NULL)",
            name="new_has_no_target",
        ),
        CheckConstraint(
            "kind <> 'CHANGED' OR (target_id IS NOT NULL AND expected_version IS NOT NULL)",
            name="changed_has_target",
        ),
        CheckConstraint("kind <> 'ERROR' OR error_reason IS NOT NULL", name="error_has_reason"),
        unique_active("import_staging_rows", "staging_id", "row_no"),
    )
