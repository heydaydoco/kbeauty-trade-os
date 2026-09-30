"""견적 헤더·라인 (S3-1 design-integrated §2.1 / design-A A1~A3 / ADR-0052·0053).

■ 상태는 DRAFT(작성)에서 시작하고 발행(ISSUED)이 동결 시점이다 — 동결 표식은 `frozen_at`(X-03).
  DRAFT ↔ frozen_at NULL, ISSUED·CONVERTED ↔ NOT NULL(CANCELLED·EXPIRED는 무제약: 초안 폐기도 취소다).
■ 라인 편집은 **헤더 행 잠금 + 헤더 version 상승**으로 직렬화한다(라인에 Version 믹스인 없음).
  라인은 자기 `currency`를 갖고 헤더와 복합 FK로 묶인다 — 혼합 통화가 DB에서 불가능하다.
■ 전 FK RESTRICT·ORM relationship 없음(명시 쿼리)·금액 BIGINT 정수 최소단위.
■ `doc_number`는 **전역 UNIQUE**(부분 인덱스 아님 — 재발급 금지 §17.3, X-01).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.mixins import (
    SalesHeaderMixin,
    SalesLineMixin,
    frozen_complete_check,
    header_common_checks,
    sales_line_checks,
)

_DEAD = ", ".join(f"'{s}'" for s in DEAD_STATUSES)


class Quotation(
    SalesHeaderMixin, PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """견적 헤더."""

    __tablename__ = "quotations"

    DOC_KIND: ClassVar[DocKind] = DocKind.QUOTATION

    buyer_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 유효기간 — 당일 KST 24:00까지 유효. 발행 시 필수(서버 기본값 없음 — UI가 발행일+30일을 미리 채운다).
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 동결 시각(발행 시 1회 채움, 지우지 않는다) — 상태 전이 통로(issue)만 대입한다.
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        *header_common_checks(DocKind.QUOTATION),
        frozen_complete_check(
            "frozen_at",
            [
                "btrim(buyer_name) <> ''",
                "valid_until IS NOT NULL",
                "buyer_address IS NOT NULL",
                "btrim(buyer_address) <> ''",
            ],
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND frozen_at IS NULL)"
            " OR (status IN ('ISSUED', 'CONVERTED') AND frozen_at IS NOT NULL)"
            " OR status IN ('CANCELLED', 'EXPIRED')",
            name="frozen_matches_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= doc_date", name="valid_until_after_doc"
        ),
        CheckConstraint("btrim(buyer_name) <> ''", name="buyer_name_not_blank"),
        UniqueConstraint("doc_number", name="uq_quotations_doc_number"),
        # 라인의 복합 FK 대상 — 라인 통화가 헤더 통화와 어긋나면 DB가 거부한다.
        UniqueConstraint("id", "currency", name="uq_quotations_id_currency"),
        # 살아 있는 복제본은 원본당 하나(복제=중복 생성 차단을 DB가 보증 — X-08).
        Index(
            "uq_quotations_copied_from_id_live",
            "copied_from_id",
            unique=True,
            postgresql_where=text(
                f"deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ({_DEAD})"
            ),
        ),
        Index("ix_quotations_buyer_partner_id_doc_date", "buyer_partner_id", "doc_date"),
        Index("ix_quotations_status_doc_date", "status", "doc_date"),
        Index("ix_quotations_assignee_id", "assignee_id"),
        # 만료 스윕 후보(PR-6) — 후보 수가 작아도 확정 포함(design-B B7).
        Index(
            "ix_quotations_expiry",
            "valid_until",
            postgresql_where=text("status = 'ISSUED' AND deleted_at IS NULL"),
        ),
    )


class QuotationLine(SalesLineMixin, PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """견적 라인 — Version 믹스인 없음(헤더 version이 직렬화한다). 안정 id: 초안 편집은 제자리 UPDATE."""

    __tablename__ = "quotation_lines"

    qt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["qt_id", "currency"],
            ["quotations.id", "quotations.currency"],
            ondelete="RESTRICT",
        ),
        *sales_line_checks(),
        # PI 라인의 복합 FK `(qt_id, qt_line_id)` 대상 — PI 라인이 가리키는 QT 라인이 PI 헤더의 QT 소속임을 DB가 보증한다(M04 ALTER).
        UniqueConstraint("qt_id", "id", name="uq_quotation_lines_qt_id_id"),
        unique_active("quotation_lines", "qt_id", "line_no"),
        # 같은 SKU는 유상 1줄 + 무상 1줄까지(FOC 병행은 정상 패턴 — A11).
        unique_active("quotation_lines", "qt_id", "sku_id", "is_free"),
        Index("ix_quotation_lines_qt_id", "qt_id"),
        Index("ix_quotation_lines_sku_id", "sku_id"),
    )
