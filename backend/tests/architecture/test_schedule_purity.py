"""K. 기일 산식·휴일 판정 모듈의 순수성 (S3-2 PR-2a / design-B §B10·B20 "schedule.py·holidays/calc.py가 순수한지").

화면(선적 상세 조립)과 기일 스캔이 **같은 정의**를 쓰려면 산식이 DB·세션·시계에 의존하지 않아야 한다 — "오늘"을 안에서 읽으면
스캔과 화면이 서로 다른 날짜로 판정하고, GC 경계를 단위 시험으로 고정할 수 없다. 그래서 임포트를 허용 목록으로 묶고
시계 함수·세션 이름의 언급을 0으로 고정한다(자기검사 포함 — 스캔이 공회전하지 않음을 위반 코퍼스로 확인).
"""

from __future__ import annotations

import ast

import pytest

from tests.support.astscan import app_sources, called_names, parse_source, referenced_names

pytestmark = pytest.mark.group_k

PURE_MODULES = ("modules/trade_docs/schedule.py", "modules/holidays/calc.py")

#: 순수 모듈이 임포트해도 되는 최상위 모듈(표준 라이브러리 일부 + 커널 상수 + 시각 상수).
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "re",
    "typing",
    "zoneinfo",
    "app.core.time",  # KST 상수만(아래 금지 이름으로 시계 함수 차단)
    "app.modules.trade_docs.constants",  # 결제유형·앵커 열거(L0 — 순수 상수)
}
#: 시계·DB 의존을 뜻하는 이름 — 언급 자체를 금지한다(별칭 임포트 우회까지 잡는다).
FORBIDDEN_NAMES = {"today_kst", "utcnow", "Session", "unit_of_work"}
#: 시계를 읽는 호출(`datetime.now()`·`date.today()`)과 DB 실행 — 인자 이름 `today`는 허용하되 호출은 금지한다.
FORBIDDEN_CALLS = {"now", "today", "utcnow", "today_kst", "execute"}


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _violations(rel: str, tree: ast.Module) -> list[str]:
    out = [f"{rel}: 허용 밖 임포트 {name}" for name in sorted(_imports(tree) - ALLOWED_IMPORTS)]
    out += [f"{rel}: 금지 이름 {name}" for name in sorted(referenced_names(tree) & FORBIDDEN_NAMES)]
    out += [f"{rel}: 금지 호출 {name}" for name in sorted(called_names(tree) & FORBIDDEN_CALLS)]
    return out


@pytest.mark.parametrize("rel", PURE_MODULES)
def test_schedule_and_holiday_calc_are_pure(rel: str) -> None:
    """산식·휴일 판정 모듈은 DB·세션·시계 무의존 — 허용 임포트만·시계/세션 이름 언급 0"""
    sources = app_sources()
    assert rel in sources, rel
    assert _violations(rel, sources[rel]) == []


def test_the_holiday_calc_knows_no_trade_domain() -> None:
    """휴일 판정은 전표 도메인을 모른다(S3_PLATFORM) — 커널 상수조차 임포트하지 않는다"""
    tree = app_sources()["modules/holidays/calc.py"]
    assert not any(name.startswith("app.") for name in _imports(tree))


def test_the_purity_scan_flags_synthetic_violations() -> None:
    """자기검사 — DB 세션·today_kst·별칭 utcnow·sqlalchemy 임포트를 넣은 가짜 소스는 전부 잡힌다(공회전 방지)"""
    fake = parse_source(
        "from sqlalchemy.orm import Session\n"
        "from app.core.time import today_kst\n"
        "from app.core import time as t\n"
        "def f():\n    return t.utcnow()\n"
        "def g():\n    import datetime as d\n    return d.date.today()\n"
    )
    found = _violations("fake.py", fake)
    assert any("sqlalchemy.orm" in v for v in found)
    assert any("app.core" in v and "임포트" in v for v in found)
    assert {"today_kst", "utcnow", "Session", "today"} <= {v.rsplit(" ", 1)[-1] for v in found}
    clean = parse_source("from datetime import date\nfrom app.core.time import KST\n")
    assert _violations("clean.py", clean) == []
