"""K. 사슬 후속 등록 계약 — 전표를 가리키는 모든 FK는 등록돼 있다 (S3-1 ADR-0052 / design-B B3 (f)).

S3-2 선적이 전표 FK를 더하고 `CHILD_LINKS`(후속 판정) 등록을 잊으면 "선적이 살아 있는데 SO 취소가 통과"하는 조용한
결함이 된다 — 이 테스트가 그 누락을 CI에서 잡는다. 후속 판정의 LIVE 술어는 machine.DEAD_STATUSES에서만 파생돼야 한다.
"""

from __future__ import annotations

import pytest

import app.registry  # noqa: F401 — 모든 모델 등록
from app.core.db.base import Base
from app.modules.trade_docs import chain
from app.modules.trade_docs.chain import CHILD_LINKS, NON_CHILD_FK_ALLOWLIST
from app.modules.trade_docs.constants import DOC_TABLES, LINE_TABLES, DocKind
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.quantities import LINE_CONSUMERS
from tests.support.astscan import APP_DIR

pytestmark = pytest.mark.group_k

CHAIN_TABLES = set(DOC_TABLES.values()) | set(LINE_TABLES.values())

#: 아직 만들어지지 않은 후속 테이블 — 각 전표 PR(PR-7: sales_orders)이 테이블을 만들면서
#: 이 집합에서 **지워야** 한다(안 지우면 아래 테스트가 실패해 CHILD_LINKS 편입을 상기시킨다). PR-6a가 proforma_invoices를 지웠다.
PENDING_CHILD_TABLES = {"sales_orders"}


def _fks_to_chain_tables() -> set[tuple[str, str, str]]:
    """(자식 테이블, FK 첫 열, 대상 전표 테이블) — 복합 FK((qt_id, currency))는 첫 열 하나로 대표한다."""
    found: set[tuple[str, str, str]] = set()
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            if constraint.referred_table.name in CHAIN_TABLES:
                found.add((table.name, constraint.column_keys[0], constraint.referred_table.name))
    return found


def _consumer_fks() -> set[tuple[str, str]]:
    return {
        (spec.child_line_table, spec.line_fk_col)
        for specs in LINE_CONSUMERS.values()
        for spec in specs
    }


def test_the_scan_is_not_vacuous() -> None:
    """검사할 FK가 실제로 있다(견적 라인·이력·복제 계보)"""
    fks = _fks_to_chain_tables()
    assert ("quotation_lines", "qt_id", "quotations") in fks
    assert ("quotation_status_log", "quotation_id", "quotations") in fks
    assert ("quotations", "copied_from_id", "quotations") in fks


def test_every_fk_to_a_chain_document_is_registered_as_child_or_explained() -> None:
    """전표·라인 테이블을 가리키는 모든 FK는 CHILD_LINKS(후속)·LINE_CONSUMERS(소비)·NON_CHILD 허용목록 중 하나에 있다"""
    registered = {(link.child_table, link.fk_column) for link in CHILD_LINKS}
    known = registered | _consumer_fks() | set(NON_CHILD_FK_ALLOWLIST)
    unregistered = {(t, c) for t, c, _ in _fks_to_chain_tables()} - known
    assert not unregistered, (
        f"사슬 등록이 없는 전표 FK: {sorted(unregistered)}\n"
        "후속 전표면 trade_docs/chain.py CHILD_LINKS에, 소비 라인이면 quantities.LINE_CONSUMERS에, "
        "후속이 아니면 NON_CHILD_FK_ALLOWLIST에 **사유와 함께** 등록하세요."
    )


def test_the_allowlist_carries_reasons_and_has_no_dead_entries() -> None:
    """허용목록은 사유가 있고, 실재하지 않는 항목이 남아 있지 않다"""
    live = {(t, c) for t, c, _ in _fks_to_chain_tables()}
    assert set(NON_CHILD_FK_ALLOWLIST) <= live, sorted(set(NON_CHILD_FK_ALLOWLIST) - live)
    assert all(len(reason) >= 10 for reason in NON_CHILD_FK_ALLOWLIST.values())


def test_pending_child_tables_are_exactly_the_ones_not_yet_created() -> None:
    """CHILD_LINKS의 후속 테이블은 (a) metadata에 있거나 (b) PENDING에 명시돼 있다 — PR-7이 PENDING을 줄인다"""
    absent = {
        link.child_table for link in CHILD_LINKS if link.child_table not in Base.metadata.tables
    }
    assert absent == PENDING_CHILD_TABLES, (
        f"metadata에 없는 후속 테이블 {sorted(absent)} ≠ PENDING {sorted(PENDING_CHILD_TABLES)} — "
        "후속 전표 PR이 테이블을 만들었다면 이 테스트의 PENDING에서 지우세요."
    )


def test_child_links_are_wellformed() -> None:
    """모든 링크의 부모는 DocKind이고, 테이블이 존재하면 FK 열·live 술어 열(deleted_at·status)이 실제로 있다"""
    assert {link.parent for link in CHILD_LINKS} == {DocKind.QUOTATION, DocKind.PROFORMA_INVOICE}
    for link in CHILD_LINKS:
        table = Base.metadata.tables.get(link.child_table)
        if table is None:
            continue
        assert {link.fk_column, "deleted_at", "status", "doc_number"} <= set(table.c.keys())
        if link.confirmed_column:
            assert link.confirmed_column in table.c


def test_the_live_predicate_is_derived_from_the_dead_status_tuple() -> None:
    """LIVE 술어에 상태 리터럴을 이중 정의하지 않는다 — chain.py에 CANCELLED·EXPIRED 문자열이 없다"""
    source = (APP_DIR / "modules" / "trade_docs" / "chain.py").read_text(encoding="utf-8")
    code_lines = [ln for ln in source.splitlines() if not ln.strip().startswith(("#", '"""'))]
    body = "\n".join(code_lines)
    assert "DEAD_STATUSES" in body
    assert '"CANCELLED"' not in body and '"EXPIRED"' not in body
    assert DEAD_STATUSES == ("CANCELLED", "EXPIRED")
    assert chain.links_for(DocKind.QUOTATION) and chain.links_for(DocKind.PROFORMA_INVOICE)
    assert (
        chain.links_for(DocKind.SALES_ORDER) == [] and chain.links_for(DocKind.PURCHASE_ORDER) == []
    )
