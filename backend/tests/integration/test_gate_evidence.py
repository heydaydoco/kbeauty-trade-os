"""A. 확정 증거 스냅샷 — `evaluation_results`·`record_evaluation` (S3-1 PR-11a / design-D D3·D5 / design-integrated X-11).

확정 통로(PR-12)가 부를 증거 기록 함수의 계약을 지금 고정한다: 결과 JSON(비PASS 결과+정산 상태·passed_gates·사용한 override id·승인 ref·정책 출처)에는 판매 단가·수량·준비 상태 요약만 있고
(원가·마진 키 0, JSON 직렬화 가능), 기록은 BLOCKED 시도가 여러 번·CONFIRMED가 SO당 1행이다. 조회 경로는 이 함수를 부르지 않는다(K 계약 스캔).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.logging.redaction import is_sensitive_key
from app.modules.gates import service as gates_service
from app.modules.gates.models import (
    EVALUATION_BLOCKED,
    EVALUATION_CONFIRMED,
    SUBJECT_SALES_ORDER,
)
from app.modules.gates.types import GateCode
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import gate_flow
from tests.factories.gates import (
    actor_of_user,
    evaluate,
    line_ids,
    one,
    passing_so,
    scalar,
    set_line,
    set_policy,
)
from tests.factories.payments import actor

pytestmark = pytest.mark.group_a


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(inner))
    elif isinstance(value, list):
        for inner in value:
            keys.extend(_walk_keys(inner))
    return keys


def _bundle(so_id: int) -> Any:
    with unit_of_work() as uow:
        return gate_flow.evaluate_sales_order(uow.session, so_id, authoritative=False)


def _blocked_so() -> tuple[dict[str, Any], int, str]:
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    (line,) = line_ids(so["id"])
    digest = one(evaluate(so["id"]), GateCode.PRICE_DEVIATION, line).basis_hash
    return so, line, digest


def test_the_results_json_lists_non_pass_results_with_their_settlement_and_the_passed_gates() -> (
    None
):
    """결과 JSON: 비PASS 결과(해소 수단·정산 상태·판정 해시·근거)와 passed_gates(전 결과 PASS인 게이트)·사용한 override id·승인 ref·정책 출처 — 값은 판매 단가·수량·준비 상태 요약뿐이고 직렬화 가능하다"""
    so, line, digest = _blocked_so()
    who = actor(RoleCode.TRADE)
    gate_flow.grant_gate_override(
        actor=who,
        idempotency_key="ev-1",
        so_id=so["id"],
        payload={
            "gate_code": "PRICE_DEVIATION",
            "line_id": line,
            "basis_hash": digest,
            "reason": "증거 시험 사유",
        },
    )
    bundle = _bundle(so["id"])
    results = gates_service.evaluation_results(
        bundle.clearance, policy_sources=bundle.policy_sources, approval_id=None
    )
    json.dumps(results)  # 직렬화 가능
    (entry,) = results["gates"]
    assert (entry["gate_code"], entry["line_id"], entry["level"], entry["resolution"]) == (
        "PRICE_DEVIATION",
        line,
        "BLOCK",
        "OVERRIDE",
    )
    assert entry["settlement"] == "OVERRIDDEN" and entry["basis_hash"] == digest
    assert (
        entry["basis"]["unit_price_amount"] == 1100
        and entry["basis"]["reference_price_amount"] == 1000
    )
    assert results["passed_gates"] == [
        "ITEM_MAPPING",
        "DUPLICATE_PO",
        "CREDIT",
        "MARKET_READINESS",
        "MOQ",
        "PI_DEPOSIT",
    ]
    assert results["used_override_ids"] == [scalar("SELECT max(id) FROM gate_overrides")]
    assert results["approval_id"] is None
    assert results["policy_source"] == {
        "pi_advance_gate_mode": "UNSET_DEFAULT",
        "price_deviation_tolerance_bp": "SET",
    }
    assert not [key for key in _walk_keys(results) if is_sensitive_key(key)]
    assert bundle.clearance.cleared is True


def test_warnings_are_kept_in_the_evidence_and_unresolved_results_are_marked() -> None:
    """WARN(PO번호 없음)도 증거에 남고(NOT_REQUIRED), 미해소 결과는 UNRESOLVED로 표시된다 — 확정 차단 시도(BLOCKED)의 근거. 모드 OFF는 PI 게이트를 passed_gates에 넣는다(스킵 사실은 이유 코드에)"""
    so = passing_so(po_no=None, price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    bundle = _bundle(so["id"])
    results = gates_service.evaluation_results(bundle.clearance)
    states = {(g["gate_code"], g["settlement"]) for g in results["gates"]}
    assert ("PRICE_DEVIATION", "UNRESOLVED") in states
    assert ("DUPLICATE_PO", "NOT_REQUIRED") in states
    assert bundle.clearance.cleared is False
    set_policy("pi_advance_gate_mode", "OFF")
    assert (
        "PI_DEPOSIT"
        in gates_service.evaluation_results(_bundle(so["id"]).clearance)["passed_gates"]
    )


def test_blocked_attempts_accumulate_and_a_sales_order_has_exactly_one_confirmed_row() -> None:
    """기록: BLOCKED 시도는 여러 번 남고 입력 digest가 실리며, CONFIRMED는 SO당 1행 — 두 번째 CONFIRMED는 DB 유니크가 거부한다(번역 없이 500으로 드러나는 확정 이중 기록 결함)"""
    so, _, _ = _blocked_so()
    who = actor(RoleCode.TRADE)
    bundle = _bundle(so["id"])
    results = gates_service.evaluation_results(
        bundle.clearance, policy_sources=bundle.policy_sources
    )
    with unit_of_work() as uow:
        for _ in range(2):
            row = gates_service.record_evaluation(
                uow.session,
                subject_type=SUBJECT_SALES_ORDER,
                subject_id=so["id"],
                outcome_kind=EVALUATION_BLOCKED,
                results=results,
                input_digest=bundle.input_digest,
                actor_user_id=who.id,
            )
            assert row.id > 0
    assert scalar("SELECT count(*) FROM gate_evaluations WHERE outcome = 'BLOCKED'") == 2
    stored = scalar("SELECT results FROM gate_evaluations WHERE outcome = 'BLOCKED' LIMIT 1")
    assert stored["gates"][0]["gate_code"] == "PRICE_DEVIATION"
    assert scalar("SELECT input_digest FROM gate_evaluations LIMIT 1") == bundle.input_digest
    with unit_of_work() as uow:
        gates_service.record_evaluation(
            uow.session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so["id"],
            outcome_kind=EVALUATION_CONFIRMED,
            results=results,
            input_digest=bundle.input_digest,
            actor_user_id=who.id,
        )
    with pytest.raises(IntegrityError), unit_of_work() as uow:
        gates_service.record_evaluation(
            uow.session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so["id"],
            outcome_kind=EVALUATION_CONFIRMED,
            results=results,
            input_digest=bundle.input_digest,
            actor_user_id=who.id,
        )
    assert scalar("SELECT count(*) FROM gate_evaluations WHERE outcome = 'CONFIRMED'") == 1


def test_a_check_violation_is_translated_and_an_unknown_actor_is_not_found() -> None:
    """번역표: 증거 행의 CHECK 위반은 422(VALIDATION), 없는 평가자 FK는 404로 번역된다 — 번역되지 않는 제약(CONFIRMED 유니크)만 500으로 드러난다"""
    from app.core.errors.exceptions import AppError

    so, _, _ = _blocked_so()
    bundle = _bundle(so["id"])
    results = gates_service.evaluation_results(bundle.clearance)
    who = actor_of_user(actor(RoleCode.TRADE).id, RoleCode.TRADE)
    with pytest.raises(AppError) as bad_digest, unit_of_work() as uow:
        gates_service.record_evaluation(
            uow.session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so["id"],
            outcome_kind=EVALUATION_BLOCKED,
            results=results,
            input_digest="not-a-digest",
            actor_user_id=who.id,
        )
    assert bad_digest.value.code.value == "COMMON.VALIDATION.INVALID_FIELD"
    with pytest.raises(AppError) as no_user, unit_of_work() as uow:
        gates_service.record_evaluation(
            uow.session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so["id"],
            outcome_kind=EVALUATION_BLOCKED,
            results=results,
            input_digest=bundle.input_digest,
            actor_user_id=9_999_999,
        )
    assert no_user.value.code.value == "COMMON.RESOURCE.NOT_FOUND"
