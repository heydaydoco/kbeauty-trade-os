"""J·K·A. 휴일 캘린더 API — 원자 교체 멱등·1TX·동시 최초 선언·권한·페이지·CSV (S3-2 PR-2a / ADR-0079·0082 / design-D §D2-3 H1~H5).

쓰기 통로는 `PUT /holidays/{country}/{year}` 하나(ADMIN). 미선언(calendar=null)과 빈 선언(0건 = '휴일 없음 확인')은 다르다 —
판정 UNVERIFIED와 CLEAR의 데이터 근거다(GC-A19 — 데이터 → `calc.holiday_flag`).
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.main import app
from app.modules.audit.models import AuditAction
from app.modules.holidays import service
from app.modules.holidays.calc import HolidayFlag, holiday_flag
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from tests.support.concurrency import run_concurrently
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_j

BASE = "/api/v1/holidays"
SOURCE = "https://www.gov.cn/zhengce/holidays-2026"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        yield client


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("hol-admin@example.com", RoleCode.ADMIN)


@pytest.fixture
def logistics() -> Iterator[TestClient]:
    yield from _client("hol-logistics@example.com", RoleCode.LOGISTICS)


def _body(
    holidays: list[tuple[str, str]],
    *,
    version: int | None = None,
    source_url: str | None = SOURCE,
    verified_on: str | None = "2026-09-01",
) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "verified_on": verified_on,
        "holidays": [{"holiday_on": d, "name": n} for d, n in holidays],
        "version": version,
    }


def _put(client: TestClient, country: str, year: int, body: dict[str, Any], *, key: str) -> Any:
    return client.put(f"{BASE}/{country}/{year}", json=body, headers={"Idempotency-Key": key})


CN_2026 = [("2026-10-01", "국경절"), ("2026-10-02", "국경절 연휴"), ("2026-02-17", "춘절")]


def _live(country: str, year: int) -> list[tuple[str, str]]:
    with owner_engine.connect() as connection:
        return [
            (r[0].isoformat(), r[1])
            for r in connection.execute(
                text(
                    "SELECT holiday_on, name FROM holidays WHERE country_code = :c AND year = :y"
                    " AND deleted_at IS NULL ORDER BY holiday_on"
                ),
                {"c": country, "y": year},
            )
        ]


def _audits() -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            r[0]
            for r in connection.execute(
                text("SELECT detail FROM audit_log WHERE action = :a ORDER BY id"),
                {"a": AuditAction.HOLIDAYS_CALENDAR_REPLACED},
            )
        ]


# ── J 안전 계약 — 멱등·원자·동시성·낙관 잠금 ─────────────────────────────────


def test_first_declaration_then_atomic_replace_with_version(admin: TestClient) -> None:
    """최초 선언(version=null) → 3건, 교체(version=1) → 정확히 새 집합(지운 날짜는 soft delete·새 날짜 INSERT)·version 2·audit 2행"""
    created = _put(admin, "CN", 2026, _body(CN_2026), key="h-1")
    assert created.status_code == 200, created.text
    first = created.json()
    assert (first["country_code"], first["year"], first["holiday_count"], first["version"]) == (
        "CN",
        2026,
        3,
        1,
    )
    assert first["source_url"] == SOURCE and first["verified_on"] == "2026-09-01"
    assert first["updated_by_name"]
    replaced = _put(
        admin,
        "CN",
        2026,
        _body([("2026-10-01", "국경절"), ("2026-10-08", "중추절")], version=1),
        key="h-2",
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["version"] == 2 and replaced.json()["holiday_count"] == 2
    assert _live("CN", 2026) == [("2026-10-01", "국경절"), ("2026-10-08", "중추절")]
    with owner_engine.connect() as connection:
        deleted = connection.execute(
            text(
                "SELECT count(*) FROM holidays WHERE country_code = 'CN' AND deleted_at IS NOT NULL"
            )
        ).scalar_one()
    assert deleted == 3  # 교체는 기존 행 soft delete + 새 행 INSERT(부활 아님)
    details = _audits()
    assert len(details) == 2 and details[0]["first_declaration"] is True
    assert details[1] | {} == details[1] and details[1]["added"] == ["2026-10-08"]
    assert details[1]["removed"] == ["2026-02-17", "2026-10-02"]
    assert details[1]["before_count"] == 3 and details[1]["after_count"] == 2
    assert details[1]["previous_source"] == {"source_url": SOURCE, "verified_on": "2026-09-01"}


def test_same_idempotency_key_replaces_once(admin: TestClient) -> None:
    """J — 같은 키로 2회(더블클릭) → 교체 1회·같은 응답·audit 1행 · 다른 키로 같은 본문 재전송은 version 409(이미 선언됨)"""
    first = _put(admin, "JP", 2026, _body([("2026-01-01", "元日")]), key="h-dup")
    again = _put(admin, "JP", 2026, _body([("2026-01-01", "元日")]), key="h-dup")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(_audits()) == 1 and len(_live("JP", 2026)) == 1
    stale = _put(admin, "JP", 2026, _body([("2026-01-01", "元日")]), key="h-dup-2")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "COMMON.CONCURRENCY.VERSION_CONFLICT"


def test_stale_version_is_409_and_changes_nothing(admin: TestClient) -> None:
    """낙관 잠금 — 선언이 있을 때 version 불일치(옛 값·null)는 409이고 휴일·근거는 그대로"""
    assert _put(admin, "VN", 2026, _body([("2026-04-30", "통일의 날")]), key="v-1").is_success
    assert _put(
        admin, "VN", 2026, _body([], version=1), key="v-2"
    ).is_success  # 0건 = 휴일 없음 확인
    for version, key in ((1, "v-3"), (None, "v-4"), (99, "v-5")):
        response = _put(
            admin, "VN", 2026, _body([("2026-09-02", "국경일")], version=version), key=key
        )
        assert response.status_code == 409, (version, response.text)
    assert _live("VN", 2026) == []
    listed = admin.get(BASE, params={"country": "VN", "year": 2026}).json()
    assert listed["calendar"]["version"] == 2 and listed["calendar"]["holiday_count"] == 0


def test_replace_is_one_transaction(admin: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """J 1TX — 교체 도중(새 휴일 INSERT 뒤 감사 기록에서) 실패하면 soft delete·INSERT·근거 갱신·version이 전부 롤백된다(부분 교체 0)"""
    assert _put(admin, "TH", 2026, _body([("2026-04-13", "송끄란")]), key="t-1").is_success

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("감사 기록 실패(주입)")

    monkeypatch.setattr(service.audit, "record", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.update(admin.cookies)
        failed = _put(
            client,
            "TH",
            2026,
            _body([("2026-12-05", "국왕 탄신일")], version=1, source_url="https://new.example"),
            key="t-2",
        )
    assert failed.status_code == 500
    assert _live("TH", 2026) == [("2026-04-13", "송끄란")]
    with owner_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT version, source_url, (SELECT count(*) FROM holidays h WHERE h.country_code = 'TH'"
                " AND h.deleted_at IS NOT NULL) FROM holiday_calendar_years WHERE country_code = 'TH'"
            )
        ).one()
    assert tuple(row) == (1, SOURCE, 0)


def _race_actor(email: str) -> AuthenticatedUser:
    admin_id = create_user(email, roles=(RoleCode.ADMIN,))
    return AuthenticatedUser(
        id=admin_id,
        email=email,
        display_name="race",
        roles=frozenset({RoleCode.ADMIN}),
        session_id=0,
    )


def _first_declaration(actor: AuthenticatedUser, key: str, country: str) -> int:
    status, _ = service.replace_calendar(
        actor=actor,
        idempotency_key=key,
        country=country,
        year=2026,
        payload={
            "source_url": SOURCE,
            "verified_on": date(2026, 9, 1),
            "holidays": [{"holiday_on": date(2026, 8, 31), "name": f"독립기념일 {key}"}],
            "version": None,
        },
    )
    return status


@pytest.mark.concurrency
def test_concurrent_first_declarations_one_wins_and_the_other_is_409() -> None:
    """X-22 — 같은 국가·연도를 동시에 처음 선언(다른 키 2건, 실제 동시 실행) → 1건 성공·1건 409(500·이중 선언 0).

    진 쪽의 코드는 도착 시점에 따라 둘 중 하나다: 이긴 쪽 커밋 **전**에 INSERT했으면 부분 유니크 대기 후 `YEAR_DUPLICATE`,
    커밋 **후**에 읽었으면 선언이 보이므로 version(null) 불일치 `VERSION_CONFLICT`. 유니크 경합 경로 자체는 아래 결정적 시험이 고정한다."""
    actor = _race_actor("hol-race@example.com")
    outcomes = run_concurrently(lambda i: _first_declaration(actor, f"race-{i}", "MY"), workers=2)
    ok = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]
    assert len(ok) == 1 and len(failed) == 1, [o.error for o in outcomes]
    assert getattr(failed[0].error, "status_code", None) == 409
    assert getattr(failed[0].error, "code", None) in {
        "HOLIDAYS.CALENDAR.YEAR_DUPLICATE",
        "COMMON.CONCURRENCY.VERSION_CONFLICT",
    }
    with owner_engine.connect() as connection:
        declared = connection.execute(
            text("SELECT count(*) FROM holiday_calendar_years WHERE country_code = 'MY'")
        ).scalar_one()
    assert declared == 1 and len(_live("MY", 2026)) == 1


@pytest.mark.concurrency
def test_first_declaration_blocked_on_an_uncommitted_rival_becomes_year_duplicate_409() -> None:
    """X-22 결정적 — 경쟁 트랜잭션이 같은 국가·연도 선언을 INSERT하고 커밋 전이면 서비스의 INSERT는 부분 유니크에서 대기하고,
    경쟁자가 커밋하면 23505 → 409 `HOLIDAYS.CALENDAR.YEAR_DUPLICATE`로 번역된다(500 아님·휴일 INSERT 0)"""
    actor = _race_actor("hol-race2@example.com")
    with owner_engine.connect() as rival:
        transaction = rival.begin()
        rival.execute(
            text(
                "INSERT INTO holiday_calendar_years (country_code, year, source_url, verified_on)"
                " VALUES ('BN', 2026, :u, DATE '2026-09-01')"
            ),
            {"u": SOURCE},
        )
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_first_declaration, actor, "blocked", "BN")
        deadline = time.monotonic() + 10
        # 서비스 쪽 INSERT가 경쟁자의 유니크 키에서 실제로 대기하는지(미부여 잠금) 확인한 뒤 커밋한다
        while time.monotonic() < deadline:
            with owner_engine.connect() as probe:
                waiting = probe.execute(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                ).scalar_one()
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, "서비스 INSERT가 경쟁 트랜잭션의 유니크 키를 기다리지 않았다"
        transaction.commit()
        with pytest.raises(AppError) as caught:
            future.result(timeout=10)
        pool.shutdown()
    assert caught.value.code == "HOLIDAYS.CALENDAR.YEAR_DUPLICATE"
    assert caught.value.status_code == 409
    assert _live("BN", 2026) == []


# ── K 입력·권한·페이지·CSV ───────────────────────────────────────────────────


@pytest.mark.group_k
@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_body(CN_2026, source_url=None), "source_url"),
        (_body(CN_2026, source_url="   "), "source_url"),
        (_body(CN_2026, source_url="ftp://gov.cn/x"), "source_url"),
        (_body(CN_2026, source_url="www.gov.cn/x"), "source_url"),
        (_body(CN_2026, source_url="https://gov.cn/a b"), "source_url"),
        (_body(CN_2026, verified_on=None), "verified_on"),
    ],
)
def test_source_link_and_verified_on_are_required(
    admin: TestClient, body: dict[str, Any], field: str
) -> None:
    """근거 2필드 필수(ADR-03) — 결측·공백·http(s) 아님·공백 포함 → 422 SOURCE_REQUIRED(detail에 필드), 저장 0"""
    response = _put(admin, "CN", 2026, body, key=f"src-{field}-{body['source_url']}")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "HOLIDAYS.CALENDAR.SOURCE_REQUIRED" and field in error["detail"]
    assert admin.get(BASE, params={"country": "CN", "year": 2026}).json()["calendar"] is None


@pytest.mark.group_k
def test_verified_on_cannot_be_in_the_future_kst(admin: TestClient) -> None:
    """확인일 ≤ 오늘(KST) — 내일은 422 SOURCE_REQUIRED, 오늘은 통과(경계)"""
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    response = _put(admin, "SG", 2026, _body([], verified_on=tomorrow), key="fut-1")
    assert response.status_code == 422 and "verified_on" in response.json()["error"]["detail"]
    ok = _put(admin, "SG", 2026, _body([], verified_on=today_kst().isoformat()), key="fut-2")
    assert ok.status_code == 200, ok.text


@pytest.mark.group_k
def test_year_mismatch_duplicate_date_and_bad_names_are_422(admin: TestClient) -> None:
    """다른 연도 날짜 → 422 YEAR_MISMATCH(detail 날짜) · 같은 날짜 2건 → 422 DUPLICATE_DATE · 공백/탭 이름 → 422 · 저장 0"""
    wrong = _put(admin, "CN", 2026, _body([("2027-01-01", "원단")]), key="m-1")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "HOLIDAYS.CALENDAR.YEAR_MISMATCH"
    assert wrong.json()["error"]["detail"] == {"holiday_on": ["2027-01-01"]}
    dup = _put(admin, "CN", 2026, _body([("2026-10-01", "a"), ("2026-10-01", "b")]), key="m-2")
    assert dup.status_code == 422
    assert dup.json()["error"]["code"] == "HOLIDAYS.CALENDAR.DUPLICATE_DATE"
    assert dup.json()["error"]["detail"] == {"holiday_on": ["2026-10-01"]}  # 화면이 칸에 붙일 날짜
    for index, name in enumerate(("   ", "탭\t휴일", "줄\n바꿈", "제로폭\u200b")):
        bad = _put(admin, "CN", 2026, _body([("2026-10-01", name)]), key=f"m-n{index}")
        assert bad.status_code == 422, name
    assert admin.get(BASE, params={"country": "CN", "year": 2026}).json()["calendar"] is None


@pytest.mark.group_k
@pytest.mark.parametrize("country", ["cn", "CHN", "C1", "Cn", "C"])
def test_country_must_be_iso_alpha2_upper(admin: TestClient, country: str) -> None:
    """국가 = ISO alpha-2 대문자(markets FK 아님) — 소문자·3자·숫자·1자는 422 COUNTRY.INVALID(조용히 대문자화하지 않는다)"""
    response = _put(admin, country, 2026, _body([]), key=f"c-{country}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "HOLIDAYS.COUNTRY.INVALID"
    listed = admin.get(BASE, params={"country": country, "year": 2026})
    assert listed.json()["error"]["code"] == "HOLIDAYS.COUNTRY.INVALID"


@pytest.mark.group_k
@pytest.mark.parametrize("year", [1999, 3000])
def test_year_must_be_in_the_declarable_range(admin: TestClient, year: int) -> None:
    """연도 2000~2999 — 밖은 422"""
    response = _put(admin, "CN", year, _body([]), key=f"y-{year}")
    assert response.status_code == 422


@pytest.mark.group_k
def test_writes_are_admin_only_and_role_check_comes_first(logistics: TestClient) -> None:
    """쓰기·CSV 미리보기 = ADMIN 전용 — 물류는 403(입력이 틀려도 403이 먼저: 401→403→404→409→422), 조회는 통과"""
    assert _put(logistics, "CN", 2026, _body(CN_2026), key="r-1").status_code == 403
    assert _put(logistics, "cn", 1999, {"bogus": 1}, key="r-2").status_code == 403
    files = {"file": ("h.csv", b"holiday_on,name\n", "text/csv")}
    assert logistics.post(f"{BASE}/CN/2026/import-csv/preview", files=files).status_code == 403
    assert logistics.get(f"{BASE}/calendars").status_code == 200
    assert logistics.get(BASE, params={"country": "CN", "year": 2026}).status_code == 200


@pytest.mark.group_k
def test_undeclared_is_null_calendar_and_empty_declaration_is_not(
    admin: TestClient, logistics: TestClient
) -> None:
    """H2 — 미선언은 calendar=null(UNVERIFIED 근거), 0건 선언은 calendar 있음·items 빈 목록(CLEAR 근거) · H1 필터"""
    undeclared = logistics.get(BASE, params={"country": "KH", "year": 2026}).json()
    assert undeclared["calendar"] is None and undeclared["items"] == [] and undeclared["total"] == 0
    assert _put(admin, "KH", 2026, _body([]), key="e-1").is_success
    empty = logistics.get(BASE, params={"country": "KH", "year": 2026}).json()
    assert empty["calendar"]["holiday_count"] == 0 and empty["items"] == []
    assert _put(admin, "KH", 2027, _body([("2027-01-01", "신정")]), key="e-2").is_success
    assert _put(admin, "AE", 2027, _body([]), key="e-3").is_success
    calendars = logistics.get(f"{BASE}/calendars").json()
    assert [(c["country_code"], c["year"]) for c in calendars["items"]] == [
        ("AE", 2027),
        ("KH", 2027),
        ("KH", 2026),
    ]
    only_kh = logistics.get(f"{BASE}/calendars", params={"country": "KH", "year": 2027}).json()
    assert only_kh["total"] == 1 and only_kh["items"][0]["holiday_count"] == 1
    assert logistics.get(f"{BASE}/calendars", params={"country": "kh"}).status_code == 422


@pytest.mark.group_k
def test_holiday_list_is_paginated_with_default_50(admin: TestClient) -> None:
    """H2 페이지네이션 — 60건 선언 → 기본 50·2쪽 10·날짜순"""
    start = date(2026, 1, 1)
    days = [((start + timedelta(days=i)).isoformat(), f"휴일 {i}") for i in range(60)]
    assert _put(admin, "LA", 2026, _body(days), key="p-1").is_success
    page1 = admin.get(BASE, params={"country": "LA", "year": 2026}).json()
    assert page1["size"] == 50 and len(page1["items"]) == 50 and page1["total"] == 60
    assert page1["items"][0]["holiday_on"] == "2026-01-01"
    page2 = admin.get(BASE, params={"country": "LA", "year": 2026, "page": 2}).json()
    assert len(page2["items"]) == 10 and page2["items"][-1]["holiday_on"] == "2026-03-01"


@pytest.mark.group_k
def test_export_csv_has_bom_escapes_formulas_and_round_trips_through_preview(
    admin: TestClient, logistics: TestClient
) -> None:
    """H4 CSV — UTF-8 BOM·머리글 holiday_on,name·수식 셀 이스케이프 · 그 파일을 미리보기에 올리면 원값 복원·문제 0(왕복) · 미선언 404"""
    names = [("2026-05-01", '=HYPERLINK("x")'), ("2026-05-02", "노동절")]
    assert _put(admin, "CN", 2026, _body(names), key="x-1").is_success
    exported = logistics.get(f"{BASE}/CN/2026/export.csv")
    assert exported.status_code == 200
    raw = exported.content
    assert raw.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == ["holiday_on", "name"]
    assert rows[1] == ["2026-05-01", '\'=HYPERLINK("x")']
    preview = admin.post(
        f"{BASE}/CN/2026/import-csv/preview", files={"file": ("휴일.csv", raw, "text/csv")}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "rows": [{"holiday_on": d, "name": n} for d, n in names],
        "problems": [],
    }
    assert logistics.get(f"{BASE}/JP/2031/export.csv").status_code == 404


@pytest.mark.group_k
def test_csv_preview_reports_row_problems_and_saves_nothing(admin: TestClient) -> None:
    """H5 — 행 문제(날짜 형식·연도·중복·빈 이름·열 수)는 problems로, 저장은 0(비저장 — 쓰기 통로는 PUT 하나)"""
    body = (
        "holiday_on,name\n"
        "2026-10-01,국경절\n"
        "2026/10/02,연휴\n"
        "2027-01-01,원단\n"
        "2026-10-01,중복\n"
        "2026-10-03,\n"
        "2026-10-04,a,b\n"
        "2026-W02-1,주차표기\n"
        "20260105T0,기본형\n"
        "\n"
    ).encode()
    response = admin.post(
        f"{BASE}/CN/2026/import-csv/preview", files={"file": ("h.csv", body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    got = response.json()
    assert [r["holiday_on"] for r in got["rows"]] == [
        "2026-10-01",
        "2027-01-01",
        "2026-10-01",
        "2026-10-03",
    ]
    assert {(p["row"], p["field"]) for p in got["problems"]} == {
        (2, "holiday_on"),
        (3, "holiday_on"),
        (4, "holiday_on"),
        (5, "name"),
        (6, "row"),
        (7, "holiday_on"),  # ISO 주차 표기 — 다른 날짜로 정규화되지 않는다
        (8, "holiday_on"),  # 기본형+시각 꼬리
    }
    assert _live("CN", 2026) == [] and _audits() == []


@pytest.mark.group_k
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        "날짜,이름\n2026-10-01,국경절\n".encode(),
        "holiday_on,name\n2026-10-01,국경절\n".encode("cp949"),
        b"holiday_on,name\n2026-10-01,\x00x\n",
        b'holiday_on,name\n2026-10-01,"unclosed\n',
        b"holiday_on,name\n" + b"2026-01-01,x\n" * 401,
    ],
)
def test_csv_preview_rejects_bad_files_whole(admin: TestClient, raw: bytes) -> None:
    """H5 파일 단위 거부 — 빈 파일·머리글 불일치·UTF-8 아님(CP949)·NUL·CSV 문법·행 초과 → 422 CSV.INVALID_FORMAT"""
    response = admin.post(
        f"{BASE}/CN/2026/import-csv/preview", files={"file": ("h.csv", raw, "text/csv")}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "HOLIDAYS.CSV.INVALID_FORMAT"


@pytest.mark.group_k
def test_reads_are_one_snapshot_and_refuse_to_join_an_open_transaction() -> None:
    """조회(H1·H2·H4)는 독립 읽기 트랜잭션의 첫 문장에서 `REPEATABLE READ, READ ONLY` — 선언 version과 휴일 행이 한 스냅샷.
    바깥 트랜잭션에 합류하면(첫 문장 보장 불가) 프로그래밍 오류로 멈춘다"""
    from app.core.db.uow import unit_of_work

    assert (
        service.SNAPSHOT_STATEMENT == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    calls = (
        lambda: service.list_calendars(country=None, year=None, offset=0, limit=50),
        lambda: service.list_holidays(country="CN", year=2026, offset=0, limit=50),
        lambda: service.export_rows(country="CN", year=2026),
    )
    for call in calls:
        with unit_of_work(), pytest.raises(RuntimeError):
            call()
    assert service.list_holidays(country="CN", year=2026, offset=0, limit=50) == (None, [], 0)


# ── A·GC-A19 — 저장 데이터 → 판정 (함수 층 배선 확인) ─────────────────────────


@pytest.mark.group_a
@pytest.mark.golden
def test_gc_a19_declared_data_drives_holiday_unverified_and_clear(admin: TestClient) -> None:
    """GC-A19 — CN 2026 선언(10-01 국경절) 데이터로 판정: 10-01 HOLIDAY('국경절') / 10-09 CLEAR / 2027 미선언 UNVERIFIED"""
    assert _put(admin, "CN", 2026, _body(CN_2026), key="g-1").is_success
    calendars = admin.get(f"{BASE}/calendars", params={"country": "CN"}).json()["items"]
    covered = {c["year"] for c in calendars}
    listed = admin.get(BASE, params={"country": "CN", "year": 2026}).json()["items"]
    days = {date.fromisoformat(h["holiday_on"]): h["name"] for h in listed}
    assert holiday_flag(date(2026, 10, 1), "CN", covered, days) == (HolidayFlag.HOLIDAY, "국경절")
    assert holiday_flag(date(2026, 10, 9), "CN", covered, days) == (HolidayFlag.CLEAR, None)
    assert holiday_flag(date(2027, 1, 4), "CN", covered, days) == (HolidayFlag.UNVERIFIED, None)
