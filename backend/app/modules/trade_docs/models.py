"""상태이력 표 — 전표별 4표, 불변 (S3-1 ADR-0051 / design-B B6 / DESIGN §17.5 확장).

문서별 테이블이다(다형 단일 테이블 기각 — FK RESTRICT 불가·상태값 CHECK가 합집합으로 약화). 이 파일은 각
전표 PR이 자기 `<doc>_status_log`를 추가하는 자리다(certification_status_log 계보). 구조 중복은
`StatusLogColumns`가 제거한다.

■ IMMUTABLE — 앱 계정은 INSERT/SELECT만 한다(`revoke_mutations`). 정정은 새 행이다.
■ PkMixin+Base만 — Version·Actor·SoftDelete 믹스인 제외(함정 ⑩: 불변 표에 Actor를 달면 users FK가
  TRUNCATE CASCADE로 PRESERVED_TABLES를 무력화한다). 행위자는 `actor_user_id` 한 컬럼으로 명시한다.
■ `automatic`은 **명시 컬럼**이다(certification_status_log는 actor NULL에서 파생) — 입금 수렴·QT 연쇄는
  "자동이지만 행위자(입금 기록자·후속 생성자)가 있다".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    desc,
    func,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import INITIAL_STATUS, REASON_REQUIRED_TO, STATUSES


class StatusLogColumns:
    """상태이력 공통 열(문서 FK는 전표별 클래스가 명시한다)."""

    #: 발생 시각(UTC) — server now. 애플리케이션 시계가 아니라 DB 시계다.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: NULL = 생성(탄생) 행.
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: 사유(자유 텍스트 — 이벤트 payload·로그에 싣지 않는다).
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: NULL = 시스템(스윕).
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False)

    @declared_attr
    def actor_user_id(cls) -> Mapped[int | None]:
        return mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)


def status_log_checks(kind: DocKind) -> list[CheckConstraint]:
    """상태이력 CHECK 6종(+사유 길이) — 서비스를 우회한 직접 INSERT도 DB가 거부한다."""
    states = ", ".join(f"'{s}'" for s in sorted(STATUSES[kind]))
    reason_states = ", ".join(f"'{s}'" for s in sorted(REASON_REQUIRED_TO[kind]))
    return [
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({states})", name="from_status_valid"
        ),
        CheckConstraint(f"to_status IN ({states})", name="to_status_valid"),
        CheckConstraint(
            "from_status IS NULL OR from_status <> to_status", name="no_self_transition"
        ),
        CheckConstraint(
            f"from_status IS NOT NULL OR to_status = '{INITIAL_STATUS[kind]}'", name="birth_row"
        ),
        CheckConstraint(
            f"to_status NOT IN ({reason_states}) OR reason IS NOT NULL", name="reason_required"
        ),
        CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500", name="reason_not_blank"
        ),
        CheckConstraint("actor_user_id IS NOT NULL OR automatic", name="actor_or_automatic"),
    ]


class QuotationStatusLog(StatusLogColumns, PkMixin, Base):
    """견적 상태 변경 이력 — 불변."""

    __tablename__ = "quotation_status_log"

    quotation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        *status_log_checks(DocKind.QUOTATION),
        # 문서당 탄생 행은 하나다.
        Index(
            "uq_quotation_status_log_quotation_id_birth",
            "quotation_id",
            unique=True,
            postgresql_where=text("from_status IS NULL"),
        ),
        Index("ix_quotation_status_log_quotation_id_id", "quotation_id", desc("id")),
    )


class ProformaInvoiceStatusLog(StatusLogColumns, PkMixin, Base):
    """PI 상태 변경 이력 — 불변. 입금 수렴·만료 스윕은 자동(`automatic=true`)이고 행위자는 유발자거나 NULL(스윕)이다."""

    __tablename__ = "proforma_invoice_status_log"

    proforma_invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("proforma_invoices.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        *status_log_checks(DocKind.PROFORMA_INVOICE),
        Index(
            "uq_proforma_invoice_status_log_proforma_invoice_id_birth",
            "proforma_invoice_id",
            unique=True,
            postgresql_where=text("from_status IS NULL"),
        ),
        Index(
            "ix_proforma_invoice_status_log_proforma_invoice_id_id",
            "proforma_invoice_id",
            desc("id"),
        ),
    )


#: 전표별 상태이력 모델 — 각 전표 PR이 자기 표를 여기 등록한다(record_birth/record_transition이 소비).
STATUS_LOG_MODELS: dict[DocKind, type[Any]] = {
    DocKind.QUOTATION: QuotationStatusLog,
    DocKind.PROFORMA_INVOICE: ProformaInvoiceStatusLog,
}
