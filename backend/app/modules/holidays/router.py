"""휴일 캘린더 API (S3-2 PR-2a / design-D §D2-3 H1~H5 / ADR-0079·0082).

권한: 조회(H1·H2·H4)는 **전 역할**(물류가 '미등록' 배지를 보고 등록 상태를 확인할 곳 — 원가 없음), 쓰기(H3 원자 교체)·CSV 미리보기(H5)는
**ADMIN 전용**(기한 데이터 관리 주체 확대 금지 — ADR-03·X-27). 역할 검사가 존재·입력 검사보다 앞선다(401→403→404→409→422).
쓰기 통로는 H3 하나다 — CSV는 미리보기(비저장) → 사람 확인 → H3. 휴일 판정 자체는 응답에 싣지 않는다(선적 조립이 `calc.holiday_flag`로 계산 — PR-4a).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.holidays import service
from app.modules.holidays.schemas import (
    CalendarReplaceRequest,
    CalendarYearOut,
    CsvPreviewOut,
    HolidayOut,
    HolidayPage,
)
from app.modules.identity.models import RoleCode

router = APIRouter(prefix="/holidays", tags=["holidays"])

CAN_WRITE = (RoleCode.ADMIN,)


@router.get("/calendars", summary="휴일 캘린더 연도 선언 목록 (국가·연도 필터, 건수·근거·확인일)")
def list_holiday_calendars(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    country: Annotated[str | None, Query(description="ISO alpha-2 대문자(예: CN)")] = None,
    year: Annotated[int | None, Query()] = None,
) -> Page[CalendarYearOut]:
    views, total = service.list_calendars(
        country=country, year=year, offset=params.offset, limit=params.limit
    )
    return Page.of([CalendarYearOut.model_validate(v.body()) for v in views], total, params)


@router.get(
    "",
    summary="한 국가·연도의 휴일 목록 — calendar=null이면 미선언(빈 목록과 다르다: 판정 UNVERIFIED)",
)
def list_holidays(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    country: Annotated[str, Query(description="ISO alpha-2 대문자(예: CN)")],
    year: Annotated[int, Query()],
) -> HolidayPage:
    calendar, items, total = service.list_holidays(
        country=country, year=year, offset=params.offset, limit=params.limit
    )
    return HolidayPage(
        items=[HolidayOut(id=h.id, holiday_on=h.holiday_on, name=h.name) for h in items],
        total=total,
        page=params.page,
        size=params.size,
        calendar=CalendarYearOut.model_validate(calendar.body()) if calendar else None,
    )


@router.get(
    "/{country}/{year}/export.csv",
    summary="한 국가·연도 휴일 CSV 내보내기 (UTF-8 BOM·수식 이스케이프 — 미선언 404)",
)
def export_holidays_csv(country: str, year: int, current: CurrentUser) -> StreamingResponse:
    rows = service.export_rows(country=country, year=year)
    return csv_response(f"휴일_{country}_{year}.csv", service.CSV_HEADER, rows)


@router.put(
    "/{country}/{year}",
    summary="한 국가·연도 휴일 원자 교체 (관리자 — 근거 링크·확인일 필수, 0건 = 휴일 없음 확인, 멱등 키·version)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def replace_holiday_calendar(
    country: str,
    year: int,
    payload: CalendarReplaceRequest,
    current: AdminUser,
    key: IdempotencyKey,
) -> CalendarYearOut:
    _, body = service.replace_calendar(
        actor=current,
        idempotency_key=key,
        country=country,
        year=year,
        payload=payload.model_dump(mode="python"),
    )
    return CalendarYearOut.model_validate(body)


@router.post(
    "/{country}/{year}/import-csv/preview",
    summary="휴일 CSV 미리보기 (관리자 — 저장하지 않음, 문제 1건이라도 있으면 저장 불가)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def preview_holiday_csv(
    country: str,
    year: int,
    file: Annotated[UploadFile, File(description="머리글 holiday_on,name — UTF-8 CSV")],
    current: AdminUser,
) -> CsvPreviewOut:
    raw = file.file.read(service.CSV_MAX_BYTES + 1)
    return CsvPreviewOut.model_validate(service.preview_csv(country=country, year=year, raw=raw))
