"""테스트 전용 임시 테이블.

S0-1에는 업무 테이블이 아직 하나도 없다. 그런데 트랜잭션 롤백·동시 커밋 같은
안전 계약(§17.1·§17.2)은 "여러 커넥션이 같은 테이블을 본다"는 조건이 있어야
검증된다 — TEMP 테이블은 커넥션마다 따로라 쓸 수 없다.

그래서 소유자 계정으로 실제 테이블을 만들고 테스트가 끝나면 지운다.

주의: 병렬 실행(pytest-xdist)을 도입하면 워커별로 이름이나 스키마를 나눠야 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.core.db.session import owner_engine


@contextmanager
def scratch_table(name: str = "scratch_probe") -> Iterator[str]:
    """소유자 계정으로 임시 테이블을 만들고, 앱 계정에 DML 권한을 준다."""
    with owner_engine.begin() as connection:
        connection.execute(text(f'DROP TABLE IF EXISTS public."{name}"'))
        connection.execute(
            text(f'CREATE TABLE public."{name}" (id BIGSERIAL PRIMARY KEY, label TEXT NOT NULL)')
        )
        # ALTER DEFAULT PRIVILEGES가 실제로 걸려 있으면 이 GRANT 없이도 되지만,
        # 테스트가 그 설정에 의존하지 않도록 명시적으로 부여한다.
        connection.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{name}" TO kbos_app')
        )
    try:
        yield name
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS public."{name}"'))


def count_rows(table: str) -> int:
    """소유자 계정의 **별도 커넥션**으로 센다 — 커밋된 것만 보인다."""
    with owner_engine.connect() as connection:
        return int(connection.execute(text(f'SELECT count(*) FROM public."{table}"')).scalar_one())
