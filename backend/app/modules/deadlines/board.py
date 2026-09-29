"""기일 캘린더 조회 — 인증 보드의 캘린더 축 (DESIGN §14 ④ / S2-3 PR-3 안건 ⑥).

기일 스캔(`service.py`)이 알림을 **만드는** 쪽이라면 이 파일은 같은 기일을 사람이
**달력으로 보는** 쪽이다. 후보 정의가 스캔과 같다: 활성 행 + 종결 2태(반려·중단)
제외 인증의 `expires_on` + 활성 문서의 `valid_until`. 다르게 정의하면 "알림은 왔는데
캘린더에 없다"가 생긴다.

★ **읽기 전용이다.** 저장하는 것도 발행하는 것도 없다(캘린더는 계산이 아니라 조회다).
★ 기간을 **최대 93일**로 묶는다 — 이 조회는 두 테이블을 기간으로 훑어 합친 뒤 쪽을
  자르므로, 기간이 무한하면 "전체를 읽고 잘라 주는" 조회가 된다(§18.4 무제한 조회 금지의
  정신). 화면은 월 단위로 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.certifications.calendar import overdue_days
from app.modules.certifications.machine import TERMINAL_STATUSES
from app.modules.certifications.models import Certification
from app.modules.certifications.service import (
    assignee_name,
    preload_assignees,
    preload_targets,
    target_label,
)
from app.modules.documents.models import Document, DocumentType
from app.modules.documents.service import owner_displays
from app.modules.markets.models import Market
from app.modules.requirements.models import RequirementTemplate

#: 조회 기간 상한(일) — 한 화면(월)에 한 달 + 앞뒤 여유가 들어가는 크기.
MAX_SPAN_DAYS = 93

KIND_CERTIFICATION = "CERTIFICATION"
KIND_DOCUMENT = "DOCUMENT"
_KIND_ORDER = {KIND_CERTIFICATION: 0, KIND_DOCUMENT: 1}


@dataclass(frozen=True, slots=True)
class CalendarItem:
    kind: str
    id: int
    title: str
    on: date
    #: 인증의 저장 상태(문서는 None). 색 판단은 화면이 아니라 `is_overdue`가 한다.
    status: str | None
    #: 오늘(KST) 기준으로 기일이 지났는가 — 도과 계산값과 같은 정의(`calendar.overdue_days`).
    is_overdue: bool
    assignee_id: int | None
    assignee_name: str | None


def validate_window(start: date, end: date) -> None:
    """기간 검증 — 사용자 안내가 있는 422(입력 실수는 서버 장애로 표시되면 안 된다)."""
    if end < start:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"to": "종료일이 시작일보다 앞섭니다. 기간을 다시 지정해 주세요."},
            log_context={"from": start.isoformat(), "to": end.isoformat()},
        )
    if (end - start).days > MAX_SPAN_DAYS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "to": f"조회 기간은 최대 {MAX_SPAN_DAYS}일입니다. 기간을 나누어 조회해 주세요."
            },
            log_context={"from": start.isoformat(), "to": end.isoformat()},
        )


def list_calendar_items(
    *, start: date, end: date, offset: int, limit: int, base_date: date | None = None
) -> tuple[list[CalendarItem], int]:
    """기간 안의 인증 만료일·문서 유효기간을 날짜순으로 (쪽 단위). 돌아오는 총계는 전체 건수."""
    validate_window(start, end)
    today = base_date or today_kst()
    with unit_of_work() as uow:
        session = uow.session
        items = [
            *_certification_items(session, start, end, today),
            *_document_items(session, start, end, today),
        ]
    items.sort(key=lambda item: (item.on, _KIND_ORDER[item.kind], item.id))
    return items[offset : offset + limit], len(items)


def _certification_items(
    session: Session, start: date, end: date, today: date
) -> list[CalendarItem]:
    rows = session.execute(
        select(Certification, Market.code)
        .join(RequirementTemplate, Certification.template_id == RequirementTemplate.id)
        .join(Market, RequirementTemplate.market_id == Market.id)
        .where(
            Certification.deleted_at.is_(None),
            Certification.status.not_in(sorted(TERMINAL_STATUSES)),
            Certification.expires_on.is_not(None),
            Certification.expires_on >= start,
            Certification.expires_on <= end,
        )
        .order_by(Certification.expires_on, Certification.id)
    ).all()
    preload_assignees(session, {row.assignee_id for row, _ in rows})
    preload_targets(session, {(row.target_type, row.target_id) for row, _ in rows})
    return [
        CalendarItem(
            kind=KIND_CERTIFICATION,
            id=row.id,
            title=f"[{market_code}] {row.template_name} — "
            f"{target_label(session, row.target_type, row.target_id)}",
            on=row.expires_on,
            status=row.status,
            is_overdue=overdue_days(row.status, row.expires_on, today) is not None,
            assignee_id=row.assignee_id,
            assignee_name=assignee_name(session, row.assignee_id),
        )
        for row, market_code in rows
    ]


def _document_items(session: Session, start: date, end: date, today: date) -> list[CalendarItem]:
    rows = session.execute(
        select(Document, DocumentType.name_ko)
        .join(DocumentType, Document.document_type_id == DocumentType.id)
        .where(
            Document.deleted_at.is_(None),
            Document.valid_until.is_not(None),
            Document.valid_until >= start,
            Document.valid_until <= end,
        )
        .order_by(Document.valid_until, Document.id)
    ).all()
    displays = owner_displays(session, {(row.owner_type, row.owner_id) for row, _ in rows})
    return [
        CalendarItem(
            kind=KIND_DOCUMENT,
            id=row.id,
            title=f"{type_name} · "
            f"{displays.get((row.owner_type, row.owner_id), f'{row.owner_type} #{row.owner_id}')}",
            on=row.valid_until,
            status=None,
            is_overdue=row.valid_until < today,
            assignee_id=None,
            assignee_name=None,
        )
        for row, type_name in rows
    ]
