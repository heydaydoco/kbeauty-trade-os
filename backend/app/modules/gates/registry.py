"""게이트 평가기 등록부 — 도메인 모듈이 자기 평가기를 **등록**한다 (S3-1 PR-11a / design-D D3).

`gates`는 어떤 도메인 모듈(sales_orders·trade_chain·credit·payments·order_intake·channels…)도 임포트하지 않는다(방향 테스트). 대신 소비 모듈이 앱 조립 시점에
`register(gate_code, evaluator)`로 평가기를 넣는다(승인 코어의 `TargetSpec` 레지스트리와 같은 방향). 평가기 시그니처:

    evaluator(session, subject, phase) -> list[GateOutcome]

★ **미등록 평가기는 UNKNOWN**이다(`service.evaluate_all`) — 평가기가 없다는 사실이 조용한 통과가 되지 않는다(배포 결함은 확정을 막아 드러난다, fail-closed).
★ 같은 게이트의 2차 등록은 거부한다(조용한 덮어쓰기 금지). 테스트는 `EvaluatorRegistry()`를 새로 만들어 `evaluate_all(..., registry=...)`에 넘긴다.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.modules.gates.types import GateCode, GateOutcome, GatePhase, GateSubject

Evaluator = Callable[[Session, GateSubject, GatePhase], list[GateOutcome]]


class EvaluatorRegistry:
    """게이트 코드 → 평가기."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, gate_code: GateCode | str, evaluator: Evaluator) -> None:
        code = GateCode(gate_code).value
        if code in self._evaluators:
            raise ValueError(f"{code}의 평가기는 이미 등록돼 있습니다.")
        self._evaluators[code] = evaluator

    def get(self, gate_code: str) -> Evaluator | None:
        return self._evaluators.get(gate_code)

    def codes(self) -> frozenset[str]:
        return frozenset(self._evaluators)


#: 앱 전역 등록부 — 소비 모듈이 import 시점에 채운다(`trade_chain.gate_evaluators`).
DEFAULT_REGISTRY = EvaluatorRegistry()


def register(gate_code: GateCode | str, evaluator: Evaluator) -> None:
    """전역 등록부에 평가기를 등록한다."""
    DEFAULT_REGISTRY.register(gate_code, evaluator)
