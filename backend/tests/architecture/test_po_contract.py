"""K. PO 계약 스캔 — 열 분류 핀·po_kind 3자 대사·동결 쓰기 0건·가격 원천 단일 통로·M06 독립성·등록 대사 (S3-1 PR-8a / ADR-0053·0057 / design-A A12 / design-F F1·F3·F4).

PI 계약(test_pi_contract)·SO 계약(test_so_contract)을 잇는 PO 몫이다. PO는 **편집 구간이 없다**(생성=발행=동결) — 그래서 CONTENT를 쓰는 서비스 함수 자체가 없어야 하고, 서비스가 바꿀 수 있는 것은 FREE 4열뿐이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db.base import Base
from app.core.db.session import owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES
from app.main import app
from app.modules.trade_docs.chain import CHILD_LINKS, NON_CHILD_FK_ALLOWLIST, links_for
from app.modules.trade_docs.constants import (
    DOC_PREFIXES,
    STATUS_LOG_TABLES,
    DocKind,
    PoKind,
)
from app.modules.trade_docs.machine import EDITABLE_STATES
from app.modules.trade_docs.models import STATUS_LOG_MODELS
from app.modules.trade_docs.policy import (
    FIELD_POLICY,
    FREE_COLUMNS,
    ColumnClass,
    classify,
    columns_of,
    frozen_columns,
)
from app.modules.trade_docs.quantities import LINE_CONSUMERS
from tests.architecture.test_doc_status_channel import attribute_assignments
from tests.support.astscan import APP_DIR, app_sources, module_of, parse_source

pytestmark = pytest.mark.group_k

KIND = DocKind.PURCHASE_ORDER
PO_SERVICE = "modules/purchase_orders/service.py"


def test_po_tables_are_classified_and_registered() -> None:
    """PO 3표가 테이블 정책(헤더·라인 MUTABLE·이력 IMMUTABLE)·FIELD_POLICY·상태이력 모델 레지스트리에 전부 등재됐다"""
    assert {"purchase_orders", "purchase_order_lines"} <= MUTABLE_TABLES
    assert STATUS_LOG_TABLES[KIND] in IMMUTABLE_TABLES and KIND in STATUS_LOG_MODELS
    assert {"purchase_orders", "purchase_order_lines"} <= set(FIELD_POLICY)
    assert {"purchase_orders", "purchase_order_lines", "purchase_order_status_log"} <= set(
        Base.metadata.tables
    )


def test_po_field_policy_pins() -> None:
    """PO 열 분류 핀 — 원가 3열·통화·환율·결제조건·Incoterms·공급사명·po_kind·증빙일은 CONTENT(동결), 공급사·복제 원본은 ORIGIN, FREE는 메모·담당자·OC 두 열(정확히 4), 동결 시각·번호·상태는 SYSTEM — PO에는 동결 편집 구간이 없다"""
    for column in (
        "total_cost", "currency", "fx_rate", "fx_rate_date", "payment_type", "advance_pct_bp",
        "balance_anchor", "balance_days", "incoterm_code", "incoterm_place", "incoterm_year",
        "supplier_name", "po_kind", "doc_date",
    ):  # fmt: skip
        assert classify("purchase_orders", column) is ColumnClass.CONTENT, column
    for column in ("supplier_partner_id", "copied_from_id"):
        assert classify("purchase_orders", column) is ColumnClass.ORIGIN, column
    assert (
        columns_of("purchase_orders", ColumnClass.FREE)
        == {
            "internal_note",
            "assignee_id",
            "oc_received_on",
            "oc_reference",
        }
        == FREE_COLUMNS
    )
    for column in (
        "doc_number",
        "status",
        "frozen_at",
        "version",
        "last_line_no",
        "id",
        "deleted_at",
    ):
        assert classify("purchase_orders", column) is ColumnClass.SYSTEM, column
    assert columns_of("purchase_order_lines", ColumnClass.FREE) == frozenset()
    for column in (
        "unit_cost",
        "line_cost",
        "quantity",
        "price_basis",
        "currency",
        "sku_id",
        "requested_delivery_date",
        "line_no",
    ):
        assert classify("purchase_order_lines", column) is ColumnClass.CONTENT, column
    assert classify("purchase_order_lines", "po_id") is ColumnClass.ORIGIN
    assert EDITABLE_STATES[KIND] == frozenset()


def test_po_kind_enum_literal_and_db_check_agree() -> None:
    """po_kind 값 집합 3자 일치 — StrEnum(PoKind) ↔ 요청 Literal ↔ DB CHECK 정의문(pg_get_constraintdef). 값을 더하려면 CHECK 재정의 마이그레이션+ADR이 세트다"""
    from app.modules.purchase_orders.schemas import PoKindLiteral

    values = {k.value for k in PoKind}
    assert values == {"PURCHASE", "OEM_PRODUCTION"} == set(PoKindLiteral.__args__)  # type: ignore[attr-defined]
    with owner_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'purchase_orders'::regclass"
                " AND conname = 'ck_purchase_orders_po_kind_valid'"
            )
        ).scalar_one()
    assert set(re.findall(r"'([A-Z_]+)'::character varying", definition)) == values


def test_the_po_status_check_is_enabled_and_agrees_with_the_machine() -> None:
    """지연 프레임 켜짐 — PO 상태 CHECK 3자 대사(StrEnum·machine·DB)가 skip이 아니라 실제로 돈다: 4종 전표 테이블이 전부 존재하고 PO 상태 CHECK가 실재한다"""
    from app.modules.trade_docs.constants import DOC_TABLES
    from app.modules.trade_docs.machine import STATUSES

    with owner_engine.connect() as connection:
        assert all(
            connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{t}"}).scalar_one()
            is not None
            for t in DOC_TABLES.values()
        )
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'purchase_orders'::regclass"
                " AND conname = 'ck_purchase_orders_status_valid'"
            )
        ).scalar_one()
    assert set(re.findall(r"'([A-Z_]+)'::character varying", definition)) == set(STATUSES[KIND])


def test_no_code_assigns_a_po_frozen_column_after_creation() -> None:
    """PO의 CONTENT·ORIGIN 열(원가·통화·공급사·po_kind 등)을 속성 대입으로 쓰는 코드가 PO·사슬 모듈에 0건 — 값은 생성자(insert_issued)로만 들어가고 이후 서비스 통로가 없다(FIELD_POLICY 파생 집합 전수 대조). 서비스가 대입하는 열은 FREE 4열뿐이다"""
    frozen = frozen_columns("purchase_orders") | frozen_columns("purchase_order_lines")
    frozen -= {"status"}  # status는 전이 통로 스캔 몫
    assert {
        "unit_cost",
        "total_cost",
        "supplier_partner_id",
        "po_kind",
        "currency",
        "supplier_name",
    } <= frozen
    checked = 0
    for rel, tree in app_sources().items():
        if module_of(rel) not in ("purchase_orders", "trade_chain"):
            continue
        checked += 1
        for column in sorted(frozen):
            assert attribute_assignments(tree, column) == [], (rel, column)
    assert checked >= 8  # 공회전 방지
    assigned = {
        column
        for column in columns_of("purchase_orders", ColumnClass.FREE)
        if attribute_assignments(app_sources()[PO_SERVICE], column)
    }
    assert assigned == {"internal_note", "assignee_id", "oc_received_on", "oc_reference"}


def test_the_po_service_has_no_edit_functions() -> None:
    """PO 서비스에는 헤더·라인 편집 함수가 없다(`update_purchase_order`·`add_line`·`update_line`·`remove_line`·`replace_lines` 부재) — 잘못된 입력은 취소+신규(복제)로만 고친다. 쓰기 함수는 생성·미리보기·메타뿐이다"""
    functions = {n.name for n in app_sources()[PO_SERVICE].body if isinstance(n, ast.FunctionDef)}
    assert not functions & {
        "update_purchase_order",
        "add_line",
        "update_line",
        "remove_line",
        "replace_lines",
        "delete_purchase_order",
    }
    assert {
        "create_purchase_order",
        "preview_purchase_order",
        "update_meta",
        "insert_issued",
    } <= functions
    source = ast.unparse(app_sources()[PO_SERVICE])
    assert "assert_editable" not in source  # 편집 구간이 없으니 편집 가드도 없다
    tree = app_sources()[PO_SERVICE]
    meta = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "update_meta")
    assert "lock_document" in ast.unparse(meta)  # 메타는 헤더 행 잠금 뒤 version을 대조한다


def test_the_master_cost_lookup_has_a_single_caller_and_the_purchase_literal_stays_home() -> None:
    """마스터 매입가 조회(`price_at`)의 호출은 PO 서비스 1곳뿐이고, 가격 종류 리터럴 "PURCHASE"는 catalog·purchase_orders·po_kind 열거(trade_docs/constants) 밖에 없다 — 전표가 매입가를 읽는 통로는 여기 하나다(F4 ④)"""
    callers = {
        rel
        for rel, tree in app_sources().items()
        if any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "price_at"
            for n in ast.walk(tree)
        )
    }
    assert callers == {PO_SERVICE}
    homes = {
        rel
        for rel, tree in app_sources().items()
        if any(isinstance(n, ast.Constant) and n.value == "PURCHASE" for n in ast.walk(tree))
    }
    assert homes == {
        "modules/catalog/models.py",
        "modules/catalog/pricing.py",
        "modules/purchase_orders/service.py",
        "modules/purchase_orders/schemas.py",
        "modules/trade_docs/constants.py",
    }
    snapshot = app_sources()["modules/trade_docs/snapshot.py"]
    assert "'SALES'" in ast.unparse(snapshot) and "'PURCHASE'" not in ast.unparse(
        snapshot
    )  # 판매 체인은 판가만


def test_the_supplier_type_check_takes_the_key_share_lock_only_when_creating() -> None:
    """공급사 유형 검증은 `partners.service.require_partner_of_any_type`를 쓰고 `lock=lock`으로 받는다 — 생성 계획은 lock=True(FOR KEY SHARE)·미리보기는 lock=False. 단일 통로이며 유형 검증을 다른 식으로 하는 곳이 없다"""
    tree = app_sources()[PO_SERVICE]
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "require_partner_of_any_type"
    ]
    assert len(calls) == 1
    assert any(kw.arg == "lock" and ast.unparse(kw.value) == "lock" for kw in calls[0].keywords)
    plan_calls = [
        ast.unparse(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_plan"
    ]
    assert sorted("lock=True" in c for c in plan_calls) == [False, True]  # 미리보기 False·생성 True
    assert "partner_type_codes" not in ast.unparse(tree)  # 유형을 직접 읽어 검증하는 우회 없음


def test_po_is_outside_the_sales_chain_and_only_the_import_shipment_follows_it() -> None:
    """PO는 판매 사슬 밖이다 — PO 자신은 어느 링크의 자식도 아니고, PO의 후속은 수입선적 하나뿐이다(S3-2 PR-3a CHILD_LINKS, 생성은 PR-5a).
    PO_LINE 소비자는 수입선적 **IN_TRANSIT** 1건(PO 잔량 불변 — 입고 FULFILL은 S4-1). PO 테이블의 전표 FK는 전부 NON_CHILD 허용목록에 사유와 함께 있다"""
    assert [(link.child_table, link.fk_column) for link in links_for(KIND)] == [
        ("shipments", "po_id")
    ]
    assert all(
        link.child_table not in {"purchase_orders", "purchase_order_lines"} for link in CHILD_LINKS
    )
    assert [(spec.name, spec.kind) for spec in LINE_CONSUMERS["PO_LINE"]] == [
        ("SHIPMENT_LINE.po_line_id", "IN_TRANSIT")
    ]
    for key in (
        ("purchase_order_lines", "po_id"),
        ("purchase_order_status_log", "purchase_order_id"),
        ("purchase_orders", "copied_from_id"),
    ):
        assert key in NON_CHILD_FK_ALLOWLIST and len(NON_CHILD_FK_ALLOWLIST[key]) >= 10


def test_the_po_number_prefix_and_table_constants_agree() -> None:
    """접두어 PO·채번은 전표 채번 래퍼 경유 — DOC_PREFIXES가 유일 출처이고 doc_number 형식 CHECK가 같은 접두어를 요구한다(3자 일치)"""
    assert DOC_PREFIXES[KIND] == "PO"
    with owner_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'purchase_orders'::regclass"
                " AND conname = 'ck_purchase_orders_doc_number_format'"
            )
        ).scalar_one()
    assert "PO-" in definition


def test_migration_m06_depends_only_on_m01_tables() -> None:
    """M06은 **M01 마스터에만 의존한다**(통합 §2.11 — PO는 QT·PI·SO·승인·여신과 무관해 어느 위치에도 끼울 수 있다): 마이그레이션이 참조하는 FK 대상 테이블이 users·partners·skus와 자기 자신(purchase_orders)뿐이다"""
    files = list(
        (Path(APP_DIR).parent / "migrations" / "versions").glob("*_s31_purchase_orders.py")
    )
    assert len(files) == 1
    source = files[0].read_text(encoding="utf-8")
    targets = set(re.findall(r'\["([a-z_]+)\.[a-z_]+"', source)) | set(
        re.findall(r"\['([a-z_]+)\.[a-z_]+'", source)
    )
    assert targets == {"users", "partners", "skus", "purchase_orders"}, targets
    assert 'revoke_mutations(op, "purchase_order_status_log")' in source
    assert "op.execute" not in source  # 데이터·시드·트리거 없음(신규 테이블만)


def test_po_routes_are_exposed_and_the_list_is_paginated_by_default() -> None:
    """라우터 누락은 조용한 미노출이다 — PO 엔드포인트 12개(S3-2 PR-4c OEM 생산 일정 4 포함)가 OpenAPI에 실재하고 목록은 페이지네이션(기본 50)·DELETE가 없다"""
    schema = app.openapi()
    operations = {
        (method.upper(), path)
        for path, ops in schema["paths"].items()
        if path.startswith("/api/v1/purchase-orders")
        for method in ops
    }
    assert operations == {
        ("GET", "/api/v1/purchase-orders"),
        ("POST", "/api/v1/purchase-orders"),
        ("GET", "/api/v1/purchase-orders/export.csv"),
        ("POST", "/api/v1/purchase-orders/preview"),
        ("GET", "/api/v1/purchase-orders/{po_id}"),
        ("PATCH", "/api/v1/purchase-orders/{po_id}/meta"),
        ("GET", "/api/v1/purchase-orders/{po_id}/status-log"),
        ("POST", "/api/v1/purchase-orders/{po_id}/transitions"),
        # S3-2 PR-4c — OEM 생산 일정(M7~M9 — 보드·계획·실적·변경 이력, 원가 키 없음)
        ("GET", "/api/v1/purchase-orders/{po_id}/milestones"),
        ("POST", "/api/v1/purchase-orders/{po_id}/milestones/{milestone_type}/plan"),
        ("POST", "/api/v1/purchase-orders/{po_id}/milestones/{milestone_type}/actual"),
        ("GET", "/api/v1/purchase-orders/{po_id}/milestone-changes"),
    }
    params = {p["name"]: p for p in schema["paths"]["/api/v1/purchase-orders"]["get"]["parameters"]}
    assert params["size"]["schema"]["default"] == 50 and params["size"]["schema"]["maximum"] == 200


def test_the_scan_helpers_are_not_vacuous() -> None:
    """자기검사 — 동결 열 대입 스캐너가 위반 소스(`row.unit_cost = 1`)를 실제로 잡는다"""
    assert attribute_assignments(
        parse_source("def f(row):\n    row.unit_cost = 1\n"), "unit_cost"
    ) == [(2, "row")]
    assert (
        attribute_assignments(
            parse_source("def f(row):\n    row.internal_note = 'x'\n"), "unit_cost"
        )
        == []
    )


def test_the_po_dead_status_set_is_derived_from_the_po_states() -> None:
    """PO의 죽은 상태 집합은 공용 DEAD_STATUSES(CANCELLED·EXPIRED)에서 **PO 상태 집합에 있는 것만** 파생한다 — PO에는 EXPIRED가 없으므로 {CANCELLED}이고 PO 상태 집합의 부분집합이다(하드코딩·타 전표 값 혼입 방지)"""
    from app.modules.purchase_orders.service import PO_DEAD_STATUSES
    from app.modules.trade_docs.machine import DEAD_STATUSES, STATUSES

    assert set(PO_DEAD_STATUSES) == {"CANCELLED"} and set(PO_DEAD_STATUSES) <= set(STATUSES[KIND])
    assert "EXPIRED" in DEAD_STATUSES and "EXPIRED" not in STATUSES[KIND]
    source = ast.unparse(app_sources()[PO_SERVICE])
    assert "notin_(DEAD_STATUSES)" not in source and "status not in DEAD_STATUSES" not in source
