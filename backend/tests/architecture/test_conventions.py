"""K. 보안·품질 — 아키텍처 규약을 코드가 강제한다 (DESIGN.md §17.1·§17.2·ADR-02).

사람이 리뷰에서 기억해야 하는 규칙은 반드시 샌다. AST로 스캔해서 규칙 위반을
CI가 잡게 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _python_files(*subdirs: str) -> list[Path]:
    root = APP_ROOT.joinpath(*subdirs) if subdirs else APP_ROOT
    return sorted(root.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _router_files() -> list[Path]:
    """HTTP 표면 전체 — app/api 아래와 각 모듈의 router.py."""
    return _python_files("api") + sorted(APP_ROOT.glob("modules/*/router.py"))


def test_no_naive_datetime_calls() -> None:
    """app 어디에서도 datetime.now()/utcnow()를 직접 부르지 않는다 (app/core/time.py 제외)

    §2 ADR-02 UTC 저장. 직접 호출은 개발 PC(KST)에서 조용히 로컬 시각을 넣는다.
    """
    offenders: list[str] = []
    for path in _python_files():
        if path.name == "time.py" and path.parent.name == "core":
            continue
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"now", "utcnow"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"datetime", "date"}
            ):
                offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{node.lineno}")
    assert not offenders, (
        "datetime을 직접 호출한 곳: " + ", ".join(offenders) + " — app.core.time.utcnow()를 쓰세요"
    )


def test_routers_do_not_control_transactions() -> None:
    """라우터(app/api·modules/*/router.py)가 commit/rollback/begin을 쓰지 않는다

    §17.1 트랜잭션 경계는 서비스 레이어(unit_of_work)만 담당한다.
    """
    forbidden = {"commit", "rollback", "begin", "sessionmaker", "create_engine"}
    offenders: list[str] = []
    for path in _router_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in forbidden:
                    offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{node.lineno} ({name})")
    assert not offenders, "라우터가 트랜잭션을 조작한다: " + ", ".join(offenders)


def test_no_float_columns() -> None:
    """app 어디에도 Float 컬럼 선언이 없다 (§2 ADR-02 금액 규율·GC-G1)"""
    offenders: list[str] = []
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in {"Float", "REAL", "DOUBLE_PRECISION"}:
                    offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{node.lineno}")
    assert not offenders, (
        "Float/실수 컬럼 사용: " + ", ".join(offenders) + " — 금액은 정수 최소단위"
    )


def test_no_direct_external_http_imports() -> None:
    """app에서 외부 HTTP/메일 라이브러리를 직접 임포트하지 않는다 (§17.1)

    트랜잭션 안에서 외부를 부르는 경로가 아예 생기지 않게 한다. 실제 발신은
    S2-3의 아웃박스 디스패처가 전담한다.
    """
    forbidden_modules = {"requests", "smtplib", "urllib.request", "httpx", "httpx2", "aiohttp"}
    # 예외: 헬스체크 미들웨어 자체 검사나 표준 라이브러리 최소 사용이 필요하면
    # 여기에 (파일, 모듈)로 화이트리스트를 추가하고 사유를 남긴다.
    allow = {
        ("middleware/request_context.py", "secrets"),
    }
    offenders: list[str] = []
    for path in _python_files():
        rel = str(path.relative_to(APP_ROOT.parent).as_posix()).removeprefix("app/")
        for node in ast.walk(_parse(path)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name in forbidden_modules and (rel, name) not in allow:
                    offenders.append(f"{rel}:{node.lineno} ({name})")
    assert not offenders, "외부 HTTP/메일 직접 임포트: " + ", ".join(offenders)


def test_every_model_module_is_registered() -> None:
    """app/modules/*/models.py는 전부 app/registry.py에서 임포트된다

    빠뜨리면 autogenerate가 그 테이블을 "없어야 할 테이블"로 보고 DROP TABLE
    마이그레이션을 만든다. 리뷰에서 걸러지길 기대할 종류의 실수가 아니다.
    """
    model_modules = sorted(p.parent.name for p in APP_ROOT.glob("modules/*/models.py"))
    imported = {
        node.module
        for node in ast.walk(_parse(APP_ROOT / "registry.py"))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    missing = [name for name in model_modules if f"app.modules.{name}" not in imported]
    assert not missing, (
        f"registry.py에 임포트되지 않은 모델 모듈: {missing} — "
        "autogenerate가 해당 테이블에 DROP TABLE을 만듭니다"
    )


def test_app_files_are_parseable() -> None:
    """스캔이 공회전이 아니다 — 실제로 파일을 읽었다"""
    assert len(_python_files()) > 10
    assert _router_files(), "라우터 파일을 하나도 찾지 못했습니다 — 스캔 경로 확인"
