"""환율 스냅샷·환산 규약 (S3-1 ADR-0055 / design-A A7).

■ 방향은 하나다 — **"전표 통화 1단위 = x KRW"**(역수 혼동 원천 차단). NUMERIC(18,8)·Decimal 문자열 입출력
  (float 파싱 금지). KRW 전표는 서버가 rate=1·기준일=증빙일을 채운다.
■ `fx_rates` 마스터는 S3-1에서 만들지 않는다(소비자 없는 마스터 금지 — ADR-0055, 재판정 트리거 3종은 PROGRESS).
■ 비교 불가는 **None**이고 None은 통과가 아니다 — 소비자(여신·결재 임계)는 None이면 승인 게이트로 보낸다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.money import CURRENCY_MINOR_UNITS, Money, minor_units

_RATE = re.compile(r"^\d{1,10}(?:\.\d{1,8})?$")
MAX_RATE = Decimal(1_000_000)


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def require_known_currency(currency: str, *, field: str = "currency") -> str:
    """미등록 통화는 422로 선차단 — `price_at`의 UnknownCurrencyError가 500이 되지 않게(A2)."""
    code = str(currency).strip().upper()
    if code not in CURRENCY_MINOR_UNITS:
        raise _invalid(field, "지원하지 않는 통화입니다. 통화 코드를 확인해 주세요.")
    return code


def resolve_fx(
    currency: str, rate_raw: object | None, rate_date: date | None, doc_date: date
) -> tuple[Decimal | None, date | None]:
    """(통화, 입력 환율·기준일, 증빙일) → 저장할 (rate, rate_date).

    KRW는 입력값과 무관하게 (1, 증빙일) — 다른 값이 오면 422. 비KRW는 rate가 있으면 기준일 기본=증빙일,
    기준일은 증빙일 이후일 수 없다. rate가 없으면 (None, None) — 동결(발행·확정) 시 필수는 frozen_complete가 강제한다.
    """
    if currency == "KRW":
        if rate_raw is not None and Decimal(str(rate_raw)) != 1:
            raise _invalid("fx_rate", "원화(KRW) 전표의 환율은 1입니다.")
        return Decimal(1), doc_date
    if rate_raw is None:
        if rate_date is not None:
            raise _invalid("fx_rate_date", "환율 없이 기준일만 입력할 수 없습니다.")
        return None, None
    text = str(rate_raw).strip()
    if not _RATE.match(text):
        raise _invalid("fx_rate", "환율은 숫자(소수 8자리까지)로 입력해 주세요.")
    rate = Decimal(text)
    if not 0 < rate <= MAX_RATE:
        raise _invalid("fx_rate", "환율은 0보다 크고 1,000,000 이하여야 합니다.")
    effective = rate_date or doc_date
    if effective > doc_date:
        raise _invalid("fx_rate_date", "환율 기준일은 증빙일 이후일 수 없습니다.")
    return rate, effective


def to_krw(amount_minor: int, currency: str, fx_rate: Decimal) -> Money:
    """전표 통화 최소단위 금액 → KRW(ROUND_HALF_UP 단일 — Money.from_decimal 규칙 재사용)."""
    value = Decimal(amount_minor).scaleb(-minor_units(currency)) * fx_rate
    return Money.from_decimal(value, "KRW")


def comparable_amount(
    amount_minor: int, doc_currency: str, fx_rate: Decimal | None, target_currency: str
) -> int | None:
    """전표 금액을 목표 통화 최소단위로 — **비교 불가는 None**(0·통과 대체 금지).

    같은 통화면 원액, 목표가 KRW이고 환율이 있으면 KRW 환산, 그 외(제3통화·역환산)는 None이다.
    """
    if doc_currency == target_currency:
        return amount_minor
    if target_currency == "KRW" and fx_rate is not None:
        return to_krw(amount_minor, doc_currency, fx_rate).amount
    return None
