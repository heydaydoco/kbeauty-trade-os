"""J. 안전 계약 — 트랜잭션 경계 (DESIGN.md §17.1)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.session import SessionFactory, TransactionBoundaryError
from app.core.db.uow import unit_of_work
from tests.support.scratch import count_rows, scratch_table

pytestmark = pytest.mark.group_j


def test_rollback_leaves_nothing_committed() -> None:
    """트랜잭션 중간에 예외가 나면 앞서 넣은 것까지 전부 사라진다"""
    with scratch_table() as table:
        with pytest.raises(RuntimeError, match="일부러"), unit_of_work() as uow:
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "첫째"}
            )
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "둘째"}
            )
            raise RuntimeError("일부러 낸 오류")
        assert count_rows(table) == 0


def test_commit_persists_all_rows() -> None:
    """예외 없이 끝나면 같은 트랜잭션의 기록이 전부 남는다"""
    with scratch_table() as table:
        with unit_of_work() as uow:
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "하나"}
            )
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "둘"}
            )
        assert count_rows(table) == 2


def test_direct_commit_is_rejected() -> None:
    """서비스 레이어 밖에서 commit()을 부르면 거부된다"""
    session = SessionFactory()
    try:
        with pytest.raises(TransactionBoundaryError, match="서비스 레이어"):
            session.commit()
    finally:
        session.close()


def test_direct_rollback_is_rejected() -> None:
    """rollback()도 마찬가지로 서비스 레이어만 부를 수 있다"""
    session = SessionFactory()
    try:
        with pytest.raises(TransactionBoundaryError):
            session.rollback()
    finally:
        session.close()


def test_nested_unit_of_work_joins_outer_transaction() -> None:
    """서비스가 서비스를 불러도 트랜잭션은 하나다 — 안쪽이 따로 커밋하지 않는다"""
    with scratch_table() as table:
        with pytest.raises(RuntimeError, match="바깥에서"), unit_of_work() as outer:
            outer.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "바깥"}
            )
            with unit_of_work() as inner:
                assert inner is outer
                inner.session.execute(
                    text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "안쪽"}
                )
            # 안쪽 블록을 빠져나와도 아직 커밋되지 않았음을 확인
            assert count_rows(table) == 0
            raise RuntimeError("바깥에서 실패")
        # 안쪽이 따로 커밋했다면 여기서 1이 남는다
        assert count_rows(table) == 0


def test_after_commit_hook_runs_outside_transaction() -> None:
    """알림·연동은 커밋이 끝난 뒤에 실행된다 (아웃박스 원칙)"""
    fired: list[str] = []
    with scratch_table() as table:
        with unit_of_work() as uow:
            uow.session.execute(
                text(f'INSERT INTO public."{table}" (label) VALUES (:v)'), {"v": "x"}
            )
            uow.add_after_commit(lambda: fired.append(f"rows={count_rows(table)}"))
            assert fired == []
        # 훅이 볼 때는 이미 커밋이 끝나 있어야 한다
        assert fired == ["rows=1"]


def test_after_commit_hook_skipped_on_rollback() -> None:
    """롤백된 일은 대외에 통보하지 않는다"""
    fired: list[str] = []
    with pytest.raises(RuntimeError), unit_of_work() as uow:
        uow.add_after_commit(lambda: fired.append("발송"))
        raise RuntimeError("실패")
    assert fired == []
