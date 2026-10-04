"""선적 헤더·라인·당사자 (S3-2 PR-3a / design-integrated §2.1 (a)~(c)·§9 R-04·R-15 / design-A A1~A5 / ADR-0074).

■ **전표 커널 편입**(DocKind `SHIPMENT`·접두어 `SH`): 상태 대입은 `record_birth`·`record_transition` 단일 통로, 상태이력은
  `trade_docs.models.ShipmentStatusLog`(IMMUTABLE). 활성 상태는 PLANNED·RELEASE_ORDERED·CANCELLED(피킹~종결 5값 RESERVED — S4-2).
■ **원천 FK 정확히 하나**(`ck_shipments_kind_source`): 수출 = `so_id`, 수입 = `po_id`. 채널입고·샘플무상 행은 DB가 거부한다(값만 싣고
  생성 경로 미개방 — 부채 Q-03). 다중 SO 합적 없음(Q-01).
■ **스냅샷 복사**(ORIGIN): 통화·환율 2열·결제조건 4열·Incoterms 3열·거래 상대·품명은 원천에서 복사하고 생성 본문에 해당 필드가 없다.
  `doc_date`는 복사가 아니라 생성 시 `today_kst()` 1회(R-15). 수입선적은 **PO 원가를 복사하지 않는다**(단가 NULL·금액 0 — ADR-0024).
■ 라인은 Version 믹스인이 없고 헤더 version이 직렬화한다(라인 편집 = 헤더 잠금 → 헤더 version 대조 → 헤더 version +1).
  라인 통화는 헤더와 복합 FK로 묶인다(혼합 통화 불가능 — S3-1 라인 규약 승계).
■ 당사자: 역할 폐쇄 5값, (선적, 역할) 살아 있는 행 유일. 원천 거래처 자동 스냅샷 행(`is_auto`)은 수출 CONSIGNEE·수입 SHIPPER뿐이다.
■ 이 모듈(L1)은 SO·PO 모델을 임포트하지 않는다 — 원천은 테이블 이름 FK로만 가리킨다(계층 DAG, ADR-0059).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    desc,
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
from app.modules.trade_docs.constants import (
    MAX_QUANTITY,
    MAX_SAFE_INTEGER,
    SKU_KINDS,
    DocKind,
    PartyRole,
    ShipmentKind,
)
from app.modules.trade_docs.machine import RESERVED, ShipmentStatus
from app.modules.trade_docs.mixins import TradeHeaderMixin, header_common_checks

#: 동결(출고지시) 이후 상태 — frozen_at NOT NULL이어야 한다(RESERVED 5값 포함 — S4-2가 엣지를 더해도 정합 유지).
_FROZEN_STATES = ", ".join(
    f"'{s}'" for s in sorted({ShipmentStatus.RELEASE_ORDERED.value} | RESERVED[DocKind.SHIPMENT])
)
#: 자동 스냅샷 당사자 역할(수출 CONSIGNEE = SO 바이어, 수입 SHIPPER = PO 공급사).
AUTO_PARTY_ROLES: tuple[str, ...] = (PartyRole.CONSIGNEE.value, PartyRole.SHIPPER.value)


class Shipment(
    TradeHeaderMixin, PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """선적 헤더 — 수출(SO 참조)·수입(PO 참조) 한 kind."""

    __tablename__ = "shipments"

    DOC_KIND: ClassVar[DocKind] = DocKind.SHIPMENT

    #: 구분 4값(§7.5) — 생성 경로는 원천 종류가 결정한다(요청 본문에 구분 필드 없음). ORIGIN.
    shipment_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 수출 원천 SO — 생성 후 불변(ORIGIN). CHILD_LINKS(SO→선적)의 FK.
    so_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=True
    )
    #: 수입 원천 PO — 생성 후 불변(ORIGIN). 생성 경로는 PR-5a.
    po_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=True
    )
    #: 거래 상대(수출 = SO 바이어, 수입 = PO 공급사) — 원천 사본(ORIGIN).
    counterparty_partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    #: 거래 상대명 스냅샷(SO `buyer_name` / PO `supplier_name`).
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 출발국·도착국 ISO 3166-1 alpha-2(**markets FK 아님** — EU 같은 비국가 코드 배제). 계획(PLANNED) 중에만 편집(CONTENT).
    origin_country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    dest_country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    #: 라인 금액 합 — 판매가 축(수입은 0). 직접 대입은 `editing.recompute_total`·생성 착지뿐.
    total_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    #: 출고지시(동결) 시각 — 동결 액션 전이(`record_transition(via_freeze_action=True)`)만 대입한다. SYSTEM.
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        *header_common_checks(DocKind.SHIPMENT),
        value_in("shipment_kind", [k.value for k in ShipmentKind], name="kind_valid"),
        # 원천 FK 정확히 하나 + 생성 경로가 열린 구분만 — 채널입고·샘플무상 행은 DB가 거부한다(ADR-0074, 여는 세션이 재정의).
        CheckConstraint(
            "(shipment_kind = 'EXPORT' AND so_id IS NOT NULL AND po_id IS NULL)"
            " OR (shipment_kind = 'IMPORT' AND po_id IS NOT NULL AND so_id IS NULL)",
            name="kind_source",
        ),
        # 수입선적 = PO 원가 비복사(금액 축 없음 — ADR-0024 10번째 채널 미개설).
        CheckConstraint(
            "shipment_kind <> 'IMPORT' OR total_amount = 0", name="import_has_no_amount"
        ),
        CheckConstraint(
            "origin_country_code ~ '^[A-Z]{2}$' AND dest_country_code ~ '^[A-Z]{2}$'",
            name="country_format",
        ),
        CheckConstraint("btrim(counterparty_name) <> ''", name="counterparty_name_not_blank"),
        # 원천(확정 SO·발행 PO)은 동결 완결 상태라 사본도 항상 완결이다 — 미완결 사본이 서류 영역으로 새지 않게 상태 무관 강제.
        CheckConstraint(
            "payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL",
            name="source_terms_complete",
        ),
        # 선적은 복제 경로가 없다(정정 = 취소+신규) — 믹스인이 주는 복제 계보 열은 항상 비어 있다.
        CheckConstraint("copied_from_id IS NULL", name="no_copy_lineage"),
        # 동결 정합 — PLANNED는 미동결, 출고지시 이후는 동결(CANCELLED는 어느 쪽이든 — 계획·출고지시 어디서든 취소된다).
        CheckConstraint("status <> 'PLANNED' OR frozen_at IS NULL", name="planned_not_frozen"),
        CheckConstraint(
            f"status NOT IN ({_FROZEN_STATES}) OR frozen_at IS NOT NULL", name="released_frozen"
        ),
        UniqueConstraint("doc_number", name="uq_shipments_doc_number"),
        # 라인의 복합 FK 대상 — 라인 통화가 헤더 통화와 어긋나면 DB가 거부한다.
        UniqueConstraint("id", "currency", name="uq_shipments_id_currency"),
        # CHILD_LINKS 후속 생존 판정·SO 수렴 계수의 조회 축.
        Index(
            "ix_shipments_so_id_live",
            "so_id",
            postgresql_where=text("deleted_at IS NULL AND so_id IS NOT NULL"),
        ),
        Index(
            "ix_shipments_po_id_live",
            "po_id",
            postgresql_where=text("deleted_at IS NULL AND po_id IS NOT NULL"),
        ),
        # 목록(Page 50) — 상태 필터 + 최신순.
        Index(
            "ix_shipments_list", "status", desc("id"), postgresql_where=text("deleted_at IS NULL")
        ),
        Index("ix_shipments_assignee_id", "assignee_id"),
        Index("ix_shipments_counterparty_partner_id", "counterparty_partner_id"),
    )


class ShipmentLine(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """선적 라인 — 원천 라인(SO 라인 또는 PO 라인)을 정확히 하나 가리킨다. 안정 id: 수량 편집은 제자리 UPDATE(PLANNED)."""

    __tablename__ = "shipment_lines"

    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 헤더 `last_line_no` 카운터(결번 허용·재사용 금지). SYSTEM.
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 수출 원천 라인 — LINE_CONSUMERS["SO_LINE"] FULFILL 소비자(SO 잔량을 줄인다).
    so_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), nullable=True
    )
    #: 수입 원천 라인 — LINE_CONSUMERS["PO_LINE"] **IN_TRANSIT** 소비자(PO 잔량 불변, 배정 가능량만 줄인다 — ADR-0077).
    po_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"), nullable=True
    )
    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(40), nullable=False)
    sku_name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    sku_name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sku_kind: Mapped[str] = mapped_column(String(6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    #: 수량(EA 정수) — PLANNED 중에만 편집(원천 잔량 안에서).
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 수출 = SO 라인 단가 사본, **수입 = NULL**(PO 원가 비복사). `SalesLineMixin`은 NOT NULL이라 재사용하지 않는다.
    unit_price_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: SO 무상 라인 표식 사본(수입 false).
    is_free: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    #: 수량 × 단가(수입 0) — numeric 곱 CHECK(bigint 오버플로 500 방지, R-04).
    line_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["shipment_id", "currency"],
            ["shipments.id", "shipments.currency"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("(so_line_id IS NULL) <> (po_line_id IS NULL)", name="one_source"),
        CheckConstraint(f"quantity BETWEEN 1 AND {MAX_QUANTITY}", name="quantity_range"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint(
            "sku_kind IN (" + ", ".join(f"'{k}'" for k in sorted(SKU_KINDS)) + ")",
            name="sku_kind_valid",
        ),
        CheckConstraint(f"line_amount BETWEEN 0 AND {MAX_SAFE_INTEGER}", name="amount_range"),
        CheckConstraint(
            f"unit_price_amount IS NULL OR unit_price_amount BETWEEN 0 AND {MAX_SAFE_INTEGER}",
            name="unit_price_range",
        ),
        # 수입 라인 — 단가·금액·무상 표식 없음(원가 비복사).
        CheckConstraint(
            "po_line_id IS NULL OR (unit_price_amount IS NULL AND line_amount = 0 AND NOT is_free)",
            name="import_no_price",
        ),
        # 수출 라인 — 단가 사본 필수, 금액 = 수량 × 단가(numeric 곱, R-04).
        CheckConstraint(
            "so_line_id IS NULL OR (unit_price_amount IS NOT NULL"
            " AND quantity::numeric * unit_price_amount = line_amount)",
            name="export_priced",
        ),
        # 수출 라인 — SO 라인 무상 규약 승계(is_free ⇔ 단가 0, R-04).
        CheckConstraint(
            "so_line_id IS NULL OR is_free = (unit_price_amount = 0)",
            name="export_free_iff_zero_price",
        ),
        unique_active("shipment_lines", "shipment_id", "line_no"),
        # 한 선적 안에서 같은 원천 라인은 1행(선적 간에는 1:N 부분선적) — 위반은 409 `SHIPMENTS.LINE.DUPLICATE_SOURCE`.
        unique_active("shipment_lines", "shipment_id", "so_line_id"),
        unique_active("shipment_lines", "shipment_id", "po_line_id"),
        Index("ix_shipment_lines_shipment_id", "shipment_id"),
        # 잔량·배정 가능량 소비 SUM(원천 라인별)의 조회 축.
        Index("ix_shipment_lines_so_line_id", "so_line_id"),
        Index("ix_shipment_lines_po_line_id", "po_line_id"),
        Index("ix_shipment_lines_sku_id", "sku_id"),
    )


class ShipmentParty(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """선적 당사자 — 거래처 마스터 참조 + 영문 스냅샷(원천 수정은 소급하지 않는다, §3 스냅샷 규율)."""

    __tablename__ = "shipment_parties"

    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    #: 거래처 `name_en` 스냅샷 — 결측이면 생성·추가가 422(`SHIPMENTS.PARTY.ENGLISH_NAME_MISSING`, fail-visible).
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    address_en: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 원천 거래처 자동 스냅샷 행(수출 CONSIGNEE·수입 SHIPPER) — 불변(삭제·교체 422 ROLE_NOT_ALLOWED).
    is_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    __table_args__ = (
        value_in("role", [r.value for r in PartyRole], name="role_valid"),
        CheckConstraint("btrim(name_en) <> '' AND name_en !~ '[[:cntrl:]]'", name="name_en_clean"),
        # 주소는 여러 줄일 수 있다(거래처 영문 주소 원문 — QT `buyer_address` 관례). 공백뿐인 값만 거부한다(서비스가 NULL로 접는다).
        CheckConstraint(
            "address_en IS NULL OR btrim(address_en) <> ''", name="address_en_not_blank"
        ),
        # 주소의 줄 구분은 탭·LF·CR만 허용 — 그 밖의 C0·DEL·C1 제어문자는 거부(서비스 422가 1차, 이 CHECK는 최후 방어선 — 번역표 등재).
        CheckConstraint(
            "address_en IS NULL OR translate(address_en, chr(9) || chr(10) || chr(13), '')"
            " !~ '[[:cntrl:]]'",
            name="address_en_clean",
        ),
        CheckConstraint(
            "NOT is_auto OR role IN ("
            + ", ".join(f"'{r}'" for r in sorted(AUTO_PARTY_ROLES))
            + ")",
            name="auto_role",
        ),
        # (선적, 역할) 살아 있는 행 유일 — 위반은 409 `SHIPMENTS.PARTY.ROLE_DUPLICATE`(soft delete 후 재유입 = 신규).
        unique_active("shipment_parties", "shipment_id", "role"),
        Index("ix_shipment_parties_shipment_id", "shipment_id"),
        Index("ix_shipment_parties_partner_id", "partner_id"),
    )
