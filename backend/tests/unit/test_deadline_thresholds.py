"""C. 인증 — 기일 문턱 순수 함수 (GC-C1 문턱 통과 의미론 / S2-3 PR-2 안건 ②).

★ 문턱은 "정각"이 아니라 "지났다"다 — D-88이면 D-90은 이미 지난 문턱이고 D-30은
  아직이다. 어제 스캔이 죽었어도 오늘 스캔이 D-90을 보내는 근거가 이 함수다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.certifications.models import Certification
from app.modules.certifications.service import overdue_days_of
from app.modules.deadlines.service import (
    DEFAULT_THRESHOLDS,
    _normalize_thresholds,
    days_left,
    passed_thresholds,
)

pytestmark = pytest.mark.group_c


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (365, ()),  # 아직 어느 문턱도 안 지났다
        (180, (180,)),  # 정각
        (179, (180,)),  # 하루 지각
        (88, (180, 90)),  # GC-C1 — D-90 발송 상태, D-30 미발송
        (30, (180, 90, 30)),
        (0, (180, 90, 30)),  # 당일
        (-5, (180, 90, 30)),  # 도과 — 문턱 전부 지났다(도과 알림은 별도)
    ],
)
def test_passed_thresholds(remaining: int, expected: tuple[int, ...]) -> None:
    """지난 문턱 전부, 큰 것부터 — 정각·지각·도과 전 구간"""
    assert passed_thresholds(remaining, DEFAULT_THRESHOLDS) == expected


def test_thresholds_are_independent_of_lead_time() -> None:
    """문턱은 리드타임(스윕의 만료임박)과 별개 축 — 함수가 리드를 받지 않는다"""
    assert passed_thresholds(88, (60,)) == ()
    assert passed_thresholds(88, (100, 60)) == (100,)


def test_days_left_counts_calendar_days() -> None:
    assert days_left(date(2026, 9, 30), date(2026, 7, 4)) == 88  # GC-C1 문면
    assert days_left(date(2026, 7, 4), date(2026, 7, 4)) == 0
    assert days_left(date(2026, 7, 3), date(2026, 7, 4)) == -1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_THRESHOLDS),
        ([], DEFAULT_THRESHOLDS),
        ([60, 14], (60, 14)),
        ([14, 60, 14], (60, 14)),  # 중복 제거·내림차순
        ([0, 30], DEFAULT_THRESHOLDS),  # 0·음수는 문턱이 아니다 — 전체를 기본값으로
        (["30"], DEFAULT_THRESHOLDS),  # 문자열은 받지 않는다
        ([True], DEFAULT_THRESHOLDS),  # bool은 int의 하위형이지만 문턱이 아니다
        ("30,60", DEFAULT_THRESHOLDS),
    ],
)
def test_rule_config_thresholds_are_validated(raw: object, expected: tuple[int, ...]) -> None:
    """규칙 config의 문턱은 양의 정수 목록만 — 흐릿한 값은 기본값으로 돈다(ADR-11)"""
    assert _normalize_thresholds(raw) == expected


@pytest.mark.parametrize(
    ("status", "expires_on", "expected"),
    [
        ("RENEWING", date(2026, 8, 1), 19),  # 갱신중 도과(안건 ⑦) — 상태는 그대로, 계산값만
        ("EXPIRED", date(2026, 8, 1), 19),
        ("APPROVED", date(2026, 8, 20), None),  # 당일은 도과가 아니다
        ("APPROVED", date(2026, 9, 1), None),
        ("APPROVED", None, None),  # 무기한
        ("REJECTED", date(2026, 8, 1), None),  # 종결은 도과가 아니다 — 닫힌 건
        ("SUSPENDED", date(2026, 8, 1), None),
    ],
)
def test_overdue_days_is_a_pure_calculation(
    status: str, expires_on: date | None, expected: int | None
) -> None:
    """도과 계산값(안건 ⑦) — 만료일·상태의 순수 함수, 저장하지 않는다"""
    row = Certification(status=status, expires_on=expires_on)
    assert overdue_days_of(row, base_date=date(2026, 8, 20)) == expected
