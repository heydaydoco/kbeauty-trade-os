"""A. `GATE_SPECS` 메타 테스트 — 명세가 선언한 (게이트, 결과, 해소, 시점) 조합마다 최소 1개 테스트가 있다 (S3-1 PR-11a / design-D D3 (f)).

테스트가 `@covers(...)`로 자기 조합을 **데코레이션 시점에** 장부(`tests/support/gate_coverage.COVERED`)에 올린다(실행 순서·`-k` 선택과 무관). 명세에 조합을 더하고 테스트를 빼먹으면 여기서 실패한다 —
평가기가 낼 수 있는 결과가 "선언만 있고 검증은 없는" 공회전을 막는다. 장부를 채우는 테스트 모듈을 임포트해 이 파일만 돌려도 같은 결과가 나오게 한다.
"""

from __future__ import annotations

import pytest

from app.modules.gates.policy import FRAMEWORK_RESULT, GATE_SPECS, declared_results
from app.modules.gates.types import GateCode, GatePhase
from tests.integration import test_gate_evaluators, test_gate_framework  # noqa: F401  (장부 채움)
from tests.support.gate_coverage import COVERED
from tests.unit import test_gate_core  # noqa: F401  (장부 채움)

pytestmark = pytest.mark.group_a


def _required() -> set[tuple[GateCode, str, str, GatePhase]]:
    """명세가 요구하는 조합 — 각 결과는 그것이 나올 수 있는 시점 중 **확정 시점**(없으면 인테이크)에서 검증돼야 한다."""
    required: set[tuple[GateCode, str, str, GatePhase]] = set()
    for code, spec in GATE_SPECS.items():
        for result in spec.results:
            phase = GatePhase.CONFIRM if GatePhase.CONFIRM in result.phases else GatePhase.INTAKE
            required.add((code, result.level.value, result.resolution.value, phase))
        required.add(
            (
                code,
                FRAMEWORK_RESULT.level.value,
                FRAMEWORK_RESULT.resolution.value,
                GatePhase.CONFIRM,
            )
        )
    return required


def test_the_ledger_is_not_empty_and_the_spec_is_not_vacuous() -> None:
    """장부와 명세가 비어 있지 않다(공회전 방지) — 7개 게이트·전 조합"""
    assert len(COVERED) >= 20
    assert len(_required()) >= 25
    assert {gate for gate, *_ in _required()} == set(GateCode)


def test_every_declared_gate_result_combination_has_at_least_one_test() -> None:
    """명세(GATE_SPECS)가 선언한 모든 (게이트, 결과, 해소, 시점) 조합이 장부에 있다 — 빠진 조합 목록을 실패 메시지로 보여 준다"""
    covered = {(g, lvl.value, res.value, ph) for g, lvl, res, ph in COVERED}
    missing = sorted(
        (g.value, lvl, res, ph.value)
        for g, lvl, res, ph in _required()
        if (g, lvl, res, ph) not in covered
    )
    assert missing == [], f"테스트가 없는 명세 조합: {missing}"


def test_the_ledger_only_claims_combinations_the_spec_declares() -> None:
    """장부가 명세에 없는 조합을 주장하지 않는다 — 테스트가 존재하지 않는 결과 조합을 검증한다고 적는 오기를 막는다"""
    for gate, level, resolution, _phase in COVERED:
        assert (level, resolution) in declared_results(gate), (gate, level, resolution)
