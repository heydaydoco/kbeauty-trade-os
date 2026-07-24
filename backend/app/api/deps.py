"""라우터가 쓰는 의존성.

★ 세션(Session)을 라우터에 직접 넘기지 않는다.
  라우터가 세션을 손에 쥐면 거기서 add/commit을 하게 되고, 그 순간
  §17.1의 "업무 동작 하나 = 트랜잭션 하나"가 화면 단위로 쪼개진다.
  쓰기는 unit_of_work()를 통해서만, 읽기는 읽기 전용 세션으로만 한다.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core.db.session import SessionFactory


def get_read_session() -> Iterator[Session]:
    """조회 전용 세션. 커밋하지 않는다(GuardedSession이 막는다)."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
