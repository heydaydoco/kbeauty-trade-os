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
