"""ORM 선언 기반 + 제약 이름 규약.

★ naming_convention은 지금 정하지 않으면 회수할 수 없다.
  이름 없이 만들어진 제약은 PostgreSQL이 임의 이름을 붙이고, 그러면 이후
  alembic이 "그 제약"을 이름으로 지목하지 못해 변경·삭제·다운그레이드가 막힌다.
  테이블이 100개가 된 뒤에는 되돌릴 방법이 없다. (ADR 대상)
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# alembic이 생성하는 모든 제약·인덱스의 이름 규칙.
#   ix_  인덱스 / uq_  유니크 / ck_  체크 / fk_  외래키 / pk_  기본키
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """모든 ORM 모델의 부모."""

    metadata = metadata
