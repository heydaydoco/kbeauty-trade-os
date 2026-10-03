"""금액 — 정수 최소단위 + 통화코드 (DESIGN.md §2 ADR-02, GC-G1).

float은 쓰지 않는다. GC-G1의 관세 533.00 / 부가세 873.30 같은 값이 float에서
0.1센트씩 어긋나기 시작하면 회계·정산 대사에서 원인을 찾을 수 없다.
아키텍처 테스트가 app/** 안의 Float 컬럼 선언을 0건으로 강제한다.

저장은 정수 최소단위(원, 센트)로 한다. USD 12.34 → 1234, KRW 5000 → 5000.
"""

from __future__ import annotations

import re
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


#: 금액을 담는 컬럼의 이름 (정확히 일치).
#:
#: ★ "이 컬럼이 돈인가"를 판정하는 **유일한 출처**다. 로그 마스킹의 민감 키
#:   목록(redaction.SENSITIVE_KEYS)과는 다른 개념이라 따로 둔다 — 그쪽은
#:   "가려야 하는가", 이쪽은 "돈인가"다. 예를 들어 판가는 가리지 않지만 돈이다.
#:   두 개념을 한 목록으로 합치면 판가를 가리거나 원가를 노출하게 된다.
MONEY_COLUMN_NAMES: frozenset[str] = frozenset({"amount", "price", "cost", "fee", "total"})

#: 이 접미사로 끝나면 금액 컬럼으로 본다.
MONEY_COLUMN_SUFFIXES: tuple[str, ...] = ("_amount", "_price", "_cost", "_fee", "_total")


def is_money_column_name(name: str) -> bool:
    """컬럼 이름이 금액을 담는 것처럼 보이는가.

    §17.4의 멱등 키·유니크 키에 금액이 들어가면 PostgreSQL이 위반 시
    `DETAIL: Key (…)=(…)`로 그 값을 뱉는다(ADR-0018 ㉠). 그 구조를 만들지
    못하게 하는 아키텍처 테스트가 이 판정을 쓴다.
    """
    lowered = str(name).lower().strip("_")
    return lowered in MONEY_COLUMN_NAMES or lowered.endswith(MONEY_COLUMN_SUFFIXES)


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


_PLAIN_AMOUNT = re.compile(r"^(?P<int>\d+)(?:\.(?P<frac>\d+))?$", re.ASCII)
_GROUPED_AMOUNT = re.compile(
    r"^(?P<int>[1-9]\d{0,2}(?:,\d{3})+)(?:\.(?P<frac>\d+))?$", re.ASCII
)  # re.ASCII: 전각·아랍 숫자 등 비ASCII 숫자는 거부(int()가 받아 값이 조용히 바뀌는 입력 차단).
# 첫 그룹은 0으로 시작하지 않는다('0,500'은 천단위 표기가 아니라 유럽식 소수 '0.500'일 수 있다 — 추측 금지, PR-14a B10).


class AmountFormatError(ValueError):
    """금액 표기 오류 — 서비스는 `str(exc)`를 쪼개지 않고 `.reason`(사용자용 문구)을 쓴다."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def parse_minor_amount(raw: object, currency: str, *, field: str, max_digits: int = 15) -> int:
    """사람 표기 금액("12.34")을 최소단위 정수로 바꾼다 — 자릿수 초과는 반올림 없이 거부한다.

    전표 입력(인테이크·CSV·라인 단가)의 공용 통로다. 12.345를 조용히 12.35로 바꾸면
    바이어가 낸 단가와 다른 금액이 확정되므로 거부하고 사용자가 고치게 한다.
    허용 형식은 `1234`·`1234.5`·`1,234.5`(3자리 그룹 콤마)뿐이다 — 지수 표기·밑줄·
    유럽식 소수점 콤마("12,34")는 값이 조용히 바뀌므로 거부한다(Decimal 연산을 거치지 않고
    문자열에서 바로 정수를 만든다: 컨텍스트 정밀도 반올림·거대 지수 폭주 없음).
    실패는 `AmountFormatError`(ValueError 하위 — `.field`·`.reason` 구조화, str은 "field: reason")이다(서비스가 422로 번역).
    """
    exponent = minor_units(currency)
    text = str(raw).strip()
    if text.startswith("-"):
        raise AmountFormatError(field, "음수는 입력할 수 없습니다.")
    match = _GROUPED_AMOUNT.match(text) or _PLAIN_AMOUNT.match(text)
    if match is None:
        raise AmountFormatError(field, "숫자 형식이 아닙니다(예: 1234.50, 1,234.50).")
    whole = match.group("int").replace(",", "")
    fraction = (match.group("frac") or "").rstrip("0")
    if len(fraction) > exponent:
        raise AmountFormatError(
            field, f"{currency.upper()}는 소수점 {exponent}자리까지만 입력할 수 있습니다."
        )
    minor = int(whole + fraction.ljust(exponent, "0"))
    if len(str(minor)) > max_digits:
        raise AmountFormatError(field, "금액이 너무 큽니다.")
    return minor


def money_columns(prefix: str) -> tuple[Mapped[int], Mapped[str]]:
    """금액 컬럼 한 쌍(금액+통화)을 만든다.

        total_amount, total_currency = money_columns("total")

    금액만 있고 통화가 없는 컬럼을 만들 수 없게 하려고 한 쌍으로 묶는다.
    """
    return (
        mapped_column(f"{prefix}_amount", BigInteger, nullable=False),
        mapped_column(f"{prefix}_currency", CHAR(3), nullable=False),
    )
