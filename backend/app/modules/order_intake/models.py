"""오더 인테이크 — 바이어 PO를 SO(접수)로 받는 전용 스테이징 (S3-1 PR-13a / design-D D1·D2 / ADR-0071).

■ **별도 2테이블**이다(`import_staging`은 마스터 왕복 전용 — 손대지 않는다). 흐름: 착지(PENDING) → 사람 검토 → **사람 1클릭 `confirm_intake`**(SO 접수 생성) 또는 거부(REJECTED, 사유 필수).
  상태 3값·허용 전이 2(PENDING→CONFIRMED·PENDING→REJECTED)·종결 2태 탈출 0 — 상태 대입의 단일 통로는 `machine.apply_intake_transition`이다(자동 전이 0).
■ **불변 필드**(등록 후 변경 불가): `extracted_snapshot`(최초 제출 원본 — "원본 나란히 검토"의 원천)·`source_kind`·`source_sha256`·`source_group_key`·`original_filename`·`buyer_partner_id`·`currency`.
  강제 3층: ① DB CHECK(쌍 규칙·형식) ② **ORM `before_update` 가드**(아래 — 순변경이 있으면 예외) ③ AST 스캔(Core `update()`가 이 열을 건드리는지 — 담당자 이관만 허용).
  트리거는 채택하지 않는다(ADR-0028·0040 계보).
■ **중복 바이어 PO 0건**: `(buyer_partner_id, buyer_po_no_key)`의 **PENDING 인테이크** 부분 유니크 + SO 쪽 부분 유니크(취소 SO는 번호 해방)가 이어받는다(CONFIRMED는 SO가, REJECTED는 해방).
  PO 비교 키(`buyer_po_no_key`)는 서버 산출 전용(`trade_docs.buyer_po.normalize_buyer_po_no`) — 요청 스키마에 필드가 없다.
■ `sales_order_id`는 **인테이크→SO 단방향 백링크**다(SO 쪽 역FK 없음 — 순환 FK·이중 진실 방지). `copied_from_so_id`(X-13)는 "같은 바이어의 취소 SO를 복제 재접수"한 계보 표시이고 서비스가 자격을 검증한다.
■ 결제조건·Incoterms·환율은 싣지 않는다(SO 접수 후 SO 편집에서 입력 — SO의 행 단위 형태 CHECK가 부분 복사를 허용하지 않는다). 무상(0원) 라인은 받지 않는다(단가 ≥1).
■ 라인은 Version 믹스인이 없고 헤더 version이 직렬화한다(라인만 바뀌어도 헤더 version +1). 라인 번호는 헤더의 `last_line_no`로 발번(결번 허용·재사용 금지).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, attributes, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.trade_docs.constants import MAX_QUANTITY, MAX_SAFE_INTEGER


class IntakeSourceKind(StrEnum):
    """입구 종류 — 소비분만(AI·이메일·채널은 소비 세션이 CHECK 재정의 마이그레이션 1건으로 더한다, ADR-0041)."""

    MANUAL = "MANUAL"
    CSV = "CSV"


class IntakeStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


SOURCE_KINDS: tuple[str, ...] = tuple(k.value for k in IntakeSourceKind)
STATUSES: tuple[str, ...] = tuple(s.value for s in IntakeStatus)

#: 인테이크당 라인 상한(초과 422 `ORDER_INTAKE.LINE.LIMIT_EXCEEDED`).
MAX_INTAKE_LINES = 200
REJECT_REASON_MIN = 5
REJECT_REASON_MAX = 500

#: 등록 후 변경할 수 없는 열 — ORM `before_update` 가드·AST 스캔이 같은 집합을 쓴다.
IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "extracted_snapshot",
    "source_kind",
    "source_sha256",
    "source_group_key",
    "original_filename",
    "buyer_partner_id",
    "currency",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class OrderIntake(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """오더 인테이크 헤더."""

    __tablename__ = "order_intakes"

    source_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 업로드 원문 바이트의 sha256(CSV 전용 — MANUAL은 NULL).
    source_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    #: `'{buyer_partner_id}|{buyer_po_no_key}'`(CSV 전용 — 한 파일이 여러 인테이크를 만든다).
    source_group_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: **불변 원본** — MANUAL=최초 제출 본문 `{kind, header, lines}`. §12.1 "원본 나란히 검토"의 원천.
    extracted_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    buyer_partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    #: 바이어 PO번호 원문(표시용) / 서버 산출 정규화 키(비교용).
    buyer_po_no: Mapped[str] = mapped_column(String(60), nullable=False)
    buyer_po_no_key: Mapped[str] = mapped_column(String(60), nullable=False)
    buyer_po_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    dest_market_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("markets.code", ondelete="RESTRICT"), nullable=False
    )
    #: 상태 대입은 `machine.apply_intake_transition` 한 통로뿐이다(생성자에 status를 넘기지 않는다 — 서버 기본 PENDING).
    status: Mapped[str] = mapped_column(String(9), nullable=False, server_default=text("'PENDING'"))
    assignee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    last_line_no: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    #: 확정으로 만들어진 SO — **단방향 백링크**(SO 쪽 역FK 없음).
    sales_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=True
    )
    #: 복제 재접수의 원본 SO(X-13) — 서비스가 "같은 바이어·취소 SO"를 검증한다. 확정 시 `sales_orders.copied_from_id`로 복사된다.
    copied_from_so_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(f"source_kind IN ({_in_list(SOURCE_KINDS)})", name="source_kind_valid"),
        CheckConstraint(f"status IN ({_in_list(STATUSES)})", name="status_valid"),
        CheckConstraint(
            "((source_kind = 'CSV') = (source_sha256 IS NOT NULL))"
            " AND ((source_sha256 IS NULL) = (source_group_key IS NULL))",
            name="source_pair",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
            name="source_sha256_format",
        ),
        CheckConstraint(
            "jsonb_typeof(extracted_snapshot) = 'object'", name="extracted_snapshot_is_object"
        ),
        CheckConstraint(
            "btrim(buyer_po_no) <> '' AND btrim(buyer_po_no_key) <> ''", name="po_key_nonblank"
        ),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint("last_line_no >= 0", name="last_line_no_nonnegative"),
        # 결정(확정·거부) 일관성 — PENDING ⇔ 결정 시각 없음 ⇔ 결정자 없음.
        CheckConstraint(
            "((status = 'PENDING') = (decided_at IS NULL))"
            " AND ((decided_at IS NULL) = (decided_by_id IS NULL))",
            name="decision_consistent",
        ),
        CheckConstraint(
            "(status = 'CONFIRMED') = (sales_order_id IS NOT NULL)", name="confirmed_has_so"
        ),
        CheckConstraint(
            "(status = 'REJECTED') = (reject_reason IS NOT NULL)", name="rejected_has_reason"
        ),
        CheckConstraint(
            f"reject_reason IS NULL OR char_length(btrim(reject_reason)) BETWEEN {REJECT_REASON_MIN} AND {REJECT_REASON_MAX}",
            name="reject_reason_length",
        ),
        CheckConstraint(
            "reject_reason IS NULL OR reject_reason !~ '[[:cntrl:]]'", name="reject_reason_clean"
        ),
        # UNIQUE(id, currency) — 라인 복합 FK 대상(라인 통화 = 헤더 통화를 DB가 강제).
        UniqueConstraint("id", "currency", name="uq_order_intakes_id_currency"),
        # 진행 중 중복 바이어 PO 차단 — CONFIRMED는 SO 유니크가 이어받고 REJECTED는 키를 해방한다(design-D D1-a).
        Index(
            "uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending",
            "buyer_partner_id",
            "buyer_po_no_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status = 'PENDING'"),
        ),
        # 파일 해시 멱등(ADR-09) — CSV 입구(PR-14)가 쓴다. PENDING 한정(확정 후 재업로드는 PO 중복 검사가 수렴시킨다).
        Index(
            "uq_order_intakes_source_sha256_source_group_key_pending",
            "source_sha256",
            "source_group_key",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND status = 'PENDING' AND source_sha256 IS NOT NULL"
            ),
        ),
        # 인테이크 1건 = SO 1건.
        Index(
            "uq_order_intakes_sales_order_id_active",
            "sales_order_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND sales_order_id IS NOT NULL"),
        ),
        # 같은 원본 SO를 복제하는 진행 중 인테이크는 하나(복제=중복 생성 차단을 DB가 보증 — X-08 정신, 자율 확정).
        Index(
            "uq_order_intakes_copied_from_so_id_pending",
            "copied_from_so_id",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND status = 'PENDING' AND copied_from_so_id IS NOT NULL"
            ),
        ),
        Index("ix_order_intakes_status_assignee_id", "status", "assignee_id"),
        Index("ix_order_intakes_buyer_partner_id", "buyer_partner_id"),
        Index("ix_order_intakes_created_at", "created_at"),
    )


class OrderIntakeLine(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """인테이크 라인 — 바이어 품번·수량·단가(바이어 PO 값)·검토자가 본 SKU 해석 저장본."""

    __tablename__ = "order_intake_lines"

    intake_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 바이어 품번 — strip 원문(대소문자 변환 없음, 추측 보정 금지).
    buyer_item_code: Mapped[str] = mapped_column(String(100), nullable=False)
    #: **검토자가 본 해석 결과의 저장본**(NULL=미매핑). 요청 스키마에 없다 — 서버가 등록·수정·`resolve`마다 재해석해 대입한다(매핑 우회 금지).
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: CSV 행 출처(엑셀 행번호와 일치 — 헤더 1행). MANUAL은 NULL.
    source_row_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["intake_id", "currency"],
            ["order_intakes.id", "order_intakes.currency"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint("btrim(buyer_item_code) <> ''", name="buyer_item_code_nonblank"),
        CheckConstraint(f"quantity BETWEEN 1 AND {MAX_QUANTITY}", name="quantity_range"),
        CheckConstraint(
            f"unit_price_amount BETWEEN 1 AND {MAX_SAFE_INTEGER}", name="unit_price_range"
        ),
        CheckConstraint("source_row_no IS NULL OR source_row_no >= 2", name="source_row_no_min"),
        unique_active("order_intake_lines", "intake_id", "line_no"),
        Index("ix_order_intake_lines_intake_id", "intake_id"),
        Index("ix_order_intake_lines_sku_id", "sku_id"),
    )


class ImmutableIntakeFieldError(RuntimeError):
    """등록 후 변경할 수 없는 열을 바꾸려는 프로그래밍 오류 — 서비스 경계에서 500(로그)이다(조용한 허용 금지)."""


@event.listens_for(OrderIntake, "before_update")
def _guard_immutable_columns(_mapper: Any, _connection: Any, target: OrderIntake) -> None:
    """ORM 가드 — 불변 열에 **순변경**(값이 실제로 달라짐)이 있으면 UPDATE 전에 거부한다. 같은 값 재대입은 통과한다."""
    for key in IMMUTABLE_COLUMNS:
        history = attributes.get_history(target, key)
        if not history.has_changes():
            continue
        # 이전 값을 알 수 없으면(미적재 속성에 대입) 같은 값임을 증명할 수 없으므로 거부한다(fail-closed).
        if not history.deleted or list(history.deleted) != list(history.added):
            raise ImmutableIntakeFieldError(f"order_intakes.{key}는 등록 후 변경할 수 없습니다.")
