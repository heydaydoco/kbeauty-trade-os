"""수주(SO) 헤더·라인 (S3-1 design-integrated §2.1 / design-A A2·A4 / design-B B1·B2 / ADR-0052·0053).

■ 상태는 RECEIVED(접수)에서 시작한다 — **편집 가능 구간은 RECEIVED 하나뿐**이고 확정(CONFIRMED)이 동결 시점이다(PR-12).
  동결 표식은 **`confirmed_at` 하나**다(X-03 — `frozen_at`은 만들지 않는다). RECEIVED ↔ confirmed_at NULL,
  CONFIRMED·후반 상태 ↔ NOT NULL이라 "확정된 SO가 편집 가능 상태로 되돌아가는" 위반을 DB가 거부한다(ON_HOLD·CANCELLED는 무제약).
■ **거래처(`buyer_partner_id`)·참조 FK(`qt_id`·`pi_id`)·복제 원본은 ORIGIN(생성 후 어느 상태에서도 불변)**이다 — 여신 잠금이
  "거래처→SO" 순서를 지키려면 SO의 거래처를 잠금 전에 읽을 수 있어야 하기 때문이다(X-09).
■ 직접(인테이크) SO는 `qt_id`·`pi_id`가 모두 NULL이고, PI 경유 SO는 `qt_id`도 PI의 `qt_id`로 채워진다(복합 FK가 강제).
■ **중복 바이어 PO 0건은 DB 부분 유니크가 보증한다** — `(buyer_partner_id, buyer_po_no_key)` 살아 있고 취소 아닌 행 한정.
  취소 SO는 번호를 점유하지 않는다(정정 = 취소+신규 — ADR-05). PI→SO도 활성 1:1 부분 유니크다.
■ 라인은 Version 믹스인이 없고 헤더 version이 직렬화한다. 출처 열(`qt_line_id`·`pi_line_id`)은 한 줄에 하나만(`num_nonnulls <= 1`).
■ 확정 증적 3열·`credit_*`·`approval_id`는 승인 코어 뒤 "확정 배선" 마이그레이션(M10, PR-12)이 더한다 — 여기엔 없다.
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


class SalesOrder(
    SalesHeaderMixin, PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """수주 헤더."""

    __tablename__ = "sales_orders"

    DOC_KIND: ClassVar[DocKind] = DocKind.SALES_ORDER

    #: 원천 견적 — 생성 후 불변(ORIGIN). NULL = 직접(인테이크) 수주. PI 경유 SO도 PI의 `qt_id`로 채워진다.
    qt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=True
    )
    #: 원천 PI — 생성 후 불변(ORIGIN). 복합 FK `(pi_id, qt_id)`가 "SO의 qt_id는 PI의 qt_id"를 강제한다.
    pi_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 바이어 PO번호 원문(strip)과 서버 산출 정규화 키 — 요청 스키마에 키 필드는 없다.
    buyer_po_no: Mapped[str | None] = mapped_column(String(60), nullable=True)
    buyer_po_no_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    buyer_po_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 동결(확정) 시각 — 확정 전이 통로(PR-12)만 대입한다. SYSTEM.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        *header_common_checks(DocKind.SALES_ORDER),
        frozen_complete_check("confirmed_at", ["btrim(buyer_name) <> ''"]),
        CheckConstraint(
            "(status = 'RECEIVED' AND confirmed_at IS NULL)"
            " OR (status IN ('CONFIRMED', 'PARTIALLY_ALLOCATED', 'ALLOCATED', 'IN_SHIPMENT',"
            " 'COMPLETED') AND confirmed_at IS NOT NULL)"
            " OR status IN ('ON_HOLD', 'CANCELLED')",
            name="confirmed_at_consistent",
        ),
        CheckConstraint("btrim(buyer_name) <> ''", name="buyer_name_not_blank"),
        CheckConstraint("pi_id IS NULL OR qt_id IS NOT NULL", name="pi_requires_qt"),
        CheckConstraint("(buyer_po_no IS NULL) = (buyer_po_no_key IS NULL)", name="po_pair"),
        CheckConstraint(
            "buyer_po_no IS NULL OR (btrim(buyer_po_no) <> '' AND btrim(buyer_po_no_key) <> '')",
            name="po_not_blank",
        ),
        ForeignKeyConstraint(
            ["pi_id", "qt_id"],
            ["proforma_invoices.id", "proforma_invoices.qt_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("doc_number", name="uq_sales_orders_doc_number"),
        # 라인의 복합 FK 대상 — 라인 통화가 헤더 통화와 어긋나면 DB가 거부한다.
        UniqueConstraint("id", "currency", name="uq_sales_orders_id_currency"),
        # 중복 바이어 PO 0건 — 취소 SO는 번호를 점유하지 않는다(정정 = 취소+신규, design-A A2).
        Index(
            "uq_sales_orders_buyer_po_live",
            "buyer_partner_id",
            "buyer_po_no_key",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status <> 'CANCELLED'"
            ),
        ),
        # PI→SO 활성 1:1(취소 후 재생성 허용).
        Index(
            "uq_sales_orders_pi_id_live",
            "pi_id",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND pi_id IS NOT NULL AND status <> 'CANCELLED'"
            ),
        ),
        # 살아 있는 복제본은 원본당 하나(X-08 — 4종 공통).
        Index(
            "uq_sales_orders_copied_from_id_live",
            "copied_from_id",
            unique=True,
            postgresql_where=text(
                f"deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ({_DEAD})"
            ),
        ),
        Index("ix_sales_orders_qt_id", "qt_id"),
        Index("ix_sales_orders_pi_id", "pi_id"),
        Index("ix_sales_orders_buyer_partner_id_doc_date", "buyer_partner_id", "doc_date"),
        Index("ix_sales_orders_status_doc_date", "status", "doc_date"),
        Index("ix_sales_orders_assignee_id", "assignee_id"),
    )


class SalesOrderLine(SalesLineMixin, PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """수주 라인 — 원천 라인(QT 또는 PI)을 하나만 가리킨다(직접 SO는 둘 다 NULL). 안정 id: 편집은 제자리 UPDATE."""

    __tablename__ = "sales_order_lines"

    so_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 요청납기(CONTENT — S3-2 마일스톤·백오더의 기준값, 조용한 변경 금지).
    requested_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 직접 경로(PI 없는 후불 거래)의 원천 QT 라인. PI 경유 라인은 `pi_line_id`만 채운다(QT 조상은 PI 라인에서 유도).
    qt_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quotation_lines.id", ondelete="RESTRICT"), nullable=True
    )
    pi_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proforma_invoice_lines.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["so_id", "currency"],
            ["sales_orders.id", "sales_orders.currency"],
            ondelete="RESTRICT",
        ),
        *sales_line_checks(),
        CheckConstraint("num_nonnulls(qt_line_id, pi_line_id) <= 1", name="one_source_only"),
        unique_active("sales_order_lines", "so_id", "line_no"),
        # 같은 SKU는 유상 1줄 + 무상 1줄까지(FOC 병행은 정상 패턴 — A11).
        unique_active("sales_order_lines", "so_id", "sku_id", "is_free"),
        Index("ix_sales_order_lines_so_id", "so_id"),
        Index("ix_sales_order_lines_sku_id", "sku_id"),
        # 잔량 소비 SUM(원천 라인별)의 조회 축.
        Index("ix_sales_order_lines_qt_line_id", "qt_line_id"),
        Index("ix_sales_order_lines_pi_line_id", "pi_line_id"),
    )
