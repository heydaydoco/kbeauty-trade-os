"""A. DoD ④ — **확정(동결) 후 단가·환율·통화 불변** 4종 전표(QT 발행·PI 생성·SO 확정·PO 발행) (S3-1 PR-12a / design-B B2·B6 / ADR-0052).

강제는 3층이다: ① **서비스** — CONTENT·ORIGIN 열은 동결 후 어떤 값으로도 바꿀 수 없다(409 FROZEN, 필드명만·행 전체 무변), 라인 추가·수정·제외도 409. PI·PO는 **내용 편집 경로 자체가 없고**
`/meta`(FREE 열)만 있다 ② **스키마·분류** — 메타 요청 스키마의 필드 = FREE 열 집합(가격·환율 필드가 구조적으로 도달 불가) ③ **DB** — 동결 시각이 있는 행은 동결 완결성 CHECK(`frozen_complete`)가 결제조건·Incoterms·
환율을 NULL로 만드는 직접 UPDATE를 거부하고, 확정 SO를 접수로 되돌리는 UPDATE는 결속 CHECK가 거부한다. (값 *변경*의 직접 SQL 방어는 트리거 미채택[ADR-0028·0040·B6]이라 서비스·분류 계층의 몫이다 — 여기서 숨기지 않는다.)
SO는 **실제 확정 통로(`confirm_sales_order`)로** 동결한다 — DB 직행 행이 아니다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.trade_docs.policy import ColumnClass, columns_of
from tests.factories.confirm import confirm, ready_so, rows, so_row, so_version
from tests.factories.trade import (
    create_bank_account,
    create_pi_via_api,
    create_po_via_api,
    create_priced_sku,
    idem,
    issued_quotation,
    logged_in,
    set_price,
)
from tests.support.factories import create_market

pytestmark = pytest.mark.group_a

SO = "/api/v1/sales-orders"
QT = "/api/v1/quotations"
PI = "/api/v1/proforma-invoices"
PO = "/api/v1/purchase-orders"


def _confirmed_so(client: TestClient) -> dict[str, Any]:
    so = ready_so()
    response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    body: dict[str, Any] = client.get(f"{SO}/{so['id']}").json()
    assert body["status"] == "CONFIRMED"
    return body


def _row_snapshot(table: str, doc_id: int) -> dict[str, Any]:
    (row,) = rows(table, "id = :i", i=doc_id)
    return row


def _freeze(kind: str, client: TestClient) -> tuple[str, str, dict[str, Any]]:
    """실제 동결 통로로 전표 1건을 동결한다 → (헤더 표, 경로 접두어, 상세 본문). QT=발행·PI=생성·SO=확정(`confirm_sales_order`)·PO=발행."""
    if kind == "QT":
        return "quotations", QT, issued_quotation(client, quantity=3)
    if kind == "PI":
        pi = create_pi_via_api(client, issued_quotation(client), create_bank_account("USD"))
        return "proforma_invoices", PI, pi
    if kind == "SO":
        return "sales_orders", SO, _confirmed_so(client)
    return "purchase_orders", PO, create_po_via_api(client)


@pytest.mark.parametrize("kind", ["QT", "PI", "SO", "PO"])
def test_confirmed_prices_and_fx_are_immutable(kind: str) -> None:
    """**DoD ④ 본체(4종 전표)** — 실제 동결 통로로 동결한 뒤, 그 전표에 열린 모든 변경 경로(내용 PATCH·라인 편집·메타 PATCH에 가격·환율·통화 필드 주입)를 시도해도 헤더·라인 행 전체가 바이트 단위로 같다 ·
    허용 방향(FREE 메모 수정)은 동작하고 가격·환율은 그대로 · DB는 동결 행의 환율·결제조건 비우기를 CHECK로 거부한다. (QT·SO는 내용 편집 라우트가 409로 거부하고, PI·PO는 라우트 자체가 없다)"""
    from app.modules.trade_docs.constants import LINE_HEADER_FK, LINE_TABLES, DocKind

    with logged_in(RoleCode.TRADE) as client:
        table, prefix, doc = _freeze(kind, client)
        doc_id, version = doc["id"], doc["version"]
        line_table = LINE_TABLES[
            {
                "QT": DocKind.QUOTATION,
                "PI": DocKind.PROFORMA_INVOICE,
                "SO": DocKind.SALES_ORDER,
                "PO": DocKind.PURCHASE_ORDER,
            }[kind]
        ]
        line_fk = LINE_HEADER_FK[
            {
                "QT": DocKind.QUOTATION,
                "PI": DocKind.PROFORMA_INVOICE,
                "SO": DocKind.SALES_ORDER,
                "PO": DocKind.PURCHASE_ORDER,
            }[kind]
        ]
        before_header = _row_snapshot(table, doc_id)
        before_lines = rows(line_table, f"{line_fk} = :i", i=doc_id)
        assert (
            before_lines and before_header["fx_rate"] is not None
        )  # 공회전 방지 — 값이 있는 동결 문서

        attempts: list[tuple[str, Any]] = []
        for payload in (
            {"fx_rate": "1499"},
            {"currency": "EUR"},
            {"unit_price": "1.00"},
            {"total_amount": 1},
        ):  # 메타 경로로 가격·환율·통화를 주입 — extra=forbid로 422
            attempts.append(
                (
                    f"meta {payload}",
                    client.patch(f"{prefix}/{doc_id}/meta", json={"version": version, **payload}),
                )
            )
        content = client.patch(f"{prefix}/{doc_id}", json={"version": version, "fx_rate": "1499"})
        line_id = before_lines[0]["id"]
        line_edit = client.patch(
            f"{prefix}/{doc_id}/lines/{line_id}", json={"version": version, "unit_price": "1.00"}
        )
        line_add = client.post(
            f"{prefix}/{doc_id}/lines",
            json={"version": version, "sku_id": create_priced_sku(amount=1000), "quantity": 1},
        )
        line_del = client.delete(f"{prefix}/{doc_id}/lines/{line_id}", params={"version": version})
        for name, response in attempts:
            assert response.status_code == 422, (kind, name, response.text)
        if kind in ("QT", "SO"):  # 내용 편집 라우트가 있다 — 동결이라 409 FROZEN
            for response in (content, line_edit, line_add, line_del):
                assert response.status_code == 409, (kind, response.text)
                assert response.json()["error"]["code"] == "TRADE_DOCS.DOCUMENT.FROZEN"
        else:  # PI·PO — 내용 편집 라우트 자체가 없다
            for response in (content, line_edit, line_add, line_del):
                assert response.status_code in (404, 405), (kind, response.status_code)

        assert _row_snapshot(table, doc_id) == before_header
        assert rows(line_table, f"{line_fk} = :i", i=doc_id) == before_lines

        meta = client.patch(
            f"{prefix}/{doc_id}/meta", json={"version": version, "internal_note": "동결 후 메모"}
        )
        assert meta.status_code == 200, (kind, meta.text)  # 허용 방향 — FREE 열은 열려 있다
    after_header = _row_snapshot(table, doc_id)
    changed = {k for k in before_header if before_header[k] != after_header[k]}
    assert changed <= {"internal_note", "version", "updated_at", "updated_by_id"}, changed
    assert after_header["fx_rate"] == before_header["fx_rate"]
    assert rows(line_table, f"{line_fk} = :i", i=doc_id) == before_lines  # 라인 단가·금액 불변
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table} SET fx_rate = NULL, fx_rate_date = NULL WHERE id = :i"),
            {"i": doc_id},
        )
    assert f"ck_{table}_frozen_complete" in str(caught.value)


# ══ ① 서비스 — 내용 편집은 409, 행 전체 무변 ═══════════════════════════════════════════


#: 확정 SO의 PATCH가 받는 CONTENT 필드 → 현재값과 다른 값(필드별 하나씩).
_SO_PATCHES: dict[str, dict[str, Any]] = {
    "doc_date": {"doc_date": "2026-01-02"},
    "dest_market_code": {"dest_market_code": "JP"},
    "fx_rate": {"fx_rate": "1499"},
    "fx_rate_date": {"fx_rate_date": "2026-01-02"},
    "payment_terms": {
        "payment_terms": {
            "payment_type": "TT_DEFERRED",
            "balance_anchor": "BL_DATE",
            "balance_days": 60,
        }
    },
    "incoterm": {"incoterm": {"code": "CIF", "place": "Los Angeles", "year": 2020}},
    "buyer_name": {"buyer_name": "다른 표기"},
    "buyer_po_no": {"buyer_po_no": "PO-CHANGED"},
    "buyer_po_date": {"buyer_po_date": "2026-01-02"},
}


def test_a_confirmed_order_rejects_every_content_field_and_stays_byte_for_byte_unchanged() -> None:
    """**실제로 확정된** SO — PATCH가 받는 CONTENT 전 필드(단가 환산 입력인 환율·결제조건·Incoterms·바이어 표기·PO 등) 수정은 409 FROZEN(필드명만, 값·금액 미기재) · 행 전체(version·updated_at 포함)가 그대로다"""
    from app.modules.sales_orders.schemas import SalesOrderUpdateRequest
    from app.modules.sales_orders.service import CONTENT_REQUEST_FIELDS

    assert set(_SO_PATCHES) == set(
        CONTENT_REQUEST_FIELDS
    )  # 매트릭스가 PATCH 스키마의 CONTENT 필드를 빠짐없이 덮는다
    assert set(_SO_PATCHES) <= set(SalesOrderUpdateRequest.model_fields)
    create_market("JP")  # 등록된 시장이어야 값 검증이 아니라 동결 거부가 시험된다
    with logged_in(RoleCode.TRADE) as client:
        so = _confirmed_so(client)
        before = _row_snapshot("sales_orders", so["id"])
        for name, body in _SO_PATCHES.items():
            response = client.patch(
                f"{SO}/{so['id']}", json={"version": so_version(so["id"]), **body}
            )
            assert response.status_code == 409, (name, response.text)
            error = response.json()["error"]
            assert error["code"] == "TRADE_DOCS.DOCUMENT.FROZEN", name
            assert error["detail"]["fields"], name
            assert "1499" not in response.text and "CHANGED" not in response.text  # 값 미기재
        assert _row_snapshot("sales_orders", so["id"]) == before


def test_a_confirmed_order_rejects_line_adds_price_quantity_edits_and_removals() -> None:
    """확정 SO의 라인 추가·수정(수량·단가·무상)·제외는 전부 409 FROZEN — 라인 값·헤더 version·합계 불변"""
    with logged_in(RoleCode.TRADE) as client:
        so = _confirmed_so(client)
        (line,) = so["lines"]
        version = so_version(so["id"])
        other = create_priced_sku(amount=1000)
        attempts = [
            client.post(
                f"{SO}/{so['id']}/lines", json={"version": version, "sku_id": other, "quantity": 1}
            ),
            client.patch(
                f"{SO}/{so['id']}/lines/{line['id']}", json={"version": version, "quantity": 99}
            ),
            client.patch(
                f"{SO}/{so['id']}/lines/{line['id']}",
                json={"version": version, "unit_price": "1.00"},
            ),
            client.patch(
                f"{SO}/{so['id']}/lines/{line['id']}",
                json={"version": version, "is_free": True, "price_reason": "샘플"},
            ),
            client.delete(f"{SO}/{so['id']}/lines/{line['id']}", params={"version": version}),
        ]
        assert [r.status_code for r in attempts] == [409] * 5
        assert {r.json()["error"]["code"] for r in attempts} == {"TRADE_DOCS.DOCUMENT.FROZEN"}
        after = client.get(f"{SO}/{so['id']}").json()
    assert after["lines"] == so["lines"]
    assert (after["version"], after["total_amount"]) == (so["version"], so["total_amount"])


def test_a_frozen_quotation_and_its_reference_documents_reject_content_edits() -> None:
    """QT 발행·PI 생성·PO 발행 — QT는 CONTENT 전 필드·라인 변경이 409 FROZEN이고 행이 그대로다 · PI·PO는 **내용 편집 라우트가 없다**(PATCH는 `/meta`뿐 — 구조적 불변)"""
    paths = app.openapi()["paths"]
    for prefix in (PI, PO):
        patch = {p for p, ops in paths.items() if p.startswith(prefix) and "patch" in ops}
        assert patch == {f"{prefix}/{{{'pi_id' if prefix == PI else 'po_id'}}}/meta"}, patch
        assert (
            not [  # 라인 쓰기 경로도 없다
                p for p, ops in paths.items() if p.startswith(prefix) and "/lines" in p
            ]
        )
    with logged_in(RoleCode.TRADE) as client:
        qt = issued_quotation(client, quantity=3)
        before = _row_snapshot("quotations", qt["id"])
        fx = client.patch(f"{QT}/{qt['id']}", json={"version": qt["version"], "fx_rate": "1499"})
        line = client.patch(
            f"{QT}/{qt['id']}/lines/{qt['lines'][0]['id']}",
            json={"version": qt["version"], "quantity": 99},
        )
        assert (fx.status_code, line.status_code) == (409, 409)
        assert (
            fx.json()["error"]["code"]
            == line.json()["error"]["code"]
            == "TRADE_DOCS.DOCUMENT.FROZEN"
        )
        assert _row_snapshot("quotations", qt["id"]) == before


# ══ ② 스키마·분류 — 가격·환율 필드는 메타 경로에 도달 불가 ═══════════════════════════════


@pytest.mark.parametrize(
    ("table", "schema_path", "schema_name"),
    [
        ("quotations", "app.modules.quotations.schemas", "QuotationMetaUpdateRequest"),
        (
            "proforma_invoices",
            "app.modules.proforma_invoices.schemas",
            "PiMetaUpdateRequest",
        ),
        ("sales_orders", "app.modules.sales_orders.schemas", "SalesOrderMetaUpdateRequest"),
        (
            "purchase_orders",
            "app.modules.purchase_orders.schemas",
            "PurchaseOrderMetaUpdateRequest",
        ),
    ],
    ids=["QT", "PI", "SO", "PO"],
)
def test_the_meta_request_schema_carries_only_free_columns_and_no_price_or_fx_field(
    table: str, schema_path: str, schema_name: str
) -> None:
    """동결 후에도 열려 있는 유일한 편집 통로(메타)의 요청 스키마 = `version` + FREE 열 — 단가·환율·통화·결제조건 필드가 구조적으로 없다(extra=forbid)"""
    import importlib

    schema = getattr(importlib.import_module(schema_path), schema_name)
    assert schema.model_config.get("extra") == "forbid"
    free = columns_of(table, ColumnClass.FREE)
    assert set(schema.model_fields) == {"version"} | set(free), (table, set(schema.model_fields))
    for banned in (
        "unit_price",
        "fx_rate",
        "currency",
        "payment_terms",
        "incoterm",
        "total_amount",
    ):
        assert banned not in schema.model_fields


# ══ 마스터 변경이 동결 문서를 건드리지 않는다 ════════════════════════════════════════════


def test_a_new_master_price_does_not_change_the_unit_prices_of_frozen_documents() -> None:
    """동결 후 마스터에 더 최근 발효 단가를 넣어도 QT·SO 라인의 단가·합계는 그대로다(라인 단가는 동결 시점 스냅샷 — 마스터를 다시 읽지 않는다)"""
    with logged_in(RoleCode.TRADE) as client:
        qt = issued_quotation(client, quantity=4)
        so = _confirmed_so(client)
        sku_ids = [line["sku_id"] for line in so["lines"]] + [
            line["sku_id"] for line in qt["lines"]
        ]
        for sku in sku_ids:
            set_price(sku, 99_999, effective_from=date(2026, 6, 1))
        qt_after = client.get(f"{QT}/{qt['id']}").json()
        so_after = client.get(f"{SO}/{so['id']}").json()
    assert [(ln["unit_price_amount"], ln["line_amount"]) for ln in qt_after["lines"]] == [
        (ln["unit_price_amount"], ln["line_amount"]) for ln in qt["lines"]
    ]
    assert [(ln["unit_price_amount"], ln["line_amount"]) for ln in so_after["lines"]] == [
        (ln["unit_price_amount"], ln["line_amount"]) for ln in so["lines"]
    ]
    assert (so_after["total_amount"], so_after["fx_rate"]) == (so["total_amount"], so["fx_rate"])


def test_free_columns_stay_editable_after_confirmation_without_touching_prices() -> None:
    """성공 방향 — 확정 SO의 내부 메모·담당자(FREE)는 /meta로 수정된다(version만 오르고 환율·합계·라인은 그대로)"""
    with logged_in(RoleCode.TRADE) as client:
        so = _confirmed_so(client)
        r = client.patch(
            f"{SO}/{so['id']}/meta",
            json={"version": so_version(so["id"]), "internal_note": "확정 후 메모"},
        )
        assert r.status_code == 200, r.text
    after = r.json()
    assert after["internal_note"] == "확정 후 메모" and after["version"] == so["version"] + 1
    for key in ("fx_rate", "total_amount", "lines", "payment_terms", "incoterm", "status"):
        assert after[key] == so[key], key


# ══ ③ DB — 동결 완결성·되돌림 거부 ═══════════════════════════════════════════════════════


def _freeze_one_of_each() -> dict[str, int]:
    """동결된 QT·PI·SO·PO 각 1건의 id."""
    with logged_in(RoleCode.TRADE) as client:
        qt = issued_quotation(client)
        pi = create_pi_via_api(client, issued_quotation(client), create_bank_account("USD"))
        so = _confirmed_so(client)
        po = create_po_via_api(client)
    return {
        "quotations": qt["id"],
        "proforma_invoices": pi["id"],
        "sales_orders": so["id"],
        "purchase_orders": po["id"],
    }


@pytest.mark.parametrize(
    "table", ["quotations", "proforma_invoices", "sales_orders", "purchase_orders"]
)
def test_the_database_refuses_to_blank_the_fx_rate_or_terms_of_a_frozen_document(
    table: str,
) -> None:
    """4종 전표 모두 — 동결된 행의 환율·결제조건·Incoterms를 직접 UPDATE로 비우면 `frozen_complete` CHECK가 거부한다(23514) · 동결 시각이 있어야 의미가 있으므로 실제 동결 행에 시험한다"""
    ids = _freeze_one_of_each()
    for column_sql in (
        "fx_rate = NULL, fx_rate_date = NULL",
        "payment_type = NULL, advance_pct_bp = NULL, balance_anchor = NULL, balance_days = NULL",
        "incoterm_code = NULL, incoterm_place = NULL, incoterm_year = NULL",
    ):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.execute(
                text(f"UPDATE {table} SET {column_sql} WHERE id = :i"), {"i": ids[table]}
            )
        assert f"ck_{table}_frozen_complete" in str(caught.value), (table, column_sql)


def test_the_database_refuses_to_send_a_confirmed_order_back_to_received_or_drop_its_evidence() -> (
    None
):
    """확정 SO — RECEIVED로 되돌리는 UPDATE·확정 증적(여신·PI 판정)만 비우는 UPDATE는 CHECK가 거부한다(되돌림·증적 위조 경로가 DB에 없다)"""
    with logged_in(RoleCode.TRADE) as client:
        so = _confirmed_so(client)
    for sql, constraint in (
        ("UPDATE sales_orders SET status = 'RECEIVED' WHERE id = :i", "confirmed_at_consistent"),
        (
            "UPDATE sales_orders SET credit_verdict = NULL WHERE id = :i",
            "credit_verdict_iff_confirmed",
        ),
        (
            "UPDATE sales_orders SET pi_gate_verdict = NULL WHERE id = :i",
            "pi_gate_verdict_iff_confirmed",
        ),
    ):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.execute(text(sql), {"i": so["id"]})
        assert f"ck_sales_orders_{constraint}" in str(caught.value), sql
    assert so_row(so["id"])["status"] == "CONFIRMED"


def test_a_confirmed_order_keeps_its_prices_after_a_hold_and_resume() -> None:
    """확정 → 보류 → 재개를 거쳐도 단가·환율·합계는 그대로다(재개는 게이트를 다시 평가하지도, 값을 다시 읽지도 않는다)"""
    with logged_in(RoleCode.TRADE) as client:
        so = _confirmed_so(client)
        for to, reason in (("ON_HOLD", "보류"), ("CONFIRMED", None)):
            r = client.post(
                f"{SO}/{so['id']}/transitions",
                json={"to": to, "version": so_version(so["id"]), "reason": reason},
                headers=idem(),
            )
            assert r.status_code == 200, r.text
        after = client.get(f"{SO}/{so['id']}").json()
    assert after["status"] == "CONFIRMED"
    assert after["lines"] == so["lines"] and after["fx_rate"] == so["fx_rate"]
    assert (
        after["total_amount"] == so["total_amount"] and after["confirmed_at"] == so["confirmed_at"]
    )
