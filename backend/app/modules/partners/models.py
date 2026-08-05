"""거래처·바이어 품번 매핑·서명권자 (DESIGN.md §4.6·§4.7 / WBS S1-3 / ADR-0026).

■ 유형은 교차 테이블이다 (ADR-0026 / [M1] 보강(S1-3) ①)

  한 거래처가 공급사이면서 포워더다(§4.6 "다중 유형"). 불리언 10개로 펼치면
  유형 추가마다 스키마가 흔들리고, 배열 컬럼은 FK·CHECK 규율(ADR-0003)과 안
  맞는다. §7.7(DG 미취급 파트너 소싱 제외)·§10.4(소싱 후보)의 유형 필터가
  이 교차 테이블 조인으로 선다.

■ 여신한도는 원가가 아니다 (마스킹 원장 2번 — 웹 세션 판정 2026-08-05)

  ADR-0018·0024의 보호 대상은 원가·마진이고, 여신한도는 수주 게이트(§7.10)가
  소비하는 운영 필요 데이터라 마스킹하지 않는다. 컬럼 이름을 금액 규약
  (`_amount` 접미 + 통화 쌍)으로 지어 K 아키텍처 검사(Float 금지·유니크 키
  금액 금지)에 **자동 편입**시킨다. 재판정 트리거: 여신 게이트 도입(S3-1)
  또는 역할 세분화 시.

■ signatories는 최소 헤더다 ([M1] 보강(S1-3) ④)

  §4.7이 "서명권자 대장 signatories"라고만 하고 컬럼 명세가 없다 — 추측 구현
  금지. 상세(서명 이미지·재직 기간 등)는 소비 세션(S4-4 법정 대장)이 재판정한다.
"""

from __future__ import annotations

from sqlalchemy import CHAR, BigInteger, Boolean, CheckConstraint, ForeignKey, String, Text
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

#: 거래처 유형 — §4.6 열거 10종과 1:1이다(순서도 문면 순).
#: 공급사/OEM/바이어/RP(EU 등 책임자)/IOR(수입자 기록)/경내책임자(중국)/
#: 포워더/관세사/3PL/인증대행.
PARTNER_TYPES = (
    "SUPPLIER",
    "OEM",
    "BUYER",
    "RP",
    "IOR",
    "DOMESTIC_RP",
    "FORWARDER",
    "CUSTOMS_BROKER",
    "THREE_PL",
    "CERT_AGENCY",
)


class Partner(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """거래처 마스터 (§3 [M1] partners / §4.6)."""

    __tablename__ = "partners"

    #: 사내 거래처 코드. 사람이 정한다(brands·materials와 같은 관례).
    partner_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 여신한도(§4.6). 정수 최소단위+통화 쌍(ADR-0003). NULL = 여신 관리 안 함.
    credit_limit_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credit_limit_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    #: DG 취급 가능 여부(§4.6 — 포워더·3PL에 의미). NULL = 미확인이다 —
    #: false(불가 확인)와 구분해야 §7.7의 소싱 제외가 보수적으로 선다.
    dg_capable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: 강점/약점 메모(§4.6 문면 그대로 두 칸).
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    weaknesses: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # 금액과 통화는 한 쌍이다(§2 ADR-02 — product_boms.cost_currency_pair와 같은 꼴).
        CheckConstraint(
            "(credit_limit_amount IS NULL AND credit_limit_currency IS NULL)"
            " OR (credit_limit_amount IS NOT NULL AND credit_limit_currency IS NOT NULL)",
            name="credit_limit_pair",
        ),
        CheckConstraint("credit_limit_amount >= 0", name="credit_limit_amount_nonnegative"),
        CheckConstraint(
            "credit_limit_currency = upper(credit_limit_currency)",
            name="credit_limit_currency_uppercase",
        ),
        unique_active("partners", "partner_code"),
    )


class PartnerTypeLink(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """거래처×유형 (ADR-0026). 유형 해제는 soft delete — 재부여는 신규다(§17.4)."""

    __tablename__ = "partner_type_links"

    partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    type_code: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        value_in("type_code", PARTNER_TYPES),
        unique_active("partner_type_links", "partner_id", "type_code"),
    )


class CustomerItemCode(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """바이어 품번 매핑 (§4.6 customer_item_codes).

    유일키는 (거래처, 바이어 품번)이다([M1] 보강(S1-3) ③) — 같은 품번이 두
    SKU를 가리키면 §7.4 인테이크의 품번→SKU 결정이 모호해진다. 같은 SKU에
    복수 품번은 허용한다(바이어측 리뉴얼 실무).
    """

    __tablename__ = "customer_item_codes"

    partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    buyer_item_code: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (unique_active("customer_item_codes", "partner_id", "buyer_item_code"),)


class Signatory(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """서명권자 대장 — 최소 헤더 (§4.7 / [M1] 보강(S1-3) ④).

    자연 유일키가 없다(동명이인은 실무다). 더블클릭·재시도는 §17.4의
    idempotency key가 흡수하고, 상세 명세는 S4-4(법정 대장)가 재판정한다.
    """

    __tablename__ = "signatories"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
