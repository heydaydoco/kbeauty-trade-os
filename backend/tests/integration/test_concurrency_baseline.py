"""J. 안전 계약 — 동시 실행 하네스 기준선.

GC-F1(재고 할당 경합, S4-2)과 채번 동시 100건(S0-2)은 "실제 동시 실행"을
요구한다. 그 테스트들이 나중에 실패했을 때 **하네스가 범인인지 코드가 범인인지**
구분할 수 있어야 하므로, 하네스 자체가 동작한다는 기준선을 지금 박아 둔다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.uow import unit_of_work
from tests.support.concurrency import run_concurrently
from tests.support.scratch import count_rows, scratch_table

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]

WORKERS = 4


def test_threads_commit_independently_and_see_each_other() -> None:
    """여러 스레드가 각자 커밋하고, 끝나면 서로의 커밋이 모두 보인다"""
    with scratch_table() as table:

        def worker(index: int) -> int:
            # 스레드마다 자기 세션을 연다 — Session은 스레드 안전하지 않다.
            with unit_of_work() as uow:
                uow.session.execute(
                    text(f'INSERT INTO public."{table}" (label) VALUES (:v)'),
                    {"v": f"worker-{index}"},
                )
            return index

        outcomes = run_concurrently(worker, workers=WORKERS)

        failures = [o for o in outcomes if not o.ok]
        assert not failures, f"실패한 스레드: {[(o.index, repr(o.error)) for o in failures]}"
        assert count_rows(table) == WORKERS


def test_row_lock_serializes_competing_updates() -> None:
    """SELECT FOR UPDATE가 같은 행을 노린 동시 갱신을 실제로 직렬화한다

    §17.2의 "확인 → 기록" 직렬화가 이 DB 설정에서 동작하는지 확인한다.
    두 스레드가 동시에 같은 행을 잠그고 1씩 더하면 결과는 반드시 2다
    (직렬화가 안 되면 갱신 손실로 1이 된다).
    """
    with scratch_table() as table:
        with unit_of_work() as uow:
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (id, label) VALUES (1, :v)'), {"v": "0"}
            )

        def worker(_: int) -> None:
            with unit_of_work() as uow:
                current = uow.session.execute(
                    text(f'SELECT label FROM public."{table}" WHERE id = 1 FOR UPDATE')
                ).scalar_one()
                uow.session.execute(
                    text(f'UPDATE public."{table}" SET label = :v WHERE id = 1'),
                    {"v": str(int(current) + 1)},
                )

        outcomes = run_concurrently(worker, workers=2)
        assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes if not o.ok]

        from app.core.db.session import owner_engine

        with owner_engine.connect() as connection:
            final = connection.execute(
                text(f'SELECT label FROM public."{table}" WHERE id = 1')
            ).scalar_one()
        assert final == "2", f"갱신 손실 발생 — 기대 '2', 실제 {final!r}"
