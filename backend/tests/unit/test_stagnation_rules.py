"""C·H. 정체 판정의 순수 함수 — N 정규화·독촉 몫·마지막 활동일 (§5.4 / S2-4 PR-1 안건 ②).

DB 없이 경계를 전수한다. 스캔 통합 테스트(tests/integration/test_stagnation_scan.py)는
이 정의가 실제 행에 적용되는 모양을, 이 파일은 정의 자체를 지킨다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.modules.certifications.machine import CERTIFICATION_STATUSES, TERMINAL_STATUSES
from app.modules.certifications.models import ACTION_OWNERS
from app.modules.collaboration.stagnation import (
    DEFAULT_STAGNATION_DAYS,
    STAGNATION_STATUSES,
    _title,
    last_activity_date,
    normalize_days,
    stagnation_period,
)
from app.modules.deadlines.service import ALERT_TITLE_MAX

pytestmark = [pytest.mark.group_c, pytest.mark.group_h]


# ── N 정규화 (데이터 — ADR-11) ────────────────────────────────────────────


def test_defaults_cover_every_action_owner() -> None:
    """기본 N은 모든 액션 주체에 있다 — 열거가 늘면 이 표도 늘어야 한다(KeyError 방지)"""
    assert set(DEFAULT_STAGNATION_DAYS) == set(ACTION_OWNERS)
    assert DEFAULT_STAGNATION_DAYS == {"INTERNAL": 7, "AGENCY": 7, "AUTHORITY": 30}


@pytest.mark.parametrize("raw", [None, [], "7", 7, True, {}, {"UNKNOWN": 3}])
def test_missing_or_malformed_config_falls_back_to_defaults(raw: object) -> None:
    """config가 없거나 형이 흐리면 전부 기본값 — 스캔이 서지 않는다"""
    assert normalize_days(raw) == DEFAULT_STAGNATION_DAYS


def test_a_partial_override_changes_only_the_named_owner() -> None:
    """일부 주체만 덮어써도 나머지는 기본값 — 한 키가 다른 키를 무효로 만들지 않는다"""
    assert normalize_days({"INTERNAL": 3}) == {"INTERNAL": 3, "AGENCY": 7, "AUTHORITY": 30}


@pytest.mark.parametrize("bad", [0, -1, True, False, "5", 2.5, None, [3]])
def test_an_invalid_value_falls_back_for_that_key_only(bad: object) -> None:
    """0·음수·bool·문자열·소수·None은 그 키만 기본값 — 다른 키의 정당한 덮어쓰기는 유지"""
    days = normalize_days({"INTERNAL": bad, "AGENCY": 2})
    assert days["INTERNAL"] == 7 and days["AGENCY"] == 2


def test_the_result_never_mutates_the_module_defaults() -> None:
    """정규화 결과를 고쳐도 모듈 기본값은 오염되지 않는다(공유 가변 상태 금지)"""
    days = normalize_days(None)
    days["INTERNAL"] = 99
    assert DEFAULT_STAGNATION_DAYS["INTERNAL"] == 7


# ── 독촉 몫 k ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stagnant", "n", "expected"),
    [
        (0, 7, 0),
        (6, 7, 0),  # N-1일 — 아직 정체가 아니다
        (7, 7, 1),  # N일째 — 경계 포함
        (13, 7, 1),
        (14, 7, 2),  # 두 번째 몫
        (30, 30, 1),
        (59, 30, 1),
        (60, 30, 2),
        (-3, 7, -1),  # 활동일이 미래(시계 어긋남) — 정체 아님으로 걸러진다(k<1)
    ],
)
def test_the_period_counts_whole_thresholds(stagnant: int, n: int, expected: int) -> None:
    """k = 정체일 ÷ N의 몫 — N일마다 1씩 오르고 N-1일까지는 0"""
    assert stagnation_period(stagnant, n) == expected


# ── 마지막 활동일 ──────────────────────────────────────────────────────────

CREATED = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)  # KST 2026-09-01 12:00


def test_without_any_activity_the_creation_date_counts() -> None:
    """공이 넘어간 날·상태 이력·통신 기록이 모두 없으면 생성일(KST) — 기록 없는 행이 안 빠진다"""
    assert last_activity_date(
        owner_changed_on=None,
        created_at=CREATED,
        status_changed_at=None,
        comm_activity_on=None,
    ) == date(2026, 9, 1)


def test_the_creation_date_is_a_kst_date_not_a_utc_date() -> None:
    """UTC 15:30은 KST 다음 날 00:30 — 생성일은 업무일(KST)로 읽는다 (§22 렌즈 6)"""
    late = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
    assert last_activity_date(
        owner_changed_on=None, created_at=late, status_changed_at=None, comm_activity_on=None
    ) == date(2026, 9, 2)


def test_the_ball_date_replaces_the_creation_date_even_when_earlier() -> None:
    """공이 넘어간 날이 기록돼 있으면 생성일 대신 그것을 쓴다(더 이른 날이어도 — 후보의 하나일 뿐이 아니다)"""
    # 생성일 대체 규칙: owner_changed_on이 있으면 생성일은 후보에서 빠진다
    assert last_activity_date(
        owner_changed_on=date(2026, 8, 1),
        created_at=CREATED,
        status_changed_at=None,
        comm_activity_on=None,
    ) == date(2026, 8, 1)


@pytest.mark.parametrize(
    ("owner", "status_at", "comm", "expected"),
    [
        (date(2026, 9, 10), None, None, date(2026, 9, 10)),
        (date(2026, 9, 10), datetime(2026, 9, 12, 3, 0, tzinfo=UTC), None, date(2026, 9, 12)),
        (date(2026, 9, 10), None, date(2026, 9, 15), date(2026, 9, 15)),
        (
            date(2026, 9, 10),
            datetime(2026, 9, 12, 3, 0, tzinfo=UTC),
            date(2026, 9, 11),
            date(2026, 9, 12),
        ),
        (
            date(2026, 9, 20),
            datetime(2026, 9, 12, 3, 0, tzinfo=UTC),
            date(2026, 9, 11),
            date(2026, 9, 20),
        ),
    ],
)
def test_the_latest_of_the_sources_wins(
    owner: date, status_at: datetime | None, comm: date | None, expected: date
) -> None:
    """공 이동·상태 변경·통신 기록 중 가장 늦은 날이 마지막 활동일이다"""
    assert (
        last_activity_date(
            owner_changed_on=owner,
            created_at=CREATED,
            status_changed_at=status_at,
            comm_activity_on=comm,
        )
        == expected
    )


def test_a_status_change_is_read_in_kst() -> None:
    """상태 변경 시각도 KST 업무일로 읽는다 — UTC 15:30 → 다음 날"""
    assert last_activity_date(
        owner_changed_on=date(2026, 9, 1),
        created_at=CREATED,
        status_changed_at=datetime(2026, 9, 10, 15, 30, tzinfo=UTC),
        comm_activity_on=None,
    ) == date(2026, 9, 11)


# ── 대상 상태 ──────────────────────────────────────────────────────────────


def test_the_stagnation_statuses_are_the_in_progress_five() -> None:
    """정체 대상은 공이 어딘가에 있는 진행 중 5태 — 미착수·승인·만료계·종결은 제외 (정의 고정)"""
    assert set(STAGNATION_STATUSES) == {
        "PREPARING",
        "SUBMITTED",
        "IN_REVIEW",
        "SUPPLEMENTING",
        "RENEWING",
    }
    assert set(STAGNATION_STATUSES) <= set(CERTIFICATION_STATUSES)
    assert not set(STAGNATION_STATUSES) & set(TERMINAL_STATUSES)
    assert "NOT_STARTED" not in STAGNATION_STATUSES  # 자동 적용분 대량 소음 — 매트릭스 🔴가 신호


def test_long_titles_are_truncated_to_the_column_width() -> None:
    """알림 제목은 컬럼 길이(200)를 넘지 않는다 — 넘치면 그 건만 영구 미발송(기일 스캔 적대 검증 계보)"""
    assert _title("가" * 500) == "가" * (ALERT_TITLE_MAX - 1) + "…"
    assert len(_title("가" * 500)) == ALERT_TITLE_MAX
    assert _title("짧은 제목") == "짧은 제목"
