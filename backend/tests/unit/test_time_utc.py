"""J. 안전 계약 — 시각은 UTC·tz-aware (DESIGN.md §2 ADR-02, §22 렌즈 6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.time import KST, ensure_aware_utc, to_kst, utcnow

pytestmark = pytest.mark.group_j


def test_utcnow_is_timezone_aware_utc() -> None:
    """utcnow()는 항상 시간대 정보가 있는 UTC 시각을 준다"""
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_naive_datetime_is_rejected() -> None:
    """시간대 없는 시각은 거부된다 — '언제인지 모르는 값'이 저장되면 되돌릴 수 없다"""
    with pytest.raises(ValueError, match="시간대"):
        ensure_aware_utc(datetime(2026, 7, 24, 12, 0, 0))  # noqa: DTZ001


def test_kst_display_is_utc_plus_9() -> None:
    """KST 표시는 UTC + 9시간이다 (저장은 UTC, 표시만 KST)"""
    midnight_utc = datetime(2026, 7, 24, 0, 0, 0, tzinfo=UTC)
    kst = to_kst(midnight_utc)
    assert kst.tzinfo == KST
    assert (kst.hour, kst.day) == (9, 24)


def test_kst_never_used_for_storage() -> None:
    """to_kst 결과는 UTC로 되돌리면 원본과 같다 (표시 변환이 값을 바꾸지 않는다)"""
    original = utcnow()
    assert to_kst(original).astimezone(UTC) == original
