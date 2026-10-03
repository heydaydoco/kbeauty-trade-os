"""오더 인테이크(S3-1 PR-13a) 테스트 팩토리 — 품번 매핑이 걸린 바이어·SKU 세계와 등록·확정 헬퍼. 테스트 데이터는 전부 익명 합성이다."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from tests.factories.trade import (
    create_buyer,
    create_priced_sku,
    idem,
    map_buyer_item_code,
    unique,
)
from tests.support.factories import create_market, create_user

INTAKES = "/api/v1/order-intakes"


def trade_actor(user_id: int | None = None) -> AuthenticatedUser:
    """서비스 직접 호출용 행위자(무역) — 사용자 행을 만들어 FK를 만족시킨다."""
    uid = user_id or create_user(f"{unique('ia')}@example.com", roles=(RoleCode.TRADE,))
    return AuthenticatedUser(
        id=uid,
        email="intake@example.com",
        display_name="인테이크 행위자",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )


def world(*, lines: int = 1, price: int = 1000, currency: str = "USD") -> dict[str, Any]:
    """품번 매핑이 전부 걸린 세계 — 바이어 1·판가 SKU `lines`개·바이어 품번(BC-n) 매핑·시장 US."""
    create_market("US")
    buyer_code = unique("BUY")
    buyer = create_buyer(code=buyer_code)
    sku_ids = [create_priced_sku(amount=price, currency=currency) for _ in range(lines)]
    codes = [unique("BC") for _ in sku_ids]
    for sku_id, code in zip(sku_ids, codes, strict=True):
        map_buyer_item_code(buyer, sku_id, code)
    return {
        "buyer": buyer,
        "buyer_code": buyer_code,
        "sku_ids": sku_ids,
        "codes": codes,
        "currency": currency,
    }


def line_body(
    code: str, *, quantity: int = 5, unit_price: str = "10.00", **extra: Any
) -> dict[str, Any]:
    return {"buyer_item_code": code, "quantity": quantity, "unit_price": unit_price, **extra}


def intake_payload(
    w: dict[str, Any],
    *,
    po_no: str | None = None,
    lines: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "buyer_partner_id": w["buyer"],
        "buyer_po_no": po_no if po_no is not None else unique("PO"),
        "currency": w["currency"],
        "dest_market_code": "US",
        "lines": lines if lines is not None else [line_body(c) for c in w["codes"]],
    }
    body.update(overrides)
    return body


def register(client: TestClient, w: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """수동 등록 — 201 본문(IntakeDetail)."""
    response = client.post(INTAKES, json=intake_payload(w, **kwargs), headers=idem())
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def confirm(client: TestClient, intake: dict[str, Any]) -> Any:
    return client.post(
        f"{INTAKES}/{intake['id']}/confirm", json={"version": intake["version"]}, headers=idem()
    )


def get(client: TestClient, intake_id: int) -> dict[str, Any]:
    response = client.get(f"{INTAKES}/{intake_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def code_of(response: Any) -> str:
    return str(response.json()["error"]["code"])


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


def execute(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def future(days: int = 30) -> str:
    return (today_kst() + timedelta(days=days)).isoformat()


def land(w: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """서비스 직접 호출로 수동 인테이크를 착지시킨다(HTTP 없이) — 201 본문."""
    from app.modules.order_intake import service as intake_service

    status, body = intake_service.create_manual_intake(
        actor=trade_actor(), idempotency_key=unique("land"), payload=intake_payload(w, **kwargs)
    )
    assert status == 201
    return body


# ── CSV 입구 (PR-14a) ─────────────────────────────────────────────────────────

IMPORT_CSV = f"{INTAKES}/import-csv"
TEMPLATE_CSV = f"{INTAKES}/template.csv"


def csv_header() -> list[str]:
    from app.modules.order_intake.csv_template import CSV_HEADER

    return list(CSV_HEADER)


def past(days: int = 1) -> str:
    return (today_kst() - timedelta(days=days)).isoformat()


def csv_row(
    w: dict[str, Any],
    *,
    po: str,
    code: str | None = None,
    qty: str = "5",
    price: str = "10.00",
    po_date: str | None = None,
    delivery: str | None = None,
    buyer_code: str | None = None,
    currency: str | None = None,
    market: str = "US",
) -> list[str]:
    """9열 한 행 — 헤더 순서(바이어코드·PO번호·PO일자·통화·시장·품번·수량·단가·납기)."""
    return [
        buyer_code if buyer_code is not None else w["buyer_code"],
        po,
        po_date if po_date is not None else past(),
        currency if currency is not None else w["currency"],
        market,
        code if code is not None else w["codes"][0],
        qty,
        price,
        delivery if delivery is not None else future(),
    ]


def csv_bytes(
    rows_: list[list[str]], *, header: list[str] | None = None, encoding: str = "utf-8-sig"
) -> bytes:
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer)
    writer.writerow(header if header is not None else csv_header())
    writer.writerows(rows_)
    return buffer.getvalue().encode(encoding)


def upload_csv(
    client: TestClient,
    content: bytes,
    *,
    key: str | None = None,
    filename: str = "po.csv",
) -> Any:
    return client.post(
        IMPORT_CSV,
        files={"file": (filename, content, "text/csv")},
        headers={"Idempotency-Key": key or unique("csv")},
    )


def error_rows(response: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = response.json()["error"]["detail"]["errors"]
    return errors
