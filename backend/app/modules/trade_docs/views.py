"""전표 응답 조립의 공용 조각 — 표시용 문자열(금액·환율·%)·결제조건·Incoterms 본문 (S3-1 design-A A5·A9).

QT·PI(·SO·PO)가 같은 모양으로 응답하도록 한 곳에 둔다(L1 서비스끼리는 서로 임포트하지 못한다 — 계층 DAG).
표시용 십진 문자열은 서버가 만든다(프런트 산술 0 — 정수 최소단위 `*_amount`와 함께 준다).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.money import Money
from app.core.time import to_kst
from app.modules.trade_docs.payment_terms import advance_pct_text


def money_text(amount: int | None, currency: str) -> str | None:
    if amount is None:
        return None
    return format(Money(amount, currency).to_decimal(), "f")


def rate_text(rate: Decimal | None) -> str | None:
    return None if rate is None else format(rate.normalize(), "f")


def created_date_kst(created_at: datetime) -> str:
    """CSV "생성일" — 저장은 UTC지만 사람이 보는 날짜는 KST다(UTC 15:00 이후는 KST 다음 날)."""
    return to_kst(created_at).date().isoformat()


def payment_terms_body(row: Any) -> dict[str, Any]:
    return {
        "payment_type": row.payment_type,
        "advance_pct": advance_pct_text(row.advance_pct_bp),
        "advance_pct_bp": row.advance_pct_bp,
        "balance_anchor": row.balance_anchor,
        "balance_days": row.balance_days,
    }


def incoterm_body(row: Any) -> dict[str, Any]:
    return {"code": row.incoterm_code, "place": row.incoterm_place, "year": row.incoterm_year}
