"""자사 은행 계좌 마스터 (S3-1 design-integrated §2.1(b) / design-A A8 / ADR-0056).

PI(선수금 청구서)가 발행 시점에 은행정보 6열을 **값 복사**하는 원천이다 — 계좌를 이후 수정·비활성해도 발행된 PI는
불변이다(스냅샷). 초기 행은 화면/API로만 등록한다(마이그레이션 시드 금지 — 함정 ⑩).

■ `account_no`는 어떤 유니크 키에도 넣지 않는다(`test_secret_boundaries`) — 중복 등록 방지는 서비스가
  (정규화 계좌번호, SWIFT)로 검사한다(경합 잔여 위험은 ADMIN 전용 저빈도라 관찰 등재).
■ 비활성 = soft delete(deleted_at). 사용 중인 PI가 FK RESTRICT로 계좌를 참조하지만 soft delete라 영향 없다.
"""

from __future__ import annotations

from sqlalchemy import CHAR, CheckConstraint, String
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
from app.modules.trade_docs.constants import SWIFT_PATTERN


class BankAccount(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """자사 수취 계좌 — 통화별로 등록한다(PI 통화와 같은 통화 계좌만 선택 가능)."""

    __tablename__ = "bank_accounts"

    label: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    beneficiary_name: Mapped[str] = mapped_column(String(200), nullable=False)
    beneficiary_address: Mapped[str] = mapped_column(String(300), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_address: Mapped[str] = mapped_column(String(300), nullable=False)
    account_no: Mapped[str] = mapped_column(String(40), nullable=False)
    swift_code: Mapped[str] = mapped_column(String(11), nullable=False)

    __table_args__ = (
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint(f"swift_code ~ '{SWIFT_PATTERN}'", name="swift_format"),
        CheckConstraint(
            "btrim(label) <> '' AND btrim(beneficiary_name) <> '' AND btrim(beneficiary_address) <> ''"
            " AND btrim(bank_name) <> '' AND btrim(bank_address) <> '' AND btrim(account_no) <> ''",
            name="text_not_blank",
        ),
        unique_active("bank_accounts", "label"),
    )
