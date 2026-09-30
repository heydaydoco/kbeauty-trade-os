"""J. 안전 계약 — 금액은 정수 최소단위 (DESIGN.md §2 ADR-02, GC-G1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import (
    CurrencyMismatchError,
    Money,
    UnknownCurrencyError,
    minor_units,
)

pytestmark = pytest.mark.group_j


def test_usd_two_decimals() -> None:
    """USD 12.34는 최소단위 1234로 저장된다"""
    money = Money.from_decimal("12.34", "USD")
    assert money.amount == 1234
    assert money.to_decimal() == Decimal("12.34")


def test_krw_no_decimals() -> None:
    """KRW는 소수가 없어 5000이 그대로 5000이다"""
    assert Money.from_decimal("5000", "KRW").amount == 5000


def test_gc_g1_customs_and_vat_are_exact() -> None:
    """GC-G1: 관세 533.00·부가세 873.30이 오차 없이 계산된다 (float 금지)"""
    # CIF 8200.00 × 6.5% = 533.00
    customs = Money.from_decimal(Decimal("8200.00") * Decimal("0.065"), "USD")
    assert customs.amount == 53300
    assert customs.to_decimal() == Decimal("533.00")
    # (8200 + 533) × 10% = 873.30
    vat = Money.from_decimal((Decimal("8200.00") + customs.to_decimal()) * Decimal("0.10"), "USD")
    assert vat.amount == 87330
    assert vat.to_decimal() == Decimal("873.30")
    # 세액 합계 1406.30
    assert (customs + vat).to_decimal() == Decimal("1406.30")


def test_half_up_rounding() -> None:
    """반올림은 반올림(ROUND_HALF_UP)이다"""
    assert Money.from_decimal("1.005", "USD").amount == 101  # 1.01


def test_unknown_currency_is_rejected() -> None:
    """등록되지 않은 통화는 거부된다 — 소수 자릿수를 모르면 환산이 틀린다"""
    with pytest.raises(UnknownCurrencyError):
        minor_units("XXX")


def test_cannot_add_different_currencies() -> None:
    """통화가 다른 금액은 그냥 더할 수 없다"""
    with pytest.raises(CurrencyMismatchError):
        Money(100, "USD") + Money(100, "KRW")


def test_float_amount_rejected() -> None:
    """금액에 float을 넣을 수 없다"""
    with pytest.raises(TypeError):
        Money(12.34, "USD")  # type: ignore[arg-type]


# --- S3-1 PR-2: parse_minor_amount (ADR-0056) ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "currency", "expected"),
    [
        ("12.34", "USD", 1234),
        ("12.340", "USD", 1234),  # 끝자리 0은 정확한 값이다
        ("1,234.5", "USD", 123450),
        ("1000", "KRW", 1000),
        ("0", "USD", 0),
        (12, "JPY", 12),
    ],
)
def test_parse_minor_amount_accepts_exact_values(raw: object, currency: str, expected: int) -> None:
    from app.core.money import parse_minor_amount

    assert parse_minor_amount(raw, currency, field="단가") == expected


@pytest.mark.parametrize(
    ("raw", "currency"),
    [
        ("12.345", "USD"),  # 자릿수 초과는 반올림하지 않고 거부
        ("1000.5", "KRW"),
        ("-1", "USD"),
        ("abc", "USD"),
        ("NaN", "USD"),
        ("Infinity", "USD"),
        ("9" * 16, "KRW"),
        ("", "USD"),
        ("1e3", "KRW"),  # 지수 표기
        ("1E+999999999", "KRW"),  # 거대 지수 — 정수를 만들기 전에 거부
        ("1_000", "KRW"),
        ("12,34", "USD"),  # 유럽식 소수점 콤마 — 100배가 되어 조용히 통과하면 안 된다
        ("1,23,456", "KRW"),
        ("1.00000000000000000000000000000000001", "USD"),  # 정밀도 반올림으로 1이 되면 안 된다
    ],
)
def test_parse_minor_amount_rejects_inexact_or_invalid(raw: object, currency: str) -> None:
    from app.core.money import parse_minor_amount

    with pytest.raises(ValueError, match="단가"):
        parse_minor_amount(raw, currency, field="단가")
