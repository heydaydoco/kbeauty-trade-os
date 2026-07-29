"""K. 보안·품질 — 비밀이 새는 구조를 기계가 막는다 (DESIGN.md §18.1 / §20 G / ADR-0018).

사람이 리뷰에서 기억해야 하는 규칙은 반드시 샌다. 'HS 자동판정 부재'를 CI가
지키는 것과 같은 방식으로, **원가가 새는 두 구조**를 여기서 고정한다.

  ① 민감·금액 계열 컬럼이 유니크 키에 들어가면 PostgreSQL이 위반 시
     `DETAIL: Key (…)=(…)`에 그 값을 실어 보낸다. 그 경로는 마스킹으로 지우지
     않기로 했으므로(진단 가치), 애초에 그런 키를 만들지 못하게 한다.
  ② 엔진이 build_engine 밖에서 만들어지면 hide_parameters가 빠진 엔진이 생기고,
     그 엔진의 예외에는 바인드 값이 그대로 실린다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import Index, UniqueConstraint

from app.core.db.session import build_engine, engine
from app.core.logging.redaction import is_sensitive_key
from app.core.money import is_money_column_name
from app.registry import Base

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"

#: 엔진을 만들어도 되는 유일한 파일.
ENGINE_FACTORY = "app/core/db/session.py"

#: 유니크 키에 민감 컬럼이 들어가는 것을 **허용한 예외**와 그 사유.
#:
#: ★ 예외를 목록으로 두는 이유: 규칙을 느슨하게 하면 다음 위반이 조용히 통과하고,
#:   규칙을 어기고 통과시키면 그 판단이 코드 어디에도 안 남는다. 여기 적힌 것만
#:   통과하며, 새 항목을 넣으려면 사유를 함께 써야 한다.
ALLOWED_SENSITIVE_UNIQUE_KEYS: dict[tuple[str, str], str] = {
    ("user_sessions", "token_hash"): (
        "세션 조회의 키 그 자체다(ADR-0013). 유니크를 빼면 두 세션이 같은 토큰을 "
        "가리킬 수 있어 인증이 무너진다 — 제약을 없애는 쪽이 훨씬 위험하다. "
        "위반하려면 sha256 충돌이 필요해 실무상 발생하지 않고, DETAIL에 실리는 "
        "값도 토큰이 아니라 그 해시다."
    ),
}


# ── ① 유니크 키에 민감·금액 컬럼 금지 (ADR-0018 ㉠) ────────────────────────


def _must_not_be_unique(column: str) -> bool:
    """유니크 키에 들어가면 안 되는 컬럼인가.

    두 개념의 **합집합**이고, 각 개념은 각자 한 곳에서만 정의된다(이중 관리 아님).

      · redaction.is_sensitive_key — "가려야 하는가"(원가·비밀 계열)
      · money.is_money_column_name — "돈인가"(금액 계열)

    ★ 둘을 하나로 합치면 안 된다. 판가는 **가리지 않지만 돈이고**, 실측으로
      두 번 유출된 당사자 컬럼이 바로 그 `amount`였다. 이름 기준 민감 목록만
      쓰면 ㉠이 막으려던 바로 그 경우가 판정 밖으로 빠진다.
    """
    return is_sensitive_key(column) or is_money_column_name(column)


def _unique_column_names() -> list[tuple[str, str, str]]:
    """(테이블, 키 이름, 컬럼) 전건 — 유니크 제약과 유니크 인덱스 양쪽."""
    found: list[tuple[str, str, str]] = []
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                found.extend(
                    (table.name, str(constraint.name), column.name) for column in constraint.columns
                )
        for index in table.indexes:
            if isinstance(index, Index) and index.unique:
                found.extend((table.name, str(index.name), column.name) for column in index.columns)
    return found


def test_the_unique_key_scan_is_not_vacuous() -> None:
    """검사할 유니크 키가 실제로 존재한다 (빈 목록으로 초록을 사지 않는다)"""
    assert len(_unique_column_names()) >= 10


def test_no_unique_key_contains_a_cost_column() -> None:
    """유니크 키에 민감·금액 계열 컬럼이 들어가지 않는다 (ADR-0018 ㉠)

    유니크 위반 시 PostgreSQL은 `DETAIL: Key (…)=(…)`에 그 값을 실어 보낸다.
    그 경로는 진단 가치 때문에 마스킹하지 않기로 했으므로(㉠), 애초에 그런
    키를 만들지 못하게 한다.

    판정은 _must_not_be_unique — 민감 이름(마스킹 정의)과 금액 이름(money 정의)의
    합집합이고, 각 개념은 각자 한 곳에서만 정의된다.

    필요한 설계가 생기면 허용 목록에 **사유와 함께** 등록하고 ADR-0018 ㉠을
    갱신할 것 — 그게 '재검토 트리거'의 기계적 형태다.
    """
    offenders = [
        f"{table}.{column} (in {key})"
        for table, key, column in _unique_column_names()
        if _must_not_be_unique(column) and (table, column) not in ALLOWED_SENSITIVE_UNIQUE_KEYS
    ]

    assert not offenders, (
        "민감 컬럼이 유니크 키에 있습니다: "
        + ", ".join(offenders)
        + " — 위반 시 PostgreSQL이 DETAIL에 그 값을 실어 보냅니다(ADR-0018 ㉠). "
        "구조상 불가피하면 ALLOWED_SENSITIVE_UNIQUE_KEYS에 **사유와 함께** 등록하세요."
    )


def test_the_allowlist_has_no_dead_entries() -> None:
    """허용 목록에 실재하지 않는 키가 남아 있지 않다

    사라진 예외가 목록에 남으면, 나중에 같은 이름의 컬럼이 다른 의미로 유니크
    키에 들어갔을 때 조용히 통과한다.
    """
    live = {(table, column) for table, _key, column in _unique_column_names()}
    stale = sorted(set(ALLOWED_SENSITIVE_UNIQUE_KEYS) - live)

    assert not stale, f"실재하지 않는 예외가 허용 목록에 있다: {stale}"


def test_the_cost_column_detector_actually_detects() -> None:
    """검사기가 민감·금액 이름을 실제로 잡는다 (자기검사)"""
    # 가려야 하는 것들(마스킹 쪽 정의)
    assert _must_not_be_unique("purchase_amount")
    assert _must_not_be_unique("unit_cost")
    # ★ 돈이지만 가리지는 않는 것들 — 실측 유출의 당사자가 여기 있다.
    assert _must_not_be_unique("amount")
    assert _must_not_be_unique("sales_price")
    assert not is_sensitive_key("amount")  # 마스킹 대상은 아니다
    # 관계없는 이름은 걸리지 않는다
    assert not _must_not_be_unique("sku_code")
    assert not _must_not_be_unique("price_type")


# ── ② 엔진은 한 곳에서만 만든다 (ADR-0018 ③) ──────────────────────────────


def test_create_engine_is_called_only_in_the_engine_factory() -> None:
    """create_engine 호출은 build_engine이 있는 파일에만 있다

    다른 곳에서 엔진을 만들면 hide_parameters가 빠진 엔진이 생기고, 그 엔진의
    예외에는 바인드 값(원가·비밀번호 해시)이 그대로 실린다. "새 엔진 생성"이
    이 규칙의 재발 지점이다.
    """
    scanned = 0
    offenders: list[str] = []
    for path in sorted((*APP_ROOT.rglob("*.py"), *(BACKEND_ROOT / "migrations").rglob("*.py"))):
        scanned += 1
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == ENGINE_FACTORY:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "create_engine":
                    offenders.append(f"{relative}:{node.lineno}")

    assert scanned > 10, "스캔 경로가 잘못됐습니다"
    assert not offenders, (
        "build_engine 밖에서 엔진을 만듭니다: "
        + ", ".join(offenders)
        + f" — 엔진 생성은 {ENGINE_FACTORY}의 build_engine()만 담당합니다."
    )


def test_hide_parameters_cannot_be_turned_off_by_a_caller() -> None:
    """호출자가 hide_parameters를 끌 수 없다 (환경 분기·토글 금지 — §18.1)"""
    assert engine.hide_parameters is True

    forced = build_engine(str(engine.url), hide_parameters=False)
    try:
        assert forced.hide_parameters is True
    finally:
        forced.dispose()
