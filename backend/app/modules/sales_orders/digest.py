"""SO 게이트 판정 입력 digest — 승인 결속 토큰의 **단일 정의** (S3-1 ADR-0060·0063 / design-integrated X-07, design-E E5).

승인 후 불변의 결속은 version이 아니라 **판정 입력 내용의 digest**다: 라인만 바뀌고 헤더 version이 안 오르는 결함 유형(S1-3 PR-3 ①)에 무력하고,
메모 수정 같은 무관 변경으로 재승인이 생겨 우회 압력을 만드는 것도 막는다. 입력 집합(승인 코어·게이트가 공유한다 — 이름을 섞지 않는다):

    {buyer_partner_id, currency, fx_rate(문자열, NUMERIC(18,8) 고정 자릿수), fx_rate_date, lines:[[sku_id, quantity, unit_price_amount, is_free]…line_no순]}

정수는 문자열로, 문자열은 NFC로 정규화한 canonical JSON(키 정렬·공백 없음)의 sha256 hex다. **메모·담당자·version·PO 번호·납기는 입력이 아니다**(무관 변경은
재승인을 만들지 않는다). 삭제된 라인은 입력이 아니다. 골든 벡터 테스트가 직렬화 형식을 고정한다 — 형식을 바꾸면 미소비 승인이 전부 무효가 되므로 ADR이 필요하다.
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import NotFoundError
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def canonical_gate_input(
    *,
    buyer_partner_id: int,
    currency: str,
    fx_rate: Decimal | None,
    fx_rate_date: object | None,
    lines: list[tuple[int, int, int, bool]],
) -> str:
    """digest 입력의 canonical JSON — 순수 함수(골든 벡터·단위 테스트용)."""
    payload: dict[str, Any] = {
        "buyer_partner_id": str(buyer_partner_id),
        "currency": _norm(currency),
        "fx_rate": format(fx_rate, "f") if fx_rate is not None else None,
        "fx_rate_date": fx_rate_date.isoformat() if fx_rate_date is not None else None,  # type: ignore[attr-defined]
        "lines": [
            [str(sku), str(qty), str(price), "1" if free else "0"]
            for sku, qty, price, free in lines
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(canonical: str) -> str:
    return sha256(canonical.encode("utf-8")).hexdigest()


def gate_input_digest(session: Session, so_id: int) -> str:
    """SO의 현재 게이트 판정 입력 digest(64자 hex) — 헤더(거래처·통화·환율)와 살아 있는 라인(SKU·수량·단가·무상 표지)."""
    header = session.execute(
        select(SalesOrder).where(SalesOrder.id == so_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if header is None:
        raise NotFoundError(log_context={"sales_order_id": so_id})
    rows = session.execute(
        select(
            SalesOrderLine.sku_id,
            SalesOrderLine.quantity,
            SalesOrderLine.unit_price_amount,
            SalesOrderLine.is_free,
        )
        .where(SalesOrderLine.so_id == so_id, SalesOrderLine.deleted_at.is_(None))
        .order_by(SalesOrderLine.line_no, SalesOrderLine.id)
    ).all()
    return digest_of(
        canonical_gate_input(
            buyer_partner_id=header.buyer_partner_id,
            currency=header.currency,
            fx_rate=header.fx_rate,
            fx_rate_date=header.fx_rate_date,
            lines=[(int(r[0]), int(r[1]), int(r[2]), bool(r[3])) for r in rows],
        )
    )
