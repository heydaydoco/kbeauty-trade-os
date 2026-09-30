"""선수금 청구서(PI) 헤더·라인 (S3-1 design-integrated §2.1 / design-A A1~A4 / ADR-0052·0053).

■ PI는 **초안이 없다** — 생성 = 발행 = 동결이다(§7.2 "발행→…"). 상태는 ISSUED에서 시작하고 입금 수렴
  (ISSUED·PARTIALLY_PAID·PAID 상호)·만료·취소로만 움직인다. 동결 시각 `frozen_at`은 `NOT NULL DEFAULT now()`라
  생성 트랜잭션이 곧 동결 시점이다(X-03) — 편집 가능 상태가 없어 CONTENT는 처음부터 잠겨 있다.
■ PI는 **QT에서만** 만든다(`qt_id NOT NULL` — 직접 PI 발행 경로 없음). 참조 FK와 거래처(`buyer_partner_id`)는 ORIGIN(불변).
■ 은행정보 6열은 발행 시점 값 복사다(`bank_accounts`를 이후 수정해도 불변 — 스냅샷). 서류는 이 열만 읽는다.
■ 선수금·잔금은 **저장하지 않는다** — `payment_terms.split_advance(total, advance_pct_bp)`가 계산한다(X-17).
■ 라인은 QT 라인을 가리킨다(`qt_line_id NOT NULL`) — 잔량(`open_quantity`)의 소비 관계다. Version 믹스인 없음(헤더가 직렬화).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
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
from app.modules.trade_docs.constants import SWIFT_PATTERN, DocKind
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.mixins import (
    SalesHeaderMixin,
    SalesLineMixin,
    frozen_complete_check,
    header_common_checks,
    sales_line_checks,
)

_DEAD = ", ".join(f"'{s}'" for s in DEAD_STATUSES)

#: 은행 스냅샷 6열 — 이름·(열 이름 → 원천 BankAccount 속성) 매핑. 복사 지점은 서비스 1곳.
BANK_SNAPSHOT_COLUMNS: dict[str, str] = {
    "bank_beneficiary_name": "beneficiary_name",
    "bank_beneficiary_address": "beneficiary_address",
    "bank_name": "bank_name",
    "bank_address": "bank_address",
    "bank_account_no": "account_no",
    "bank_swift_code": "swift_code",
}


class ProformaInvoice(
    SalesHeaderMixin, PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """선수금 청구서 헤더."""

    __tablename__ = "proforma_invoices"

    DOC_KIND: ClassVar[DocKind] = DocKind.PROFORMA_INVOICE

    #: 원천 견적 — 생성 후 불변(ORIGIN). PI는 QT에서만 만든다.
    qt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=False
    )
    buyer_address: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 유효기간 — 당일 KST 24:00까지 유효. 생성 시 필수(서버 기본값 없음).
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    #: 동결 시각 — 생성 즉시 발행이라 DB 기본값(now())이 곧 동결 시점이다(애플리케이션 대입 없음).
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    bank_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    bank_beneficiary_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_beneficiary_address: Mapped[str] = mapped_column(String(300), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_address: Mapped[str] = mapped_column(String(300), nullable=False)
    bank_account_no: Mapped[str] = mapped_column(String(40), nullable=False)
    bank_swift_code: Mapped[str] = mapped_column(String(11), nullable=False)

    __table_args__ = (
        *header_common_checks(DocKind.PROFORMA_INVOICE),
        frozen_complete_check(
            "frozen_at",
            [
                "btrim(buyer_name) <> ''",
                "btrim(buyer_address) <> ''",
                "btrim(bank_beneficiary_name) <> ''",
                "btrim(bank_beneficiary_address) <> ''",
                "btrim(bank_name) <> ''",
                "btrim(bank_address) <> ''",
                "btrim(bank_account_no) <> ''",
            ],
        ),
        CheckConstraint("valid_until >= doc_date", name="valid_until_after_doc"),
        CheckConstraint("btrim(buyer_name) <> ''", name="buyer_name_not_blank"),
        CheckConstraint(f"bank_swift_code ~ '{SWIFT_PATTERN}'", name="bank_swift_format"),
        UniqueConstraint("doc_number", name="uq_proforma_invoices_doc_number"),
        # 라인의 복합 FK 대상 — 라인 통화가 헤더 통화와 어긋나면 DB가 거부한다.
        UniqueConstraint("id", "currency", name="uq_proforma_invoices_id_currency"),
        # SO의 복합 FK `(pi_id, qt_id)` 대상 — SO가 PI를 가리키면 SO의 qt_id는 PI의 qt_id로 강제된다(PR-7).
        UniqueConstraint("id", "qt_id", name="uq_proforma_invoices_id_qt_id"),
        Index(
            "uq_proforma_invoices_copied_from_id_live",
            "copied_from_id",
            unique=True,
            postgresql_where=text(
                f"deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ({_DEAD})"
            ),
        ),
        Index("ix_proforma_invoices_qt_id", "qt_id"),
        Index("ix_proforma_invoices_buyer_partner_id_doc_date", "buyer_partner_id", "doc_date"),
        Index("ix_proforma_invoices_status_doc_date", "status", "doc_date"),
        Index("ix_proforma_invoices_assignee_id", "assignee_id"),
        Index("ix_proforma_invoices_bank_account_id", "bank_account_id"),
        # 만료 스윕 후보 — 미입금 발행 상태만(design-B B7).
        Index(
            "ix_proforma_invoices_expiry",
            "valid_until",
            postgresql_where=text("status = 'ISSUED' AND deleted_at IS NULL"),
        ),
    )


class ProformaInvoiceLine(
    SalesLineMixin, PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base
):
    """PI 라인 — 원천 QT 라인을 가리킨다(`qt_line_id`). 값은 QT 라인의 복사이고 라인금액은 새 수량×복사 단가."""

    __tablename__ = "proforma_invoice_lines"

    pi_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    qt_line_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quotation_lines.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["pi_id", "currency"],
            ["proforma_invoices.id", "proforma_invoices.currency"],
            ondelete="RESTRICT",
        ),
        *sales_line_checks(),
        unique_active("proforma_invoice_lines", "pi_id", "line_no"),
        unique_active("proforma_invoice_lines", "pi_id", "sku_id", "is_free"),
        Index("ix_proforma_invoice_lines_pi_id", "pi_id"),
        Index("ix_proforma_invoice_lines_sku_id", "sku_id"),
        # 잔량 소비 SUM(원천 QT 라인별)의 조회 축.
        Index("ix_proforma_invoice_lines_qt_line_id", "qt_line_id"),
    )
