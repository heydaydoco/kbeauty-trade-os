"""A·H. 인테이크 시점 게이트(INTAKE phase 어댑터) — 같은 평가기·단일 통과 판정·인테이크측 중복 PO·STALE_MAPPING (S3-1 PR-13a / design-D D4 / ADR-0071).

SO 확정과 **같은 평가기 등록부**를 쓰되 `GatePhase.INTAKE`로 평가한다: 품번 매핑·중복 PO·가격 편차·준비도·MOQ 5종만 돌고(여신·PI 입금은 확정 전용), 결과는 정보이며
접수 확정의 하드 조건은 품번 매핑·중복 PO뿐이다. 통과 판정은 `gates.service.clearance` 하나다(복제 0). 인테이크 단계에는 override가 없다(SO에서만 부여).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.modules.gates import service as gates_service
from app.modules.gates.policy import GATE_SPECS
from app.modules.gates.registry import DEFAULT_REGISTRY
from app.modules.gates.types import (
    SUBJECT_INTAKE,
    GateCode,
    GateLevel,
    GateOutcome,
    GatePhase,
    GateResolution,
)
from app.modules.order_intake import service as intake_service
from app.modules.trade_chain import gate_evaluators, intake_flow
from tests.factories.gates import set_moq, set_policy, set_readiness
from tests.factories.intake import (
    execute,
    land,
    line_body,
    trade_actor,
    world,
)
from tests.factories.trade import create_direct_so, create_priced_sku, map_buyer_item_code
from tests.support.gate_coverage import COVERED, covers

pytestmark = pytest.mark.group_a

G = GateCode
L = GateLevel
R = GateResolution
I = GatePhase.INTAKE  # noqa: E741


def _evaluate(intake_id: int, only: tuple[GateCode, ...] | None = None) -> list[GateOutcome]:
    with unit_of_work() as uow:
        session = uow.session
        intake = intake_service.require_intake(session, intake_id)
        lines = intake_service.live_lines(session, intake_id)
        return intake_flow.evaluate_intake(session, intake, lines, only=only).outcomes


def _one(outcomes: list[GateOutcome], gate: GateCode, line_no: int | None = None) -> GateOutcome:
    found = [o for o in outcomes if o.gate_code == gate.value]
    if line_no is not None:
        found = [o for o in found if o.line_id is not None]
    assert found, (gate, [(o.gate_code, o.level.value) for o in outcomes])
    return found[0]


def test_intake_phase_runs_exactly_the_five_gates_that_apply_before_acceptance() -> None:
    """INTAKE 평가는 품번·중복 PO·가격·준비도·MOQ 5종만 돈다 — 여신·PI 입금은 SO 확정 전용이라 평가되지 않는다(명세의 phases가 정본)"""
    w = world()
    intake = land(w)
    codes = {o.gate_code for o in _evaluate(intake["id"])}
    assert codes == {g.value for g, spec in GATE_SPECS.items() if I in spec.phases}
    assert codes == {"ITEM_MAPPING", "DUPLICATE_PO", "PRICE_DEVIATION", "MARKET_READINESS", "MOQ"}
    assert _evaluate(intake["id"], only=(G.CREDIT, G.PI_DEPOSIT)) == []


@covers(G.ITEM_MAPPING, L.PASS, R.NONE, I)
@covers(G.DUPLICATE_PO, L.PASS, R.NONE, I)
def test_a_clean_intake_passes_the_hard_gates_through_the_single_clearance() -> None:
    """품번이 전부 매핑되고 PO가 비점유면 하드 게이트 2종이 PASS이고, `clearance`가 확정 가능으로 정산한다"""
    w = world()
    intake = land(w)
    outcomes = _evaluate(intake["id"], only=intake_flow.HARD_GATES)
    assert {(o.gate_code, o.level) for o in outcomes} == {
        ("ITEM_MAPPING", L.PASS),
        ("DUPLICATE_PO", L.PASS),
    }
    assert gates_service.clearance(outcomes, {}, False).cleared is True


@covers(G.ITEM_MAPPING, L.BLOCK, R.NONE, I)
def test_unmapped_deleted_and_changed_mappings_block_with_distinct_reason_codes() -> None:
    """미매핑=ITEM_UNMAPPED·삭제 SKU=SKU_DELETED·검토 뒤 매핑 변경=MAPPING_CHANGED(STALE) — 전부 BLOCK/NONE이고 clearance가 미해소로 정산한다"""
    w = world(lines=3)
    intake = land(
        w, lines=[line_body("UNMAPPED-X"), line_body(w["codes"][1]), line_body(w["codes"][2])]
    )
    execute(
        "UPDATE skus SET deleted_at = now() WHERE id = :s", s=w["sku_ids"][1]
    )  # 저장본 SKU 삭제
    other = create_priced_sku()
    execute(
        "UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p AND buyer_item_code = :c",
        p=w["buyer"],
        c=w["codes"][2],
    )
    map_buyer_item_code(w["buyer"], other, w["codes"][2])  # 3번 라인의 매핑이 다른 SKU로
    outcomes = list(_evaluate(intake["id"], only=(G.ITEM_MAPPING,)))
    reasons = sorted(o.reason_code for o in outcomes)
    assert reasons == ["ITEM_UNMAPPED", "MAPPING_CHANGED", "SKU_DELETED"]
    assert all(o.level is L.BLOCK and o.resolution is R.NONE for o in outcomes)
    assert gates_service.clearance(outcomes, {}, False).cleared is False
    # 미매핑 라인에 매핑을 새로 걸어도(검토 뒤 신규 매핑) 저장본 None과 다르므로 STALE이다 — resolve 전에는 통과하지 않는다
    map_buyer_item_code(w["buyer"], create_priced_sku(), "UNMAPPED-X")
    again = _evaluate(intake["id"], only=(G.ITEM_MAPPING,))
    assert "MAPPING_CHANGED" in {o.reason_code for o in again}


@covers(G.ITEM_MAPPING, L.WARN, R.NONE, I)
def test_a_discontinued_sku_is_only_a_warning_at_intake_but_blocks_at_confirm() -> None:
    """단종 SKU: INTAKE=WARN(접수 허용 — 통과 판정은 NOT_REQUIRED)·CONFIRM=BLOCK/NONE(A11-2) — 같은 평가기가 phase로 갈린다"""
    w = world()
    intake = land(w)
    execute("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s", s=w["sku_ids"][0])
    outcomes = _evaluate(intake["id"], only=(G.ITEM_MAPPING,))
    assert [(o.level, o.reason_code) for o in outcomes] == [(L.WARN, "SKU_DISCONTINUED")]
    assert gates_service.clearance(outcomes, {}, False).cleared is True
    with unit_of_work() as uow:  # 같은 대상을 CONFIRM 시점으로 평가하면 BLOCK이다
        row = intake_service.require_intake(uow.session, intake["id"])
        lines = intake_service.live_lines(uow.session, intake["id"])
        subject = intake_flow.subject_from_intake(uow.session, row, lines)
        confirm = gate_evaluators.evaluate_item_mapping(uow.session, subject, GatePhase.CONFIRM)
    assert [(o.level, o.resolution) for o in confirm] == [(L.BLOCK, R.NONE)]


@covers(G.DUPLICATE_PO, L.BLOCK, R.NONE, I)
def test_duplicate_po_at_intake_sees_pending_intakes_and_active_sos_but_not_itself() -> None:
    """인테이크측 DUPLICATE_PO — 다른 PENDING 인테이크(착지가 막지만 평가기는 방어적으로 본다)·비취소 SO는 BLOCK/NONE, 자기 자신·거부된 인테이크는 점유가 아니다"""
    w = world()
    intake = land(w, po_no="PO-GATE-1")
    own = _evaluate(intake["id"], only=(G.DUPLICATE_PO,))
    assert [(o.level, o.reason_code) for o in own] == [(L.PASS, "NO_DUPLICATE")]  # 자기 자신은 제외
    so = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="po-gate-1")
    blocked = _evaluate(intake["id"], only=(G.DUPLICATE_PO,))
    assert [(o.level, o.resolution, o.reason_code) for o in blocked] == [
        (L.BLOCK, R.NONE, "DUPLICATE_PO_NO")
    ]
    assert (
        blocked[0].detail["other_doc_number"] == so["doc_number"]
    )  # 표시 전용 상세 — 해시·basis 밖
    assert "other_doc_number" not in blocked[0].basis
    # 가상 대상: 같은 키의 다른 PENDING 인테이크(DB 유니크가 정상 경로를 막으므로 합성 대상으로 평가기를 직접 호출)
    other = world()
    second = land(other, po_no="PO-GATE-2")
    execute(
        "UPDATE order_intakes SET buyer_partner_id = buyer_partner_id WHERE id = :i", i=second["id"]
    )
    with unit_of_work() as uow:
        row = intake_service.require_intake(uow.session, second["id"])
        lines = intake_service.live_lines(uow.session, second["id"])
        subject = intake_flow.subject_from_intake(uow.session, row, lines)
        import dataclasses

        probe = dataclasses.replace(
            subject, id=999_999
        )  # 다른 인테이크 id인 척 — 자기 자신이 점유자로 보여야 한다
        found = gate_evaluators.evaluate_duplicate_po(uow.session, probe, GatePhase.INTAKE)
    assert [(o.level, o.detail["other_intake_id"]) for o in found] == [(L.BLOCK, second["id"])]


def test_an_so_whose_id_equals_the_intake_id_does_not_hide_a_duplicate() -> None:
    """인테이크 id와 같은 숫자 id를 가진 SO가 점유자여도 가려지지 않는다(SO 평가기의 `id != subject.id` 제외가 인테이크 대상에 새지 않는다)"""
    w = world()
    intake = land(w, po_no="PO-ID-1")
    so = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="PO-ID-1")
    with unit_of_work() as uow:
        row = intake_service.require_intake(uow.session, intake["id"])
        lines = intake_service.live_lines(uow.session, intake["id"])
        subject = intake_flow.subject_from_intake(uow.session, row, lines)
        import dataclasses

        same_numeric_id = dataclasses.replace(subject, id=so["id"])
        found = gate_evaluators.evaluate_duplicate_po(
            uow.session, same_numeric_id, GatePhase.INTAKE
        )
    assert found[0].level is L.BLOCK


@covers(G.PRICE_DEVIATION, L.PASS, R.NONE, I)
@covers(G.PRICE_DEVIATION, L.BLOCK, R.OVERRIDE, I)
def test_price_deviation_at_intake_uses_the_master_price_and_integer_cross_multiplication() -> None:
    """기준가=인테이크 시점 마스터 판가(1000)·허용치 500bp — 정확히 5%는 PASS, 1 최소단위 초과는 BLOCK/OVERRIDE(정보 — 접수는 막지 않는다), 방향 양쪽"""
    set_policy("price_deviation_tolerance_bp", 500)
    w = world()
    ok = land(w, lines=[line_body(w["codes"][0], unit_price="10.50")])  # +5.00% 정확히 허용치
    assert _one(_evaluate(ok["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION).level is L.PASS
    over = land(w, lines=[line_body(w["codes"][0], unit_price="10.51")])
    result = _one(_evaluate(over["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.OVERRIDE,
        "TOLERANCE_EXCEEDED",
    )
    under = land(w, lines=[line_body(w["codes"][0], unit_price="9.49")])
    assert (
        _one(_evaluate(under["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION).level is L.BLOCK
    )
    below_ok = land(w, lines=[line_body(w["codes"][0], unit_price="9.50")])
    assert (
        _one(_evaluate(below_ok["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION).level
        is L.PASS
    )
    # 인테이크 단계에는 override가 없다 — clearance는 BLOCK/OVERRIDE를 미해소로 정산하지만 접수 확정은 하드 게이트만 본다
    assert gates_service.clearance([result], {}, False).cleared is False


@covers(G.PRICE_DEVIATION, L.UNKNOWN, R.OVERRIDE, I)
def test_a_missing_or_foreign_currency_reference_price_is_unknown_not_converted() -> None:
    """기준가 부재·다른 통화만 있는 SKU는 UNKNOWN/OVERRIDE — 환산하지 않고 0·NULL로 대체하지 않는다"""
    set_policy("price_deviation_tolerance_bp", 500)
    w = world()
    no_price_sku = create_priced_sku(currency="KRW", amount=1000)  # USD 판가가 없다
    map_buyer_item_code(w["buyer"], no_price_sku, "KRW-ONLY")
    intake = land(w, lines=[line_body("KRW-ONLY")])
    result = _one(_evaluate(intake["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION)
    assert (result.level, result.resolution, result.reason_code) == (
        L.UNKNOWN,
        R.OVERRIDE,
        "NO_REFERENCE_PRICE",
    )
    assert gates_service.clearance([result], {}, False).cleared is False


@covers(G.MOQ, L.PASS, R.NONE, I)
@covers(G.MOQ, L.BLOCK, R.OVERRIDE, I)
def test_moq_at_intake_compares_the_paid_quantity_sum_and_null_moq_passes() -> None:
    """같은 SKU 수량 합 < MOQ면 BLOCK/OVERRIDE(정보), 경계(=MOQ)는 PASS, MOQ NULL은 PASS('정책 없음'은 평가 실패가 아니다)"""
    w = world()
    sku = w["sku_ids"][0]
    below = land(w, lines=[line_body(w["codes"][0], quantity=4)])
    set_moq(sku, 5)
    result = _one(_evaluate(below["id"], only=(G.MOQ,)), G.MOQ)
    assert (result.level, result.resolution) == (L.BLOCK, R.OVERRIDE) and result.basis["moq"] == 5
    at_moq = land(w, lines=[line_body(w["codes"][0], quantity=5)])
    assert _one(_evaluate(at_moq["id"], only=(G.MOQ,)), G.MOQ).level is L.PASS
    set_moq(sku, None)
    assert _one(_evaluate(below["id"], only=(G.MOQ,)), G.MOQ).level is L.PASS


@covers(G.MARKET_READINESS, L.PASS, R.NONE, I)
@covers(G.MARKET_READINESS, L.WARN, R.NONE, I)
@covers(G.MARKET_READINESS, L.BLOCK, R.OVERRIDE, I)
@covers(G.MARKET_READINESS, L.UNKNOWN, R.OVERRIDE, I)
@pytest.mark.parametrize(
    ("color", "level", "resolution"),
    [
        ("GREEN", L.PASS, R.NONE),
        ("YELLOW", L.WARN, R.NONE),
        ("RED", L.BLOCK, R.OVERRIDE),
        ("GRAY", L.UNKNOWN, R.OVERRIDE),
    ],
)
def test_market_readiness_at_intake_maps_the_matrix_cell_and_gray_is_unknown(
    color: str, level: GateLevel, resolution: GateResolution
) -> None:
    """준비도 셀 색 → 결과(GREEN=PASS·YELLOW=WARN·RED=BLOCK·GRAY=UNKNOWN — 통과로 읽지 않는다). 계산값 표시이며 법적 판정이 아니다(고정 문구)"""
    w = world()
    set_readiness(w["sku_ids"][0], color)
    intake = land(w)
    result = _one(_evaluate(intake["id"], only=(G.MARKET_READINESS,)), G.MARKET_READINESS)
    assert (result.level, result.resolution) == (level, resolution)
    assert "법적 판정 아님" in result.message_ko or color == "GREEN"


def test_an_evaluator_exception_at_intake_is_unknown_and_blocks_the_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """평가기 예외는 UNKNOWN(EVALUATION_ERROR) — 통과가 아니다. 하드 게이트가 평가 불능이면 접수 확정은 409 `GATE.UNRESOLVED`(fail-closed)이고 SO는 만들어지지 않는다"""
    w = world()
    intake = land(w)

    def boom(*_a: Any, **_k: Any) -> list[GateOutcome]:
        raise RuntimeError("평가기 장애 주입")

    monkeypatch.setitem(DEFAULT_REGISTRY._evaluators, "ITEM_MAPPING", boom)
    outcomes = _evaluate(intake["id"], only=(G.ITEM_MAPPING,))
    assert [(o.level, o.resolution, o.reason_code) for o in outcomes] == [
        (L.UNKNOWN, R.NONE, "EVALUATION_ERROR")
    ]
    assert gates_service.clearance(outcomes, {}, False).cleared is False
    with pytest.raises(AppError) as caught:
        intake_flow.confirm_intake(
            actor=trade_actor(),
            idempotency_key="k-unknown",
            intake_id=intake["id"],
            version=intake["version"],
        )
    assert (
        caught.value.code.value == "ORDER_INTAKE.GATE.UNRESOLVED"
        and caught.value.status_code == 409
    )


def test_an_unregistered_evaluator_at_intake_is_unknown_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """미등록 평가기는 UNKNOWN(EVALUATOR_NOT_REGISTERED) — 배포 결함이 조용한 통과가 되지 않고 접수 확정을 막는다"""
    w = world()
    intake = land(w)
    monkeypatch.delitem(DEFAULT_REGISTRY._evaluators, "DUPLICATE_PO")
    outcomes = _evaluate(intake["id"], only=(G.DUPLICATE_PO,))
    assert [(o.level, o.reason_code) for o in outcomes] == [(L.UNKNOWN, "EVALUATOR_NOT_REGISTERED")]
    with pytest.raises(AppError) as caught:
        intake_flow.confirm_intake(
            actor=trade_actor(),
            idempotency_key="k-unreg",
            intake_id=intake["id"],
            version=intake["version"],
        )
    assert caught.value.code.value == "ORDER_INTAKE.GATE.UNRESOLVED"


def test_the_so_only_evaluators_refuse_an_intake_subject() -> None:
    """여신·PI 입금 평가기는 인테이크 대상을 받지 않는다(ValueError — 평가 틀이 UNKNOWN으로 바꾼다) — SO 전용 게이트가 인테이크에 새지 않는다"""
    w = world()
    intake = land(w)
    with unit_of_work() as uow:
        row = intake_service.require_intake(uow.session, intake["id"])
        subject = intake_flow.subject_from_intake(
            uow.session, row, intake_service.live_lines(uow.session, intake["id"])
        )
        assert subject.kind == SUBJECT_INTAKE
        for evaluator in (
            gate_evaluators.evaluate_credit_gate,
            gate_evaluators.evaluate_pi_deposit,
        ):
            with pytest.raises(ValueError):
                evaluator(uow.session, subject, GatePhase.CONFIRM)


def test_the_intake_phase_ledger_covers_every_declared_intake_combination() -> None:
    """메타 — `GATE_SPECS`가 INTAKE 시점에 낼 수 있다고 선언한 (게이트, 결과, 해소) 조합마다 이 파일에 INTAKE 시점 테스트가 있다(선언만 있고 검증 없는 공회전 방지)"""
    required = {
        (code, result.level, result.resolution, I)
        for code, spec in GATE_SPECS.items()
        if I in spec.phases
        for result in spec.results
        if I in result.phases
    }
    assert len(required) >= 12
    assert required <= COVERED, sorted(required - COVERED, key=str)


def test_price_policy_default_when_unset_is_zero_bp_and_visible() -> None:
    """허용치 미설정=0bp(가장 엄격 — 조용한 기본값이 아니라 UNSET_DEFAULT 출처가 근거에 실린다) — 1 최소단위 편차도 BLOCK"""
    set_policy("price_deviation_tolerance_bp", None)
    w = world()
    intake = land(w, lines=[line_body(w["codes"][0], unit_price="10.01")])
    result = _one(_evaluate(intake["id"], only=(G.PRICE_DEVIATION,)), G.PRICE_DEVIATION)
    assert result.level is L.BLOCK and result.basis["policy_source"] == "UNSET_DEFAULT"
