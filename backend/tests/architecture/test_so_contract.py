"""K. SO 계약 — 커널 등록 대사·열 분류 핀·요청 스키마·라우터 구조·포트·소비자 FK·PO 키 통로 (S3-1 PR-7a / ADR-0051~0053 / design-A A2·A4 / design-B B2·B5).

PR-5a·6a가 남긴 지연 프레임(PENDING 핀·SO 상태 CHECK 3자 대사 skip)이 이번 PR에서 켜졌다 — 여기서 그 활성 여부를 명시적으로 고정한다.
"""

from __future__ import annotations

import ast

import pytest
from sqlalchemy import text

import app.registry  # noqa: F401 — 모든 모델 등록
from app.core.db.base import Base
from app.core.db.session import owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES
from app.modules.trade_docs import chain
from app.modules.trade_docs.constants import DOC_TABLES, LINE_TABLES, STATUS_LOG_TABLES, DocKind
from app.modules.trade_docs.models import STATUS_LOG_MODELS
from app.modules.trade_docs.policy import FIELD_POLICY, ColumnClass, classify, columns_of
from app.modules.trade_docs.quantities import LINE_CONSUMERS, PENDING_CONSUMER_TABLES
from tests.support.astscan import app_sources, imported_modules

pytestmark = pytest.mark.group_k

KIND = DocKind.SALES_ORDER


def test_so_tables_are_registered_in_every_kernel_registry() -> None:
    """SO 3테이블이 전표 상수·이력 모델·table_policy 분류·CHILD_LINKS에 빠짐없이 등재돼 있고 PENDING 핀은 비었다"""
    assert DOC_TABLES[KIND] == "sales_orders" and LINE_TABLES[KIND] == "sales_order_lines"
    assert STATUS_LOG_TABLES[KIND] == "sales_order_status_log"
    assert KIND in STATUS_LOG_MODELS
    assert STATUS_LOG_TABLES[KIND] in IMMUTABLE_TABLES
    assert {"sales_orders", "sales_order_lines"} <= MUTABLE_TABLES
    assert {"sales_orders", "sales_order_lines"} <= set(FIELD_POLICY)
    assert set(PENDING_CONSUMER_TABLES) == set()
    assert {
        (link.parent, link.fk_column)
        for link in chain.CHILD_LINKS
        if link.child_table == "sales_orders"
    } == {(DocKind.QUOTATION, "qt_id"), (DocKind.PROFORMA_INVOICE, "pi_id")}
    # 확정 SO만 QT 수주전환(CONVERTED)을 만든다 — 그 열은 confirmed_at(X-18)
    assert (
        next(
            link.confirmed_column
            for link in chain.CHILD_LINKS
            if link.child_table == "sales_orders" and link.parent is DocKind.QUOTATION
        )
        == "confirmed_at"
    )


def test_so_state_check_three_way_agreement_is_enabled() -> None:
    """지연 프레임 켜짐 — SO 상태 CHECK 3자 대사(StrEnum·machine·DB)가 skip이 아니라 실제로 도는 테이블이 존재한다(PO는 PR-8)"""
    with owner_engine.connect() as connection:
        found = connection.execute(text("SELECT to_regclass('public.sales_orders')")).scalar_one()
    assert found is not None


def test_so_field_policy_pins() -> None:
    """SO 열 분류 핀 — 거래처·참조 FK·복제 원본은 ORIGIN(X-09), PO번호 원문·키·일자·통화·환율·결제조건·요청납기·수량·단가는 CONTENT, FREE는 메모·담당자뿐,
    확정 시각·상태는 SYSTEM — `frozen_at`·`content_rev` 열은 없다(X-03·X-07)"""
    for column in ("qt_id", "pi_id", "buyer_partner_id", "copied_from_id"):
        assert classify("sales_orders", column) is ColumnClass.ORIGIN, column
    for column in (
        "buyer_po_no", "buyer_po_no_key", "buyer_po_date", "buyer_name", "dest_market_code",
        "currency", "fx_rate", "fx_rate_date", "payment_type", "incoterm_code", "total_amount",
        "doc_date",
    ):  # fmt: skip
        assert classify("sales_orders", column) is ColumnClass.CONTENT, column
    assert columns_of("sales_orders", ColumnClass.FREE) == {"internal_note", "assignee_id"}
    assert columns_of("sales_order_lines", ColumnClass.FREE) == frozenset()
    for column in (
        "confirmed_at", "status", "doc_number", "version",
        "credit_verdict", "credit_approval_id", "pi_gate_verdict",
    ):  # fmt: skip
        assert classify("sales_orders", column) is ColumnClass.SYSTEM, column
    for column in ("qt_line_id", "pi_line_id", "so_id"):
        assert classify("sales_order_lines", column) is ColumnClass.ORIGIN, column
    for column in ("quantity", "unit_price_amount", "requested_delivery_date", "sku_id"):
        assert classify("sales_order_lines", column) is ColumnClass.CONTENT, column
    assert {"frozen_at", "content_rev"}.isdisjoint(Base.metadata.tables["sales_orders"].c.keys())


def test_so_request_schemas_forbid_extra_fields_and_carry_no_server_owned_names() -> None:
    """SO 요청 스키마는 전부 extra=forbid이고 상태·번호·합계·확정 시각·PO 키·거래처·통화·참조 FK가 구조적으로 없다"""
    from app.modules.sales_orders import schemas as so_schemas
    from app.modules.trade_chain import router as chain_router

    forbidden = {
        "status",
        "doc_number",
        "total_amount",
        "line_amount",
        "confirmed_at",
        "frozen_at",
        "id",
        "buyer_po_no_key",
        "buyer_partner_id",
        "currency",
        "qt_id",
        "pi_id",
        "content_rev",
        # M10 확정 증적·우회 표식 — 요청 본문으로 확정 판정·승인·게이트를 지정하거나 건너뛸 수 없다(PR-12a)
        "credit_verdict",
        "credit_approval_id",
        "pi_gate_verdict",
        "approval_id",
        "force",
        "skip",
        "bypass",
        "override",
    }
    checked: set[type] = set()
    for module in (so_schemas, chain_router):
        for name, obj in vars(module).items():
            if isinstance(obj, type) and name.startswith(("SalesOrder", "SoLine")):
                if not name.endswith("Request"):
                    continue
                assert obj.model_config.get("extra") == "forbid", name
                assert not set(obj.model_fields) & forbidden, (
                    name,
                    set(obj.model_fields) & forbidden,
                )
                checked.add(obj)
    # 참조 생성·편집·메타 편집·참조 라인·라인 추가·라인 수정·전이 7종 + 확정·승인 요청 2종(PR-12a — 라우터 네임스페이스로 재노출된 `trade_chain/schemas`)
    assert len(checked) == 9
    assert {c.__name__ for c in checked} >= {
        "SalesOrderConfirmRequest",
        "SalesOrderApprovalRequest",
    }
    assert chain_router.SalesOrderTransitionRequest.model_config.get("extra") == "forbid"
    assert not set(chain_router.SalesOrderTransitionRequest.model_fields) & forbidden


def test_so_routes_are_bounded_no_create_no_document_delete() -> None:
    """SO 표면 고정 — 직접 POST /sales-orders 없음(생성은 참조 생성·인테이크 확정 착지뿐)·문서 DELETE 없음(라인 제외만)·게이트 3경로(PR-11a)·**확정·승인 요청 2경로(PR-12a)**"""
    from app.main import app

    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if "/sales-orders" in path
    }
    assert ("POST", "/api/v1/sales-orders") not in routes
    assert {m for m, p in routes if p == "/api/v1/sales-orders/{so_id}"} == {"GET", "PATCH"}
    # PR-12a: 확정(`/confirm`)·여신 초과 승인 요청(`/approval-requests`) — 확정 통로는 이 1경로뿐이다(상태 대입 `PATCH`·범용 전이로는 못 넘는다)
    assert {p for _, p in routes if "confirm" in p or "approv" in p} == {
        "/api/v1/sales-orders/{so_id}/confirm",
        "/api/v1/sales-orders/{so_id}/approval-requests",
    }
    # PR-11a: 게이트 판정 조회·override 부여·철회 3경로
    assert {p for _, p in routes if "gate" in p} == {
        "/api/v1/sales-orders/{so_id}/gates",
        "/api/v1/sales-orders/{so_id}/gate-overrides",
        "/api/v1/sales-orders/{so_id}/gate-overrides/revoke",
    }
    assert routes == {
        ("GET", "/api/v1/sales-orders"),
        ("GET", "/api/v1/sales-orders/export.csv"),
        ("GET", "/api/v1/sales-orders/{so_id}"),
        ("PATCH", "/api/v1/sales-orders/{so_id}"),
        ("PATCH", "/api/v1/sales-orders/{so_id}/meta"),
        ("GET", "/api/v1/sales-orders/{so_id}/status-log"),
        ("POST", "/api/v1/sales-orders/{so_id}/lines"),
        ("PATCH", "/api/v1/sales-orders/{so_id}/lines/{line_id}"),
        ("DELETE", "/api/v1/sales-orders/{so_id}/lines/{line_id}"),
        ("POST", "/api/v1/sales-orders/{so_id}/transitions"),
        ("POST", "/api/v1/sales-orders/{so_id}/confirm"),
        ("POST", "/api/v1/sales-orders/{so_id}/approval-requests"),
        ("GET", "/api/v1/sales-orders/{so_id}/gates"),
        ("POST", "/api/v1/sales-orders/{so_id}/gate-overrides"),
        ("POST", "/api/v1/sales-orders/{so_id}/gate-overrides/revoke"),
        ("POST", "/api/v1/quotations/{qt_id}/sales-orders"),
        ("POST", "/api/v1/proforma-invoices/{pi_id}/sales-orders"),
        # S3-2 PR-3a — SO 참조 수출선적(미리보기·생성). SO 자신은 바뀌지 않고 같은 TX에서 IN_SHIPMENT로 수렴할 뿐이다(확정 경로 아님)
        ("POST", "/api/v1/sales-orders/{so_id}/shipments/preview"),
        ("POST", "/api/v1/sales-orders/{so_id}/shipments"),
    }


def test_so_routes_require_a_trade_role_and_creation_and_transition_an_idempotency_key() -> None:
    """SO 쓰기 라우트는 무역 역할 게이트를(권한 매트릭스와 이중), 참조 생성·전이는 멱등 키를 소스 수준에서도 요구하고 목록은 페이지네이션한다"""
    sources = app_sources()
    chain_router = sources["modules/trade_chain/router.py"]
    for name in (
        "create_sales_order_from_quotation",
        "create_sales_order_from_proforma_invoice",
        "transition_sales_order",
        "confirm_sales_order",
        "request_sales_order_credit_approval",
    ):
        function = next(
            n for n in ast.walk(chain_router) if isinstance(n, ast.FunctionDef) and n.name == name
        )
        assert "IdempotencyKey" in ast.unparse(function.args), name
        assert "require_roles(*CAN_WRITE)" in ast.unparse(function.decorator_list[0]), name
    so_router = ast.unparse(sources["modules/sales_orders/router.py"])
    assert so_router.count("require_roles(*CAN_WRITE)") == 5  # 헤더 편집·메타·라인 추가·수정·제외
    listing = next(
        n
        for n in ast.walk(sources["modules/sales_orders/router.py"])
        if isinstance(n, ast.FunctionDef) and n.name == "list_sales_orders"
    )
    assert "PageParams" in ast.unparse(listing.args)


def test_the_allocation_port_is_bound_non_null_and_fail_visible() -> None:
    """`get_allocation_port()`는 항상 non-None이고 Protocol(on_confirmed·on_cancelled)을 만족하며 P3 기본 구현은 NOT_IMPLEMENTED를 그대로 노출한다(할당이 된 것처럼 보이지 않는다)"""
    from app.modules.sales_orders.ports import (
        AllocationOutcome,
        AllocationStatus,
        NoAllocationPort,
        get_allocation_port,
    )

    port = get_allocation_port()
    assert port is not None and isinstance(port, NoAllocationPort)
    for method in ("on_confirmed", "on_cancelled"):
        outcome = getattr(port, method)(None, None)
        assert isinstance(outcome, AllocationOutcome)
        assert outcome.status is AllocationStatus.NOT_IMPLEMENTED and outcome.note
    assert [s.value for s in AllocationStatus] == ["NOT_IMPLEMENTED"]


def test_the_sales_order_modules_have_no_external_call_or_ledger_imports() -> None:
    """포트·SO 모듈은 네트워크 라이브러리를 임포트하지 않는다(트랜잭션 안 외부 호출 금지) — 재고 원장(Phase 4)도 건드리지 않는다"""
    network = {"httpx", "requests", "urllib", "urllib3", "aiohttp", "smtplib", "socket", "ftplib"}
    for rel, tree in app_sources().items():
        if not rel.startswith("modules/sales_orders/"):
            continue
        used = imported_modules(tree)
        assert not used & network, (rel, used & network)
        assert not used & {"inventory", "stock", "ledger"}, rel
    ports_tree = app_sources()["modules/sales_orders/ports.py"]
    assert not any(
        isinstance(n, ast.Import | ast.ImportFrom)
        and any(part in network for part in ast.unparse(n).replace(".", " ").split())
        for n in ast.walk(ports_tree)
    )


def test_so_line_consumers_point_at_the_right_source_line_tables() -> None:
    """LINE_CONSUMERS 대사 — SO 라인의 `qt_line_id`는 quotation_lines를, `pi_line_id`는 proforma_invoice_lines를 가리키는 FK이고
    소비 헤더 FK·수량·삭제 열이 실재한다(등록 문자열 오타 방지)"""
    expected = {
        ("QT_LINE", "qt_line_id"): "quotation_lines",
        ("PI_LINE", "pi_line_id"): "proforma_invoice_lines",
    }
    seen = set()
    child = Base.metadata.tables["sales_order_lines"]
    for kind, specs in LINE_CONSUMERS.items():
        for spec in specs:
            if spec.child_line_table != "sales_order_lines":
                continue
            targets = {fk.column.table.name for fk in child.c[spec.line_fk_col].foreign_keys}
            assert targets == {expected[(kind, spec.line_fk_col)]}, (kind, spec.name)
            assert spec.child_header_table == "sales_orders" and spec.child_header_fk == "so_id"
            assert {spec.qty_col, "deleted_at"} <= set(child.c.keys())
            seen.add((kind, spec.line_fk_col))
    assert seen == set(expected)  # 공회전 방지 — 두 소비 관계가 모두 검사됐다


def test_the_po_comparison_key_is_written_only_through_the_normalizer() -> None:
    """`buyer_po_no_key`는 정규화 함수(`po_columns`)의 결과로만 쓴다 — SO 서비스 밖에서 이 열을 대입·생성자 키워드로 쓰는 코드는 0건(손으로 만든 키 금지)"""
    allowed = {
        "modules/sales_orders/service.py",
        "modules/sales_orders/models.py",
        # 오더 인테이크(PR-13a)의 PO 비교 키도 같은 정규화기(`po_columns`)의 결과만 쓴다 — 아래에서 소스로 대사한다.
        "modules/order_intake/service.py",
        "modules/order_intake/models.py",
    }
    for rel, tree in app_sources().items():
        if rel in allowed:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "buyer_po_no_key":
                assert not isinstance(node.ctx, ast.Store), rel
            if isinstance(node, ast.keyword) and node.arg == "buyer_po_no_key":
                raise AssertionError(f"{rel}: buyer_po_no_key를 직접 만든다")
    intake_service = ast.unparse(app_sources()["modules/order_intake/service.py"])
    assert "po_columns(" in intake_service and "normalize_buyer_po_no" not in intake_service
    service = ast.unparse(app_sources()["modules/sales_orders/service.py"])
    assert "po_columns(" in service
    assert (
        service.count("normalize_buyer_po_no") == 0
    )  # 서비스는 정규화기를 직접 쓰지 않고 po_columns만 쓴다


def test_every_fk_from_so_tables_is_either_a_chain_link_or_explained() -> None:
    """SO 테이블에서 나가는 전표 FK(qt_id·pi_id 복합·라인 출처·이력)는 사슬 레지스트리에 전수 등록돼 있다 — 새 후속이 생겨도 CI가 잡는다"""
    from tests.architecture.test_doc_chain_contract import _fks_to_chain_tables

    scanned = {
        (child, col)
        for child, col, _target in _fks_to_chain_tables()
        if child.startswith("sales_order")
    }
    assert ("sales_orders", "qt_id") in scanned and ("sales_orders", "pi_id") in scanned
    assert ("sales_order_lines", "qt_line_id") in scanned
    assert ("sales_order_lines", "pi_line_id") in scanned
    assert ("sales_orders", "copied_from_id") in scanned
    registered = (
        {(link.child_table, link.fk_column) for link in chain.CHILD_LINKS}
        | {
            (spec.child_line_table, spec.line_fk_col)
            for specs in LINE_CONSUMERS.values()
            for spec in specs
        }
        | set(chain.NON_CHILD_FK_ALLOWLIST)
    )
    assert scanned <= registered, sorted(scanned - registered)


def test_the_patch_schema_fields_are_exactly_the_content_whitelist_plus_free_columns() -> None:
    """PATCH 스키마 필드 집합 == CONTENT 요청 화이트리스트 + FREE 2열 + version — 스키마와 서비스 화이트리스트가 따로 놀지 않는다(FIELD_POLICY 핀과 3자 대사)"""
    from app.modules.sales_orders.schemas import (
        SalesOrderMetaUpdateRequest,
        SalesOrderUpdateRequest,
    )
    from app.modules.sales_orders.service import _FREE_FIELDS, CONTENT_REQUEST_FIELDS

    assert set(SalesOrderUpdateRequest.model_fields) == (
        {"version"} | CONTENT_REQUEST_FIELDS | set(_FREE_FIELDS)
    )
    assert set(SalesOrderMetaUpdateRequest.model_fields) == {"version"} | set(_FREE_FIELDS)
    content = columns_of("sales_orders", ColumnClass.CONTENT)
    # 요청 필드 중 컬럼으로 바로 대응하는 것은 전부 CONTENT 분류다(결제조건·Incoterms는 묶음 필드라 제외)
    direct = CONTENT_REQUEST_FIELDS - {"payment_terms", "incoterm"}
    assert direct <= content, sorted(direct - content)
    assert set(_FREE_FIELDS) == columns_of("sales_orders", ColumnClass.FREE)
