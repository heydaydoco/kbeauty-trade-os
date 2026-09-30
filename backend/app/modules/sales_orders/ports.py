"""할당·백오더 분기 포트 — 같은 트랜잭션의 동기 훅 (S3-1 design-B B5 / ADR-0054).

SO 확정·취소가 재고 할당(Phase 4, S4-2)과 만나는 지점이다. **P3 기본 구현은 아무것도 쓰지 않고 `NOT_IMPLEMENTED`를 돌려준다** —
할당이 된 것처럼 보이지 않게 응답에 그대로 노출한다(fail-visible). 포트가 예외를 던지면 호출한 전이 전체가 롤백된다(fail-closed).
바인딩은 `get_allocation_port()` 한 곳이다(S4-2가 교체 — 항상 non-None). 구현 모듈은 외부 호출(네트워크)을 하지 않는다(트랜잭션 안
외부 호출 금지 — §17.1). 백오더 분기는 S3-4가 소비하므로 포트에 넣지 않는다(죽은 메서드 배제).

`on_confirmed`는 확정(PR-12)이 소비한다 — 정의는 지금 두어 계약을 고정한다. `on_cancelled`는 SO 취소(PR-7a)가 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session


class AllocationStatus(StrEnum):
    #: P3 기본 — 재고 할당은 Phase 4에서 구현된다(S4-2가 값을 더한다).
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True, slots=True)
class AllocationOutcome:
    status: AllocationStatus
    note: str


class AllocationPort(Protocol):
    def on_confirmed(self, session: Session, order: Any) -> AllocationOutcome: ...

    def on_cancelled(self, session: Session, order: Any) -> AllocationOutcome: ...


class NoAllocationPort:
    """P3 기본 구현 — 아무것도 쓰지 않는다(재고·할당 행 0)."""

    _NOTE = "재고 할당은 Phase 4에서 구현됩니다."

    def on_confirmed(self, session: Session, order: Any) -> AllocationOutcome:
        return AllocationOutcome(AllocationStatus.NOT_IMPLEMENTED, self._NOTE)

    def on_cancelled(self, session: Session, order: Any) -> AllocationOutcome:
        return AllocationOutcome(AllocationStatus.NOT_IMPLEMENTED, self._NOTE)


_PORT: AllocationPort = NoAllocationPort()


def get_allocation_port() -> AllocationPort:
    """현재 바인딩된 포트(항상 non-None). S4-2가 이 함수의 반환만 교체한다."""
    return _PORT
