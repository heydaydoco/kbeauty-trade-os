"""시각 규약 — UTC 저장, KST 표시 (DESIGN.md §2 ADR-02, §22 렌즈 6).

이 모듈이 시각을 만드는 유일한 통로다. `datetime.now()`를 직접 부르면
개발 PC(KST)에서는 조용히 로컬 시각이 들어가고, 저장된 뒤에는 그것이
UTC였는지 KST였는지 되돌릴 방법이 없다. 아키텍처 테스트가 app/** 안의
직접 호출을 0건으로 강제한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

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


def today_kst() -> date:
    """업무 기준 '오늘'(한국 날짜).

    ★ UTC 날짜로 "오늘"을 판단하면 안 된다. KST는 UTC+9라 한국의 00:00~09:00
      사이에는 UTC 날짜가 아직 어제다 — 그 시간대에 사용자가 오늘 날짜를 적으면
      "미래 날짜"로 거절당한다. 확인일·발효일처럼 사람이 KST로 적는 **업무
      날짜**는 이 함수로 비교한다(저장은 여전히 날짜 그대로다).
    """
    return utcnow().astimezone(KST).date()
