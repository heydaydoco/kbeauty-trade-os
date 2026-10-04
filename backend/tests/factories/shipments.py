"""선적(S3-2 PR-3a) 테스트 팩토리 — 확정 SO·원시 선적 행·선적 API 호출. 테스트 데이터는 전부 익명 합성이다.

`confirmed_so`는 직접(인테이크형) 수주를 만들고 결제조건·Incoterms·환율을 채운 뒤 **원시 SQL로 확정 상태**를 만든다(게이트·승인 경로를
다시 시험하지 않는다 — 확정 자체는 PR-12a 시험의 몫). 실제 확정 API를 거치는 관통은 `api_confirmed_so`를 쓴다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.time import today_kst
from tests.factories.trade import (
    CONFIRMED_EVIDENCE_SQL,
    create_buyer,
    create_direct_so,
    create_priced_sku,
    idem,
    unique,
)

SO = "/api/v1/sales-orders"
SHIPMENTS = "/api/v1/shipments"


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [dict(r) for r in connection.execute(text(sql), params).mappings()]


def confirmed_so(
    quantities: tuple[int, ...] = (10,),
    *,
    buyer: int | None = None,
    price: int = 1000,
    terms: str = "TT_DEFERRED",
    free_line: bool = False,
    currency: str = "USD",
) -> dict[str, Any]:
    """확정(CONFIRMED) SO — 라인 수 = len(quantities), 각 라인 수량 지정. 반환: SO 상세 본문 + `buyer`·`line_ids`(라인 번호 순).

    `free_line=True`면 마지막 라인을 무상(단가 0·사유)으로 만든다(선적 라인 무상 규약 승계 시험용).
    """
    from tests.factories.gates import set_terms

    buyer_id = buyer or create_buyer()
    skus = [create_priced_sku(amount=price, currency=currency) for _ in quantities]
    so = create_direct_so(
        buyer_id,
        skus,
        quantity=quantities[0],
        buyer_po_no=unique("PO"),
        price_amount=price,
        currency=currency,
    )
    with owner_engine.begin() as connection:
        lines = connection.execute(
            text(
                "SELECT id, line_no FROM sales_order_lines WHERE so_id = :s AND deleted_at IS NULL"
                " ORDER BY line_no"
            ),
            {"s": so["id"]},
        ).all()
        for (line_id, _no), qty in zip(lines, quantities, strict=True):
            connection.execute(
                text(
                    "UPDATE sales_order_lines SET quantity = :q, line_amount = :q * unit_price_amount"
                    " WHERE id = :i"
                ),
                {"q": qty, "i": line_id},
            )
        if free_line:
            connection.execute(
                text(
                    "UPDATE sales_order_lines SET unit_price_amount = 0, line_amount = 0, is_free = true,"
                    " price_basis = 'MANUAL', price_reason = '샘플 무상' WHERE id = :i"
                ),
                {"i": lines[-1][0]},
            )
        connection.execute(
            text(
                "UPDATE sales_orders SET total_amount = (SELECT COALESCE(SUM(line_amount), 0)"
                " FROM sales_order_lines WHERE so_id = :s AND deleted_at IS NULL) WHERE id = :s"
            ),
            {"s": so["id"]},
        )
    set_terms(so["id"], terms)
    rate = 1 if currency == "KRW" else 1350
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sales_orders SET incoterm_code = 'FOB', incoterm_place = 'Busan',"
                " incoterm_year = 2020, fx_rate = :r, fx_rate_date = doc_date WHERE id = :i"
            ),
            {"i": so["id"], "r": rate},
        )
        connection.execute(
            text(
                "UPDATE sales_orders SET status = 'CONFIRMED', confirmed_at = now(),"
                f" {CONFIRMED_EVIDENCE_SQL} WHERE id = :i"
            ),
            {"i": so["id"]},
        )
        connection.execute(
            text(
                "INSERT INTO sales_order_status_log (sales_order_id, from_status, to_status,"
                " actor_user_id, automatic) VALUES (:s, 'RECEIVED', 'CONFIRMED', :a, false)"
            ),
            {"s": so["id"], "a": so["assignee_id"]},
        )
    so["buyer"] = buyer_id
    so["line_ids"] = [int(line_id) for line_id, _ in lines]
    so["sku_ids"] = skus
    return so


def so_status(so_id: int) -> str:
    return str(scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id))


def so_version(so_id: int) -> int:
    return int(scalar("SELECT version FROM sales_orders WHERE id = :i", i=so_id))


def shipment_version(shipment_id: int) -> int:
    return int(scalar("SELECT version FROM shipments WHERE id = :i", i=shipment_id))


def create_body(
    lines: list[tuple[int, int]],
    *,
    origin: str = "KR",
    dest: str = "US",
    parties: list[dict[str, Any]] | None = None,
    internal_note: str | None = None,
) -> dict[str, Any]:
    """SO 참조 선적 생성 본문 — lines = [(so_line_id, quantity)]."""
    body: dict[str, Any] = {
        "lines": [{"so_line_id": line_id, "quantity": qty} for line_id, qty in lines],
        "origin_country_code": origin,
        "dest_country_code": dest,
    }
    if parties is not None:
        body["parties"] = parties
    if internal_note is not None:
        body["internal_note"] = internal_note
    return body


def create_shipment(
    client: TestClient,
    so_id: int,
    lines: list[tuple[int, int]],
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    """`POST /sales-orders/{id}/shipments` 응답(상태 확인은 호출자)."""
    return client.post(
        f"{SO}/{so_id}/shipments", json=create_body(lines, **kwargs), headers=headers or idem()
    )


def created(
    client: TestClient, so_id: int, lines: list[tuple[int, int]], **kwargs: Any
) -> dict[str, Any]:
    response = create_shipment(client, so_id, lines, **kwargs)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def cancel(
    client: TestClient,
    shipment_id: int,
    *,
    reason: str = "선적 일정 취소",
    version: int | None = None,
) -> Any:
    return client.post(
        f"{SHIPMENTS}/{shipment_id}/transitions",
        json={
            "to_status": "CANCELLED",
            "reason": reason,
            "version": version if version is not None else shipment_version(shipment_id),
        },
        headers=idem(),
    )


def release(client: TestClient, shipment_id: int, *, version: int | None = None) -> Any:
    return client.post(
        f"{SHIPMENTS}/{shipment_id}/release-order",
        json={"version": version if version is not None else shipment_version(shipment_id)},
        headers=idem(),
    )


def raw_shipment(
    so_id: int,
    *,
    status: str = "PLANNED",
    frozen: bool | None = None,
    kind: str = "EXPORT",
    doc_date: date | None = None,
) -> int:
    """원시 SQL 선적 헤더(라인 없음 — 제약·사슬 시험용). 거래 상대·통화·조건은 SO에서 읽는다."""
    so = rows("SELECT * FROM sales_orders WHERE id = :i", i=so_id)[0]
    if frozen is None:
        frozen = status not in ("PLANNED", "CANCELLED")
    values = {
        "doc_number": f"SH-2026-{9000 + int(unique('n').split('-')[1]):04d}",
        "doc_date": doc_date
        or today_kst(),  # 서비스와 같이 생성일 = 오늘(KST) — 원천 환율일 ≤ 증빙일(fx_date_not_future)
        "status": status,
        "currency": so["currency"],
        "fx_rate": so["fx_rate"],
        "fx_rate_date": so["fx_rate_date"],
        "payment_type": so["payment_type"],
        "advance_pct_bp": so["advance_pct_bp"],
        "balance_anchor": so["balance_anchor"],
        "balance_days": so["balance_days"],
        "incoterm_code": so["incoterm_code"],
        "incoterm_place": so["incoterm_place"],
        "incoterm_year": so["incoterm_year"],
        "assignee_id": so["assignee_id"],
        "shipment_kind": kind,
        "so_id": so_id if kind == "EXPORT" else None,
        "counterparty_partner_id": so["buyer_partner_id"],
        "counterparty_name": so["buyer_name"],
        "origin_country_code": "KR",
        "dest_country_code": "US",
        "frozen_at": "2026-09-02T00:00:00+00:00" if frozen else None,
    }
    columns = list(values)
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    f"INSERT INTO shipments ({', '.join(columns)}) VALUES"
                    f" ({', '.join(':' + c for c in columns)}) RETURNING id"
                ),
                values,
            ).scalar_one()
        )
