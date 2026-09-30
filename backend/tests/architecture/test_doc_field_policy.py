"""K. FIELD_POLICY 완전성·핀·파생 — 동결 계약의 유일 정본 (S3-1 ADR-0053 / design-B B2 / X-04).

"모든 컬럼은 정확히 하나로 분류된다"가 동결 계약의 전부다. 새 컬럼을 더하고 분류를 잊으면 이 파일이 CI에서 실패한다
(조용한 미동결 불가). 아직 만들어지지 않은 전표 테이블(PI·SO·PO)은 각 전표 PR이 FIELD_POLICY에 등재하는 순간부터
같은 검사에 들어온다 — 테이블이 metadata에 있는데 등재가 없으면 실패한다.
"""

from __future__ import annotations

import ast

import pytest

import app.registry  # noqa: F401 — 모든 모델 등록
from app.core.db.base import Base
from app.modules.trade_docs.constants import DOC_TABLES, LINE_TABLES
from app.modules.trade_docs.policy import (
    FIELD_POLICY,
    FREE_COLUMNS,
    ColumnClass,
    classify,
    columns_of,
    frozen_columns,
)
from tests.support.astscan import app_sources

pytestmark = pytest.mark.group_k

DOC_AND_LINE_TABLES = set(DOC_TABLES.values()) | set(LINE_TABLES.values())


def _existing_doc_tables() -> set[str]:
    return {name for name in DOC_AND_LINE_TABLES if name in Base.metadata.tables}


def test_the_scan_is_not_vacuous() -> None:
    """검사 대상이 실제로 있다 — 견적 헤더·라인이 metadata에 있고 FIELD_POLICY에도 있다"""
    assert {"quotations", "quotation_lines"} <= _existing_doc_tables()
    assert {"quotations", "quotation_lines"} <= set(FIELD_POLICY)


def test_every_existing_document_table_is_classified_column_for_column() -> None:
    """metadata에 있는 전표 헤더·라인 테이블은 FIELD_POLICY에 등재돼 있고 컬럼이 1:1이다(미등재·유령 모두 실패)"""
    missing_tables = _existing_doc_tables() - set(FIELD_POLICY)
    assert not missing_tables, f"FIELD_POLICY에 없는 전표 테이블: {sorted(missing_tables)}"
    for table in sorted(FIELD_POLICY):
        actual = {column.name for column in Base.metadata.tables[table].columns}
        classified = set(FIELD_POLICY[table])
        assert actual - classified == set(), (
            f"{table}: 분류되지 않은 컬럼 {sorted(actual - classified)}"
        )
        assert classified - actual == set(), (
            f"{table}: 존재하지 않는 컬럼 {sorted(classified - actual)}"
        )


def test_policy_tables_are_document_tables_and_classes_are_valid() -> None:
    """FIELD_POLICY의 키는 전표 헤더·라인 테이블이고 값은 4분류뿐이다"""
    assert set(FIELD_POLICY) <= DOC_AND_LINE_TABLES
    for table, columns in FIELD_POLICY.items():
        assert set(columns.values()) <= set(ColumnClass), table


def test_free_columns_are_exactly_the_four_and_only_where_they_exist() -> None:
    """FREE는 정확히 internal_note·assignee_id·oc_reference·oc_received_on 4종 — 추가는 ADR이 필요하다(핀)"""
    assert {"internal_note", "assignee_id", "oc_reference", "oc_received_on"} == FREE_COLUMNS
    for table in FIELD_POLICY:
        assert columns_of(table, ColumnClass.FREE) <= FREE_COLUMNS, table
    header_free = columns_of("quotations", ColumnClass.FREE)
    assert header_free == {"internal_note", "assignee_id"}  # QT에는 OC 열이 없다
    assert columns_of("quotation_lines", ColumnClass.FREE) == frozenset()  # 라인 열은 전부 CONTENT


def test_price_currency_rate_quantity_and_party_columns_can_never_be_silently_free() -> None:
    """핀 — 단가·통화·환율·수량·거래처·결제조건·유효기간은 CONTENT이고 참조 FK는 ORIGIN이다(FREE 재분류 불가)"""
    header_content = {
        "currency", "total_amount", "fx_rate", "fx_rate_date", "payment_type", "advance_pct_bp",
        "balance_anchor", "balance_days", "incoterm_code", "incoterm_place", "incoterm_year",
        "buyer_partner_id", "buyer_name", "buyer_address", "dest_market_code", "valid_until", "doc_date",
    }  # fmt: skip
    for column in header_content:
        assert classify("quotations", column) is ColumnClass.CONTENT, column
    line_content = {
        "quantity", "unit_price_amount", "list_price_amount", "line_amount", "currency", "sku_id",
        "sku_code", "price_basis", "is_free", "price_reason", "line_no", "buyer_item_code",
    }  # fmt: skip
    for column in line_content:
        assert classify("quotation_lines", column) is ColumnClass.CONTENT, column
    assert classify("quotations", "copied_from_id") is ColumnClass.ORIGIN
    assert classify("quotation_lines", "qt_id") is ColumnClass.ORIGIN
    for column in (
        "doc_number",
        "status",
        "version",
        "frozen_at",
        "last_line_no",
        "id",
        "deleted_at",
    ):
        assert classify("quotations", column) is ColumnClass.SYSTEM, column


def test_frozen_sets_are_derived_from_the_policy_not_hand_written() -> None:
    """FROZEN_* 집합은 FIELD_POLICY에서 파생(CONTENT+ORIGIN) — 앱 어디에도 수기 정의가 없다"""
    for table in FIELD_POLICY:
        derived = frozen_columns(table)
        assert derived == columns_of(table, ColumnClass.CONTENT, ColumnClass.ORIGIN)
        assert not (derived & columns_of(table, ColumnClass.FREE, ColumnClass.SYSTEM))
    for rel, tree in app_sources().items():
        source = " ".join(getattr(node, "id", "") for node in ast.walk(tree))
        assert "FROZEN_HEADER_COLUMNS" not in source and "FROZEN_LINE_COLUMNS" not in source, rel


def test_classification_lookup_is_fail_closed() -> None:
    """미등재 컬럼·테이블 조회는 KeyError — 조용한 기본 분류가 없다"""
    with pytest.raises(KeyError):
        classify("quotations", "brand_new_column")
    with pytest.raises(KeyError):
        classify("no_such_table", "id")


def test_the_completeness_check_would_catch_an_unclassified_column() -> None:
    """자기검사 — 등재에서 컬럼 하나를 빼면 위 완전성 비교가 실제로 어긋난다(공회전 방지)"""
    actual = {column.name for column in Base.metadata.tables["quotations"].columns}
    dropped = dict(FIELD_POLICY["quotations"])
    dropped.pop("fx_rate")
    assert actual - set(dropped) == {"fx_rate"}
    padded = {**FIELD_POLICY["quotations"], "ghost": ColumnClass.CONTENT}
    assert set(padded) - actual == {"ghost"}
