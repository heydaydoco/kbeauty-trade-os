"""휴일 캘린더 — 국가·연도 선언 + 휴일 (S3-2 PR-2a / ADR-0082 / design-B §B11 / design-integrated §2.1 (j)(k)·§9 R-24 — 마이그레이션 M13).

■ `holiday_calendar_years` = 국가×연도 단위의 **적재 선언**이다. 선언이 있어야 그 해의 판정이 HOLIDAY/CLEAR가 되고, 없으면 UNVERIFIED다
  (빈 목록과 미선언은 다르다 — 0건 선언 = '휴일 없음 확인'). 근거 2필드(`source_url`·`verified_on`)는 선언 단위로 필수(ADR-03 "기한 데이터에
  근거링크+최종확인일 필수"). 국가 키는 ISO alpha-2 CHECK이고 **markets FK가 아니다**(시장 축 ≠ 출발·도착국 축).
■ `holidays` = 선언에 속한 휴일 날짜·이름. **연도·국가 정합은 DB가 강제한다**(R-24): `holidays.year` NOT NULL + 복합 FK
  `(calendar_year_id, country_code, year)` → `holiday_calendar_years(id, country_code, year)` + CHECK `extract(year from holiday_on) = year`.
  서비스 결함 1건이 다른 국가·연도 선언에 휴일을 붙여 UNVERIFIED/CLEAR 판정을 조용히 틀어지게 하는 길을 DB가 닫는다.
■ 복합 FK의 자동 이름(`fk_holidays_calendar_year_id_country_code_year_holiday_calendar_years`)은 69자로 PostgreSQL 식별자 상한(63)을
  넘는다 — 잘린 이름은 마이그레이션이 지목하지 못하므로 **짧은 이름 `fk_holidays_calendar_year`를 직접 지정**한다(PR-1 §8 ④ 실측).
■ 쓰기 통로는 `PUT /holidays/{country}/{year}` 하나(원자 교체 — 연도 선언 upsert + 기존 휴일 soft delete + 새 휴일 INSERT가 1TX, ADMIN 전용).
  둘 다 MUTABLE(교체가 앱 계정의 정상 UPDATE·soft delete) — 변경 이력의 정본은 audit_log `holidays.calendar.replaced`다. **시드 0**.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.holidays.calc import COUNTRY_PATTERN, YEAR_MAX, YEAR_MIN

SOURCE_URL_MAX = 500
HOLIDAY_NAME_MAX = 100

#: 제약·인덱스 이름(서비스의 제약명 → 업무 오류 번역표가 이 이름을 쓴다 — 실제 DB 이름과 대사하는 시험이 있다).
CALENDAR_YEAR_UNIQUE = "uq_holiday_calendar_years_country_code_year_active"
CALENDAR_YEAR_KEY = "uq_holiday_calendar_years_id_country_code_year"
HOLIDAY_DAY_UNIQUE = "uq_holidays_country_code_holiday_on_active"
HOLIDAY_CALENDAR_FK = "fk_holidays_calendar_year"


class HolidayCalendarYear(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    __tablename__ = "holiday_calendar_years"

    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: 근거 링크(관보·정부 공고 등 — http(s)).
    source_url: Mapped[str] = mapped_column(String(SOURCE_URL_MAX), nullable=False)
    #: 근거를 마지막으로 확인한 날(KST 업무 날짜 — 미래 불가는 서비스가 `today_kst()`로 검증).
    verified_on: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint(f"country_code ~ '{COUNTRY_PATTERN}'", name="country_code_format"),
        CheckConstraint(f"year BETWEEN {YEAR_MIN} AND {YEAR_MAX}", name="year_range"),
        CheckConstraint("source_url ~ '^https?://'", name="source_url_http"),
        CheckConstraint("source_url !~ '[[:cntrl:][:space:]]'", name="source_url_clean"),
        unique_active("holiday_calendar_years", "country_code", "year"),
        # 복합 FK 대상(R-24) — holidays가 (선언 id, 국가, 연도) 세 값을 함께 가리켜 정합을 DB가 강제한다.
        UniqueConstraint("id", "country_code", "year"),
    )


class Holiday(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    __tablename__ = "holidays"

    calendar_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    holiday_on: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(HOLIDAY_NAME_MAX), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["calendar_year_id", "country_code", "year"],
            [
                "holiday_calendar_years.id",
                "holiday_calendar_years.country_code",
                "holiday_calendar_years.year",
            ],
            name=HOLIDAY_CALENDAR_FK,
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"country_code ~ '{COUNTRY_PATTERN}'", name="country_code_format"),
        CheckConstraint("extract(year FROM holiday_on) = year", name="day_in_year"),
        CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
        CheckConstraint("name !~ '[[:cntrl:]]'", name="name_clean"),
        unique_active("holidays", "country_code", "holiday_on"),
        Index("ix_holidays_calendar_year_id", "calendar_year_id"),
    )
