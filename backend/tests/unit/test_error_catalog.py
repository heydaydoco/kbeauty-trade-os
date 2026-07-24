"""K. 보안·품질 — 에러 코드·문구 계약 (DESIGN.md §18.4)."""

from __future__ import annotations

import re

import pytest

from app.core.errors.catalog import ERROR_CATALOG, spec_for
from app.core.errors.codes import ErrorCode

pytestmark = pytest.mark.group_k

# <도메인>.<대상>.<사유> — 각 세그먼트는 대문자로 시작하는 스네이크
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*(\.[A-Z][A-Z0-9]*(_[A-Z0-9]+)*){2}$")
ACTION_HINTS = ("주세요", "하십시오", "요청", "문의", "확인")


@pytest.mark.parametrize("code", list(ErrorCode))
def test_code_format(code: ErrorCode) -> None:
    """모든 에러 코드가 3세그먼트 형식을 지킨다"""
    assert CODE_PATTERN.match(str(code)), f"{code} 형식 위반"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_code_has_catalog_entry(code: ErrorCode) -> None:
    """모든 코드에 한국어 문구와 상태 코드가 있다"""
    spec = spec_for(code)
    assert 400 <= spec.status_code <= 599
    assert spec.message_ko


@pytest.mark.parametrize("code", list(ErrorCode))
def test_message_has_cause_and_action(code: ErrorCode) -> None:
    """문구가 원인과 조치를 함께 담는다 (§18.4)"""
    message = spec_for(code).message_ko
    assert len(message) >= 10
    assert any(hint in message for hint in ACTION_HINTS), f"{code}에 조치 안내가 없다: {message!r}"


def test_version_conflict_message_is_fixed() -> None:
    """§17.2가 지정한 낙관적 잠금 문구가 그대로 있다"""
    message = spec_for(ErrorCode.CONCURRENCY_VERSION_CONFLICT).message_ko
    assert message.startswith("다른 사용자가 먼저 수정했습니다")


def test_catalog_covers_exactly_the_enum() -> None:
    """카탈로그에 코드 누락도, 유령 코드도 없다"""
    assert set(ERROR_CATALOG) == set(ErrorCode)
