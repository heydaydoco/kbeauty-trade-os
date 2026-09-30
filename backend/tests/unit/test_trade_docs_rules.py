"""A. 전표 커널의 순수 규칙 — 결제조건·Incoterms·환율·유효기간·수량/금액 경계 (S3-1 ADR-0055·0056 / design-A A5~A9).

DB가 필요 없는 규칙이다. 경계값(±1)과 성공/거부 양방향을 함께 시험하고, 무작위 대량으로 산식 불변식(합 일치)을 고정한다.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.trade_docs import fx
from app.modules.trade_docs.constants import MAX_QUANTITY, MAX_SAFE_INTEGER, DocKind
from app.modules.trade_docs.editing import compute_total
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.incoterms import build_incoterm
from app.modules.trade_docs.payment_terms import (
    advance_pct_text,
    build_payment_terms,
    parse_advance_pct,
    split_advance,
)
from app.modules.trade_docs.snapshot import line_amount, validate_quantity

pytestmark = pytest.mark.group_a


@pytest.mark.parametrize(
    ("raw", "bp"),
    [
        ("30", 3000),
        ("33.33", 3333),
        ("0.01", 1),
        ("100", 10000),
        ("100.00", 10000),
        (" 5 ", 500),
        ("2.5", 250),
    ],
)
def test_advance_percent_parses_exactly_to_basis_points(raw: str, bp: int) -> None:
    """퍼센트 문자열은 반올림 없이 정확히 bp로 — 소수 2자리까지"""
    assert parse_advance_pct(raw) == bp
    assert parse_advance_pct(advance_pct_text(bp)) == bp  # 표시 ↔ 입력 왕복


@pytest.mark.parametrize(
    "raw", ["0", "0.00", "100.01", "33.333", "abc", "-5", "1e2", "5,5", "", "1.", ".5", "1000"]
)
def test_advance_percent_rejects_out_of_range_and_over_precise_values(raw: str) -> None:
    """0 이하·100 초과·소수 3자리·지수 표기·콤마는 거부(반올림해 받아 주지 않는다)"""
    with pytest.raises(AppError) as caught:
        parse_advance_pct(raw)
    assert caught.value.status_code == 422


def test_advance_text_formats_without_trailing_zeros() -> None:
    """표시 문자열: 끝의 0과 소수점 제거"""
    assert [advance_pct_text(v) for v in (3000, 3333, 1, 10000, 250, None)] == [
        "30", "33.33", "0.01", "100", "2.5", None,
    ]  # fmt: skip


def test_split_advance_sums_to_the_total_and_rounds_half_up() -> None:
    """선수금=HALF_UP, 잔금=총액−선수금 — 무작위 2000건에서 합이 항상 총액과 같다(홀수 금액·경계 포함)"""
    assert split_advance(5, 5000) == (3, 2)  # 2.5 → 3
    assert split_advance(1, 4999) == (0, 1)  # 0.4999 → 0
    assert split_advance(1, 5000) == (1, 0)  # 0.5 → 1
    assert split_advance(10001, 3333) == (3333, 6668)
    assert (
        split_advance(MAX_SAFE_INTEGER, 9999)[0] + split_advance(MAX_SAFE_INTEGER, 9999)[1]
        == MAX_SAFE_INTEGER
    )
    rng = random.Random(7)
    for _ in range(2000):
        total, bp = rng.randint(0, MAX_SAFE_INTEGER), rng.randint(1, 10000)
        advance, balance = split_advance(total, bp)
        assert advance + balance == total and 0 <= advance <= total
    for bad in (0, 10001, -1):
        with pytest.raises(ValueError):
            split_advance(100, bad)


def test_payment_terms_shapes_and_the_receipt_anchor_rule() -> None:
    """결제조건 형태 — 100% 선수금은 기산점 없음·부분 선수금은 기산점 필수·후불은 기산점 필수·L/C는 전부 없음·입고일은 PO 전용"""
    ok_full = build_payment_terms({"payment_type": "TT_ADVANCE", "advance_pct": "100"})
    assert (
        ok_full is not None and ok_full.advance_pct_bp == 10000 and ok_full.balance_anchor is None
    )
    partial = build_payment_terms(
        {
            "payment_type": "TT_ADVANCE",
            "advance_pct": "30",
            "balance_anchor": "ETD_DATE",
            "balance_days": -14,
        }
    )
    assert partial is not None and partial.balance_days == -14
    lc = build_payment_terms({"payment_type": "LC"})
    assert lc is not None and lc.columns() == {
        "payment_type": "LC", "advance_pct_bp": None, "balance_anchor": None, "balance_days": None,
    }  # fmt: skip
    assert build_payment_terms(None) is None
    receipt = {"payment_type": "TT_DEFERRED", "balance_anchor": "RECEIPT_DATE", "balance_days": 30}
    with pytest.raises(AppError):
        build_payment_terms(receipt)  # 판매 체인에서는 거부
    assert build_payment_terms(receipt, allow_receipt_anchor=True) is not None  # PO에서는 허용
    for bad in (
        {"payment_type": "LC", "balance_days": 3},
        {"payment_type": "LC", "advance_pct": "10"},
        {
            "payment_type": "TT_DEFERRED",
            "advance_pct": "10",
            "balance_anchor": "BL_DATE",
            "balance_days": 1,
        },
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE"},
        {"payment_type": "TT_ADVANCE", "advance_pct": "50", "balance_anchor": "BL_DATE"},
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE", "balance_days": True},
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE", "balance_days": 366},
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE", "balance_days": -91},
        {"payment_type": "TT_DEFERRED", "balance_anchor": "ARRIVAL_DATE", "balance_days": -1},
        {"payment_type": None},
    ):
        with pytest.raises(AppError):
            build_payment_terms(bad)
    boundary = build_payment_terms(
        {"payment_type": "TT_DEFERRED", "balance_anchor": "ETD_DATE", "balance_days": -90}
    )
    assert boundary is not None and boundary.balance_days == -90


def test_incoterm_versions_and_place_rules() -> None:
    """Incoterms: 12코드·연도 2010/2020·DAT는 2010만·DPU는 2020만·장소는 비공백 100자 이하 제어문자 없음"""
    for code in ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"):
        assert build_incoterm({"code": code, "place": "Busan", "year": 2020}) is not None
    assert build_incoterm({"code": "DAT", "place": "Busan", "year": 2010}) is not None
    assert build_incoterm({"code": "FOB", "place": " Busan "}).place == "Busan"  # type: ignore[union-attr]
    assert build_incoterm({"code": "FOB", "place": "Busan"}).year == 2020  # type: ignore[union-attr]
    assert build_incoterm(None) is None
    for bad in (
        {"code": "DAT", "place": "Busan", "year": 2020},
        {"code": "DPU", "place": "Busan", "year": 2010},
        {"code": "fob", "place": "Busan"},
        {"code": "FOB", "place": ""},
        {"code": "FOB", "place": "x" * 101},
        {"code": "FOB", "place": "a\tb"},
        {"code": "FOB", "place": "Busan", "year": 2015},
        {"code": "FOB", "place": "Busan", "year": True},
    ):
        with pytest.raises(AppError):
            build_incoterm(bad)
    assert build_incoterm({"code": "FOB", "place": "x" * 100}) is not None  # 경계 100자


def test_fx_resolution_rules() -> None:
    """환율: KRW=1·기준일=증빙일 강제 · 비KRW는 8자리·0<rate≤1,000,000·기준일은 증빙일 이후 불가 · rate 없으면 (None, None)"""
    doc = date(2026, 9, 10)
    assert fx.resolve_fx("KRW", None, None, doc) == (Decimal(1), doc)
    assert fx.resolve_fx("KRW", "1", date(2026, 1, 1), doc) == (Decimal(1), doc)
    assert fx.resolve_fx("USD", None, None, doc) == (None, None)
    assert fx.resolve_fx("USD", "1350.12345678", None, doc) == (Decimal("1350.12345678"), doc)
    assert fx.resolve_fx("USD", "1350", date(2026, 9, 1), doc) == (Decimal(1350), date(2026, 9, 1))
    assert fx.resolve_fx("USD", "1000000", None, doc)[0] == Decimal(1_000_000)
    for rate, rate_date, cur in [
        ("2", None, "KRW"), ("0", None, "USD"), ("-1", None, "USD"), ("1000000.01", None, "USD"),
        ("1.123456789", None, "USD"), ("1e3", None, "USD"), ("", None, "USD"),
        ("1350", date(2026, 9, 11), "USD"), (None, date(2026, 9, 1), "USD"),
        ("abc", None, "KRW"), ("1e2", None, "KRW"), ("", None, "KRW"), ("１", None, "KRW"),
        ("１", None, "USD"), ("NaN", None, "KRW"), (" ", None, "KRW"),
    ]:  # fmt: skip
        with pytest.raises(AppError):
            fx.resolve_fx(cur, rate, rate_date, doc)
    for bad_krw in ("abc", "１", "1e2", ""):  # KRW도 형식 오류는 500이 아니라 422(검증 오류)
        with pytest.raises(AppError) as info:
            fx.resolve_fx("KRW", bad_krw, None, doc)
        assert info.value.code == ErrorCode.VALIDATION_INVALID_FIELD
    assert fx.resolve_fx("KRW", "1.0", None, doc) == (Decimal(1), doc)
    with pytest.raises(AppError):
        fx.require_known_currency("ZZZ")
    assert fx.require_known_currency(" usd ") == "USD"


def test_krw_conversion_is_half_up_and_comparison_failure_is_none_not_zero() -> None:
    """to_krw는 HALF_UP · comparable_amount는 동일 통화·KRW 환산 외에는 None(0이나 통과로 대체하지 않는다)"""
    assert fx.to_krw(1234, "USD", Decimal("1350.5")).amount == 16665  # 12.34 × 1350.5 = 16665.17
    assert fx.to_krw(1, "USD", Decimal(50)).amount == 1  # 0.01 × 50 = 0.5 → 1
    assert fx.to_krw(1, "USD", Decimal("49.99")).amount == 0
    assert fx.to_krw(1000, "JPY", Decimal("9.5")).amount == 9500  # 0자리 통화
    assert fx.comparable_amount(500, "USD", Decimal(1300), "USD") == 500
    assert fx.comparable_amount(500, "USD", Decimal(1300), "KRW") == 6500
    assert fx.comparable_amount(500, "USD", None, "KRW") is None  # 환율 없음
    assert fx.comparable_amount(500, "EUR", Decimal(1300), "USD") is None  # 제3통화
    assert fx.comparable_amount(500, "KRW", None, "USD") is None  # 역환산 금지
    assert fx.comparable_amount(500, "KRW", Decimal(1), "KRW") == 500


def test_lapse_boundaries_and_states() -> None:
    """유효기간은 당일 24:00까지 — valid_until==오늘은 유효, 어제는 경과 · QT는 발행·전환 상태, PI는 미입금 발행만"""
    today = date(2026, 9, 30)
    yesterday, tomorrow = date(2026, 9, 29), date(2026, 10, 1)
    assert is_lapsed(DocKind.QUOTATION, "ISSUED", today, today) is False
    assert is_lapsed(DocKind.QUOTATION, "ISSUED", yesterday, today) is True
    assert is_lapsed(DocKind.QUOTATION, "CONVERTED", yesterday, today) is True
    assert is_lapsed(DocKind.QUOTATION, "ISSUED", tomorrow, today) is False
    for status in ("DRAFT", "EXPIRED", "CANCELLED"):
        assert is_lapsed(DocKind.QUOTATION, status, yesterday, today) is False
    assert is_lapsed(DocKind.QUOTATION, "ISSUED", None, today) is False
    assert is_lapsed(DocKind.PROFORMA_INVOICE, "ISSUED", yesterday, today) is True
    for status in (
        "PARTIALLY_PAID",
        "PAID",
    ):  # 입금으로 수락이 이행됐다 — 경과가 SO 생성을 막지 않는다
        assert is_lapsed(DocKind.PROFORMA_INVOICE, status, yesterday, today) is False
    assert is_lapsed(DocKind.SALES_ORDER, "RECEIVED", yesterday, today) is False


def test_quantity_and_amount_limits() -> None:
    """수량 1..99,999,999(EA 정수) · 라인금액 ≤ 2^53−1 · 합계 상한 — 경계 ±1"""
    assert validate_quantity(1) == 1 and validate_quantity(MAX_QUANTITY) == MAX_QUANTITY
    for bad in (0, -1, MAX_QUANTITY + 1, 1.5, "3", True, None):
        with pytest.raises(AppError):
            validate_quantity(bad)
    assert line_amount(MAX_QUANTITY, 90_071_992) == MAX_QUANTITY * 90_071_992  # 상한 이내
    assert line_amount(1, MAX_SAFE_INTEGER) == MAX_SAFE_INTEGER
    with pytest.raises(AppError) as caught:
        line_amount(2, MAX_SAFE_INTEGER)
    assert caught.value.code == "TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE"
    assert compute_total([MAX_SAFE_INTEGER - 1, 1]) == MAX_SAFE_INTEGER
    with pytest.raises(AppError):
        compute_total([MAX_SAFE_INTEGER, 1])
