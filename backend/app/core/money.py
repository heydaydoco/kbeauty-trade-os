"""금액 — 정수 최소단위 + 통화코드 (DESIGN.md §2 ADR-02, GC-G1).

float은 쓰지 않는다. GC-G1의 관세 533.00 / 부가세 873.30 같은 값이 float에서
0.1센트씩 어긋나기 시작하면 회계·정산 대사에서 원인을 찾을 수 없다.
아키텍처 테스트가 app/** 안의 Float 컬럼 선언을 0건으로 강제한다.

저장은 정수 최소단위(원, 센트)로 한다. USD 12.34 → 1234, KRW 5000 → 5000.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import CHAR, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

#: 통화별 소수 자릿수. 새 통화를 쓰기 전에 반드시 여기에 추가한다.
CURRENCY_MINOR_UNITS: dict[str, int] = {
    "KRW": 0,
    "JPY": 0,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CNY": 2,
    "HKD": 2,
    "SGD": 2,
    "AUD": 2,
    "CAD": 2,
    "VND": 0,
    "IDR": 2,
    "THB": 2,
    "TWD": 2,
    "MYR": 2,
    "PHP": 2,
    "AED": 2,
    "SAR": 2,
}


class UnknownCurrencyError(ValueError):
    pass


class CurrencyMismatchError(ValueError):
    pass


def minor_units(currency: str) -> int:
    code = currency.upper()
    if code not in CURRENCY_MINOR_UNITS:
        raise UnknownCurrencyError(
            f"통화 코드 {code!r}의 소수 자릿수가 등록돼 있지 않습니다. "
            "app/core/money.py의 CURRENCY_MINOR_UNITS에 추가하세요."
        )
    return CURRENCY_MINOR_UNITS[code]


@dataclass(frozen=True, slots=True)
class Money:
    """정수 최소단위로 표현한 금액."""

    amount: int
    currency: str

    def __post_init__(self) -> None:
        minor_units(self.currency)  # 미등록 통화면 여기서 실패
        if not isinstance(self.amount, int):
            raise TypeError("금액은 정수 최소단위여야 합니다(float 금지).")

    @classmethod
    def from_decimal(cls, value: Decimal | str | int, currency: str) -> Money:
        """사람이 쓰는 표기(12.34)를 최소단위(1234)로 바꾼다."""
        exponent = minor_units(currency)
        quantum = Decimal(1).scaleb(-exponent)
        quantized = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        return cls(int(quantized.scaleb(exponent)), currency.upper())

    def to_decimal(self) -> Decimal:
        return Decimal(self.amount).scaleb(-minor_units(self.currency))

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"통화가 다른 금액은 그대로 더할 수 없습니다: {self.currency} vs {other.currency}. "
                "환율을 적용해 같은 통화로 맞춘 뒤 계산하세요."
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.to_decimal()} {self.currency}"


def money_columns(prefix: str) -> tuple[Mapped[int], Mapped[str]]:
    """금액 컬럼 한 쌍(금액+통화)을 만든다.

        total_amount, total_currency = money_columns("total")

    금액만 있고 통화가 없는 컬럼을 만들 수 없게 하려고 한 쌍으로 묶는다.
    """
    return (
        mapped_column(f"{prefix}_amount", BigInteger, nullable=False),
        mapped_column(f"{prefix}_currency", CHAR(3), nullable=False),
    )
