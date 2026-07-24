"""Unit of Work — 업무 동작 하나 = 트랜잭션 하나 (DESIGN.md §17.1).

규칙 세 가지가 여기 구현돼 있다.

1. **전부 커밋 아니면 전부 롤백.** 헤더·라인·원장·이력·이벤트가 한 트랜잭션이다.
2. **중첩은 새 트랜잭션이 아니라 합류.** 서비스가 서비스를 부를 때 안쪽이
   따로 커밋해 버리면 "업무 동작 하나"가 쪼개진다.
3. **외부 호출은 커밋 후에.** 트랜잭션 안에서 메일·슬랙·AI를 부르면
   롤백된 일을 대외에 통보하게 된다. 알림·연동은 같은 트랜잭션으로
   events(아웃박스)에 적고, 커밋 뒤 디스패처가 보낸다.
   `add_after_commit()`이 그 자리다 — events 테이블은 S0-2에서 생긴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.core.db.session import GuardedSession, SessionFactory

#: 현재 열려 있는 UnitOfWork. 중첩 호출이 여기에 합류한다.
_current_uow: ContextVar[UnitOfWork | None] = ContextVar("current_uow", default=None)


class UnitOfWork:
    """한 트랜잭션의 수명 동안 유지되는 작업 단위."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._after_commit: list[Callable[[], None]] = []

    def add_after_commit(self, hook: Callable[[], None]) -> None:
        """커밋이 성공한 뒤에 실행할 작업을 예약한다(아웃박스 디스패치 등).

        트랜잭션 안에서 외부를 호출하지 않기 위한 통로다 (§17.1).
        """
        self._after_commit.append(hook)

    def _run_after_commit(self) -> None:
        for hook in self._after_commit:
            hook()


@contextmanager
def unit_of_work() -> Iterator[UnitOfWork]:
    """업무 동작 하나를 감싸는 트랜잭션 경계.

        with unit_of_work() as uow:
            uow.session.add(...)
        # 여기서 커밋됨 (예외가 나면 전체 롤백)
    """
    outer = _current_uow.get()
    if outer is not None:
        # 이미 열려 있는 트랜잭션에 합류한다 — 안쪽이 따로 커밋하지 않는다.
        yield outer
        return

    session = SessionFactory()
    uow = UnitOfWork(session)
    token = _current_uow.set(uow)
    committed = False
    try:
        yield uow
        _commit(session)
        committed = True
    except BaseException:
        _rollback(session)
        raise
    finally:
        _current_uow.reset(token)
        session.close()

    if committed:
        # 트랜잭션이 닫힌 뒤에 실행한다 — 여기서만 외부 호출이 허용된다.
        uow._run_after_commit()


def _commit(session: Session) -> None:
    with _txn_control(session):
        session.commit()


def _rollback(session: Session) -> None:
    with _txn_control(session):
        session.rollback()


@contextmanager
def _txn_control(session: Session) -> Iterator[None]:
    """GuardedSession의 커밋 잠금을 잠깐 푼다 — UnitOfWork만 쓸 수 있다."""
    if not isinstance(session, GuardedSession):
        yield
        return
    session._txn_control_allowed = True
    try:
        yield
    finally:
        session._txn_control_allowed = False
