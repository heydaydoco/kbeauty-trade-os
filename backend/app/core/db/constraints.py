"""제약·인덱스 생성 헬퍼 — 이름과 조건을 한 곳에서 만든다.

손으로 쓰면 세션마다 이름·조건이 갈리고, 갈린 순간 alembic이 그 제약을
지목하지 못한다. 특히 §17.4의 멱등 UNIQUE는 `UniqueConstraint`로는 표현할 수
없어(부분 조건이 필요) 반드시 부분 인덱스여야 하는데, raw SQL로 쓰면
autogenerate가 매번 drop/create로 오탐하거나 아예 놓쳐서 멱등 키가 조용히 사라진다.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import CheckConstraint, Index, text

# PostgreSQL 식별자 상한. 넘으면 조용히 잘려서 이후 alembic이 이름으로
# 지목하지 못한다 — 잘리기 전에 실패시킨다.
MAX_IDENTIFIER_LENGTH = 63


def _guard_name(name: str) -> str:
    if len(name) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(
            f"제약 이름이 PostgreSQL 상한({MAX_IDENTIFIER_LENGTH}자)을 넘습니다: "
            f"{name!r} ({len(name)}자). 테이블명이나 컬럼 조합을 줄이세요 — "
            "잘린 이름은 마이그레이션이 해당 제약을 지목하지 못하게 만듭니다."
        )
    return name


def unique_active(table_name: str, *columns: str) -> Index:
    """soft delete와 공존하는 멱등 UNIQUE (§17.4).

    `WHERE deleted_at IS NULL` 부분 유니크 인덱스를 만든다. 삭제된 행이 같은
    키의 재유입을 영구 차단하지 않되, 재유입은 부활이 아니라 신규다.

        __table_args__ = (unique_active("skus", "sku_code"),)
    """
    name = _guard_name(f"uq_{table_name}_{'_'.join(columns)}_active")
    return Index(
        name,
        *columns,
        unique=True,
        postgresql_where=text("deleted_at IS NULL"),
    )


def value_in(column: str, values: Iterable[str], *, name: str | None = None) -> CheckConstraint:
    """상태·유형 값을 CHECK로 제한한다 (§17.5 "가능한 불변식은 CHECK").

    네이티브 PG ENUM을 쓰지 않는 이유: 값 삭제·순서 변경이 사실상 불가능하고
    alembic autogenerate가 특히 취약하다. ADR-11이 상태 머신은 코드 고정,
    값 추가는 마이그레이션으로 일어난다고 규정하므로 VARCHAR + CHECK가 맞다.
    """
    allowed = sorted(values)
    if not allowed:
        raise ValueError(f"{column}: 허용 값 목록이 비어 있습니다.")
    joined = ", ".join(f"'{v}'" for v in allowed)
    constraint_name = name or f"{column}_valid"
    _guard_name(f"ck_x_{constraint_name}")  # 테이블명은 규약이 붙이므로 여유를 둔다
    return CheckConstraint(f"{column} IN ({joined})", name=constraint_name)


def positive(column: str, *, name: str | None = None) -> CheckConstraint:
    """수량·금액이 0보다 커야 하는 제약."""
    return CheckConstraint(f"{column} > 0", name=name or f"{column}_positive")


def nonzero(column: str, *, name: str | None = None) -> CheckConstraint:
    """0이면 안 되는 제약 (예: stock_movements.quantity — §17.5)."""
    return CheckConstraint(f"{column} <> 0", name=name or f"{column}_nonzero")
