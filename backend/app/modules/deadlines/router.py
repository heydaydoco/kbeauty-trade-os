"""기일 캘린더 엔드포인트 (§14 ④ 인증 보드 — 캘린더 / S2-3 PR-3 안건 ⑥).

읽기 전용·전 역할 열람(원가·마진 필드 없음 — 마스킹 비대상). 기간은 최대 93일(시작일·종료일 포함)이다
(`board.MAX_SPAN_DAYS` — 이유는 그 파일). 목록 응답은 Page 봉투다(§18.4).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.pagination import Page, PageParams
from app.modules.deadlines import board

router = APIRouter(prefix="/deadlines", tags=["deadlines"])


class CalendarItemSummary(BaseModel):
    #: CERTIFICATION(인증 만료일) 또는 DOCUMENT(문서 유효기간).
    kind: str
    id: int
    title: str
    #: 기일(인증=expires_on, 문서=valid_until).
    date: date
    status: str | None
    is_overdue: bool
    assignee_id: int | None
    assignee_name: str | None

    @classmethod
    def of(cls, item: board.CalendarItem) -> CalendarItemSummary:
        return cls(
            kind=item.kind,
            id=item.id,
            title=item.title,
            date=item.on,
            status=item.status,
            is_overdue=item.is_overdue,
            assignee_id=item.assignee_id,
            assignee_name=item.assignee_name,
        )


@router.get(
    "/calendar", summary="기일 캘린더 (인증 만료일·문서 유효기간, 시작·종료 포함 최대 93일)"
)
def list_calendar_items(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    from_date: Annotated[date, Query(alias="from", description="시작일(포함) YYYY-MM-DD")],
    to_date: Annotated[date, Query(alias="to", description="종료일(포함) YYYY-MM-DD")],
) -> Page[CalendarItemSummary]:
    items, total = board.list_calendar_items(
        start=from_date, end=to_date, offset=params.offset, limit=params.limit
    )
    return Page.of([CalendarItemSummary.of(item) for item in items], total, params)
