"""전표 헤더·라인 공통 컬럼과 CHECK 생성기 (S3-1 design-integrated §2.1 (a)(c) / design-A A2).

컬럼은 믹스인으로, CHECK는 함수로 준다 — 4종 전표가 같은 규칙을 **한 곳에서** 정의해야 조용한 분기가
없다. CHECK는 `create_table` 안에 `op.f()` 이름으로 넣고(alembic check가 못 보므로) 정의문 테스트
(`pg_get_constraintdef`)가 고정한다(함정 ①·⑪). 이름은 63자 이내다(unique_active 규칙과 같은 이유).

금액 열은 **손으로 선언**한다(`money_columns()` 미사용 — design-A 검증 #10): 판매 체인 `*_amount`(BIGINT)+
`currency`(CHAR(3)), 라인도 자기 `currency`를 갖고 헤더와 복합 FK로 묶인다(혼합 통화 불가능).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.modules.trade_docs.constants import (
    DOC_PREFIXES,
    INCOTERM_YEARS,
    MAX_QUANTITY,
    MAX_SAFE_INTEGER,
    SALES_PRICE_BASES,
    SKU_KINDS,
    BalanceAnchor,
    DocKind,
    IncotermCode,
    PaymentType,
)
from app.modules.trade_docs.machine import STATUSES


def _in_list(values: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class TradeHeaderMixin:
    """QT·PI·SO·PO 헤더 공통 열. 합계 열·거래 상대 열은 전표별 모델이 정의한다."""

    doc_number: Mapped[str] = mapped_column(String(20), nullable=False)
    #: 증빙일(KST 업무일). 미래 422·소급 허용(§21).
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: 상태 대입은 transition.record_birth/record_transition 한 통로뿐이다(생성자에 status를 넘기지 않는다).
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    #: 환율 스냅샷 — "전표 통화 1단위 = x KRW"(한 방향만). KRW는 1 고정(ADR-0055).
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fx_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    payment_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    #: 선수금 비율(basis point, 1%=100). API는 퍼센트 문자열을 주고받고 프런트 산술은 0이다.
    advance_pct_bp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_anchor: Mapped[str | None] = mapped_column(String(16), nullable=True)
    balance_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    incoterm_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    incoterm_place: Mapped[str | None] = mapped_column(String(100), nullable=True)
    incoterm_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    #: 내부 메모(서류에 출력하지 않음) — FREE 열(동결 후에도 수정 가능).
    internal_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: 라인 번호 카운터 — 헤더 행 잠금 하에서만 +1, 결번 허용·재사용 금지.
    last_line_no: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )

    @declared_attr
    def assignee_id(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        )

    @declared_attr
    def copied_from_id(cls) -> Mapped[int | None]:
        """복제 원본(계보 표시 — 사슬 후속이 아니다, X-08). 4종 공통 자기참조."""
        return mapped_column(
            BigInteger,
            ForeignKey(f"{cls.__tablename__}.id", ondelete="RESTRICT"),  # type: ignore[attr-defined]
            nullable=True,
        )


class SalesHeaderMixin(TradeHeaderMixin):
    """판매 체인(QT·PI·SO) 공통 — 합계·바이어·목적지 시장."""

    total_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )

    @declared_attr
    def buyer_partner_id(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
        )

    #: 바이어 표기 스냅샷(기본 COALESCE(name_en, name_ko)) — 서류는 이 열만 읽는다.
    buyer_name: Mapped[str] = mapped_column(String(200), nullable=False)

    @declared_attr
    def dest_market_code(cls) -> Mapped[str]:
        return mapped_column(
            String(2), ForeignKey("markets.code", ondelete="RESTRICT"), nullable=False
        )


class SalesLineMixin:
    """판매 라인(QT·PI·SO) 공통 열 — 값은 전부 스냅샷(마스터 재조회 금지)."""

    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    @declared_attr
    def sku_id(cls) -> Mapped[int]:
        return mapped_column(BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False)

    sku_code: Mapped[str] = mapped_column(String(40), nullable=False)
    sku_name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    sku_name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sku_kind: Mapped[str] = mapped_column(String(6), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_item_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_price_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 라인 생성 시점의 마스터 표준 판가 스냅샷(단가 편차 게이트가 mutable한 sku_prices를 다시 읽지 않게).
    list_price_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    line_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(10), nullable=False)
    is_free: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    price_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


# ── CHECK 생성기 ─────────────────────────────────────────────────────────────


def header_common_checks(kind: DocKind) -> list[CheckConstraint]:
    """4종 헤더 공통 CHECK — 이름은 규약이 `ck_<테이블>_`을 접두한다."""
    prefix = DOC_PREFIXES[kind]
    total = "total_cost" if kind is DocKind.PURCHASE_ORDER else "total_amount"
    checks = [
        CheckConstraint(f"status IN ({_in_list(STATUSES[kind])})", name="status_valid"),
        CheckConstraint(
            f"doc_number ~ '^{prefix}-[0-9]{{4}}-[0-9]{{4,}}$'", name="doc_number_format"
        ),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint(f"{total} BETWEEN 0 AND {MAX_SAFE_INTEGER}", name="total_range"),
        CheckConstraint("last_line_no >= 0", name="last_line_no_nonnegative"),
        CheckConstraint("fx_rate > 0 AND fx_rate <= 1000000", name="fx_rate_range"),
        CheckConstraint("(fx_rate IS NULL) = (fx_rate_date IS NULL)", name="fx_pair"),
        CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1", name="krw_fx_is_one"
        ),
        CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date", name="fx_date_not_future"
        ),
        CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id", name="copied_from_not_self"
        ),
        # 결제조건 — 4열 형태(A5). 4열 전부 NULL은 동결 전 초안이다(동결 시 NOT NULL은 frozen_complete).
        CheckConstraint(
            f"payment_type IS NULL OR payment_type IN ({_in_list([p.value for p in PaymentType])})",
            name="payment_type_valid",
        ),
        CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN "
            f"({_in_list([a.value for a in BalanceAnchor])})",
            name="balance_anchor_valid",
        ),
        CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365", name="balance_days_range"
        ),
        CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name="neg_days_etd_only",
        ),
        CheckConstraint(
            "(payment_type IS NULL AND advance_pct_bp IS NULL AND balance_anchor IS NULL"
            " AND balance_days IS NULL)"
            " OR (payment_type = 'TT_ADVANCE' AND advance_pct_bp BETWEEN 1 AND 10000"
            " AND ((advance_pct_bp = 10000 AND balance_anchor IS NULL AND balance_days IS NULL)"
            " OR (advance_pct_bp < 10000 AND balance_anchor IS NOT NULL"
            " AND balance_days IS NOT NULL)))"
            " OR (payment_type = 'TT_DEFERRED' AND advance_pct_bp IS NULL"
            " AND balance_anchor IS NOT NULL AND balance_days IS NOT NULL)"
            " OR (payment_type = 'LC' AND advance_pct_bp IS NULL AND balance_anchor IS NULL"
            " AND balance_days IS NULL)",
            name="payment_terms_shape",
        ),
        # Incoterms — 셋 다 NULL이거나 셋 다 NOT NULL(A6). DAT는 2010판에만, DPU는 2020판에만.
        CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN "
            f"({_in_list([c.value for c in IncotermCode])})",
            name="incoterm_code_valid",
        ),
        CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN "
            f"({', '.join(str(y) for y in INCOTERM_YEARS)})",
            name="incoterm_year_valid",
        ),
        CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL)"
            " OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL"
            " AND incoterm_year IS NOT NULL)",
            name="incoterm_all_or_none",
        ),
        CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''", name="incoterm_place_not_blank"
        ),
        CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010", name="incoterm_dat_2010"
        ),
        CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020", name="incoterm_dpu_2020"
        ),
    ]
    return checks


def frozen_complete_check(freeze_column: str, required_clauses: list[str]) -> CheckConstraint:
    """동결 완결성 — 동결 시각이 있으면 서류에 필요한 값이 전부 있어야 한다.

    ★ 상태 리터럴이 아니라 **동결 시각 열에 의존**한다(B의 열거 확정과 독립, X-03).
    """
    body = " AND ".join(
        [
            "payment_type IS NOT NULL",
            "incoterm_code IS NOT NULL",
            "fx_rate IS NOT NULL",
            *required_clauses,
        ]
    )
    return CheckConstraint(f"{freeze_column} IS NULL OR ({body})", name="frozen_complete")


def sales_line_checks() -> list[CheckConstraint]:
    """판매 라인(QT·PI·SO) 공통 CHECK — 라인금액=수량×단가는 **numeric 곱**(bigint 오버플로 500 방지)."""
    return [
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint(f"quantity BETWEEN 1 AND {MAX_QUANTITY}", name="quantity_range"),
        CheckConstraint(f"sku_kind IN ({_in_list(SKU_KINDS)})", name="sku_kind_valid"),
        CheckConstraint(
            f"price_basis IN ({_in_list(SALES_PRICE_BASES)})", name="price_basis_valid"
        ),
        CheckConstraint(
            f"unit_price_amount BETWEEN 0 AND {MAX_SAFE_INTEGER}", name="unit_price_range"
        ),
        CheckConstraint(
            f"list_price_amount IS NULL OR list_price_amount BETWEEN 0 AND {MAX_SAFE_INTEGER}",
            name="list_price_range",
        ),
        CheckConstraint(f"line_amount BETWEEN 0 AND {MAX_SAFE_INTEGER}", name="line_amount_range"),
        CheckConstraint(
            "quantity::numeric * unit_price_amount = line_amount", name="line_amount_matches"
        ),
        # 무상은 명시(양방향): is_free ⇔ 단가 0. 마스터 판가 0을 자동 무상으로 받지 않는다.
        CheckConstraint("is_free = (unit_price_amount = 0)", name="free_iff_zero_price"),
        CheckConstraint(
            "NOT is_free OR (price_reason IS NOT NULL AND btrim(price_reason) <> '')",
            name="free_requires_reason",
        ),
        CheckConstraint(
            "NOT is_free OR price_basis IN ('MANUAL', 'BUYER_PO')", name="free_not_from_master"
        ),
    ]
