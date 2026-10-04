"""휴일 캘린더 서비스 (S3-2 PR-2a / ADR-0082 / design-B §B11 / design-C T10 / design-D §D2-3 H1~H5).

■ 쓰기 통로는 `replace_calendar`(PUT /holidays/{country}/{year}) **하나**다 — 화면 입력·CSV 업로드는 이 통로의 클라이언트다.
  한 트랜잭션(T10): 멱등 claim → 그 국가·연도 선언 행 `FOR UPDATE`(없으면 INSERT — 동시 최초 선언은 부분 유니크가 하나만 통과시켜 409) →
  version 대조 → 선언 근거 갱신 → 기존 휴일 **soft delete** → 새 휴일 INSERT → audit_log → 멱등 완료. 외부 호출 0, 아웃박스 이벤트 없음(소비자 0).
  부분 교체 상태(휴일은 지웠는데 새 휴일이 없는)는 커밋되지 않는다 — 교체는 원자다.
■ 휴일·선언 표는 LOCK_ORDER 밖이다 — 전표 트랜잭션은 휴일을 잠그지 않고 읽기만 하고, 이 트랜잭션은 전표를 잠그지 않는다(교차 없음, design-C §C2).
■ 서비스 선검증이 1차(근거·연도·중복 날짜)이고 DB 제약(복합 FK·연도 CHECK·부분 유니크 — R-24)은 최후 방어선이다. DB 위반은 `CONSTRAINT_ERRORS`로
  업무 오류로 번역한다(500 금지 — 번역표 ⊇ 실제 제약 집합을 시험이 대사).
■ 시드 0·외부 자동 수집 0(ADR-0082). 판정(HOLIDAY/CLEAR/UNVERIFIED)은 `calc.holiday_flag`(순수)이고 이 모듈은 데이터 통로다.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.csv_export import unescape_formula_cell
from app.core.db.uow import in_unit_of_work, unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.text import invisible_char_problem
from app.core.time import today_kst
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.holidays.calc import is_country_code, is_declarable_year
from app.modules.holidays.models import (
    CALENDAR_YEAR_KEY,
    CALENDAR_YEAR_UNIQUE,
    HOLIDAY_CALENDAR_FK,
    HOLIDAY_DAY_UNIQUE,
    HOLIDAY_NAME_MAX,
    SOURCE_URL_MAX,
    Holiday,
    HolidayCalendarYear,
)
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser

REPLACE_ENDPOINT = "PUT /api/v1/holidays/{country}/{year}"

#: CSV 머리글 — 내보내기·미리보기가 **같은 객체**를 쓴다(왕복).
CSV_HEADER: tuple[str, str] = ("holiday_on", "name")
#: 미리보기 파일 상한 — 1년 366행 × 넉넉한 이름 길이. 넘으면 휴일 파일이 아니다.
CSV_MAX_BYTES = 256 * 1024
CSV_MAX_ROWS = 400


#: DB 제약·유니크 인덱스 → 업무 오류. **모델·마이그레이션의 제약 집합 ⊆ 이 표**임을 시험이 pg_constraint·pg_indexes로 대사한다
#: (신규 제약이 번역 없이 500으로 새지 않게). 서비스 선검증을 통과한 뒤에도 남는 경합·결함의 마지막 방어선이다.
CONSTRAINT_ERRORS: dict[str, ErrorCode] = {
    CALENDAR_YEAR_UNIQUE: ErrorCode.HOLIDAYS_CALENDAR_YEAR_DUPLICATE,  # 동시 최초 선언(X-22)
    HOLIDAY_DAY_UNIQUE: ErrorCode.HOLIDAYS_CALENDAR_DUPLICATE_DATE,
    HOLIDAY_CALENDAR_FK: ErrorCode.HOLIDAYS_CALENDAR_YEAR_MISMATCH,  # R-24 — 국가·연도가 선언과 다름
    "ck_holidays_day_in_year": ErrorCode.HOLIDAYS_CALENDAR_YEAR_MISMATCH,  # R-24 — 날짜 연도 ≠ year
    "ck_holidays_country_code_format": ErrorCode.HOLIDAYS_COUNTRY_INVALID,
    "ck_holiday_calendar_years_country_code_format": ErrorCode.HOLIDAYS_COUNTRY_INVALID,
    "ck_holiday_calendar_years_year_range": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_holiday_calendar_years_source_url_http": ErrorCode.HOLIDAYS_CALENDAR_SOURCE_REQUIRED,
    "ck_holiday_calendar_years_source_url_clean": ErrorCode.HOLIDAYS_CALENDAR_SOURCE_REQUIRED,
    "ck_holidays_name_nonblank": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_holidays_name_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    # 감사 열 users FK — 인증된 행위자라 정상 경로에서 발생 불가(호출 계약 위반)
    "fk_holiday_calendar_years_created_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    "fk_holiday_calendar_years_updated_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    "fk_holidays_created_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    "fk_holidays_updated_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    # 자동 생성 키·복합 FK 대상(id 포함)이라 위반 불가 — 번역표 완결성을 위해 등재
    "pk_holiday_calendar_years": ErrorCode.INTERNAL_UNEXPECTED,
    "pk_holidays": ErrorCode.INTERNAL_UNEXPECTED,
    CALENDAR_YEAR_KEY: ErrorCode.INTERNAL_UNEXPECTED,
}


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


@dataclass(frozen=True, slots=True)
class HolidayLookup:
    """한 국가의 판정 입력 — 선언된 연도 집합(빈 목록 ≠ 미선언)과 요청한 날짜 중 휴일인 날짜 → 이름. `calc.holiday_flag`에 그대로 넘긴다."""

    covered_years: frozenset[int]
    holidays: dict[date, str]


def lookup_days(session: Session, country: str, days: set[date]) -> HolidayLookup:
    """국가·날짜들의 휴일 판정 입력을 **질의 2회**로 일괄 로드한다(선적 마일스톤 조립 — N+1 금지, 호출자 트랜잭션에서 읽기만).

    도메인을 모른다(국가 코드·날짜만 받는다 — S3_PLATFORM). 날짜가 없거나 국가 형식이 아니면 질의 없이 빈 결과(= UNVERIFIED 판정).
    """
    if not days or not is_country_code(country):
        return HolidayLookup(frozenset(), {})
    years = sorted({day.year for day in days})
    covered = frozenset(
        int(year)
        for year in session.execute(
            select(HolidayCalendarYear.year).where(
                HolidayCalendarYear.country_code == country,
                HolidayCalendarYear.year.in_(years),
                HolidayCalendarYear.deleted_at.is_(None),
            )
        ).scalars()
    )
    found = {
        row[0]: str(row[1])
        for row in session.execute(
            select(Holiday.holiday_on, Holiday.name).where(
                Holiday.country_code == country,
                Holiday.holiday_on.in_(sorted(days)),
                Holiday.deleted_at.is_(None),
            )
        ).all()
    }
    return HolidayLookup(covered, found)


def _constraint_of(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _flush(session: Session) -> None:
    """flush하고 DB 제약 위반을 업무 오류로 번역한다 — 번역 불가(내부)는 삼키지 않고 그대로 전파(500)."""
    try:
        session.flush()
    except IntegrityError as exc:
        code = CONSTRAINT_ERRORS.get(_constraint_of(exc) or "")
        if code is None or code is ErrorCode.INTERNAL_UNEXPECTED:
            raise
        raise AppError(code, log_context={"constraint": _constraint_of(exc)}) from exc


# ── 입력 검증 ────────────────────────────────────────────────────────────────


def require_country(country: str) -> str:
    """경로·쿼리의 국가 코드 — ISO alpha-2 대문자가 아니면 422 `HOLIDAYS.COUNTRY.INVALID`(소문자를 조용히 올리지 않는다)."""
    if not is_country_code(country):
        raise AppError(ErrorCode.HOLIDAYS_COUNTRY_INVALID, detail={"country": "예: KR, CN, US"})
    return country


def require_year(year: int) -> int:
    if not is_declarable_year(year):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD, detail={"year": "연도는 2000~2999 사이여야 합니다."}
        )
    return year


def _source(source_url: str | None, verified_on: date | None) -> tuple[str, date]:
    """근거 2필드 — 결측·공백·http(s) 아님·공백 포함·제어문자·미래 확인일은 422 SOURCE_REQUIRED(근거 없는 휴일 데이터 금지)."""
    problems: dict[str, str] = {}
    url = (source_url or "").strip()
    if not url:
        problems["source_url"] = "근거 링크를 입력해 주세요."
    elif not url.startswith(("http://", "https://")):
        problems["source_url"] = "근거 링크는 http:// 또는 https://로 시작해야 합니다."
    elif any(ch.isspace() for ch in url) or invisible_char_problem(url, label="근거 링크"):
        problems["source_url"] = "근거 링크에 공백·보이지 않는 문자가 있습니다."
    elif len(url) > SOURCE_URL_MAX:
        problems["source_url"] = f"근거 링크는 {SOURCE_URL_MAX}자 이하여야 합니다."
    if verified_on is None:
        problems["verified_on"] = "확인일을 입력해 주세요."
    elif verified_on > today_kst():
        problems["verified_on"] = "확인일은 오늘(한국 날짜) 또는 그 이전이어야 합니다."
    if problems:
        raise AppError(ErrorCode.HOLIDAYS_CALENDAR_SOURCE_REQUIRED, detail=problems)
    assert verified_on is not None
    return url, verified_on


def _name_problem(name: str) -> str | None:
    """휴일 이름(원문 — strip 전) 문제 문구 또는 None. 공백뿐·길이 초과·보이지 않는 글자(제어·서식·채움)를 거부한다."""
    invisible = invisible_char_problem(name, label="휴일 이름")
    if invisible is not None:
        return invisible
    if not name.strip():
        return "휴일 이름을 입력해 주세요."
    if len(name.strip()) > HOLIDAY_NAME_MAX:
        return f"휴일 이름은 {HOLIDAY_NAME_MAX}자 이하여야 합니다."
    return None


def _holidays(year: int, items: list[dict[str, Any]]) -> list[tuple[date, str]]:
    """본문 휴일 → (날짜, 이름) 날짜순. 연도 불일치 422 YEAR_MISMATCH · 같은 날짜 2건 422 DUPLICATE_DATE · 이름 문제 422."""
    raw = [(item["holiday_on"], str(item["name"])) for item in items]
    wrong_year = sorted({d.isoformat() for d, _ in raw if d.year != year})
    if wrong_year:
        raise AppError(ErrorCode.HOLIDAYS_CALENDAR_YEAR_MISMATCH, detail={"holiday_on": wrong_year})
    seen: set[date] = set()
    duplicates: set[str] = set()
    for day, _ in raw:
        if day in seen:
            duplicates.add(day.isoformat())
        seen.add(day)
    if duplicates:
        raise AppError(
            ErrorCode.HOLIDAYS_CALENDAR_DUPLICATE_DATE, detail={"holiday_on": sorted(duplicates)}
        )
    bad_names = {d.isoformat(): p for d, n in raw if (p := _name_problem(n)) is not None}
    if bad_names:
        raise AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={"name": bad_names})
    return sorted((d, n.strip()) for d, n in raw)


# ── 조회 ────────────────────────────────────────────────────────────────────

#: 조회 트랜잭션의 첫 문장 — 선언(version·건수)과 휴일 행·건수가 **한 스냅샷**이다(사이에 커밋된 교체가 섞여
#: `calendar.version`과 다른 판의 휴일이 함께 보이지 않게 — 오더 보드 `read_snapshot` 선례).
SNAPSHOT_STATEMENT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


@contextmanager
def _read_snapshot() -> Iterator[Session]:
    if (
        in_unit_of_work()
    ):  # SET TRANSACTION은 첫 문장이어야 한다 — 바깥 트랜잭션 합류는 프로그래밍 오류
        raise RuntimeError("휴일 조회는 독립 트랜잭션이어야 합니다.")
    with unit_of_work() as uow:
        uow.session.execute(text(SNAPSHOT_STATEMENT))
        yield uow.session


@dataclass(frozen=True, slots=True)
class CalendarYearView:
    id: int
    country_code: str
    year: int
    source_url: str
    verified_on: date
    holiday_count: int
    version: int
    updated_by_name: str | None
    updated_at: datetime

    def body(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "country_code": self.country_code,
            "year": self.year,
            "source_url": self.source_url,
            "verified_on": self.verified_on.isoformat(),
            "holiday_count": self.holiday_count,
            "version": self.version,
            "updated_by_name": self.updated_by_name,
            "updated_at": self.updated_at.isoformat(),
        }


def _live_count():  # type: ignore[no-untyped-def]
    return (
        select(func.count(Holiday.id))
        .where(Holiday.calendar_year_id == HolidayCalendarYear.id, Holiday.deleted_at.is_(None))
        .correlate(HolidayCalendarYear)
        .scalar_subquery()
    )


def _calendar_query():  # type: ignore[no-untyped-def]
    editor = func.coalesce(HolidayCalendarYear.updated_by_id, HolidayCalendarYear.created_by_id)
    return (
        select(HolidayCalendarYear, _live_count().label("holiday_count"), User.display_name)
        .outerjoin(User, User.id == editor)
        .where(HolidayCalendarYear.deleted_at.is_(None))
    )


def _view(row: HolidayCalendarYear, count: int, editor: str | None) -> CalendarYearView:
    return CalendarYearView(
        id=row.id,
        country_code=row.country_code,
        year=row.year,
        source_url=row.source_url,
        verified_on=row.verified_on,
        holiday_count=int(count),
        version=row.version,
        updated_by_name=editor,
        updated_at=row.updated_at,
    )


def list_calendars(
    *, country: str | None, year: int | None, offset: int, limit: int
) -> tuple[list[CalendarYearView], int]:
    """H1 — 선언 목록(국가 오름차순·연도 내림차순). 필터: 국가·연도. 페이지 기본 50."""
    if country is not None:
        require_country(country)
    if year is not None:
        require_year(year)
    with _read_snapshot() as session:
        query = _calendar_query()
        count_query = select(func.count(HolidayCalendarYear.id)).where(
            HolidayCalendarYear.deleted_at.is_(None)
        )
        if country is not None:
            query = query.where(HolidayCalendarYear.country_code == country)
            count_query = count_query.where(HolidayCalendarYear.country_code == country)
        if year is not None:
            query = query.where(HolidayCalendarYear.year == year)
            count_query = count_query.where(HolidayCalendarYear.year == year)
        total = int(session.execute(count_query).scalar_one())
        rows = session.execute(
            query.order_by(
                HolidayCalendarYear.country_code,
                HolidayCalendarYear.year.desc(),
                HolidayCalendarYear.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [_view(r[0], r[1], r[2]) for r in rows], total


def _find_calendar(session: Session, country: str, year: int) -> CalendarYearView | None:
    row = session.execute(
        _calendar_query().where(
            HolidayCalendarYear.country_code == country, HolidayCalendarYear.year == year
        )
    ).one_or_none()
    return _view(row[0], row[1], row[2]) if row is not None else None


@dataclass(frozen=True, slots=True)
class HolidayView:
    id: int
    holiday_on: date
    name: str


def list_holidays(
    *, country: str, year: int, offset: int, limit: int
) -> tuple[CalendarYearView | None, list[HolidayView], int]:
    """H2 — 한 국가·연도의 휴일(날짜순). 선언이 없으면 (None, [], 0) — **미선언 ≠ 빈 선언**(UNVERIFIED의 화면 근거)."""
    require_country(country)
    require_year(year)
    with _read_snapshot() as session:
        calendar = _find_calendar(session, country, year)
        if calendar is None:
            return None, [], 0
        live = (Holiday.calendar_year_id == calendar.id, Holiday.deleted_at.is_(None))
        total = int(session.execute(select(func.count(Holiday.id)).where(*live)).scalar_one())
        rows = session.execute(
            select(Holiday.id, Holiday.holiday_on, Holiday.name)
            .where(*live)
            .order_by(Holiday.holiday_on, Holiday.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return calendar, [HolidayView(r[0], r[1], r[2]) for r in rows], total


def export_rows(*, country: str, year: int) -> list[tuple[str, str]]:
    """H4 — 한 국가·연도 휴일 CSV 행(날짜 문자열·이름). 선언이 없으면 404(빈 파일로 '휴일 없음'처럼 보이지 않게)."""
    require_country(country)
    require_year(year)
    with _read_snapshot() as session:
        calendar = _find_calendar(session, country, year)
        if calendar is None:
            raise NotFoundError(log_context={"country": country, "year": year})
        rows = session.execute(
            select(Holiday.holiday_on, Holiday.name)
            .where(Holiday.calendar_year_id == calendar.id, Holiday.deleted_at.is_(None))
            .order_by(Holiday.holiday_on, Holiday.id)
        ).all()
        return [(r[0].isoformat(), r[1]) for r in rows]


# ── 원자 교체 (T10) ──────────────────────────────────────────────────────────


def replace_calendar(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    country: str,
    year: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """H3 — 그 국가·연도의 휴일 집합 전체를 **원자 교체**한다(0건 = '휴일 없음 확인'). 한 트랜잭션.

    `payload.version`: 선언이 없으면 null(최초 선언), 있으면 화면이 본 version(불일치 409). 같은 키 재요청은 최초 결과(교체 1회).
    """
    require_country(country)
    require_year(year)
    source_url, verified_on = _source(payload.get("source_url"), payload.get("verified_on"))
    holidays = _holidays(year, list(payload.get("holidays") or []))
    expected = payload.get("version")
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REPLACE_ENDPOINT,
            key=idempotency_key,
            request_body={"country": country, "year": year, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        calendar = session.execute(
            select(HolidayCalendarYear)
            .where(
                HolidayCalendarYear.country_code == country,
                HolidayCalendarYear.year == year,
                HolidayCalendarYear.deleted_at.is_(None),
            )
            .with_for_update()
        ).scalar_one_or_none()
        first = calendar is None
        before: dict[date, str] = {}
        old_source: dict[str, Any] | None = None
        if calendar is None:
            if expected is not None:
                raise VersionConflictError(log_context={"country": country, "year": year})
            calendar = HolidayCalendarYear(
                country_code=country,
                year=year,
                source_url=source_url,
                verified_on=verified_on,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(calendar)
            _flush(session)  # 동시 최초 선언 — 부분 유니크가 하나만 통과시킨다(409 YEAR_DUPLICATE)
        else:
            if expected is None or int(expected) != calendar.version:
                raise VersionConflictError(
                    log_context={"country": country, "year": year, "actual": calendar.version}
                )
            old_source = {
                "source_url": calendar.source_url,
                "verified_on": calendar.verified_on.isoformat(),
            }
            before = {
                r[0]: r[1]
                for r in session.execute(
                    select(Holiday.holiday_on, Holiday.name).where(
                        Holiday.calendar_year_id == calendar.id, Holiday.deleted_at.is_(None)
                    )
                ).all()
            }
            calendar.source_url = source_url
            calendar.verified_on = verified_on
            calendar.updated_by_id = actor.id
            calendar.updated_at = (
                func.now()
            )  # 값이 같아도 교체는 새 version이다(낙관 잠금 — 화면이 본 상태 폐기)
            session.execute(
                update(Holiday)
                .where(Holiday.calendar_year_id == calendar.id, Holiday.deleted_at.is_(None))
                .values(deleted_at=func.now(), updated_by_id=actor.id, updated_at=func.now())
            )
        for day, name in holidays:
            session.add(
                Holiday(
                    calendar_year_id=calendar.id,
                    country_code=country,
                    year=year,
                    holiday_on=day,
                    name=name,
                    created_by_id=actor.id,
                    updated_by_id=actor.id,
                )
            )
        _flush(session)
        session.refresh(calendar)

        after = dict(holidays)
        audit.record(
            session,
            action=AuditAction.HOLIDAYS_CALENDAR_REPLACED,
            actor_user_id=actor.id,
            entity_type="holiday_calendar_years",
            entity_id=calendar.id,
            detail={
                "country": country,
                "year": year,
                "first_declaration": first,
                "before_count": len(before),
                "after_count": len(after),
                "added": sorted(d.isoformat() for d in after.keys() - before.keys()),
                "removed": sorted(d.isoformat() for d in before.keys() - after.keys()),
                "renamed": sorted(
                    d.isoformat() for d in after.keys() & before.keys() if after[d] != before[d]
                ),
                "source_url": source_url,
                "verified_on": verified_on.isoformat(),
                "previous_source": old_source,
            },
        )
        view = _find_calendar(session, country, year)
        assert view is not None
        body = view.body()
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


# ── CSV 미리보기 (H5 — 비저장) ────────────────────────────────────────────────


def _invalid_csv(message: str) -> AppError:
    return AppError(ErrorCode.HOLIDAYS_CSV_INVALID_FORMAT, detail={"file": message})


def preview_csv(*, country: str, year: int, raw: bytes) -> dict[str, Any]:
    """H5 — CSV를 읽어 행·문제 목록을 돌려준다(**저장하지 않는다** — 저장은 사람이 확인 후 H3로).

    파일 단위 거부(422 `HOLIDAYS.CSV.INVALID_FORMAT`): 빈 파일·크기·UTF-8 아님·NUL·머리글 불일치·CSV 문법·행 수 초과.
    행 단위 문제는 `problems`로(날짜 형식·연도 불일치·이름·중복 날짜) — 1건이라도 있으면 화면은 저장 버튼을 막는다(파일 전체 원자).
    셀은 내보내기의 수식 이스케이프를 역변환한다(왕복 — §12.2).
    """
    require_country(country)
    require_year(year)
    if not raw:
        raise _invalid_csv("빈 파일입니다.")
    if len(raw) > CSV_MAX_BYTES:
        raise _invalid_csv(f"파일이 너무 큽니다({CSV_MAX_BYTES // 1024}KB 이하).")
    if b"\x00" in raw:
        raise _invalid_csv("텍스트 CSV 파일이 아닙니다.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _invalid_csv(
            "UTF-8로 읽을 수 없습니다. 엑셀에서 'CSV UTF-8(쉼표로 분리)'로 저장해 주세요."
        ) from None
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise _invalid_csv(f"CSV 문법 오류: {exc}") from None
    if not records or [cell.strip() for cell in records[0]] != list(CSV_HEADER):
        raise _invalid_csv("첫 줄 머리글은 정확히 holiday_on,name 이어야 합니다.")
    body = [r for r in records[1:] if any(cell.strip() for cell in r)]
    if len(body) > CSV_MAX_ROWS:
        raise _invalid_csv(f"행이 너무 많습니다({CSV_MAX_ROWS}행 이하).")

    rows: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    seen: dict[date, int] = {}
    for index, record in enumerate(body, start=1):
        if len(record) != len(CSV_HEADER):
            problems.append(
                {"row": index, "field": "row", "message": "열은 holiday_on,name 두 개여야 합니다."}
            )
            continue
        raw_day = unescape_formula_cell(record[0].strip())
        raw_name = unescape_formula_cell(
            record[1].strip(" ")
        )  # 원문 검사(탭·줄바꿈을 strip으로 숨기지 않는다)
        try:
            if (
                _ISO_DAY.fullmatch(raw_day) is None
            ):  # ISO 주차·기본형('2026-W02-1'·'20260105T0')이 다른 날짜로 정규화되지 않게
                raise ValueError
            day = date.fromisoformat(raw_day)
        except ValueError:
            problems.append(
                {
                    "row": index,
                    "field": "holiday_on",
                    "message": "날짜는 YYYY-MM-DD 형식이어야 합니다.",
                }
            )
            continue
        name_problem = _name_problem(raw_name)
        if day.year != year:
            problems.append(
                {"row": index, "field": "holiday_on", "message": f"{year}년 날짜가 아닙니다."}
            )
        elif day in seen:
            problems.append(
                {
                    "row": index,
                    "field": "holiday_on",
                    "message": f"{seen[day]}행과 같은 날짜입니다.",
                }
            )
        if name_problem is not None:
            problems.append({"row": index, "field": "name", "message": name_problem})
        seen.setdefault(day, index)
        rows.append({"holiday_on": day.isoformat(), "name": raw_name.strip()})
    return {"rows": rows, "problems": problems}
