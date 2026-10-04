"""K. 전표 상태 기계 — 총수·도달성·RESERVED·3자 대사 (S3-1 ADR-0051 / design-B B1).

허용 30방향(사람 18 + 자동 12) / 미허용 152 / 총 182쌍(S3-2 PR-3a 선적 편입 — 8상태·사람 3엣지·RESERVED 5, SO 자동 수렴 2엣지) — 전이를 더하거나 빼면
EXPECTED와 machine.py 독스트링을 함께 고친다(ADR-0038 관용). 공회전 방지: 5종이 전부 검사에 들어오고 각 검사가 자기 자신을 시험한다.
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
    DocKind.SALES_ORDER: (10, 46, 8, 2),  # S3-2 PR-3a — CONFIRMED↔IN_SHIPMENT 자동 수렴 2(ADR-0075)
    DocKind.PURCHASE_ORDER: (3, 27, 3, 0),
    DocKind.SHIPMENT: (3, 53, 3, 0),
}


def test_all_five_documents_are_covered() -> None:
    """검사 대상이 5종 전부(선적 포함)이고 총합이 30/152/182다(빈 검사로 초록을 사지 않는다)"""
    assert set(EXPECTED) == set(DocKind) == set(STATUSES)
    assert len(DocKind) == 5
    assert sum(v[0] for v in EXPECTED.values()) == 30
    assert sum(v[1] for v in EXPECTED.values()) == 152
    assert sum(v[2] for v in EXPECTED.values()) == 18
    assert sum(v[3] for v in EXPECTED.values()) == 12
    assert sum(v[0] + v[1] for v in EXPECTED.values()) == 182


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


def test_po_has_no_automatic_edges_and_so_auto_edges_are_only_the_shipping_convergence() -> None:
    """PO 자동 엣지 0(발주는 사람 1클릭 — 4금 ①) / SO 자동 엣지는 **CONFIRMED↔IN_SHIPMENT 선적 수렴 2개뿐**(S3-2 PR-3a·ADR-0075 —
    이행 진행·복귀이지 약정 진입이 아니다). 어느 자동 엣지도 RECEIVED에서 CONFIRMED로 가지 않는다(확정 = 사람 동결 액션 전용)"""
    assert AUTO_TRANSITIONS[DocKind.PURCHASE_ORDER] == frozenset()
    assert AUTO_TRANSITIONS[DocKind.SALES_ORDER] == {
        ("CONFIRMED", "IN_SHIPMENT"),
        ("IN_SHIPMENT", "CONFIRMED"),
    }
    for kind in DocKind:
        assert ("RECEIVED", "CONFIRMED") not in AUTO_TRANSITIONS[kind]
    # IN_SHIPMENT의 사람 엣지 0 — 보류·취소는 선적을 먼저 취소해 CONFIRMED로 복귀한 뒤(역순)
    assert not any("IN_SHIPMENT" in pair for pair in HUMAN_TRANSITIONS[DocKind.SALES_ORDER])
    # SO 공개 전이 대상은 사람 엣지만 센다 — 자동 2엣지는 Literal 값 공간을 바꾸지 않는다(R-23, 라우터 assert 무변경)
    assert "IN_SHIPMENT" not in public_transition_targets(DocKind.SALES_ORDER)


def test_so_completed_stays_reserved_while_the_receivable_provider_is_the_default() -> None:
    """ADR-0076 결속 — 미수 provider가 기본값(`is_default_provider()`)인 동안 SO COMPLETED는 RESERVED이고 COMPLETED로 들어가는 엣지는 0이다.
    노출 술어가 COMPLETED를 통째로 빼므로(credit/exposure.py) provider 전에 열면 노출 공백 — COMPLETED는 S3-3 provider PR에서만 연다"""
    from app.modules.credit.exposure import CLOSED_STATUSES
    from app.modules.credit.providers import is_default_provider

    completed_edges = [
        pair for pair in allowed_transitions(DocKind.SALES_ORDER) if pair[1] == "COMPLETED"
    ]
    if is_default_provider():
        assert "COMPLETED" in RESERVED[DocKind.SALES_ORDER]
        assert completed_edges == []
    assert "COMPLETED" in CLOSED_STATUSES  # exposure 무변경(P-01) — 이 시험이 지키는 전제


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


def test_editable_states_are_only_qt_draft_so_received_and_shipment_planned() -> None:
    """편집 가능 상태는 QT:DRAFT·SO:RECEIVED·선적:PLANNED 세 곳뿐이다(PI·PO는 편집 구간이 없다, 선적은 출고지시가 동결)"""
    assert {
        DocKind.QUOTATION: {"DRAFT"},
        DocKind.PROFORMA_INVOICE: set(),
        DocKind.SALES_ORDER: {"RECEIVED"},
        DocKind.PURCHASE_ORDER: set(),
        DocKind.SHIPMENT: {"PLANNED"},
    } == EDITABLE_STATES


def test_prefixes_are_unique_and_two_letters() -> None:
    """채번 접두어는 5종이 서로 다르다(선적 SH — 수출·수입 공유)"""
    assert sorted(DOC_PREFIXES.values()) == ["PI", "PO", "QT", "SH", "SO"]


def test_shipment_edges_match_the_design() -> None:
    """선적 활성 엣지 3 — 출고지시(동결 액션 전용)·계획/출고지시→취소(사유 필수), 자동 0, RESERVED 5(피킹·검수·출고·선적·종결 — S4-2)"""
    assert HUMAN_TRANSITIONS[DocKind.SHIPMENT] == {
        ("PLANNED", "RELEASE_ORDERED"),
        ("PLANNED", "CANCELLED"),
        ("RELEASE_ORDERED", "CANCELLED"),
    }
    assert AUTO_TRANSITIONS[DocKind.SHIPMENT] == frozenset()
    assert FREEZE_ACTION_EDGES[DocKind.SHIPMENT] == {("PLANNED", "RELEASE_ORDERED")}
    assert RESERVED[DocKind.SHIPMENT] == {"PICKING", "INSPECTED", "RELEASED", "SHIPPED", "CLOSED"}
    assert REASON_REQUIRED_TO[DocKind.SHIPMENT] == {"CANCELLED"}
    assert TERMINAL_STATUSES[DocKind.SHIPMENT] == {"CANCELLED"}
    assert INITIAL_STATUS[DocKind.SHIPMENT] == "PLANNED"
    # 범용 전이의 `to`는 취소 1값 — 출고지시는 전용 경로(release-order)로만 넘는다.
    assert public_transition_targets(DocKind.SHIPMENT) == {"CANCELLED"}


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
