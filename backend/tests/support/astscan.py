"""아키텍처 테스트용 AST 스캔 유틸 — 앱 소스 전체를 한 번만 읽어 파일별 트리를 준다.

스캔 테스트는 **공회전하기 쉽다**(대상이 없으면 조용히 초록). 그래서 쓰는 쪽이 (a) 실제로 뭔가 찾았는지 (b) 위반
코퍼스를 넣으면 잡는지를 함께 검사한다 — 이 모듈의 `parse_source`가 그 자기검사용 진입점이다.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@lru_cache(maxsize=1)
def app_sources() -> dict[str, ast.Module]:
    """앱 상대 경로(예: `modules/quotations/service.py`) → AST. `__pycache__`는 제외한다."""
    out: dict[str, ast.Module] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out[path.relative_to(APP_DIR).as_posix()] = ast.parse(path.read_text(encoding="utf-8"))
    return out


def parse_source(source: str) -> ast.Module:
    return ast.parse(source)


def module_of(rel_path: str) -> str | None:
    """`modules/<이름>/…` → `<이름>`, 그 밖은 None."""
    parts = rel_path.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "modules" else None


def imported_modules(tree: ast.Module) -> set[str]:
    """이 트리가 임포트하는 `app.modules.<이름>`의 이름 집합(from/import 양쪽)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            parts = name.split(".")
            if len(parts) >= 3 and parts[0] == "app" and parts[1] == "modules":
                found.add(parts[2])
    return found


def called_names(tree: ast.Module) -> set[str]:
    """호출되는 함수 이름(`f(...)`의 f, `a.b.f(...)`의 f) 집합."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def referenced_names(tree: ast.Module) -> set[str]:
    """이름·속성으로 언급된 모든 식별자(호출·임포트·참조 무관) — 보호 함수의 우회 참조까지 잡는다."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names
