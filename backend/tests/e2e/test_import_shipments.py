"""A·G·K. 수입선적 API — 응답 갈래(금액·통화 키 0) (S3-2 PR-5a / GC-G3 / ADR-0024·0074·0077 / design-D D3 / R-3c-2).

수입선적은 PO 원가를 복사하지 않는다(단가 NULL·금액 0 — M14 CHECK). 응답은 그 0을 '0.00'으로 그리지 않고 **금액·통화 계열 키를 아예 싣지 않는다**
(null이 아니라 미포함 — design-D D3). PO 통화·환율은 PO CostHidden이 원가 비열람 역할에게 가리는 필드라 전 역할 같은 모양이다(원가 열람 역할로도 0).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from tests.factories.shipments import SHIPMENTS, confirmed_so, created, raw_shipment
from tests.factories.trade import (
    SENTINEL_UNIT_COST,
    SENTINEL_UNIT_COST_TEXT,
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    logged_in,
)

pytestmark = pytest.mark.group_g

#: PO 원가 마스킹 정규식(설계 F5(f) — PO CostHidden 스캔과 같은 식).
COST_KEY = re.compile(r"(_cost$|^cost$|^currency$|^price_)")
#: 수입선적 응답에 **있으면 안 되는** 금액·통화 계열 키(PO CostHidden 추가 목록 + 선적 라인 판매가 축 키).
IMPORT_ABSENT_KEYS = frozenset(
    {
        "currency",
        "minor_units",
        "fx_rate",
        "fx_rate_date",
        "total_amount",
        "total_text",
        "unit_price_amount",
        "unit_price_text",
        "line_amount",
        "line_amount_text",
        "is_free",
    }
)
#: 수출선적 응답에는 **있어야 하는** 키(갈래가 수출을 잘라 먹지 않음 — 판별자 합집합의 양성 대조).
EXPORT_PRESENT_KEYS = frozenset(
    {"currency", "minor_units", "fx_rate", "total_amount", "total_text"}
)


def scan_keys(node: Any) -> set[str]:
    """JSON을 재귀로 훑어 모든 키를 모은다(중첩 라인·출처·보드 포함)."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= scan_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= scan_keys(item)
    return keys


def leaked_keys(body: Any) -> set[str]:
    keys = scan_keys(body)
    return {k for k in keys if COST_KEY.search(k)} | (keys & IMPORT_ABSENT_KEYS)


SENTINELS = (str(SENTINEL_UNIT_COST), SENTINEL_UNIT_COST_TEXT)


def sentinel_po(
    client: TestClient, *, quantities: tuple[int, ...] = (100,), supplier: int | None = None
) -> dict[str, Any]:
    """발행 PO — 라인마다 센티널 매입 단가(원가 열람 역할에게만 보이는 값)·지정 수량. 응답 본문(무역 = 원가 포함)."""
    lines = [
        {
            "sku_id": create_purchase_priced_sku(),
            "quantity": qty,
            "unit_cost": SENTINEL_UNIT_COST_TEXT,
        }
        for qty in quantities
    ]
    return create_po_via_api(client, supplier or create_supplier(), [], lines=lines)


def _raw_import_line(shipment_id: int, po_line: dict[str, Any], quantity: int) -> None:
    """원시 수입선적 라인(원가 비복사 — 단가 NULL·금액 0·무상 false, M14 CHECK `import_no_price`)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO shipment_lines (shipment_id, line_no, po_line_id, sku_id, sku_code, sku_name_ko,"
                " sku_name_en, sku_kind, currency, quantity, unit_price_amount, is_free, line_amount)"
                " SELECT :s, 1, :p, sku_id, sku_code, sku_name_ko, sku_name_en, sku_kind,"
                " (SELECT currency FROM shipments WHERE id = :s), :q, NULL, false, 0"
                " FROM purchase_order_lines WHERE id = :p"
            ),
            {"s": shipment_id, "p": po_line["id"], "q": quantity},
        )
        connection.execute(
            text("UPDATE shipments SET last_line_no = 1 WHERE id = :s"), {"s": shipment_id}
        )


@pytest.mark.group_k
def test_import_rows_carry_no_amount_or_currency_keys_for_any_role() -> None:
    """R-3c-2 해소·G3 — 수입선적(원시 행) 상세·목록 응답에 통화·소수 자릿수·환율·합계·라인 단가/금액/무상 키가 **없다**(null 아님 — 미포함),
    PO 원가 센티널 0회 — 원가 열람 역할(관리자·무역·물류·인증)과 조회 역할 모두 같은 모양. 같은 목록의 수출 행은 금액 키를 그대로 싣는다(양성 대조)"""
    with logged_in(RoleCode.TRADE) as trade:
        po = sentinel_po(trade, quantities=(9,))
        assert (
            po["lines"][0]["unit_cost"] == SENTINEL_UNIT_COST
        )  # 센티널이 실제로 저장됐다(0회 단언이 공회전이 아님)
        holder = confirmed_so((3,))
        export = created(trade, holder["id"], [(holder["line_ids"][0], 1)])
    imported = raw_shipment(holder["id"], kind="IMPORT", po_id=po["id"])
    _raw_import_line(imported, po["lines"][0], 4)
    for role in RoleCode:
        with logged_in(role) as client:
            detail = client.get(f"{SHIPMENTS}/{imported}")
            assert detail.status_code == 200, (role, detail.text)
            body = detail.json()
            assert body["shipment_kind"] == "IMPORT" and len(body["lines"]) == 1
            assert leaked_keys(body) == set(), (role, leaked_keys(body))
            line = body["lines"][0]
            assert line["po_line_id"] == po["lines"][0]["id"]
            assert line["source_line"] == {
                "id": po["lines"][0]["id"],
                "line_no": 1,
                "quantity": 9,
                "remaining_after": 5,  # 배정 가능량 = 9 − 살아 있는 수입선적 4
            }
            listing = client.get(SHIPMENTS, params={"shipment_kind": "IMPORT"})
            [item] = [i for i in listing.json()["items"] if i["id"] == imported]
            assert leaked_keys(item) == set(), (role, item)
            for sentinel in SENTINELS:
                assert sentinel not in detail.text and sentinel not in listing.text, role
            exported = client.get(f"{SHIPMENTS}/{export['id']}").json()
            assert set(exported) >= EXPORT_PRESENT_KEYS, role  # 수출은 판매가 축 그대로
            assert {"unit_price_amount", "line_amount", "currency"} <= set(exported["lines"][0])
            [export_item] = [
                i
                for i in client.get(SHIPMENTS, params={"so_id": holder["id"]}).json()["items"]
                if i["id"] == export["id"]
            ]
            assert {"currency", "total_amount", "total_text"} <= set(export_item), role
