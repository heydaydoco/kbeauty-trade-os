"""SQL 문 수 측정 — N+1 회귀 검출용 (§18.4 "행 수와 무관한 질의 수").

행이 늘어날수록 질의가 늘면 목록이 느려지는 사고는 데이터가 쌓인 뒤에야 드러난다.
"행 1개일 때와 20개일 때 질의 수가 같다"를 테스트로 고정한다. 측정이 0을 세는 빈 검증이
되지 않도록 호출측이 `> 0` 확인을 함께 한다(빈 측정 가드).
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import event

from app.core.db.session import engine


def count_statements(work: Callable[[], object]) -> int:
    seen: list[str] = []

    def listener(*args: object) -> None:
        seen.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return len(seen)
