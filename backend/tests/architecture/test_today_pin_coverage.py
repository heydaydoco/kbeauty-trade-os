"""K. 보안·품질 — KST '오늘' 고정 지점 커버리지(S3-3 PR-1b · R-37 · sC C13 ③ · CK-15).

`pin_today_kst`는 import 지점마다 `today_kst`를 바꾼다. 커버 대상 패키지(`TODAY_PINNED_PACKAGES`)의 모듈이 `today_kst`를 이름으로 새로
가져왔는데 `TODAY_IMPORT_POINTS`에 없으면 그 모듈만 실제 시계를 읽어 **CI가 KST 23:55~00:04에만 실패**한다(PR #50 실측). 이 시험은
그 누락을 자정 운 없이 즉시 실패로 바꾼다. 종전 `test_milestone_contract`의 마일스톤 전용 검사(5곳)를 일반화해 옮겼다.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from types import ModuleType

import pytest

from app.core import time as core_time
from tests.support.astscan import app_sources, parse_source
from tests.support.kst import TODAY_IMPORT_POINTS, TODAY_PINNED_PACKAGES, pin_today_kst

pytestmark = pytest.mark.group_k

_ORIGIN = "app.core.time"


def _module_name(rel: str) -> str:
    return "app." + rel.removesuffix(".py").replace("/", ".")


def _is_covered(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 3 and parts[0] == "modules" and parts[1] in TODAY_PINNED_PACKAGES


def _today_bindings(tree: ast.Module) -> tuple[bool, list[str]]:
    """(최상위에서 `today_kst`를 그 이름으로 가져오는가, 문제 목록[별칭 임포트]).

    함수 안 임포트는 호출 때 원본 속성을 읽으므로 원본 고정이 덮는다 — 최상위 바인딩만 지점 목록 대상이다. 별칭(`as tk`)은
    `today_kst` 속성 패치가 닿지 않으므로 어디서든 금지한다.
    """
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _ORIGIN:
            for alias in node.names:
                if alias.name == "today_kst" and alias.asname not in (None, "today_kst"):
                    problems.append(f"today_kst를 별칭 {alias.asname!r}로 가져온다(고정 불가)")
    top_level = any(
        isinstance(node, ast.ImportFrom)
        and node.module == _ORIGIN
        and any(alias.name == "today_kst" for alias in node.names)
        for node in tree.body
    )
    return top_level, problems


def unpinned_today_readers(sources: dict[str, ast.Module], pinned: set[str]) -> list[str]:
    """커버 대상 패키지에서 고정 목록에 빠진 최상위 이름 임포트 모듈·별칭 임포트 문제(비면 통과)."""
    problems: list[str] = []
    for rel, tree in sorted(sources.items()):
        if not _is_covered(rel):
            continue
        binds, alias_problems = _today_bindings(tree)
        problems.extend(f"{rel}: {problem}" for problem in alias_problems)
        if binds and _module_name(rel) not in pinned:
            problems.append(f"{rel}: today_kst를 이름으로 가져오는데 TODAY_IMPORT_POINTS에 없다")
    return problems


def _pinned_names() -> set[str]:
    return {module.__name__ for module in TODAY_IMPORT_POINTS}


def test_every_covered_today_import_point_is_pinned() -> None:
    """CK-15 — 커버 대상 패키지에서 `today_kst`를 이름으로 가져오는 모듈 ⊆ `TODAY_IMPORT_POINTS`(별칭 임포트 0)"""
    sources = app_sources()
    readers = {
        _module_name(rel)
        for rel, tree in sources.items()
        if _is_covered(rel) and _today_bindings(tree)[0]
    }
    # 공회전 방지 — trade_chain 11곳 + payments.service + trade_docs.validation(2026-10-05 실측 13)
    assert len(readers) >= 13, sorted(readers)
    assert unpinned_today_readers(sources, _pinned_names()) == []


def test_the_pin_list_has_no_stale_or_duplicate_entries() -> None:
    """목록의 앱 모듈은 전부 실제로 `today_kst` 속성을 쥐고(낡은 지점 0 — 패치가 헛돌지 않는다) 중복이 없다"""
    names = [module.__name__ for module in TODAY_IMPORT_POINTS]
    assert len(names) == len(set(names)), names
    for module in TODAY_IMPORT_POINTS:
        assert hasattr(module, "today_kst"), module.__name__
    assert len(TODAY_IMPORT_POINTS) <= 30, (
        "부채 C-D4 트리거 — 모듈 속성 접근 전환(패치 1곳)을 검토한다"
    )


def test_the_coverage_scan_catches_a_missing_point() -> None:
    """자기검사(CK-15 변이) — 목록에 없는 커버 대상 모듈·별칭 임포트를 실제로 잡고, 대상 밖·함수 안 임포트·목록 등재에는 조용하다"""
    reader = parse_source("from app.core.time import today_kst, utcnow\nx = today_kst()\n")
    nested = parse_source(
        "def f():\n    from app.core.time import today_kst\n    return today_kst()\n"
    )
    aliased = parse_source(
        "def f():\n    from app.core.time import today_kst as tk\n    return tk()\n"
    )
    sources = {
        "modules/receivables/aging.py": reader,  # S3-3 신규 패키지 — 등재 안 됨 → 지목
        "modules/lc_terms/flow.py": reader,  # 등재됨 → 조용
        "modules/catalog/service.py": reader,  # 커버 대상 밖 → 조용
        "modules/trade_docs/verify.py": nested,  # 함수 안 임포트 → 원본 고정이 덮음 → 조용
        "modules/payments/odd.py": aliased,  # 별칭 → 지목
    }
    problems = unpinned_today_readers(sources, {"app.modules.lc_terms.flow"})
    assert len(problems) == 2, problems
    assert problems[0].startswith("modules/payments/odd.py") and "별칭" in problems[0]
    assert problems[1].startswith("modules/receivables/aging.py")


def test_the_pin_holds_one_day_even_when_the_clock_crosses_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """고정 뒤 시계(`app.core.time.utcnow`)를 하루 밀어도 원본·전 지점·함수 안 임포트·extra가 읽는 '오늘'은 전부 고정한 날이다
    (원본 고정이 빠지면 원본·함수 안 임포트가 밀린 날을 읽어 실패 — 자정 경계 재현)"""
    extra = ModuleType("extra_probe")
    extra.today_kst = lambda: date(1999, 1, 1)  # type: ignore[attr-defined]
    base = pin_today_kst(monkeypatch, extra)
    shifted = core_time.utcnow() + timedelta(days=1)
    monkeypatch.setattr(core_time, "utcnow", lambda: shifted)
    seen = {module.today_kst() for module in (core_time, *TODAY_IMPORT_POINTS, extra)}
    assert seen == {base}
    assert _function_local_today() == base


def _function_local_today() -> date:
    # 함수 안 임포트 경로 그 자체가 시험 대상이다(최상위로 올리지 않는다).
    from app.core.time import today_kst

    return today_kst()
