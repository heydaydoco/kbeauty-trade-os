"""입금 원장(S3-1 PR-10a) 테스트 팩토리 — 선수금 PI·입금/역기록 API 호출·원장 조회. 테스트 데이터는 전부 익명 합성이다."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from tests.factories.trade import idem, raw_pi, raw_quotation, unique
from tests.support.factories import create_user

#: 기본 PI: USD·선수금 T/T 30%·총액 1000.00 → 선수금 청구액(due) 300.00(30000센트).
DEFAULT_TOTAL = 100_000
DEFAULT_DUE = 30_000


def advance_pi(status: str = "ISSUED", *, total_amount: int = DEFAULT_TOTAL, **kwargs: Any) -> int:
    """선수금 T/T 30% PI(DB 직행). `status`로 입금 가능 여부를 갈린다."""
    return raw_pi(raw_quotation("ISSUED"), status, total_amount=total_amount, **kwargs)


def set_payment_terms(pi_id: int, payment_type: str) -> None:
    """PI의 결제조건을 바꾼다(DB 직행 — 결제조건 형태 CHECK를 만족하는 값으로)."""
    shapes = {
        "LC": "advance_pct_bp = NULL, balance_anchor = NULL, balance_days = NULL",
        "TT_DEFERRED": "advance_pct_bp = NULL, balance_anchor = 'ETD_DATE', balance_days = -7",
    }
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE proforma_invoices SET payment_type = :t, {shapes[payment_type]} WHERE id = :i"
            ),
            {"t": payment_type, "i": pi_id},
        )


def actor(*roles: RoleCode) -> AuthenticatedUser:
    address = f"{unique('payer')}@example.com"
    roles = roles or (RoleCode.TRADE,)
    user_id = create_user(address, roles=roles)
    return AuthenticatedUser(
        id=user_id, email=address, display_name="입금", roles=frozenset(roles), session_id=0
    )


def receipt_body(
    amount: str = "100.00",
    *,
    currency: str = "USD",
    received_on: date | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    return {
        "received_amount": amount,
        "received_currency": currency,
        "received_on": (received_on or date(2026, 9, 20)).isoformat(),
        "reference": reference or unique("BANKREF"),
    }


def pay(client: TestClient, pi_id: int, amount: str = "100.00", **kwargs: Any) -> Any:
    return client.post(
        f"/api/v1/proforma-invoices/{pi_id}/payments",
        json=receipt_body(amount, **kwargs),
        headers=idem(),
    )


def reverse(client: TestClient, payment_id: int, reason: str = "은행 오입금 정정") -> Any:
    return client.post(
        f"/api/v1/payments/{payment_id}/reversal", json={"reason": reason}, headers=idem()
    )


def ledger(pi_id: int) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r._mapping)
            for r in connection.execute(
                text("SELECT * FROM payments WHERE pi_id = :p ORDER BY id"), {"p": pi_id}
            )
        ]


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def pi_status(pi_id: int) -> str:
    return str(scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id))


def status_log(pi_id: int) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [
            tuple(r)
            for r in connection.execute(
                text(
                    "SELECT from_status, to_status, automatic, reason FROM proforma_invoice_status_log"
                    " WHERE proforma_invoice_id = :p ORDER BY id"
                ),
                {"p": pi_id},
            )
        ]
