"""문서 보관소 — 종류·문서·품목군 서류 세트 (DESIGN.md §4.7·§4.8 / WBS S1-3).

■ 종류 코드는 닫힌 CHECK가 아니라 열린 데이터다 (ADR-11 / ADR-0028)

  §4.6 거래처 유형은 보강 문면이 "열거 10종 CHECK"를 명시했지만, §4.7의 종류
  열거는 "…까지 한 곳"이라는 예시적 문면이고 ADR-11이 "유형 코드는 데이터"라고
  못박는다. 그래서 `document_types` 테이블 + FK다. 시드는 §4.7 열거를 커버하고,
  추가는 마이그레이션·관리 화면(P6)의 몫이다.

■ 소유는 폴리모픽, 열거는 소비분 한정 (ADR-0028)

  documents는 "폴리모픽 소유"(§4.7)라 FK를 걸 수 없다 — owner_type CHECK로
  값을 잠그고 소유 실재는 서비스가 검증한다. 열거는 이번 세션이 실제로
  소비하는 SKU(MSDS)·LABEL(라벨 파일) 2종뿐이다. 전표·인증·시설처럼 아직
  없는 소유자를 미리 넣으면 검증할 수 없는 값이 된다(ADR-0021과 같은 규율) —
  후속 세션이 자기 소유자를 CHECK 확장 마이그레이션으로 추가한다.

■ FILE/LINK 2형 (ADR-0028·0029)

  FILE은 서버가 저장한 실물(저장 파일명=서버 생성 hex, 원본 파일명은 표시
  전용), LINK는 외부 URL이다. 두 형이 서로의 컬럼을 갖지 못하도록 CHECK로
  잠근다 — "URL도 있고 파일도 있는" 행은 어느 쪽이 진본인지 답할 수 없다.

■ 보존기한·파기 잠금 (§4.7)

  원산지 증빙은 보존기한(발급일+5년)+파기 잠금이다. 연수는 종류의 속성이라
  `document_types.retention_years`(데이터)로 두고, 보존기한 내 soft delete
  거부는 서비스가 강제한다 — "오늘"과의 비교는 DB CHECK로 표현할 수 없다.
  강제 삭제 액션은 만들지 않는다(웹 세션 승인 문면).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import positive, unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

#: 문서의 소유자 유형 — 이번 세션이 소비하는 2종뿐이다(위 독스트링).
#: SKU — MSDS 등 SKU 단위 문서(ADR-0020 승격). LABEL — 라벨 아트웍 파일.
DOCUMENT_OWNER_TYPES = ("SKU", "LABEL")

#: 문서의 저장 형태. FILE — 서버 저장 실물. LINK — 외부 URL(승격 이관분 포함).
DOCUMENT_STORAGE_KINDS = ("FILE", "LINK")


class DocumentType(PkMixin, TimestampMixin, SoftDeleteMixin, Base):
    """문서 종류 코드 (§4.7 "종류 코드" / ADR-11 "유형 코드는 데이터").

    시드는 마이그레이션이 넣는다(§4.7 열거 추적 — 각 행의 note에 근거 절 번호).

    ★ roles와 같은 지위라 믹스인도 같다 — Version·Actor 없음. 마이그레이션이
      소유하는 시드 테이블에 사람 행위자는 없고, **users로의 FK가 있으면
      테스트 하네스의 `TRUNCATE users … CASCADE`가 이 시드를 함께 지운다**
      (PRESERVED_TABLES 등재가 무력화되는 경로 — 실측으로 확인했다).
    """

    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 보존기한 산정 연수 (§4.7 "원산지 증빙은 보존기한(발급일+5년)").
    #: NULL = 보존 규율 없음. 값이 있으면 발급일이 필수이고 파기 잠금이 선다.
    retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        positive("retention_years"),
        unique_active("document_types", "code"),
    )


class Document(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """문서 (§4.7 — 폴리모픽 소유, 종류 코드, 유효기간, 파일, 해시)."""

    __tablename__ = "documents"

    #: 폴리모픽 소유 — FK가 불가능해 CHECK+서비스 검증이다(위 독스트링).
    owner_type: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=False
    )
    storage_kind: Mapped[str] = mapped_column(String(10), nullable=False)

    # ── FILE 전용 (ADR-0029 파일 보안 계약) ────────────────────────────────
    #: 저장 파일명 = 서버 생성 hex 32자. 사용자 파일명은 절대 경로에 쓰지 않는다.
    stored_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 원본 파일명 — 표시·다운로드 헤더 전용(정규화 후 저장).
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 업로드 시 신고된 MIME. 서빙은 항상 octet-stream이라 표시 참고값이다.
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 파일 해시 (§4.7 "해시" / §12.1 해시 중복 감지). 같은 소유자에 같은
    #: 파일의 재업로드를 부분 유니크가 막는다(§17.4).
    sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)

    # ── LINK 전용 (msds_url·labels.file_url 승격 이관분 — ADR-0020) ────────
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: 발급일(업무 날짜). 보존기한 산정의 기준이다(§4.7).
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 유효기간 만료일. 값이 있는 문서가 기일 엔진(S2-3) 스캔 대상이다 —
    #: 스캔 배치는 이번 범위가 아니다(scheduled_jobs 0행 유지).
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 보존기한. 이 날짜까지 soft delete가 거부된다(파기 잠금 — 서비스 강제).
    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("owner_type", DOCUMENT_OWNER_TYPES),
        value_in("storage_kind", DOCUMENT_STORAGE_KINDS),
        # 두 형이 서로의 컬럼을 갖지 못한다 — FILE은 파일 5속성 전부+URL 부재,
        # LINK는 URL만. 반쪽 행은 다운로드도 링크 이동도 못 하는 유령이 된다.
        CheckConstraint(
            "(storage_kind = 'FILE' AND stored_name IS NOT NULL"
            " AND original_filename IS NOT NULL AND content_type IS NOT NULL"
            " AND size_bytes IS NOT NULL AND sha256 IS NOT NULL AND url IS NULL)"
            " OR (storage_kind = 'LINK' AND url IS NOT NULL"
            " AND stored_name IS NULL AND original_filename IS NULL"
            " AND content_type IS NULL AND size_bytes IS NULL AND sha256 IS NULL)",
            name="storage_kind_fields",
        ),
        positive("size_bytes"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        # 서버 생성 파일명(uuid hex 32자) 외의 값이 경로가 되는 일을 DB가 막는다.
        CheckConstraint("stored_name ~ '^[0-9a-f]{32}$'", name="stored_name_format"),
        # 기간 순서 (§17.5). NULL은 통과한다 — 값이 있으면 지켜야 한다는 뜻.
        CheckConstraint("valid_until >= issued_on", name="validity_after_issue"),
        CheckConstraint("retention_until >= issued_on", name="retention_after_issue"),
        unique_active("documents", "owner_type", "owner_id", "sha256"),
    )


class ItemProfileDocumentType(
    PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """품목군 → 서류 세트 (§4.8 / ADR-0021 "서류=S1-3").

    §4.8의 "신규 등록 시 자동 적용"은 소비할 대상(요건·태스크)이 서는 후속
    세션의 일이다 — 지금은 세트의 정의만 담는다.
    """

    __tablename__ = "item_profile_document_types"

    item_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    document_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        # unique_active()가 만들 이름이 PG 상한(63자)을 넘어 같은 꼴을 손으로
        # 줄여 만든다 — 컬럼 전체 이름 대신 profile_type 축약.
        Index(
            "uq_item_profile_document_types_profile_type_active",
            "item_profile_id",
            "document_type_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
