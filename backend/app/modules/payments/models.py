"""입금 원장 `payments` — 부호 있는 INSERT-only 원장 (S3-1 PR-10a / design-E E7 / ADR-0068).

■ **입금 사실의 정본**이다. S3-3이 같은 이름·부호 규약·불변 규율로 확장한다(채권 연결은 `pi_id`의 NOT NULL 완화+`receivable_id`
  추가+"둘 중 정확히 하나" CHECK — 기존 열·CHECK·kind 값은 바뀌지 않는다).
■ **원장형**: PkMixin만(Version·SoftDelete·Actor·Timestamp 믹스인 없음) — 수정·삭제가 없고(앱 계정은 INSERT·SELECT만 — 마이그레이션의
  `revoke_mutations`) 정정은 **반대 부호의 신규 행(역기록)** 이다(ADR-05). 행위자는 `recorded_by_id` 한 열로 명시한다(시스템 행위자 없음).
■ 부호 규약: `RECEIPT` > 0 / `REVERSAL` < 0(원 입금의 −전액). 순입금 = `SUM(received_amount)` — 유일한 정의는 `service.net_received_for_pi`.
■ 역기록은 **같은 PI·같은 통화**의 원 입금만 가리킨다 — 복합 FK `(reverses_payment_id, pi_id, received_currency)` →
  `UNIQUE(id, pi_id, received_currency)`가 DB에서 강제하고, 한 입금의 역기록은 1회뿐이다(`uq_payments_reverses_payment_id`).
  MATCH SIMPLE은 NULL 열이 있으면 FK를 검사하지 않으므로, REVERSAL이면 `reverses_payment_id NOT NULL`을 CHECK(kind_sign)가 요구해 보강한다.
■ `reference`(은행 거래 참조·확인 메모)는 **유니크가 아니다**(0-1 #13 — 같은 참조가 정당하게 반복될 수 있어 이중 입력 탐지는 S3-3/P7의 몫).
  참조 텍스트는 audit·outbox·로그에 싣지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CHAR,
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
from app.core.db.mixins import PkMixin

KIND_RECEIPT = "RECEIPT"
KIND_REVERSAL = "REVERSAL"
KINDS = (KIND_RECEIPT, KIND_REVERSAL)

#: 금액 컬럼 상한(JS 안전 정수 — 전 금액 컬럼 공통 규약).
MAX_MINOR_AMOUNT = 2**53 - 1
REFERENCE_MAX = 100
REASON_MAX = 300
REASON_MIN = 2


class Payment(PkMixin, Base):
    """입금 원장 1행 — 입금(RECEIPT) 또는 그 역기록(REVERSAL). INSERT-only."""

    __tablename__ = "payments"

    #: 입금 거래처(PI의 바이어) — 서비스가 PI에서 복사·일치 검증한다(S3-3 거래처 단위 입금이 같은 열을 쓴다).
    partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    #: S3-1은 PI 입금만 만든다. S3-3이 채권 입금을 넣을 때 NOT NULL을 풀고 `receivable_id`를 더한다.
    pi_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("proforma_invoices.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 부호 있는 금액(최소단위) — RECEIPT > 0, REVERSAL < 0.
    received_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    #: 증빙일(KST 실입금일) — 입력일은 `created_at`(UTC). 미래 불가(서비스), 소급 허용.
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(String(REFERENCE_MAX), nullable=False)
    reverses_payment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 역기록 사유(REVERSAL 필수).
    reason: Mapped[str | None] = mapped_column(String(REASON_MAX), nullable=True)
    recorded_by_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(kind = 'RECEIPT' AND received_amount > 0 AND reverses_payment_id IS NULL"
            " AND reason IS NULL)"
            " OR (kind = 'REVERSAL' AND received_amount < 0 AND reverses_payment_id IS NOT NULL"
            f" AND reason IS NOT NULL AND char_length(btrim(reason)) >= {REASON_MIN})",
            name="kind_sign",
        ),
        CheckConstraint("kind IN ('RECEIPT', 'REVERSAL')", name="kind_valid"),
        CheckConstraint("received_currency = upper(received_currency)", name="currency_upper"),
        CheckConstraint("btrim(reference) <> ''", name="reference_not_blank"),
        CheckConstraint(
            f"abs(received_amount) <= {MAX_MINOR_AMOUNT}", name="received_amount_range"
        ),
        # 복합 FK의 대상 — 역기록은 같은 PI·같은 통화의 원 입금만 가리킨다.
        UniqueConstraint(
            "id", "pi_id", "received_currency", name="uq_payments_id_pi_id_received_currency"
        ),
        ForeignKeyConstraint(
            ["reverses_payment_id", "pi_id", "received_currency"],
            ["payments.id", "payments.pi_id", "payments.received_currency"],
            name="fk_payments_reverses_same_pi_currency",
            ondelete="RESTRICT",
        ),
        # 한 입금은 한 번만 역기록한다(삭제가 없어 deleted_at 술어 불필요).
        Index(
            "uq_payments_reverses_payment_id",
            "reverses_payment_id",
            unique=True,
            postgresql_where=text("reverses_payment_id IS NOT NULL"),
        ),
        Index("ix_payments_pi", "pi_id", "id"),
        Index("ix_payments_partner_id", "partner_id"),
        Index("ix_payments_recorded_by_id", "recorded_by_id"),
    )
