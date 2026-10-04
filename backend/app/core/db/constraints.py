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

# '보이는 글자 1개 이상' CHECK용 PG ARE 괄호식 내용 — `btrim`은 U+0020만 잘라 U+3000·U+00A0·U+2003만의 값이 통과한다(S3-2 PR-4a
# 적대 검토 반영 ⑥). 쓰는 법: `col ~ '[^<집합>]'`(집합 밖 글자가 하나라도 있다). 글자는 `\\uXXXX` 이스케이프로 만든다(소스·DB 정의문에
# 보이지 않는 글자를 직접 넣지 않는다). 마이그레이션 M15는 같은 값을 스스로 만든다(앱 상수 비임포트 — 시험이 대사).
#: 유니코드 공백 — 파이썬 `str.isspace`·`str.strip`이 지우는 집합(서비스 strip을 통과한 기존 행과 호환). 범위는 (시작, 끝).
SPACE_CODE_POINTS: tuple[int | tuple[int, int], ...] = (
    0x0085,
    0x00A0,
    0x1680,
    (0x2000, 0x200A),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)
#: 보이지 않는 서식·채움 글자 — 몽골 모음 구분·ZWSP~ZWJ·WJ·BOM·한글 채움 4종.
INVISIBLE_CODE_POINTS: tuple[int | tuple[int, int], ...] = (
    0x180E,
    (0x200B, 0x200D),
    0x2060,
    0xFEFF,
    0x115F,
    0x1160,
    0x3164,
    0xFFA0,
)


def pg_char_class(points: Iterable[int | tuple[int, int]]) -> str:
    """PG ARE 괄호식 내용 — 글자는 `\\uXXXX`, 범위는 `\\uXXXX-\\uYYYY`(ARE는 괄호식 안의 글자 이스케이프를 받는다)."""
    parts: list[str] = []
    for point in points:
        if isinstance(point, tuple):
            parts.append(f"\\u{point[0]:04x}-\\u{point[1]:04x}")
        else:
            parts.append(f"\\u{point:04x}")
    return "".join(parts)


#: 유니코드 공백(`\\s` = ASCII 공백 + 위 집합).
SPACE_CHAR_CLASS = "\\s" + pg_char_class(SPACE_CODE_POINTS)
#: 공백 + 보이지 않는 글자 — 신설 표의 사유·메모·선적 통보 요지.
BLANK_CHAR_CLASS = SPACE_CHAR_CLASS + pg_char_class(INVISIBLE_CODE_POINTS)


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
    """수량·금액이 0보다 커야 하는 제약.

    ★ NULL 허용 컬럼에도 그대로 쓴다. SQL의 CHECK는 식이 FALSE일 때만 거부하고
      NULL(=미지)은 통과시킨다 — "값이 있으면 0보다 커야 한다"가 정확히 이 의미다.
    """
    return CheckConstraint(f"{column} > 0", name=name or f"{column}_positive")


def in_range(
    column: str, low: str | int, high: str | int, *, name: str | None = None
) -> CheckConstraint:
    """값이 있으면 [low, high] 안이어야 하는 제약 (예: 알코올 함량 0~100%).

    positive()와 같은 이유로 NULL은 통과한다.
    """
    return CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=name or f"{column}_range")


def nonzero(column: str, *, name: str | None = None) -> CheckConstraint:
    """0이면 안 되는 제약 (예: stock_movements.quantity — §17.5)."""
    return CheckConstraint(f"{column} <> 0", name=name or f"{column}_nonzero")
