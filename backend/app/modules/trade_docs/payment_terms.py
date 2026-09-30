"""결제조건 — 구조화 4열 (S3-1 ADR-0055 / design-A A5 / DESIGN §7.1 "결제조건은 구조화 — 자유 텍스트 금지").

API는 **퍼센트 문자열**(`"30"`, `"33.33"` — 소수 2자리까지, 초과는 반올림 없이 422)을 주고받고 서버가 bp 정수로
변환한다(프런트 산술 0 — `frontend/src/lib/money.test.ts`의 `/100` 가드 통과). 선수금은 저장하지 않고 함수로
계산한다(`split_advance`, HALF_UP·잔금=총액−선수금이라 합이 항상 맞는다).

L/C는 DB CHECK가 항상 허용하되 **서비스가 새로 입력할 때** 기능 플래그 `lc`를 본다(행 없음·꺼짐·조회 실패=꺼짐,
fail-closed). 참조 복사로 상속되는 L/C·동결·조회·진행은 검사하지 않는다(플래그를 꺼도 진행 중 거래가 갇히지 않게).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.platform.service import is_feature_enabled
from app.modules.trade_docs.constants import BalanceAnchor, PaymentType

_PCT = re.compile(r"^(?P<int>\d{1,3})(?:\.(?P<frac>\d{1,2}))?$")

#: 결제조건 필드 이름(요청·응답 공통).
FIELD = "payment_terms"


@dataclass(frozen=True, slots=True)
class PaymentTerms:
    payment_type: str
    advance_pct_bp: int | None
    balance_anchor: str | None
    balance_days: int | None

    def columns(self) -> dict[str, Any]:
        return {
            "payment_type": self.payment_type,
            "advance_pct_bp": self.advance_pct_bp,
            "balance_anchor": self.balance_anchor,
            "balance_days": self.balance_days,
        }


EMPTY_TERMS_COLUMNS: dict[str, Any] = {
    "payment_type": None,
    "advance_pct_bp": None,
    "balance_anchor": None,
    "balance_days": None,
}


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def parse_advance_pct(raw: object, *, field: str = f"{FIELD}.advance_pct") -> int:
    """퍼센트 문자열 → bp. 소수 2자리 초과·범위 밖(0 이하·100 초과)은 반올림 없이 422."""
    text = str(raw).strip()
    match = _PCT.match(text)
    if match is None:
        raise _invalid(field, "선수금 비율은 숫자(예: 30, 33.33)로 소수 2자리까지 입력해 주세요.")
    bp = int(match.group("int")) * 100 + int((match.group("frac") or "").ljust(2, "0"))
    if not 1 <= bp <= 10000:
        raise _invalid(field, "선수금 비율은 0보다 크고 100 이하여야 합니다.")
    return bp


def advance_pct_text(bp: int | None) -> str | None:
    """bp → 퍼센트 문자열(끝의 0 제거). 표시 전용 — 프런트가 산술하지 않는다."""
    if bp is None:
        return None
    whole, frac = divmod(bp, 100)
    return f"{whole}.{frac:02d}".rstrip("0").rstrip(".")


def build_payment_terms(
    payload: dict[str, Any] | None, *, allow_receipt_anchor: bool = False
) -> PaymentTerms | None:
    """요청 본문 → 검증된 결제조건. None이면 4열 전부 NULL(동결 전 초안)."""
    if payload is None:
        return None
    try:
        ptype = PaymentType(str(payload.get("payment_type"))).value
    except ValueError:
        raise _invalid(f"{FIELD}.payment_type", "결제유형을 선택해 주세요.") from None
    pct_raw = payload.get("advance_pct")
    anchor_raw = payload.get("balance_anchor")
    days = payload.get("balance_days")

    anchor: str | None = None
    if anchor_raw is not None:
        try:
            anchor = BalanceAnchor(str(anchor_raw)).value
        except ValueError:
            raise _invalid(f"{FIELD}.balance_anchor", "잔금 기산점을 확인해 주세요.") from None
        if anchor == BalanceAnchor.RECEIPT_DATE.value and not allow_receipt_anchor:
            raise _invalid(
                f"{FIELD}.balance_anchor", "입고 확정일 기산은 구매 발주에서만 쓸 수 있습니다."
            )
    if days is not None and (
        isinstance(days, bool) or not isinstance(days, int) or not -90 <= days <= 365
    ):
        raise _invalid(f"{FIELD}.balance_days", "잔금 일수는 -90 ~ 365 사이 정수로 입력해 주세요.")
    if days is not None and days < 0 and anchor != BalanceAnchor.ETD_DATE.value:
        raise _invalid(
            f"{FIELD}.balance_days", "음수 일수(선적 N일 전 잔금)는 ETD 기준에서만 쓸 수 있습니다."
        )

    bp: int | None = None
    if ptype == PaymentType.TT_ADVANCE.value:
        if pct_raw is None:
            raise _invalid(f"{FIELD}.advance_pct", "선수금 T/T는 선수금 비율이 필요합니다.")
        bp = parse_advance_pct(pct_raw)
        if bp == 10000:
            if anchor is not None or days is not None:
                raise _invalid(
                    f"{FIELD}.balance_anchor", "100% 선수금에는 잔금 기산점·일수가 없습니다."
                )
        elif anchor is None or days is None:
            raise _invalid(
                f"{FIELD}.balance_anchor",
                "선수금이 100% 미만이면 잔금 기산점과 일수를 입력해 주세요.",
            )
    elif ptype == PaymentType.TT_DEFERRED.value:
        if pct_raw is not None:
            raise _invalid(f"{FIELD}.advance_pct", "후불 T/T에는 선수금 비율이 없습니다.")
        if anchor is None or days is None:
            raise _invalid(
                f"{FIELD}.balance_anchor", "후불 T/T는 잔금 기산점과 일수를 입력해 주세요."
            )
    else:  # LC — 기산·선수금은 S3-3 lc_terms 소관이라 전표에 복사하지 않는다.
        if pct_raw is not None or anchor is not None or days is not None:
            raise _invalid(FIELD, "L/C에는 선수금 비율·잔금 기산점·일수를 입력하지 않습니다.")
    return PaymentTerms(ptype, bp, anchor, days)


def require_lc_enabled(session: Session, terms: PaymentTerms | None) -> None:
    """L/C를 **새로 입력**할 때 기능 플래그 `lc` 확인 — 꺼짐·행 없음·조회 실패는 전부 422(fail-closed)."""
    if terms is None or terms.payment_type != PaymentType.LC.value:
        return
    try:
        enabled = is_feature_enabled(session, "lc")
    except Exception:
        enabled = False
    if not enabled:
        raise AppError(ErrorCode.TRADE_DOCS_PAYMENT_LC_DISABLED)


def split_advance(total_amount: int, advance_pct_bp: int) -> tuple[int, int]:
    """(선수금, 잔금) — 선수금은 HALF_UP 정수, 잔금은 총액−선수금(합 일치 보장).

    SQL에서 곱하지 않는다(총액 2^53 × 10^4가 BIGINT를 넘긴다) — 파이썬 정수로만 계산한다.
    """
    if not 1 <= advance_pct_bp <= 10000:
        raise ValueError("advance_pct_bp는 1..10000이어야 합니다.")
    advance = (total_amount * advance_pct_bp + 5000) // 10000
    return advance, total_amount - advance
