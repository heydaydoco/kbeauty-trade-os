"""실제 동시 실행 헬퍼.

GC-F1이 명시한다 — "테스트는 실제 동시 실행(스레드/태스크 2개)으로 검증할 것.
순차 실행 테스트는 이 케이스의 통과 증거로 인정 안 됨."

순차 실행은 경합을 만들지 못한다. Barrier로 출발선을 맞춰야 두 스레드가
같은 순간에 같은 행을 노리고, 그래야 SELECT FOR UPDATE가 실제로 시험된다.

각 스레드는 **자기 세션**을 열어야 한다. SQLAlchemy Session은 스레드 안전하지
않아서 공유하면 경합이 아니라 그냥 깨진다.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(slots=True)
class Outcome:
    """스레드 하나의 실행 결과."""

    index: int
    value: Any = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_concurrently[T](
    worker: Callable[[int], T],
    *,
    workers: int = 2,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[Outcome]:
    """worker(i)를 workers개 스레드에서 **동시에** 실행한다.

    모든 스레드가 Barrier 앞에 모인 뒤 한꺼번에 출발한다.
    예외는 삼키지 않고 Outcome.error에 담아 돌려준다 — 어느 쪽이 실패했는지가
    바로 검증 대상이기 때문이다(예: 재고 경합에서 한 건만 성공).
    """
    barrier = threading.Barrier(workers, timeout=timeout)

    def run(index: int) -> Outcome:
        try:
            barrier.wait()
        except threading.BrokenBarrierError as exc:  # pragma: no cover
            return Outcome(index=index, error=exc)
        try:
            return Outcome(index=index, value=worker(index))
        except BaseException as exc:
            return Outcome(index=index, error=exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, i) for i in range(workers)]
        return [future.result(timeout=timeout) for future in futures]
