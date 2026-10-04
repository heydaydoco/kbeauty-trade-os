"""휴일 캘린더 요청·응답 (S3-2 PR-2a / design-D §D2-3 H1~H5 / ADR-0082).

날짜는 `YYYY-MM-DD` 문자열(현지 달력일 — 시간대 없음)로 주고받는다. 프런트는 날짜 문자열을 `new Date()`로 해석하지 않는다(PR-2b 소스 계약).
쓰기 스키마는 전부 `extra="forbid"`. 근거 2필드는 형식상 선택으로 받고 서비스가 422 `HOLIDAYS.CALENDAR.SOURCE_REQUIRED`로 거부한다
(누락을 일반 검증 오류가 아니라 '근거 필요' 코드로 드러내기 위해서다).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.pagination import Page
from app.modules.holidays.models import HOLIDAY_NAME_MAX

#: 한 국가·연도의 휴일 상한 — 1년의 날 수. 넘으면 입력 오류다.
MAX_HOLIDAYS_PER_YEAR = 366


class HolidayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holiday_on: date
    name: str = Field(min_length=1, max_length=HOLIDAY_NAME_MAX)


class CalendarReplaceRequest(BaseModel):
    """`PUT /holidays/{country}/{year}` — 그 국가·연도의 휴일 집합 **전체**(0건 = '휴일 없음 확인')."""

    model_config = ConfigDict(extra="forbid")

    source_url: str | None = Field(default=None, max_length=500)
    verified_on: date | None = None
    holidays: list[HolidayIn] = Field(max_length=MAX_HOLIDAYS_PER_YEAR)
    #: 선언이 없으면 null(최초 선언), 있으면 화면이 본 version(낙관 잠금 — 불일치 409).
    version: int | None = Field(default=None, ge=1)


class CalendarYearOut(BaseModel):
    id: int
    country_code: str
    year: int
    source_url: str
    verified_on: date
    holiday_count: int
    version: int
    updated_by_name: str | None
    updated_at: datetime


class HolidayOut(BaseModel):
    id: int
    holiday_on: date
    name: str


class HolidayPage(Page[HolidayOut]):
    """`GET /holidays?country=&year=` — 봉투 밖 `calendar`가 null이면 **미선언**(빈 목록과 다르다 — UNVERIFIED의 화면 근거)."""

    calendar: CalendarYearOut | None


class CsvPreviewRow(BaseModel):
    holiday_on: date
    name: str


class CsvPreviewProblem(BaseModel):
    #: 데이터 행 번호(머리글 다음 줄이 1).
    row: int
    field: str
    message: str


class CsvPreviewOut(BaseModel):
    """미리보기(비저장) — `problems`가 1건이라도 있으면 화면은 저장(H3) 버튼을 막는다(파일 전체 원자)."""

    rows: list[CsvPreviewRow]
    problems: list[CsvPreviewProblem]
