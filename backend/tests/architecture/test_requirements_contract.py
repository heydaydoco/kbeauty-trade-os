"""K. 보안·품질 — 템플릿 확정은 사람 1클릭+멱등 키 하나뿐이다 (§15 L2 / ADR-0033).

test_no_auto_confirm_code_path_exists(§12.2 임포트 선례)의 템플릿 확장 —
S2-1 판정 핵심 계약 "자동 확정 경로 부재=아키텍처 테스트 고정"의 그 테스트다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.modules.requirements import service

pytestmark = pytest.mark.group_k

_MODULE_ROOT = Path(__file__).resolve().parents[2] / "app" / "modules" / "requirements"


def test_no_auto_confirm_code_path_exists() -> None:
    """requirements 모듈에 자동 확정 흔적이 없다 — 확정은 사람 1클릭뿐 (§15 L2)

    ADR-09의 조건부 자동 확정은 "인테이크 전표까지"이고 요건 템플릿(마스터
    데이터)은 범위 밖이다. 함수 이름이든 호출이든 auto_confirm이 모듈에
    등장하면 그 자체가 위반이다(임포트 모듈 선례와 같은 계약).
    """
    offenders = [
        path.name
        for path in _MODULE_ROOT.glob("*.py")
        if "auto_confirm" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"requirements 모듈에 자동 확정 흔적이 있다: {offenders}"


def test_the_confirm_service_cannot_be_called_without_an_idempotency_key() -> None:
    """확정 서비스의 idempotency_key는 기본값 없는 키워드 전용 인자다 (§17.4)

    기본값이 생기는 순간 "키 없이 확정"이라는 호출 형태가 가능해진다 — 사람
    1클릭(키 발급 주체=화면)이 아닌 경로가 코드상 성립하지 않게 시그니처로
    고정한다(GC-H2의 "승인 토큰 없이 호출 불가한 시그니처" 같은 원리).
    """
    parameter = inspect.signature(service.confirm_template).parameters["idempotency_key"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_the_scan_is_not_vacuous() -> None:
    """모듈 파일이 실제로 잡혀 있다 — 경로가 어긋나면 위 검사가 공회전한다"""
    names = {path.name for path in _MODULE_ROOT.glob("*.py")}
    assert {"service.py", "models.py"} <= names
