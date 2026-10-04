"""K·J. 휴일 캘린더 2표(M13)의 불변식을 DB가 강제한다 (S3-2 PR-2a / ADR-0082 / design-integrated §9 R-24).

★ 서비스를 거치지 않고 raw INSERT로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 위반마다 양성 대조 INSERT가 성공함을 함께 본다.
핵심(R-24): **연도·국가 정합은 DB가 강제** — 휴일의 (선언 id, 국가, 연도)가 선언과 다르면 복합 FK가, 날짜의 연도가 `year`와 다르면 CHECK가 거부한다.
서비스 결함 1건이 다른 국가·연도 선언에 휴일을 붙여 UNVERIFIED/CLEAR 판정을 조용히 틀어지게 하는 길이 DB에서 닫혔는지 확인한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.constraints import MAX_IDENTIFIER_LENGTH
from app.core.db.session import owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES
from app.modules.holidays.models import (
    CALENDAR_YEAR_KEY,
    CALENDAR_YEAR_UNIQUE,
    HOLIDAY_CALENDAR_FK,
    HOLIDAY_DAY_UNIQUE,
)

pytestmark = pytest.mark.group_k

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


def _year(
    country: str,
    year: int,
    *,
    url: str = "https://example.gov/holidays",
    deleted: bool = False,
) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO holiday_calendar_years (country_code, year, source_url, verified_on, deleted_at)"
                    " VALUES (:c, :y, :u, DATE '2026-09-01', CASE WHEN :d THEN now() END) RETURNING id"
                ),
                {"c": country, "y": year, "u": url, "d": deleted},
            ).scalar_one()
        )


def _holiday(
    calendar_id: int,
    country: str,
    year: int,
    day: date,
    name: str = "휴일",
    *,
    deleted: bool = False,
) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO holidays (calendar_year_id, country_code, year, holiday_on, name, deleted_at)"
                    " VALUES (:k, :c, :y, :d, :n, CASE WHEN :x THEN now() END) RETURNING id"
                ),
                {"k": calendar_id, "c": country, "y": year, "d": day, "n": name, "x": deleted},
            ).scalar_one()
        )


@pytest.fixture(autouse=True)
def _clean() -> None:
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM holidays"))
        connection.execute(text("DELETE FROM holiday_calendar_years"))


@pytest.mark.parametrize(
    ("country", "year", "url", "constraint"),
    [
        ("cn", 2026, "https://a.example", "ck_holiday_calendar_years_country_code_format"),
        ("C ", 2026, "https://a.example", "ck_holiday_calendar_years_country_code_format"),
        ("C1", 2026, "https://a.example", "ck_holiday_calendar_years_country_code_format"),
        ("CN", 1999, "https://a.example", "ck_holiday_calendar_years_year_range"),
        ("CN", 3000, "https://a.example", "ck_holiday_calendar_years_year_range"),
        ("CN", 2026, "ftp://a.example", "ck_holiday_calendar_years_source_url_http"),
        ("CN", 2026, "관보2026", "ck_holiday_calendar_years_source_url_http"),
        ("CN", 2026, "https://a.example/x y", "ck_holiday_calendar_years_source_url_clean"),
    ],
)
def test_calendar_year_checks_refuse_bad_rows(
    country: str, year: int, url: str, constraint: str
) -> None:
    """선언 CHECK — 국가 ISO alpha-2 대문자·연도 2000~2999·근거 링크 http(s)·공백/제어문자 불가(양성 대조 통과)"""
    with pytest.raises(IntegrityError) as caught:
        _year(country, year, url=url)
    assert _state(caught.value) == (CHECK_VIOLATION, constraint)
    assert _year("CN", 2026) > 0


def test_active_declaration_is_unique_per_country_year_and_soft_delete_frees_it() -> None:
    """활성 선언 유일(부분 유니크) — 같은 국가·연도 두 번째 선언은 23505, 지운 선언은 점유하지 않는다(재유입 = 신규, J-13)"""
    first = _year("CN", 2026)
    with pytest.raises(IntegrityError) as caught:
        _year("CN", 2026)
    assert _state(caught.value) == (UNIQUE_VIOLATION, CALENDAR_YEAR_UNIQUE)
    assert _year("CN", 2027) > 0 and _year("JP", 2026) > 0
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE holiday_calendar_years SET deleted_at = now() WHERE id = :i"),
            {"i": first},
        )
    assert _year("CN", 2026) != first


def test_r24_holiday_must_match_its_declaration_country_and_year() -> None:
    """R-24 — 복합 FK: 다른 국가·다른 연도·없는 선언을 가리키는 휴일은 23503(`fk_holidays_calendar_year`) · 양성 대조 통과"""
    cn_2026 = _year("CN", 2026)
    assert _holiday(cn_2026, "CN", 2026, date(2026, 10, 1), "국경절") > 0
    for country, year, day in (
        ("JP", 2026, date(2026, 10, 2)),  # 국가 불일치 — CN 선언에 JP 휴일
        (
            "CN",
            2027,
            date(2027, 1, 1),
        ),  # 연도 불일치 — 2026 선언에 2027 휴일(날짜·year는 서로 일치)
    ):
        with pytest.raises(IntegrityError) as caught:
            _holiday(cn_2026, country, year, day)
        assert _state(caught.value) == (FK_VIOLATION, HOLIDAY_CALENDAR_FK)
    with pytest.raises(IntegrityError) as caught:
        _holiday(987_654_321, "CN", 2026, date(2026, 10, 3))
    assert _state(caught.value) == (FK_VIOLATION, HOLIDAY_CALENDAR_FK)


def test_r24_holiday_date_must_fall_in_its_year() -> None:
    """R-24 — CHECK `extract(year FROM holiday_on) = year`: 2026 선언에 2027-01-01 날짜(year=2026)는 23514 · 12-31 경계는 통과"""
    cn_2026 = _year("CN", 2026)
    with pytest.raises(IntegrityError) as caught:
        _holiday(cn_2026, "CN", 2026, date(2027, 1, 1))
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_holidays_day_in_year")
    with pytest.raises(IntegrityError) as caught:
        _holiday(cn_2026, "CN", 2026, date(2025, 12, 31))
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_holidays_day_in_year")
    assert _holiday(cn_2026, "CN", 2026, date(2026, 12, 31)) > 0
    assert _holiday(cn_2026, "CN", 2026, date(2026, 1, 1)) > 0


@pytest.mark.parametrize(
    ("name", "constraint"),
    [("   ", "ck_holidays_name_nonblank"), ("탭\t휴일", "ck_holidays_name_clean")],
)
def test_holiday_name_checks(name: str, constraint: str) -> None:
    """휴일 이름 — 공백뿐·제어문자는 23514"""
    cn = _year("CN", 2026)
    with pytest.raises(IntegrityError) as caught:
        _holiday(cn, "CN", 2026, date(2026, 5, 1), name)
    assert _state(caught.value) == (CHECK_VIOLATION, constraint)


def test_active_holiday_day_is_unique_per_country_and_soft_delete_frees_it() -> None:
    """활성 휴일 유일(국가, 날짜) — 중복 23505, 지운 행은 점유하지 않는다(교체 = soft delete 후 새 INSERT, J-13 재유입 = 신규)"""
    cn = _year("CN", 2026)
    first = _holiday(cn, "CN", 2026, date(2026, 10, 1))
    with pytest.raises(IntegrityError) as caught:
        _holiday(cn, "CN", 2026, date(2026, 10, 1))
    assert _state(caught.value) == (UNIQUE_VIOLATION, HOLIDAY_DAY_UNIQUE)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE holidays SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    assert _holiday(cn, "CN", 2026, date(2026, 10, 1)) != first
    jp = _year("JP", 2026)
    assert _holiday(jp, "JP", 2026, date(2026, 10, 1)) > 0  # 다른 국가는 같은 날짜를 가진다


def test_definitions_are_registered_and_names_fit_the_identifier_limit() -> None:
    """등재 — 두 표 MUTABLE·시드 0 대상, CHECK 정의문·복합 FK·부분 유니크가 DB에 있고 식별자는 전부 63자 이내(복합 FK 짧은 이름)"""
    for table in ("holiday_calendar_years", "holidays"):
        assert table in MUTABLE_TABLES and table not in IMMUTABLE_TABLES
    with owner_engine.connect() as connection:
        found: dict[str, Any] = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid IN ('holidays'::regclass, 'holiday_calendar_years'::regclass)"
                )
            )
        }
        indexes = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE tablename IN ('holidays', 'holiday_calendar_years')"
                )
            )
        }
    assert "FOREIGN KEY (calendar_year_id, country_code, year)" in found[HOLIDAY_CALENDAR_FK]
    assert "holiday_calendar_years(id, country_code, year)" in found[HOLIDAY_CALENDAR_FK]
    assert "ON DELETE RESTRICT" in found[HOLIDAY_CALENDAR_FK]
    assert "UNIQUE (id, country_code, year)" in found[CALENDAR_YEAR_KEY]
    assert "EXTRACT(year FROM holiday_on)" in found["ck_holidays_day_in_year"]
    assert "2000" in found["ck_holiday_calendar_years_year_range"]
    for name in (CALENDAR_YEAR_UNIQUE, HOLIDAY_DAY_UNIQUE):
        assert "UNIQUE" in indexes[name] and "deleted_at IS NULL" in indexes[name]
    assert all(len(name) <= MAX_IDENTIFIER_LENGTH for name in [*found, *indexes])
    # markets FK가 아니다(ADR-0082) — 국가 열은 어떤 FK에도 단독으로 묶이지 않는다
    assert not any("REFERENCES markets" in definition for definition in found.values())
