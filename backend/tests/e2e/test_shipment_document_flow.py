"""A·K. 문서 흐름 SHIPMENT 노드 — 수출선적을 SO 아래에 (S3-2 PR-3c / design-D X1·D8 / design-A A12 / DESIGN §14 ⑦).

■ A: QT→PI→SO→**SH** 사슬을 어느 노드(선적 포함)에서 조회해도 같은 트리. 선적 노드는 **자기 SO 바로 뒤**(부모 = 그 SO), 취소 선적도 이력으로 포함.
  직접(인테이크) 수주 아래의 선적도 붙는다. 수입선적(PO 원천)은 흐름 밖 → 404(PR-5a·Q-13 몫).
■ K(렌즈 7): 흐름 서비스 쿼리 **5회 이내**(진입 종류 4가지 전부)이고 SO·선적 수를 늘려도 같은 횟수(선적은 SO id 목록 1쿼리 — N+1 0).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import document_flow
from app.modules.trade_docs.constants import DocKind
from tests.factories.shipments import (
    cancel,
    confirm_in_place,
    confirmed_so,
    created,
    raw_shipment,
)
from tests.factories.trade import (
    create_bank_account,
    create_buyer,
    create_pi_via_api,
    create_priced_sku,
    create_so_from_pi_via_api,
    create_so_from_qt_via_api,
    issued_quotation,
    logged_in,
    raw_po,
)

pytestmark = pytest.mark.group_a

FLOW = "/api/v1/document-flow"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _flow(client: TestClient, kind: str, doc_id: int) -> dict[str, Any]:
    response = client.get(f"{FLOW}/{kind}/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _shape(flow: dict[str, Any]) -> list[tuple[str, int, tuple[str, int] | None]]:
    return [
        (n["kind"], n["id"], (n["parent_kind"], n["parent_id"]) if n["parent_kind"] else None)
        for n in flow["nodes"]
    ]


def _chain(client: TestClient, *, qt_quantity: int = 20) -> dict[str, Any]:
    """QT(20) → PI(10) → SO_pi(10) · QT 직접 SO_qt(10). 두 SO를 확정하고 SO_pi에 선적 2건(둘째 취소)·SO_qt에 선적 1건."""
    qt = issued_quotation(
        client, create_buyer(), [create_priced_sku(amount=1500)], quantity=qt_quantity
    )
    line = qt["lines"][0]["id"]
    pi = create_pi_via_api(
        client, qt, create_bank_account("USD"), lines=[{"source_line_id": line, "quantity": 10}]
    )
    so_pi = create_so_from_pi_via_api(client, pi)
    so_qt = create_so_from_qt_via_api(client, qt, lines=[{"source_line_id": line, "quantity": 10}])
    for so in (so_pi, so_qt):
        confirm_in_place(so["id"])
    sh_a = created(client, so_pi["id"], [(so_pi["lines"][0]["id"], 4)])
    sh_b = created(client, so_pi["id"], [(so_pi["lines"][0]["id"], 3)])
    assert cancel(client, sh_b["id"]).status_code == 200
    sh_c = created(client, so_qt["id"], [(so_qt["lines"][0]["id"], 5)])
    return {"qt": qt, "pi": pi, "so_pi": so_pi, "so_qt": so_qt, "a": sh_a, "b": sh_b, "c": sh_c}


def test_export_shipments_hang_under_their_sales_order_from_every_entry(trade: TestClient) -> None:
    """A — 어느 노드(QT·PI·SO·선적)에서 들어와도 같은 트리: 선적은 자기 SO 바로 뒤·부모 = 그 SO·SO 안에서 id 순, 취소 선적 포함, `is_current` 1개"""
    c = _chain(trade)
    qt, pi, so_pi, so_qt = c["qt"], c["pi"], c["so_pi"], c["so_qt"]
    expected = [
        ("QUOTATION", qt["id"], None),
        ("PROFORMA_INVOICE", pi["id"], ("QUOTATION", qt["id"])),
        ("SALES_ORDER", so_pi["id"], ("PROFORMA_INVOICE", pi["id"])),
        ("SHIPMENT", c["a"]["id"], ("SALES_ORDER", so_pi["id"])),
        ("SHIPMENT", c["b"]["id"], ("SALES_ORDER", so_pi["id"])),
        ("SALES_ORDER", so_qt["id"], ("QUOTATION", qt["id"])),
        ("SHIPMENT", c["c"]["id"], ("SALES_ORDER", so_qt["id"])),
    ]
    for kind, doc_id, _parent in expected:
        flow = _flow(trade, kind, doc_id)
        assert flow["root_kind"] == "QUOTATION" and flow["root_id"] == qt["id"], (kind, doc_id)
        assert _shape(flow) == expected, (kind, doc_id)
        current = [n for n in flow["nodes"] if n["is_current"]]
        assert [(n["kind"], n["id"]) for n in current] == [(kind, doc_id)]
    flow = _flow(trade, "SHIPMENT", c["a"]["id"])
    by_id = {(n["kind"], n["id"]): n for n in flow["nodes"]}
    shipment = by_id[("SHIPMENT", c["a"]["id"])]
    assert shipment == {
        "kind": "SHIPMENT",
        "id": c["a"]["id"],
        "doc_number": c["a"]["doc_number"],
        "status": "PLANNED",
        "doc_date": c["a"]["doc_date"],
        "currency": "USD",
        "total_amount": 6000,
        "total_text": "60.00",
        "parent_kind": "SALES_ORDER",
        "parent_id": so_pi["id"],
        "is_current": True,
    }
    assert (
        by_id[("SHIPMENT", c["b"]["id"])]["status"] == "CANCELLED"
    )  # 흐름은 이력 — 취소 선적도 보인다
    assert by_id[("SALES_ORDER", so_pi["id"])]["status"] == "IN_SHIPMENT"


def test_a_direct_sales_order_keeps_its_shipments_and_is_the_root(trade: TestClient) -> None:
    """A — 직접(인테이크) 수주: 뿌리 = 그 SO, 노드 = SO + 그 수출선적(SO에서·선적에서 같은 트리). 다른 SO의 선적은 섞이지 않는다"""
    so = confirmed_so((10,))
    other = confirmed_so((10,))
    first = created(trade, so["id"], [(so["line_ids"][0], 2)])
    second = created(trade, so["id"], [(so["line_ids"][0], 2)])
    created(trade, other["id"], [(other["line_ids"][0], 2)])
    expected = [
        ("SALES_ORDER", so["id"], None),
        ("SHIPMENT", first["id"], ("SALES_ORDER", so["id"])),
        ("SHIPMENT", second["id"], ("SALES_ORDER", so["id"])),
    ]
    for kind, doc_id in (("SALES_ORDER", so["id"]), ("SHIPMENT", second["id"])):
        flow = _flow(trade, kind, doc_id)
        assert flow["root_kind"] == "SALES_ORDER" and flow["root_id"] == so["id"]
        assert _shape(flow) == expected
        assert [n["id"] for n in flow["nodes"] if n["is_current"]] == [doc_id]
    # 선적이 없는 직접 수주는 예전처럼 노드 1개
    lone = confirmed_so((3,))
    assert _shape(_flow(trade, "SALES_ORDER", lone["id"])) == [("SALES_ORDER", lone["id"], None)]


def test_import_or_unknown_shipments_are_outside_the_flow(trade: TestClient) -> None:
    """A — 수입선적(PO 원천)은 판매 사슬 밖이라 404(흐름에 PO가 없다 — PR-5a·Q-13), 없는 선적 404, 0 id·PO 종류 422. 조회 전용 역할도 읽는다"""
    holder = confirmed_so((5,))
    imported = raw_shipment(holder["id"], kind="IMPORT", po_id=raw_po("ISSUED"))
    response = trade.get(f"{FLOW}/SHIPMENT/{imported}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COMMON.RESOURCE.NOT_FOUND"
    assert trade.get(f"{FLOW}/SHIPMENT/999999999").status_code == 404
    assert trade.get(f"{FLOW}/SHIPMENT/0").status_code == 422
    assert trade.get(f"{FLOW}/PURCHASE_ORDER/1").status_code == 422
    exported = created(trade, holder["id"], [(holder["line_ids"][0], 1)])
    flow = _flow(trade, "SALES_ORDER", holder["id"])
    assert [n["id"] for n in flow["nodes"] if n["kind"] == "SHIPMENT"] == [exported["id"]]
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert client.get(f"{FLOW}/SHIPMENT/{exported['id']}").status_code == 200, role


def test_a_soft_deleted_shipment_is_neither_an_entry_nor_a_node(trade: TestClient) -> None:
    """A — 삭제(soft delete) 행은 흐름에 없다: 삭제 선적으로 들어오면 404, SO의 흐름에도 노드가 없다(취소 선적은 이력이라 남는 것과 구분 —
    선적 헤더 삭제 API는 없고 원시 UPDATE로만 만든다)"""
    so = confirmed_so((10,))
    kept = created(trade, so["id"], [(so["line_ids"][0], 1)])
    gone = created(trade, so["id"], [(so["line_ids"][0], 1)])
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE shipments SET deleted_at = now() WHERE id = :i"), {"i": gone["id"]}
        )
    assert trade.get(f"{FLOW}/SHIPMENT/{gone['id']}").status_code == 404
    for kind, doc_id in (("SALES_ORDER", so["id"]), ("SHIPMENT", kept["id"])):
        assert [n["id"] for n in _flow(trade, kind, doc_id)["nodes"]] == [so["id"], kept["id"]]


def test_a_shipment_of_a_soft_deleted_sales_order_is_not_an_entry(trade: TestClient) -> None:
    """A — 원천 SO가 삭제(soft delete)됐으면 그 SO의 살아 있는 선적으로 들어와도 SO 진입과 같은 404다(QT 사슬·직접 수주 모두 — 현재 전표 강조가
    없는 트리나 삭제 SO 뿌리를 내보내지 않는다. SO 헤더 삭제 API는 없고 원시 UPDATE로만 만든다 — PR-3c 적대 검토)"""
    chain = _chain(trade)
    direct = confirmed_so((10,))
    direct_shipment = created(trade, direct["id"], [(direct["line_ids"][0], 1)])
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET deleted_at = now() WHERE id IN (:a, :b)"),
            {"a": chain["so_pi"]["id"], "b": direct["id"]},
        )
    for so_id, shipment_id in (
        (chain["so_pi"]["id"], chain["a"]["id"]),
        (direct["id"], direct_shipment["id"]),
    ):
        assert trade.get(f"{FLOW}/SALES_ORDER/{so_id}").status_code == 404
        response = trade.get(f"{FLOW}/SHIPMENT/{shipment_id}")
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "COMMON.RESOURCE.NOT_FOUND"
    # 살아 있는 SO의 선적은 그대로 들어온다(필터가 과하게 막지 않는다)
    flow = _flow(trade, "SHIPMENT", chain["c"]["id"])
    assert [n["id"] for n in flow["nodes"] if n["is_current"]] == [chain["c"]["id"]]


# ── K. 쿼리 수 상한 ─────────────────────────────────────────────────────────────


def _service_queries(kind: DocKind, doc_id: int) -> int:
    """흐름 서비스 1회 호출의 SQL 문 수(인증·세션 조회 제외 — 서비스 자체 계약)."""
    from sqlalchemy import event

    from app.core.db.session import engine

    statements: list[str] = []

    def hook(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", hook)
    try:
        document_flow.document_flow(kind, doc_id)
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    return len(statements)


@pytest.mark.group_k
def test_the_flow_stays_within_five_queries_from_every_entry_and_does_not_grow(
    trade: TestClient,
) -> None:
    """K(렌즈 7) — QT·PI·SO·선적 어디서 들어와도 쿼리 ≤ 5(design-D X1 "5회 이내"), SO·선적을 늘려도 같은 횟수(선적은 SO id 목록 1쿼리).
    직접(인테이크) 수주 사슬도 ≤ 5이고 선적 수와 무관하다"""
    c = _chain(trade, qt_quantity=40)
    entries = [
        (DocKind.QUOTATION, c["qt"]["id"]),
        (DocKind.PROFORMA_INVOICE, c["pi"]["id"]),
        (DocKind.SALES_ORDER, c["so_pi"]["id"]),
        (DocKind.SHIPMENT, c["a"]["id"]),
    ]
    before = {kind: _service_queries(kind, doc_id) for kind, doc_id in entries}
    assert all(1 <= n <= 5 for n in before.values()), before
    # SO 1건 더(QT 직접)·선적 4건 더 — 횟수는 그대로여야 한다
    extra = create_so_from_qt_via_api(
        trade, c["qt"], lines=[{"source_line_id": c["qt"]["lines"][0]["id"], "quantity": 5}]
    )
    confirm_in_place(extra["id"])
    created(trade, extra["id"], [(extra["lines"][0]["id"], 1)])
    for _ in range(3):
        created(trade, c["so_pi"]["id"], [(c["so_pi"]["lines"][0]["id"], 1)])
    after = {kind: _service_queries(kind, doc_id) for kind, doc_id in entries}
    assert after == before
    assert len(_flow(trade, "QUOTATION", c["qt"]["id"])["nodes"]) == 12
    direct = confirmed_so((10,))
    probe = created(trade, direct["id"], [(direct["line_ids"][0], 1)])
    direct_before = (
        _service_queries(DocKind.SALES_ORDER, direct["id"]),
        _service_queries(DocKind.SHIPMENT, probe["id"]),
    )
    for _ in range(3):
        created(trade, direct["id"], [(direct["line_ids"][0], 1)])
    direct_after = (
        _service_queries(DocKind.SALES_ORDER, direct["id"]),
        _service_queries(DocKind.SHIPMENT, probe["id"]),
    )
    assert direct_after == direct_before and max(direct_before) <= 5, direct_before


@pytest.mark.group_k
def test_the_http_flow_query_count_is_constant_in_the_number_of_shipments(
    trade: TestClient,
) -> None:
    """K — HTTP 관통(인증 포함)에서도 선적 1건 vs 6건의 쿼리 수가 같다(N+1 0)"""
    from sqlalchemy import event

    from app.core.db.session import engine

    so = confirmed_so((10, 10))
    one = created(trade, so["id"], [(so["line_ids"][0], 1)])

    def count() -> int:
        seen: list[str] = []

        def hook(*_args: Any) -> None:
            seen.append("q")

        event.listen(engine, "before_cursor_execute", hook)
        try:
            assert trade.get(f"{FLOW}/SHIPMENT/{one['id']}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len(seen)

    small = count()
    for _ in range(5):
        created(trade, so["id"], [(so["line_ids"][1], 1)])
    assert len(_flow(trade, "SHIPMENT", one["id"])["nodes"]) == 7
    assert count() == small
