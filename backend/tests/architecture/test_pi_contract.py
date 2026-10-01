"""K. PI·잔량 소비자·만료 스윕 계약 — 등록 대사·요청 스키마·PENDING 소거·자기검사 (S3-1 PR-6a / ADR-0052·0053·0056 / design-integrated X-19·§2.6).

PR-5a가 남긴 PENDING 핀(`PENDING_CHILD_TABLES`)이 이번 PR에서 소거를 요구했고(proforma_invoices), 지연 프레임이던
"상태 CHECK 3자 대사"(test_doc_machines)의 PI 몫이 이 PR에서 켜진다 — 여기서 그 활성 여부를 명시적으로 고정한다.
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
from tests.support.astscan import app_sources, module_of

pytestmark = pytest.mark.group_k


def test_line_consumers_register_exactly_the_three_s31_relations() -> None:
    """LINE_CONSUMERS 등록 대사 — QT_LINE←PI_LINE.qt_line_id·QT_LINE←SO_LINE.qt_line_id·PI_LINE←SO_LINE.pi_line_id 3건(X-19)이고
    SO_LINE·PO_LINE 소비자(선적·입고)는 S3-1에 없다"""
    registered = {
        (
            kind,
            spec.child_line_table,
            spec.line_fk_col,
            spec.child_header_table,
            spec.child_header_fk,
        )
        for kind, specs in LINE_CONSUMERS.items()
        for spec in specs
    }
    assert registered == {
        ("QT_LINE", "proforma_invoice_lines", "qt_line_id", "proforma_invoices", "pi_id"),
        ("QT_LINE", "sales_order_lines", "qt_line_id", "sales_orders", "so_id"),
        ("PI_LINE", "sales_order_lines", "pi_line_id", "sales_orders", "so_id"),
    }
    assert LINE_CONSUMERS["SO_LINE"] == () and LINE_CONSUMERS["PO_LINE"] == ()
    assert all(spec.kind == "FULFILL" for specs in LINE_CONSUMERS.values() for spec in specs)


def test_pending_consumer_tables_are_exactly_the_registered_tables_missing_from_metadata() -> None:
    """PENDING_CONSUMER_TABLES는 등록된 소비자 중 metadata에 아직 없는 테이블과 정확히 같다 — PR-7a가 SO 라인을 만들며 소거했다(지금은 공집합)"""
    absent = {
        spec.child_line_table
        for specs in LINE_CONSUMERS.values()
        for spec in specs
        if spec.child_line_table not in Base.metadata.tables
    }
    assert absent == set(PENDING_CONSUMER_TABLES) == set(), (
        f"metadata에 없는 소비자 테이블 {sorted(absent)} ≠ PENDING {sorted(PENDING_CONSUMER_TABLES)} — "
        "소비자 테이블을 만든 PR은 quantities.PENDING_CONSUMER_TABLES에서 지우세요(테이블이 없는 등록을 더했다면 거기 적으세요)."
    )


def test_registered_consumer_columns_exist_when_the_table_exists() -> None:
    """실재하는 소비자 테이블은 소비 FK·수량·헤더 FK·삭제 열과 헤더의 삭제·상태 열이 실제로 있다(등록 문자열 오타 방지)"""
    checked = 0
    for specs in LINE_CONSUMERS.values():
        for spec in specs:
            child = Base.metadata.tables.get(spec.child_line_table)
            if child is None:
                continue
            header = Base.metadata.tables[spec.child_header_table]
            assert {spec.line_fk_col, spec.qty_col, spec.child_header_fk, "deleted_at"} <= set(
                child.c.keys()
            )
            assert {"id", "deleted_at", "status"} <= set(header.c.keys())
            fk_targets = {fk.column.table.name for fk in child.c[spec.line_fk_col].foreign_keys}
            assert fk_targets, f"{spec.child_line_table}.{spec.line_fk_col}에 FK가 없다"
            checked += 1
    assert checked >= 1  # 공회전 방지 — PI 라인 소비자가 실제로 검사됐다


def test_chain_pending_is_empty_now() -> None:
    """PENDING_CHILD_TABLES에서 proforma_invoices(PR-6a)·sales_orders(PR-7a)가 모두 소거됐다 — CHILD_LINKS는 PI·SO를 실테이블로 판정한다"""
    from tests.architecture.test_doc_chain_contract import PENDING_CHILD_TABLES

    assert set() == PENDING_CHILD_TABLES
    assert "sales_orders" in Base.metadata.tables
    assert "proforma_invoices" in Base.metadata.tables
    assert any(link.child_table == "proforma_invoices" for link in chain.CHILD_LINKS)
    assert chain.links_for(DocKind.QUOTATION)[0].child_table == "proforma_invoices"


def test_pi_tables_are_registered_in_every_kernel_registry() -> None:
    """PI 4테이블이 전표 상수·이력 모델·table_policy 분류에 빠짐없이 등재돼 있다"""
    kind = DocKind.PROFORMA_INVOICE
    assert DOC_TABLES[kind] == "proforma_invoices" and LINE_TABLES[kind] == "proforma_invoice_lines"
    assert STATUS_LOG_TABLES[kind] == "proforma_invoice_status_log"
    assert kind in STATUS_LOG_MODELS
    assert STATUS_LOG_TABLES[kind] in IMMUTABLE_TABLES
    assert {"proforma_invoices", "proforma_invoice_lines", "bank_accounts"} <= MUTABLE_TABLES
    assert {"proforma_invoices", "proforma_invoice_lines"} <= set(FIELD_POLICY)


def test_pi_field_policy_pins() -> None:
    """PI 열 분류 핀 — 참조 FK·거래처는 ORIGIN, 은행 스냅샷 6열·계좌 FK·유효기간·주소는 CONTENT(동결), FREE는 메모·담당자뿐, PI에는 동결 편집 구간이 없다"""
    for column in ("qt_id", "buyer_partner_id", "copied_from_id"):
        assert classify("proforma_invoices", column) is ColumnClass.ORIGIN, column
    for column in (
        "bank_account_id", "bank_beneficiary_name", "bank_beneficiary_address", "bank_name",
        "bank_address", "bank_account_no", "bank_swift_code", "valid_until", "buyer_address",
        "total_amount", "currency", "fx_rate", "payment_type", "incoterm_code",
    ):  # fmt: skip
        assert classify("proforma_invoices", column) is ColumnClass.CONTENT, column
    assert columns_of("proforma_invoices", ColumnClass.FREE) == {"internal_note", "assignee_id"}
    assert columns_of("proforma_invoice_lines", ColumnClass.FREE) == frozenset()
    assert classify("proforma_invoice_lines", "qt_line_id") is ColumnClass.ORIGIN
    assert classify("proforma_invoices", "frozen_at") is ColumnClass.SYSTEM


def test_the_pi_state_check_is_now_enabled_and_agrees_with_the_machine() -> None:
    """지연 프레임 켜짐 — PI(PR-6a)·SO(PR-7a) 상태 CHECK 3자 대사(StrEnum·machine·DB)가 skip이 아니라 실제로 도는 테이블이 존재한다(PO는 PR-8a가 켰다)"""
    with owner_engine.connect() as connection:
        exists = {
            table: connection.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
            ).scalar_one()
            is not None
            for table in DOC_TABLES.values()
        }
    assert all(exists.values()), (
        exists
    )  # 4종 전부 — PO는 PR-8a(M06)가 켰다(StrEnum·machine·DB CHECK 3자 대사 skip 0)


def test_bank_account_number_is_in_no_unique_key() -> None:
    """계좌번호 열은 어떤 유니크 인덱스·제약에도 없다(중복 계좌는 서비스가 (정규화 번호, SWIFT)로 검사) — test_secret_boundaries의 전표 확장 핀"""
    for name in ("bank_accounts", "proforma_invoices"):
        table = Base.metadata.tables[name]
        for constraint in table.constraints:
            if type(constraint).__name__ == "UniqueConstraint":
                assert not any("account_no" in c.name for c in constraint.columns), constraint.name
        for index in table.indexes:
            if index.unique:
                assert not any("account_no" in c.name for c in index.columns), index.name


# ── 요청 스키마·라우터 구조 ─────────────────────────────────────────────────────


def test_pi_request_schemas_forbid_extra_fields_and_carry_no_server_owned_names() -> None:
    """PI·은행 계좌 요청 스키마는 전부 extra=forbid이고 status·doc_number·total_amount·frozen_at 같은 서버 소유 필드가 구조적으로 없다"""
    from app.modules.bank_accounts import schemas as bank_schemas
    from app.modules.proforma_invoices import schemas as pi_schemas

    forbidden = {"status", "doc_number", "total_amount", "line_amount", "frozen_at", "id", "qt_id"}
    checked = 0
    for module in (bank_schemas, pi_schemas):
        for name, obj in vars(module).items():
            if isinstance(obj, type) and name.endswith(("Request", "Overrides")):
                assert obj.model_config.get("extra") == "forbid", name
                assert not set(obj.model_fields) & forbidden, (
                    name,
                    set(obj.model_fields) & forbidden,
                )
                checked += 1
    assert checked == 6  # 은행 2(Create·Update) + PI(Line·Overrides·Create·MetaUpdate)


def test_pi_create_and_transition_routes_require_a_trade_role_and_an_idempotency_key() -> None:
    """PI 생성·취소 라우트는 무역 역할 게이트+멱등 키를, 은행 계좌 쓰기는 ADMIN 게이트를 소스 수준에서도 요구한다(권한 매트릭스와 이중)"""
    sources = app_sources()
    router_text = ast.unparse(sources["modules/trade_chain/router.py"])
    assert router_text.count("require_roles(*CAN_WRITE)") >= 5  # QT 3 + PI 미리보기·생성·취소
    for name in ("create_proforma_invoice", "transition_proforma_invoice"):
        function = next(
            n
            for n in ast.walk(sources["modules/trade_chain/router.py"])
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        assert "IdempotencyKey" in ast.unparse(function.args), name
    bank_router = ast.unparse(sources["modules/bank_accounts/router.py"])
    assert bank_router.count("dependencies=[ADMIN_ONLY]") == 3  # 등록·수정·비활성


def test_pi_and_bank_lists_are_paginated() -> None:
    """목록 API는 전부 PageParams(기본 50) — PI·은행 계좌 목록 함수가 offset/limit을 받는다"""
    for rel, function in (
        ("modules/proforma_invoices/router.py", "list_proforma_invoices"),
        ("modules/bank_accounts/router.py", "list_bank_accounts"),
    ):
        node = next(
            n
            for n in ast.walk(app_sources()[rel])
            if isinstance(n, ast.FunctionDef) and n.name == function
        )
        assert "PageParams" in ast.unparse(node.args), rel


def test_converge_payment_status_has_no_production_caller_yet() -> None:
    """`converge_payment_status`는 PR-6a에서 호출자가 테스트뿐이다(PR-10 payments가 배선) — 정의 파일 밖 앱 코드에서 언급되면 이 핀이 실패해 등록(no_auto_confirm 엔트리)을 상기시킨다"""
    mentions = {
        rel
        for rel, tree in app_sources().items()
        if any(
            (isinstance(n, ast.Name) and n.id == "converge_payment_status")
            or (isinstance(n, ast.Attribute) and n.attr == "converge_payment_status")
            or (
                isinstance(n, ast.ImportFrom)
                and any(a.name == "converge_payment_status" for a in n.names)
            )
            for n in ast.walk(tree)
        )
    }
    assert mentions == set()


def test_pi_l1_module_makes_no_transitions_and_bank_module_never_touches_documents() -> None:
    """L1(PI)은 전이 통로를 부르지 않고(record_transition 언급 0), 은행 계좌 모듈은 전표 모듈을 임포트하지 않는다(마스터는 전표를 모른다)"""
    from tests.support.astscan import imported_modules, referenced_names

    sources = app_sources()
    for rel, tree in sources.items():
        if module_of(rel) == "proforma_invoices":
            assert "record_transition" not in referenced_names(tree), rel
        if module_of(rel) == "bank_accounts":
            assert not imported_modules(tree) & {
                "quotations",
                "proforma_invoices",
                "trade_chain",
            }, rel
