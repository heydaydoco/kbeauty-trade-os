"""시각 규약 — UTC 저장, KST 표시 (DESIGN.md §2 ADR-02, §22 렌즈 6).

이 모듈이 시각을 만드는 유일한 통로다. `datetime.now()`를 직접 부르면
개발 PC(KST)에서는 조용히 로컬 시각이 들어가고, 저장된 뒤에는 그것이
UTC였는지 KST였는지 되돌릴 방법이 없다. 아키텍처 테스트가 app/** 안의
직접 호출을 0건으로 강제한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

# 한국 표준시. DB에는 절대 이 시간대로 저장하지 않는다 — 표시 전용.
KST = timezone(timedelta(hours=9), name="KST")


def utcnow() -> datetime:
    """현재 시각(UTC, tz-aware)."""
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    """tz 정보가 없는 시각을 거부한다.

    naive datetime을 그대로 받으면 "어느 시간대인지 모르는 값"이 저장된다.
    """
    if value.tzinfo is None:
        raise ValueError(
            "시간대 정보가 없는 시각(naive datetime)은 사용할 수 없습니다. "
            "app.core.time.utcnow()를 쓰거나 tzinfo를 붙이세요."
        )
    return value.astimezone(UTC)


def to_kst(value: datetime) -> datetime:
    """저장된 UTC 시각을 화면 표시용 KST로 바꾼다."""
    return ensure_aware_utc(value).astimezone(KST)
