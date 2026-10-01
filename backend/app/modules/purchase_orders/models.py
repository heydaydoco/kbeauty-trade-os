"""구매 발주서(PO) 헤더·라인 (S3-1 design-integrated §2.1 / design-A A12 / design-F F1~F5 / ADR-0057).

■ PO는 **초안이 없다** — 생성 = 발행 = 발주 확정 = 동결이다(§7.2 "발행→…"). 상태는 ISSUED에서 시작하고 사람 전이(공급사 확인[OC]·취소)로만
  움직인다(자동 엣지 0 — §15 L3 4금 ①). 동결 시각 `frozen_at`은 `NOT NULL DEFAULT now()`라 생성 트랜잭션이 곧 동결 시점이다(X-03) —
  편집 가능 상태가 없어 CONTENT(품목·수량·원가·통화·공급사·`po_kind`)는 처음부터 잠겨 있고 고칠 길은 취소+신규다.
■ **원가 열은 `unit_cost`·`line_cost`·`total_cost`**(`_cost` 접미)다 — 로그 마스킹 키·접미사·금액 판정에 코드 변경 없이 걸린다
  (`amount`·`price`는 의도적으로 마스킹 제외라 `_amount` 이름은 쓰지 않는다 — ADR-0057 ④).
■ 라인 품목은 **SKU 전용**(`sku_id NOT NULL` — 자재 PO는 P4 가산 경로, ADR-0057 ①). 단가는 양수(무상 매입 미지원).
■ OC(공급사 확인) 부속 열(`oc_received_on`·`oc_reference`)은 ISSUED에서는 비어 있고 SUPPLIER_CONFIRMED 이후 `oc_received_on`이 필수다(DB CHECK).
  발행일≤OC 일자≤오늘 경계는 서비스가 지킨다(오늘 상한은 CHECK로 표현 불가).
■ 공급사 유형 검증은 생성 1회(거래처 행 `FOR KEY SHARE`) — 이후 유형 해제는 이미 발행된 PO를 무효화하지 않는다(역사 전표, F3).
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
from app.core.db.constraints import unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.trade_docs.constants import DocKind, PoKind
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.mixins import (
    PurchaseLineMixin,
    TradeHeaderMixin,
    frozen_complete_check,
    header_common_checks,
    purchase_line_checks,
)

_DEAD = ", ".join(f"'{s}'" for s in DEAD_STATUSES)
#: OC가 기록돼 있어야 하는 상태(공급사 확인 이후) — 예약 후반 상태 3값 포함(엣지는 S4-1이 더한다).
_OC_STATES = "'SUPPLIER_CONFIRMED', 'PARTIALLY_RECEIVED', 'FULLY_RECEIVED', 'CLOSED'"


class PurchaseOrder(
    TradeHeaderMixin, PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """구매 발주서 헤더."""

    __tablename__ = "purchase_orders"

    DOC_KIND: ClassVar[DocKind] = DocKind.PURCHASE_ORDER

    #: 헤더 합계 원가 = Σ 라인 원가(서버 계산 — 요청 스키마에 없다). 원가 열이라 이름이 `_cost`다.
    total_cost: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    #: 공급사(SUPPLIER 또는 OEM 유형) — 생성 후 불변(ORIGIN). 이름은 발행 시점 스냅샷이다.
    supplier_partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 구분 슬롯 — PURCHASE(일반 구매)·OEM_PRODUCTION(OEM 생산 발주, S3-2 마일스톤 프로파일의 키). 생성 후 불변.
    po_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PURCHASE'"), default=PoKind.PURCHASE.value
    )
    #: 공급사 확인(OC) 일자·참조 — FREE 열(오기 정정). ISSUED에서는 비어 있다.
    oc_received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    oc_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: 동결 시각 — 생성 즉시 발행이라 DB 기본값(now())이 곧 동결 시점이다(애플리케이션 대입 없음).
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        *header_common_checks(DocKind.PURCHASE_ORDER),
        frozen_complete_check("frozen_at", ["btrim(supplier_name) <> ''"]),
        value_in("po_kind", [kind.value for kind in PoKind], name="po_kind_valid"),
        CheckConstraint("btrim(supplier_name) <> ''", name="supplier_name_not_blank"),
        CheckConstraint(
            "oc_reference IS NULL OR btrim(oc_reference) <> ''", name="oc_reference_not_blank"
        ),
        # 발행(ISSUED) 중에는 OC가 비어 있고, 공급사 확인 이후에는 OC 일자가 필수다(취소는 무제약 — ISSUED에서 바로 취소할 수 있다).
        CheckConstraint(
            "status <> 'ISSUED' OR (oc_received_on IS NULL AND oc_reference IS NULL)",
            name="oc_empty_when_issued",
        ),
        CheckConstraint(
            f"status NOT IN ({_OC_STATES}) OR oc_received_on IS NOT NULL",
            name="oc_required_after_confirm",
        ),
        CheckConstraint(
            "oc_received_on IS NULL OR oc_received_on >= doc_date", name="oc_not_before_doc"
        ),
        UniqueConstraint("doc_number", name="uq_purchase_orders_doc_number"),
        # 라인의 복합 FK 대상 — 라인 통화가 헤더 통화와 어긋나면 DB가 거부한다.
        UniqueConstraint("id", "currency", name="uq_purchase_orders_id_currency"),
        # 살아 있는 복제본은 원본당 하나(X-08 — 4종 공통).
        Index(
            "uq_purchase_orders_copied_from_id_live",
            "copied_from_id",
            unique=True,
            postgresql_where=text(
                f"deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ({_DEAD})"
            ),
        ),
        Index("ix_purchase_orders_supplier_partner_id_doc_date", "supplier_partner_id", "doc_date"),
        Index("ix_purchase_orders_status_doc_date", "status", "doc_date"),
        Index("ix_purchase_orders_assignee_id", "assignee_id"),
    )


class PurchaseOrderLine(
    PurchaseLineMixin, PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base
):
    """PO 라인 — 생성 시 INSERT만 한다(동결 후 변경 경로 없음). 안정 id는 S3-2·S4-1의 소비 FK 대상이다."""

    __tablename__ = "purchase_order_lines"

    po_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["po_id", "currency"],
            ["purchase_orders.id", "purchase_orders.currency"],
            ondelete="RESTRICT",
        ),
        *purchase_line_checks(),
        unique_active("purchase_order_lines", "po_id", "line_no"),
        # 같은 SKU는 한 줄(분할 납기 미지원 — A11·P-43 준용).
        unique_active("purchase_order_lines", "po_id", "sku_id"),
        Index("ix_purchase_order_lines_po_id", "po_id"),
        Index("ix_purchase_order_lines_sku_id", "sku_id"),
    )
