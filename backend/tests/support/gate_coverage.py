"""`GATE_SPECS` 메타 테스트의 장부 — 게이트 결과 조합(게이트, 결과, 해소, 시점)마다 최소 1개 테스트가 있음을 **정적으로** 대사한다 (S3-1 PR-11a / design-D D3 (f)).

테스트가 `@covers(...)`로 자기가 검증하는 조합을 **데코레이션 시점에** 등록한다(실행 순서·-k 선택과 무관 — 런타임 기록이 아니다). 메타 테스트(`test_gate_specs_meta`)가 명세(`GATE_SPECS`)의 모든 조합이
이 장부에 있는지 확인한다: 명세에 조합을 더하고 테스트를 빼먹으면 실패한다(공회전 방지).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.modules.gates.types import GateCode, GateLevel, GatePhase, GateResolution

COVERED: set[tuple[GateCode, GateLevel, GateResolution, GatePhase]] = set()


def register(
    gate: GateCode,
    level: GateLevel,
    resolution: GateResolution,
    phase: GatePhase = GatePhase.CONFIRM,
) -> None:
    COVERED.add((gate, level, resolution, phase))


def covers(
    gate: GateCode,
    level: GateLevel,
    resolution: GateResolution,
    phase: GatePhase = GatePhase.CONFIRM,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """테스트 함수가 (게이트, 결과, 해소, 시점) 조합을 검증한다고 장부에 올린다."""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        register(gate, level, resolution, phase)
        return function

    return decorate
