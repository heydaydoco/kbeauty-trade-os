"""K. 전표 상태 기계 — 총수·도달성·RESERVED·3자 대사 (S3-1 ADR-0051 / design-B B1).

허용 25방향(사람 15 + 자동 10) / 미허용 101 / 총 126쌍 — 전이를 더하거나 빼면 EXPECTED와 machine.py 독스트링을
함께 고친다(ADR-0038 관용). 공회전 방지: 4종이 전부 검사에 들어오고 각 검사가 자기 자신을 시험한다.
"""

from __future__ import annotations

import re
from itertools import permutations

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.trade_docs.constants import DOC_PREFIXES, DOC_TABLES, DocKind
from app.modules.trade_docs.machine import (
    AUTO_TRANSITIONS,
    DEAD_STATUSES,
    EDITABLE_STATES,
    FREEZE_ACTION_EDGES,
    HUMAN_TRANSITIONS,
    INITIAL_STATUS,
    REASON_REQUIRED_TO,
    RESERVED,
    STATUS_ENUMS,
    STATUSES,
    TERMINAL_STATUSES,
    allowed_transitions,
    public_transition_targets,
)

pytestmark = pytest.mark.group_k

#: 문서별 (허용, 미허용, 사람, 자동) — 집계로 고정한다.
EXPECTED: dict[DocKind, tuple[int, int, int, int]] = {
    DocKind.QUOTATION: (6, 14, 3, 3),
    DocKind.PROFORMA_INVOICE: (8, 12, 1, 7),
    DocKind.SALES_ORDER: (8, 48, 8, 0),
    DocKind.PURCHASE_ORDER: (3, 27, 3, 0),
}


def test_all_four_documents_are_covered() -> None:
    """검사 대상이 4종 전부이고 총합이 25/101/126이다(빈 검사로 초록을 사지 않는다)"""
    assert set(EXPECTED) == set(DocKind) == set(STATUSES)
    assert sum(v[0] for v in EXPECTED.values()) == 25
    assert sum(v[1] for v in EXPECTED.values()) == 101
    assert sum(v[2] for v in EXPECTED.values()) == 15
    assert sum(v[3] for v in EXPECTED.values()) == 10
    assert sum(v[0] + v[1] for v in EXPECTED.values()) == 126


@pytest.mark.parametrize("kind", list(DocKind))
def test_transition_totals_are_pinned(kind: DocKind) -> None:
    """문서별 허용·미허용·사람·자동 방향 수가 EXPECTED와 같다"""
    n = len(STATUSES[kind])
    allowed = allowed_transitions(kind)
    total_pairs = n * (n - 1)
    human, auto = HUMAN_TRANSITIONS[kind], AUTO_TRANSITIONS[kind]
    assert (len(allowed), total_pairs - len(allowed), len(human), len(auto)) == EXPECTED[kind]
    assert not (human & auto), "한 방향이 사람이면서 자동일 수 없다"


@pytest.mark.parametrize("kind", list(DocKind))
def test_every_edge_uses_known_statuses_and_no_self_transition(kind: DocKind) -> None:
    """모든 엣지의 양 끝은 그 문서의 상태 값이고 자기전이는 없다"""
    known = set(STATUSES[kind])
    for frm, to in allowed_transitions(kind):
        assert frm in known and to in known and frm != to


@pytest.mark.parametrize("kind", list(DocKind))
def test_reserved_states_have_no_edges_and_others_are_reachable(kind: DocKind) -> None:
    """RESERVED는 in/out 엣지 0, 나머지 상태는 초기 상태에서 도달 가능하다(도달성)"""
    edges = allowed_transitions(kind)
    for status in RESERVED[kind]:
        assert not any(status in pair for pair in edges), status
    reachable = {INITIAL_STATUS[kind]}
    changed = True
    while changed:
        changed = False
        for frm, to in edges:
            if frm in reachable and to not in reachable:
                reachable.add(to)
                changed = True
    assert reachable == set(STATUSES[kind]) - RESERVED[kind]


@pytest.mark.parametrize("kind", list(DocKind))
def test_terminal_states_have_no_exit(kind: DocKind) -> None:
    """종결 상태(CANCELLED 등)에서 나가는 엣지가 없다(탈출성)"""
    for frm, _to in allowed_transitions(kind):
        assert frm not in TERMINAL_STATUSES[kind]
    assert TERMINAL_STATUSES[kind] <= set(STATUSES[kind])


def test_qt_edges_match_the_design() -> None:
    """QT 6방향 — 사람 3(발행·초안 폐기·취소)·자동 3(전환·복귀·만료)이고 CONVERTED→CANCELLED는 없다"""
    assert HUMAN_TRANSITIONS[DocKind.QUOTATION] == {
        ("DRAFT", "ISSUED"),
        ("DRAFT", "CANCELLED"),
        ("ISSUED", "CANCELLED"),
    }
    assert AUTO_TRANSITIONS[DocKind.QUOTATION] == {
        ("ISSUED", "CONVERTED"),
        ("CONVERTED", "ISSUED"),
        ("ISSUED", "EXPIRED"),
    }
    assert ("CONVERTED", "CANCELLED") not in allowed_transitions(DocKind.QUOTATION)
    assert ("CONVERTED", "EXPIRED") not in allowed_transitions(DocKind.QUOTATION)


def test_pi_payment_convergence_is_the_six_directions_among_three_states() -> None:
    """PI 입금 수렴 6방향 — ISSUED·PARTIALLY_PAID·PAID 상호 전부, 자동이다"""
    pay = {"ISSUED", "PARTIALLY_PAID", "PAID"}
    convergence = {(a, b) for a, b in permutations(pay, 2)}
    assert convergence <= AUTO_TRANSITIONS[DocKind.PROFORMA_INVOICE]
    assert ("ISSUED", "EXPIRED") in AUTO_TRANSITIONS[DocKind.PROFORMA_INVOICE]
    assert HUMAN_TRANSITIONS[DocKind.PROFORMA_INVOICE] == {("ISSUED", "CANCELLED")}


def test_po_and_so_have_no_automatic_edges() -> None:
    """PO·SO는 자동 엣지가 0이다 — 발주·확정은 사람 1클릭 단일 경로(4금 ①)"""
    assert AUTO_TRANSITIONS[DocKind.PURCHASE_ORDER] == frozenset()
    assert AUTO_TRANSITIONS[DocKind.SALES_ORDER] == frozenset()


def test_freeze_action_edges_are_human_edges_excluded_from_the_public_targets() -> None:
    """동결 엣지(QT 발행·SO 확정)는 사람 엣지이지만 범용 전이의 `to` 대상에서 빠진다"""
    for kind, edges in FREEZE_ACTION_EDGES.items():
        assert edges <= HUMAN_TRANSITIONS[kind]
    assert public_transition_targets(DocKind.QUOTATION) == {"CANCELLED"}
    assert "ISSUED" not in public_transition_targets(DocKind.QUOTATION)
    assert public_transition_targets(DocKind.SALES_ORDER) == {
        "ON_HOLD",
        "CANCELLED",
        "RECEIVED",
        "CONFIRMED",  # ON_HOLD→CONFIRMED 재개용 — RECEIVED→CONFIRMED는 record_transition이 거부한다
    }
    for kind in DocKind:
        auto_targets = {to for _, to in AUTO_TRANSITIONS[kind]} - {
            to for _, to in HUMAN_TRANSITIONS[kind]
        }
        assert not (public_transition_targets(kind) & auto_targets)
        assert not (public_transition_targets(kind) & RESERVED[kind])


@pytest.mark.parametrize("kind", list(DocKind))
def test_reason_required_states_and_dead_statuses(kind: DocKind) -> None:
    """사유 필수 상태는 그 문서의 상태이고 CANCELLED는 항상 포함된다. 죽은 상태는 CANCELLED·EXPIRED"""
    assert REASON_REQUIRED_TO[kind] <= set(STATUSES[kind])
    assert "CANCELLED" in REASON_REQUIRED_TO[kind]
    assert DEAD_STATUSES == ("CANCELLED", "EXPIRED")
    assert EDITABLE_STATES[kind] <= set(STATUSES[kind])
    assert set(STATUS_ENUMS[kind]) and {m.value for m in STATUS_ENUMS[kind]} == set(STATUSES[kind])


def test_editable_states_are_only_qt_draft_and_so_received() -> None:
    """편집 가능 상태는 QT:DRAFT·SO:RECEIVED 두 곳뿐이다(PI·PO는 편집 구간이 없다)"""
    assert {
        DocKind.QUOTATION: {"DRAFT"},
        DocKind.PROFORMA_INVOICE: set(),
        DocKind.SALES_ORDER: {"RECEIVED"},
        DocKind.PURCHASE_ORDER: set(),
    } == EDITABLE_STATES


def test_prefixes_are_unique_and_two_letters() -> None:
    """채번 접두어는 4종이 서로 다르다"""
    assert sorted(DOC_PREFIXES.values()) == ["PI", "PO", "QT", "SO"]


@pytest.mark.parametrize("kind", list(DocKind))
def test_status_enum_machine_and_db_check_agree(kind: DocKind) -> None:
    """StrEnum ↔ machine ↔ DB CHECK 정의문 3자 대사 — 테이블이 있는 문서만 DB를 본다(나머지는 각 전표 PR)"""
    table = DOC_TABLES[kind]
    with owner_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
        ).scalar_one()
        if exists is None:
            pytest.skip(f"{table}는 아직 없다 — 해당 전표 PR이 이 검사를 켠다")
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = to_regclass(:t) AND conname = :c"
            ),
            {"t": f"public.{table}", "c": f"ck_{table}_status_valid"},
        ).scalar_one()
    assert set(re.findall(r"'([A-Z_]+)'::character varying", definition)) == set(STATUSES[kind])
