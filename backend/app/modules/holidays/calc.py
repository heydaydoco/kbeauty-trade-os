"""휴일 판정 — 순수 함수 (S3-2 PR-2a / ADR-0082 / design-B §B10·B12 / design-integrated §9 R-09).

판정은 3값이다: HOLIDAY(이름 동반) · CLEAR(선언된 연도의 평일) · **UNVERIFIED(그 국가·연도의 휴일 캘린더가 선언되지 않음)**.
UNVERIFIED는 평일이 아니다 — "경고 없음"을 "휴일 아님"으로 읽으면 fail-open이다(GC-A13·GC-A19 계보, 화면 배지 '휴일 캘린더 미등록 — 확인 불가').

■ **경고만**: 이 모듈은 날짜를 옮기지 않는다(자동 순연·당김 0 — 만기를 미루는 것은 계약 해석이라 사람 몫). 판정 대상 날짜를 그대로 판정한다.
■ **적용 = ETA(도착국)만**(DESIGN §7.5 문면 — R-09). 출발국·주말 판정은 부채(Q-19·Q-07). 적용 범위 결정은 조립자(PR-4a)의 몫이고
  이 함수는 날짜·국가 하나를 판정할 뿐이다.
■ 순수: DB·세션·시계 의존 0, 도메인(전표) 임포트 0(S3_PLATFORM — import-direction 테스트). 휴일 데이터는 호출자가 일괄 로드해 넘긴다(N+1 금지).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from datetime import date
from enum import StrEnum

#: 국가 키 = ISO 3166-1 alpha-2 대문자(markets FK 아님 — ADR-0082). DB CHECK `^[A-Z]{2}$`와 같은 식이다.
COUNTRY_PATTERN = "^[A-Z]{2}$"
_COUNTRY_RE = re.compile(COUNTRY_PATTERN)
#: 연도 선언 범위(DB CHECK와 같다).
YEAR_MIN, YEAR_MAX = 2000, 2999


class HolidayFlag(StrEnum):
    HOLIDAY = "HOLIDAY"
    CLEAR = "CLEAR"
    #: 그 국가·연도의 캘린더가 선언되지 않았다 — 확인 불가(평일 아님).
    UNVERIFIED = "UNVERIFIED"


def is_country_code(value: object) -> bool:
    """ISO alpha-2 대문자 두 글자인가(형식만 — 실재 국가 목록 대조는 하지 않는다: 근거 없는 목록을 만들지 않는다)."""
    return isinstance(value, str) and _COUNTRY_RE.fullmatch(value) is not None


def is_declarable_year(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and YEAR_MIN <= value <= YEAR_MAX


def holiday_flag(
    day: date,
    country: str | None,
    covered_years: Collection[int],
    holidays: Mapping[date, str],
) -> tuple[HolidayFlag, str | None]:
    """한 날짜·국가의 휴일 판정 → (판정, 휴일 이름 | None).

    - `country`가 없으면(방어 계약 — 선적 국가는 NOT NULL, X-28) UNVERIFIED
    - `covered_years`(그 국가에서 **선언된** 연도 집합)에 `day.year`가 없으면 UNVERIFIED — 빈 목록과 미선언을 구분한다
    - 선언돼 있고 `holidays`(그 국가의 휴일 날짜 → 이름)에 있으면 HOLIDAY(이름), 아니면 CLEAR
    """
    if not country:
        return HolidayFlag.UNVERIFIED, None
    if day.year not in covered_years:
        return HolidayFlag.UNVERIFIED, None
    name = holidays.get(day)
    if name is not None:
        return HolidayFlag.HOLIDAY, name
    return HolidayFlag.CLEAR, None
