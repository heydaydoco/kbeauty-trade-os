"""H·C. 정체 N일·다음 액션 독촉 스캔 (§5.4 / WBS S2-4 DoD "정체 N일 초과 → 독촉 알림").

★ 기준일은 항상 명시한다(`base_date`) — 실제 날짜에 의존하는 테스트는 어느 날 돌려도 같은
  결과여야 하고, 생성 시각(`created_at`)도 명시해 "생성일" 대체 규칙이 우연히 결과를 좌우하지
  않게 한다.
★ 스캔은 알림 생성 코어를 직접 부른다(아웃박스 비경유) — 여기서 보는 알림은 `alerts` 행이다.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.cli import main as cli_main
from app.core.db.uow import unit_of_work
from app.modules.certifications.models import Certification, CertificationStatusLog
from app.modules.collaboration import stagnation
from app.modules.collaboration.models import CommLog
from app.modules.identity.models import RoleCode
from app.modules.outbox.models import Event
from app.modules.platform import scheduler
from app.modules.worklist.models import Alert, AlertRule
from tests.support.factories import (
    create_certification_instance,
    create_partner,
    create_requirement_template,
    create_sku,
    create_user,
)

pytestmark = [pytest.mark.group_h, pytest.mark.group_c]

BASE = date(2026, 9, 30)
#: 어떤 정체 기준(≤30일)보다도 앞선 생성 시각 — 생성일 대체 규칙이 결과를 좌우하지 않게 한다.
LONG_AGO = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
_SEQ = itertools.count(1)


@pytest.fixture
def assignee() -> int:
    return create_user("stagnation-cert@example.com", roles=(RoleCode.CERT,))


@pytest.fixture
def admins() -> Iterator[tuple[int, int]]:
    yield (
        create_user("stagnation-admin-a@example.com", roles=(RoleCode.ADMIN,)),
        create_user("stagnation-admin-b@example.com", roles=(RoleCode.ADMIN,)),
    )


def _agency() -> int:
    return create_partner("AGY-STG", name_ko="정체 대행사", types=("CERT_AGENCY",))


def _cert(
    *,
    status: str = "SUBMITTED",
    owner: str = "INTERNAL",
    changed_on: date | None = None,
    created_at: datetime = LONG_AGO,
    assignee_id: int | None = None,
    agency_id: int | None = None,
) -> int:
    number = next(_SEQ)
    template_id = create_requirement_template("US", name=f"정체 요건 {number}")
    return create_certification_instance(
        template_id,
        "SKU",
        create_sku(f"SKU-STG-{number}"),
        status=status,
        action_owner=owner,
        action_owner_changed_on=changed_on,
        created_at=created_at,
        assignee_id=assignee_id,
        handling_mode="AGENCY" if agency_id is not None else "DIRECT",
        agency_partner_id=agency_id,
        lead_days=None,
    )


def _log(
    certification_id: int,
    *,
    occurred_on: date,
    next_action: str | None = None,
    due: date | None = None,
    done: date | None = None,
    deleted: bool = False,
) -> int:
    with unit_of_work() as uow:
        row = CommLog(
            subject_type="CERTIFICATION",
            subject_id=certification_id,
            occurred_on=occurred_on,
            summary="통신",
            next_action=next_action,
            next_action_due=due,
            next_action_done_on=done,
        )
        if deleted:
            from app.core.time import utcnow

            row.deleted_at = utcnow()
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _status_log(certification_id: int, at: datetime) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            CertificationStatusLog(
                certification_id=certification_id,
                occurred_at=at,
                from_status="PREPARING",
                to_status="SUBMITTED",
            )
        )


def _rule(event_type: str = stagnation.STAGNATION_EVENT, **overrides: Any) -> int:
    columns: dict[str, Any] = {
        "code": f"RULE-{event_type.replace('.', '-').upper()}",
        "name_ko": "정체 규칙",
        "event_type": event_type,
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = AlertRule(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _alerts(prefix: str = "") -> list[Alert]:
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(Alert).where(Alert.dedup_key.like(f"{prefix}%")).order_by(Alert.id)
            ).scalars()
        )
        for row in rows:
            uow.session.expunge(row)
        return rows


def _stagnation_alerts() -> list[Alert]:
    return _alerts(f"{stagnation.KIND_STAGNATION}:")


def _follow_up_alerts() -> list[Alert]:
    return _alerts(f"{stagnation.KIND_FOLLOW_UP}:")


def _scan(base: date = BASE) -> dict[str, int]:
    return stagnation.scan_stagnation(base_date=base)


# ═══ 정체 N일 ════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("owner", "days", "fires"),
    [
        ("INTERNAL", 6, False),
        ("INTERNAL", 7, True),  # 사내 기본 7일 — 경계 포함
        ("AGENCY", 6, False),
        ("AGENCY", 7, True),
        ("AUTHORITY", 7, False),  # 기관은 회신이 느리다 — 7일에는 아직
        ("AUTHORITY", 29, False),
        ("AUTHORITY", 30, True),  # 기관 기본 30일 — 경계 포함
    ],
)
def test_stagnation_fires_at_the_owner_specific_threshold(
    owner: str, days: int, fires: bool, assignee: int
) -> None:
    """정체 N일 초과 → 독촉 알림 (WBS DoD) — N은 공 주체별(사내 7·대행사 7·기관 30), 경계 포함"""
    agency = _agency() if owner == "AGENCY" else None
    _cert(
        owner=owner,
        changed_on=BASE - timedelta(days=days),
        assignee_id=assignee,
        agency_id=agency,
    )
    counts = _scan()
    assert (counts["stagnant"], counts["certifications"]) == (1 if fires else 0, 1)
    assert len(_stagnation_alerts()) == (1 if fires else 0)


@pytest.mark.parametrize("status", stagnation.STAGNATION_STATUSES)
def test_every_in_progress_status_is_a_stagnation_target(status: str, assignee: int) -> None:
    """서류준비·신청제출·심사중·보완요청·갱신중 — 5태 전부 정체 대상"""
    _cert(status=status, changed_on=BASE - timedelta(days=10), assignee_id=assignee)
    assert _scan()["stagnant"] == 1


@pytest.mark.parametrize(
    "status",
    ["NOT_STARTED", "APPROVED", "EXPIRING", "EXPIRED", "REJECTED", "SUSPENDED"],
)
def test_other_statuses_are_never_stagnation_targets(status: str, assignee: int) -> None:
    """미착수·승인·만료계·종결 2태는 정체 대상이 아니다 — 스캔 건수에도 안 잡힌다"""
    _cert(status=status, changed_on=BASE - timedelta(days=400), assignee_id=assignee)
    counts = _scan()
    assert counts["certifications"] == 0 and counts["stagnant"] == 0
    assert _stagnation_alerts() == []


def test_deleted_certifications_are_ignored(assignee: int) -> None:
    """soft delete된 인증은 스캔 대상이 아니다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    with unit_of_work() as uow:
        from app.core.time import utcnow

        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.deleted_at = utcnow()
    assert _scan()["certifications"] == 0


def test_the_same_episode_is_alerted_once(assignee: int) -> None:
    """같은 에피소드(같은 마지막 활동일·같은 몫)는 재실행해도 알림 1건 — DB 유니크가 멱등의 유일 장치"""
    _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    first, again = _scan(), _scan()
    assert first["stagnant"] == 1 and again["stagnant"] == 0
    assert len(_stagnation_alerts()) == 1


def test_the_reminder_repeats_every_threshold_period_and_skips_missed_periods(
    assignee: int,
) -> None:
    """독촉은 N일마다 1건 — 7일째(#1)·14일째(#2)·21일째(#3), 스캔이 빠진 몫은 소급하지 않는다"""
    certification_id = _cert(changed_on=date(2026, 9, 1), assignee_id=assignee)
    key = f"stagnation:certifications:{certification_id}:INTERNAL@2026-09-01"
    _scan(date(2026, 9, 8))  # 7일째 → #1
    _scan(date(2026, 9, 9))  # 같은 몫 — 신규 0
    _scan(date(2026, 9, 15))  # 14일째 → #2
    # 21일째 스캔이 통째로 빠지고 22일째에 돈다 — 그 시점 몫(#3) 1건만, 과거 몫 소급 없음
    _scan(date(2026, 9, 23))
    keys = {row.dedup_key.rsplit(":", 1)[0] for row in _stagnation_alerts()}
    assert keys == {f"{key}#1", f"{key}#2", f"{key}#3"}
    assert len(_stagnation_alerts()) == 3


def test_a_skipped_period_is_not_backfilled(assignee: int) -> None:
    """스캔이 며칠 죽었다 살아나도 그 시점의 몫 1건만 만든다(#1, #2를 한꺼번에 만들지 않는다)"""
    certification_id = _cert(changed_on=date(2026, 9, 1), assignee_id=assignee)
    _scan(date(2026, 9, 16))  # 15일째 — 몫 #2 (#1은 건너뜀)
    keys = [row.dedup_key for row in _stagnation_alerts()]
    assert len(keys) == 1
    assert f"stagnation:certifications:{certification_id}:INTERNAL@2026-09-01#2:" in keys[0]


def test_an_owner_change_starts_a_new_episode(assignee: int) -> None:
    """공이 다른 쪽으로 넘어가면 키(주체·활동일)가 달라져 새 에피소드로 다시 셈한다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    _scan()
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.action_owner = "AUTHORITY"
        row.action_owner_changed_on = BASE - timedelta(days=31)
    _scan()
    prefixes = {row.dedup_key.split("#")[0].split(":")[3] for row in _stagnation_alerts()}
    assert len(_stagnation_alerts()) == 2 and {p.split("@")[0] for p in prefixes} == {
        "INTERNAL",
        "AUTHORITY",
    }


# ── 활동이 시계를 되돌린다 ───────────────────────────────────────────────────


def test_a_recent_ball_move_resets_the_clock(assignee: int) -> None:
    """공이 최근에 넘어왔으면(6일 전) 정체가 아니다 — 오래된 생성일과 무관"""
    _cert(changed_on=BASE - timedelta(days=6), assignee_id=assignee)
    assert _scan()["stagnant"] == 0


def test_a_recent_status_change_resets_the_clock(assignee: int) -> None:
    """상태 변경(이력 최신)이 최근이면 공 이동이 오래됐어도 정체가 아니다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    _status_log(certification_id, datetime(2026, 9, 27, 3, 0, tzinfo=UTC))  # KST 9/27 → 3일 전
    assert _scan()["stagnant"] == 0


def test_the_status_change_date_is_read_in_kst(assignee: int) -> None:
    """상태 변경 UTC 9/22 15:30 = KST 9/23 — 기준일 9/30이면 정체일 7(경계), UTC 날짜로 읽으면 8"""
    certification_id = _cert(changed_on=BASE - timedelta(days=40), assignee_id=assignee)
    _status_log(certification_id, datetime(2026, 9, 22, 15, 30, tzinfo=UTC))
    assert _scan()["stagnant"] == 1
    alert = _stagnation_alerts()[0]
    assert "@2026-09-23#1" in alert.dedup_key  # 마지막 활동일=KST 날짜
    assert "정체 7일" in alert.title


def test_a_recent_communication_resets_the_clock(assignee: int) -> None:
    """통신 기록(오간 날)이 최근이면 정체가 아니다 — 독촉 메일을 보낸 것이 곧 움직임이다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=3))
    assert _scan()["stagnant"] == 0


def test_completing_a_follow_up_counts_as_activity(assignee: int) -> None:
    """다음 액션 완료일도 움직임이다 — 오간 날이 오래돼도 최근 완료면 정체가 아니다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    _log(
        certification_id,
        occurred_on=BASE - timedelta(days=20),
        next_action="회신",
        due=BASE - timedelta(days=10),
        done=BASE - timedelta(days=2),
    )
    assert _scan()["stagnant"] == 0


def test_deleted_communications_do_not_count_as_activity(assignee: int) -> None:
    """삭제된 통신 기록은 활동이 아니다 — 지워 놓고 시계가 안 돌게 둘 수 없다"""
    certification_id = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=1), deleted=True)
    assert _scan()["stagnant"] == 1


def test_without_a_recorded_ball_date_the_creation_date_is_used(assignee: int) -> None:
    """공이 넘어간 날이 NULL이면 생성일(KST)로 본다 — 자동 적용·이관 행이 조용히 빠지지 않는다"""
    old = _cert(created_at=datetime(2026, 9, 20, 3, 0, tzinfo=UTC), assignee_id=assignee)
    _cert(created_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC), assignee_id=assignee)
    counts = _scan()
    assert counts["certifications"] == 2 and counts["stagnant"] == 1
    assert f"certifications:{old}:" in _stagnation_alerts()[0].dedup_key


# ── N은 데이터 ──────────────────────────────────────────────────────────────


def test_a_rule_overrides_the_threshold_per_owner(assignee: int) -> None:
    """알림 규칙 config.days가 N을 덮어쓴다 — 사내 3일로 줄이면 3일째에 발동, 다른 주체는 기본값 유지"""
    _rule(config={"days": {"INTERNAL": 3}})
    _cert(owner="INTERNAL", changed_on=BASE - timedelta(days=3), assignee_id=assignee)
    _cert(
        owner="AGENCY",
        changed_on=BASE - timedelta(days=3),
        assignee_id=assignee,
        agency_id=_agency(),
    )
    counts = _scan()
    assert counts["stagnant"] == 1  # 사내만 — 대행사는 기본 7일이라 3일째엔 미발동


def test_a_malformed_rule_config_never_stops_the_scan(assignee: int) -> None:
    """config가 엉망이어도(문자열·음수) 스캔은 기본값으로 돈다 — 조용한 정지 금지"""
    _rule(config={"days": {"INTERNAL": "abc", "AGENCY": -5}})
    _cert(changed_on=BASE - timedelta(days=7), assignee_id=assignee)
    assert _scan()["stagnant"] == 1


def test_a_disabled_rule_is_ignored(assignee: int) -> None:
    """비활성 규칙의 N은 적용되지 않는다 — 기본값으로 돈다"""
    _rule(config={"days": {"INTERNAL": 1}}, is_enabled=False)
    _cert(changed_on=BASE - timedelta(days=2), assignee_id=assignee)
    assert _scan()["stagnant"] == 0


# ── 수신자 ──────────────────────────────────────────────────────────────────


def test_an_assigned_certification_alerts_only_its_assignee(
    assignee: int, admins: tuple[int, int]
) -> None:
    """담당자가 있으면 담당자에게만 — 전사 폭포 0 (ADR-07 배타 라우팅)"""
    _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    _scan()
    assert {row.recipient_user_id for row in _stagnation_alerts()} == {assignee}


def test_an_unassigned_certification_falls_back_to_the_admins(admins: tuple[int, int]) -> None:
    """담당자도 규칙도 없으면 ADMIN 전원 폴백 — 조용한 미발송 0 (fail-visible)"""
    _cert(changed_on=BASE - timedelta(days=8))
    _scan()
    assert {row.recipient_user_id for row in _stagnation_alerts()} == set(admins)


def test_a_rule_recipient_receives_unassigned_stagnation(admins: tuple[int, int]) -> None:
    """담당자가 없고 규칙에 수신자가 있으면 그 사람 — 규칙 경로(담당자 → 규칙 → ADMIN)"""
    boss = create_user("stagnation-boss@example.com", roles=(RoleCode.TRADE,))
    _rule(recipient_user_id=boss)
    _cert(changed_on=BASE - timedelta(days=8))
    _scan()
    assert {row.recipient_user_id for row in _stagnation_alerts()} == {boss}


def test_the_alert_says_whose_court_the_ball_is_in_and_how_long(assignee: int) -> None:
    """본문 — 누구 쪽 공인지(대행사명 포함)·며칠째·기준·다음 액션"""
    agency = _agency()
    certification_id = _cert(
        owner="AGENCY",
        changed_on=BASE - timedelta(days=9),
        assignee_id=assignee,
        agency_id=agency,
    )
    _log(
        certification_id,
        occurred_on=BASE - timedelta(days=9),
        next_action="샘플 재발송 요청",
        due=BASE + timedelta(days=5),
    )
    # 통신 기록이 9일 전이라 시계는 그대로(공 이동 9일 전과 같은 날)
    _scan()
    alert = _stagnation_alerts()[0]
    assert alert.severity == "WARN"
    assert "정체 9일" in alert.title
    body = alert.body or ""
    assert "대행사(정체 대행사)" in body and "9일째" in body and "기준 7일" in body
    assert "샘플 재발송 요청" in body
    assert alert.entity_type == "certifications" and alert.entity_id == certification_id


def test_a_long_template_name_is_truncated_not_lost(assignee: int) -> None:
    """요건명이 길어도 제목이 컬럼 폭을 넘지 않는다 — 넘치면 그 건만 영구 미발송(기일 스캔 계보)"""
    certification_id = _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.template_name = "가" * 200
    assert _scan()["failed"] == 0
    assert len(_stagnation_alerts()[0].title) <= 200


def test_the_scan_writes_only_alerts(assignee: int) -> None:
    """읽기+알림 생성뿐 — 인증 행(상태·version)·이벤트는 불변 (4금 비저촉 실측)"""
    certification_id = _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    with unit_of_work() as uow:
        before = uow.session.execute(
            select(Certification.status, Certification.version).where(
                Certification.id == certification_id
            )
        ).one()
        events_before = uow.session.execute(select(func.count()).select_from(Event)).scalar_one()
    _scan()
    with unit_of_work() as uow:
        after = uow.session.execute(
            select(Certification.status, Certification.version).where(
                Certification.id == certification_id
            )
        ).one()
        events_after = uow.session.execute(select(func.count()).select_from(Event)).scalar_one()
    assert tuple(before) == tuple(after) and events_before == events_after


# ═══ 다음 액션 독촉 ═══════════════════════════════════════════════════════════


def test_a_follow_up_due_today_alerts_once_as_due(assignee: int) -> None:
    """기한 당일 — DUE(INFO) 1건, 재실행 0"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    log_id = _log(
        certification_id, occurred_on=BASE - timedelta(days=3), next_action="회신 확인", due=BASE
    )
    first, again = _scan(), _scan()
    assert first["follow_up_due"] == 1 and again["follow_up_due"] == 0
    (alert,) = _follow_up_alerts()
    assert alert.dedup_key == f"followup:comm_logs:{log_id}@{BASE.isoformat()}:DUE:{assignee}"
    assert alert.severity == "INFO" and "기한 오늘" in alert.title


def test_an_overdue_follow_up_alerts_again_as_overdue(assignee: int) -> None:
    """기한 다음 날 — OVERDUE(WARN) 별도 키로 1건 더(DUE와 합쳐 최대 2건), 그 뒤 재실행 0"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    log_id = _log(
        certification_id, occurred_on=BASE - timedelta(days=3), next_action="회신 확인", due=BASE
    )
    _scan(BASE)
    next_day = BASE + timedelta(days=1)
    _scan(next_day)
    _scan(next_day + timedelta(days=1))
    keys = sorted(row.dedup_key for row in _follow_up_alerts())
    assert keys == [
        f"followup:comm_logs:{log_id}@{BASE.isoformat()}:DUE:{assignee}",
        f"followup:comm_logs:{log_id}@{BASE.isoformat()}:OVERDUE:{assignee}",
    ]
    overdue = next(row for row in _follow_up_alerts() if ":OVERDUE:" in row.dedup_key)
    assert overdue.severity == "WARN" and "1일 지남" in overdue.title


def test_first_scan_after_the_due_day_only_creates_the_overdue_alert(assignee: int) -> None:
    """당일 스캔이 죽었다가 다음 날 처음 돌면 OVERDUE만 — 지난 DUE를 소급하지 않는다"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=3), next_action="회신", due=BASE)
    _scan(BASE + timedelta(days=2))
    assert [":OVERDUE:" in row.dedup_key for row in _follow_up_alerts()] == [True]


def test_changing_the_due_date_makes_a_new_alert(assignee: int) -> None:
    """기한을 고치면 새 키 — 옛 기한 알림에 막히지 않는다(기일 스캔의 만료일 세그먼트와 같은 이치)"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    log_id = _log(
        certification_id, occurred_on=BASE - timedelta(days=9), next_action="회신", due=BASE
    )
    _scan()
    with unit_of_work() as uow:
        row = uow.session.get(CommLog, log_id)
        assert row is not None
        row.next_action_due = BASE + timedelta(days=3)
    _scan(BASE + timedelta(days=3))
    assert len(_follow_up_alerts()) == 2


@pytest.mark.parametrize(
    "case",
    ["future_due", "no_due", "done", "deleted_log", "no_action"],
)
def test_follow_ups_that_are_not_due_or_already_handled_are_silent(
    case: str, assignee: int
) -> None:
    """미래 기한·기한 없음·완료·삭제된 기록은 독촉하지 않는다"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    occurred = BASE - timedelta(days=5)
    if case == "future_due":
        _log(certification_id, occurred_on=occurred, next_action="a", due=BASE + timedelta(days=1))
    elif case == "no_due":
        _log(certification_id, occurred_on=occurred, next_action="a")
    elif case == "done":
        _log(certification_id, occurred_on=occurred, next_action="a", due=BASE, done=BASE)
    elif case == "deleted_log":
        _log(certification_id, occurred_on=occurred, next_action="a", due=BASE, deleted=True)
    else:
        _log(certification_id, occurred_on=occurred)
    assert _scan()["follow_ups"] == 0
    assert _follow_up_alerts() == []


@pytest.mark.parametrize("status", ["REJECTED", "SUSPENDED"])
def test_follow_ups_of_closed_certifications_are_silent(status: str, assignee: int) -> None:
    """종결(반려·중단) 인증의 다음 액션은 독촉하지 않는다"""
    certification_id = _cert(status=status, changed_on=BASE, assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=5), next_action="a", due=BASE)
    assert _scan()["follow_ups"] == 0


def test_follow_ups_of_deleted_certifications_are_silent(assignee: int) -> None:
    """삭제된 인증의 다음 액션은 독촉하지 않는다"""
    certification_id = _cert(changed_on=BASE, assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=5), next_action="a", due=BASE)
    with unit_of_work() as uow:
        from app.core.time import utcnow

        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.deleted_at = utcnow()
    assert _scan()["follow_ups"] == 0


def test_an_approved_certification_still_gets_its_follow_up_reminders(assignee: int) -> None:
    """승인 상태 인증도 다음 액션 독촉은 받는다(정체 대상은 아니지만 종결도 아니다)"""
    certification_id = _cert(status="APPROVED", changed_on=BASE, assignee_id=assignee)
    _log(certification_id, occurred_on=BASE - timedelta(days=5), next_action="갱신 서류", due=BASE)
    counts = _scan()
    assert counts["certifications"] == 0 and counts["follow_up_due"] == 1


# ═══ 실행 계약 ═══════════════════════════════════════════════════════════════


def test_one_failing_item_does_not_stop_the_scan_but_fails_the_job(
    assignee: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """건별 트랜잭션 격리 — 한 건이 터져도 나머지는 처리되고, 잡은 FAILED로 올라간다(조용한 정체 방지)"""
    bad = _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    good = _cert(changed_on=BASE - timedelta(days=9), assignee_id=assignee)
    real = stagnation.notifications.notify

    def flaky(session: Any, **kwargs: Any) -> list[int]:
        if kwargs["entity_id"] == bad:
            raise RuntimeError("boom")
        return real(session, **kwargs)

    monkeypatch.setattr(stagnation.notifications, "notify", flaky)
    counts = _scan()
    assert counts["failed"] == 1 and counts["stagnant"] == 1
    assert [row.entity_id for row in _stagnation_alerts()] == [good]
    with pytest.raises(RuntimeError, match="정체 스캔 1건 실패"):
        scheduler._fail_if_any_failed(counts, what="정체 스캔")


def test_the_registered_job_runs_the_scan_and_escalates_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """등록 잡 본체 — 스캔 함수를 부르고, 실패 건이 있으면 예외로 올려 잡을 FAILED로 만든다"""
    monkeypatch.setattr(stagnation, "scan_stagnation", lambda: {"failed": 2, "stagnant": 0})
    with pytest.raises(RuntimeError, match="정체 스캔 2건 실패"):
        scheduler.JOBS_BY_CODE["stagnation-scan"].run()
    monkeypatch.setattr(stagnation, "scan_stagnation", lambda: {"failed": 0, "stagnant": 3})
    assert scheduler.JOBS_BY_CODE["stagnation-scan"].run() == {"failed": 0, "stagnant": 3}


def test_the_job_is_registered_daily_at_seven_after_the_deadline_scan() -> None:
    """레지스트리 계약 — 07:00 KST, 기일 스캔(06:30) 뒤·브리핑(09:00) 앞"""
    spec = scheduler.JOBS_BY_CODE["stagnation-scan"]
    assert spec.schedule == "daily@07:00"
    assert scheduler.JOBS_BY_CODE["deadline-scan"].schedule < spec.schedule
    assert spec.schedule < scheduler.JOBS_BY_CODE["daily-briefing"].schedule


def test_the_cli_runs_the_scan_and_reports_counts(
    assignee: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI 수동 실행 겸용 — 기준일 지정·한국어 요약·종료 코드 0"""
    _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    code = cli_main(["stagnation-scan", "--base-date", BASE.isoformat()])
    output = capsys.readouterr().out
    assert code == 0
    assert "정체 스캔 완료" in output and "정체 1" in output and "실패 0건" in output


def test_a_non_object_rule_config_does_not_break_the_scan(assignee: int) -> None:
    """알림 규칙 config가 객체가 아니어도(배열 등) 스캔은 기본값으로 돈다 — 건마다 실패하지 않는다"""
    _rule(config=["x"])
    _cert(changed_on=BASE - timedelta(days=8), assignee_id=assignee)
    counts = _scan()
    assert counts["failed"] == 0 and counts["stagnant"] == 1


# ═══ 리뷰 보강 (S2-4 PR-1 렌즈 C) ═════════════════════════════════════════════


def test_activity_of_another_certification_does_not_reset_the_clock(assignee: int) -> None:
    """다른 인증의 상태 이력·통신 기록은 이 인증의 시계를 되돌리지 않는다(활동원천 조회의 소유 조건)"""
    quiet = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    busy = _cert(changed_on=BASE - timedelta(days=20), assignee_id=assignee)
    _status_log(busy, datetime(2026, 9, 29, 3, 0, tzinfo=UTC))
    _log(busy, occurred_on=BASE - timedelta(days=1))
    assert _scan()["stagnant"] == 1
    (alert,) = _stagnation_alerts()
    assert alert.dedup_key.startswith(f"stagnation:certifications:{quiet}:")


def test_follow_up_alerts_follow_their_own_rule_and_recipient(admins: tuple[int, int]) -> None:
    """다음 액션 독촉은 comm_logs.follow_up.due 규칙의 수신자·규칙 연결을 쓴다 — 정체 규칙과 섞이지 않는다"""
    boss = create_user("followup-boss@example.com", roles=(RoleCode.CERT,))
    stagnation_boss = create_user("stagnation-boss@example.com", roles=(RoleCode.CERT,))
    rule_id = _rule(stagnation.FOLLOW_UP_EVENT, recipient_user_id=boss)
    _rule(stagnation.STAGNATION_EVENT, recipient_user_id=stagnation_boss, code="RULE-OTHER")
    certification_id = _cert(changed_on=BASE)  # 담당자 없음
    _log(certification_id, occurred_on=BASE - timedelta(days=3), next_action="회신", due=BASE)
    _scan()
    (alert,) = _follow_up_alerts()
    assert alert.recipient_user_id == boss and alert.alert_rule_id == rule_id


def test_an_unassigned_follow_up_without_a_rule_falls_back_to_admins(
    admins: tuple[int, int],
) -> None:
    """담당자도 규칙도 없으면 독촉도 ADMIN 전원 — 조용한 미발송 0"""
    certification_id = _cert(changed_on=BASE)
    _log(certification_id, occurred_on=BASE - timedelta(days=3), next_action="회신", due=BASE)
    _scan()
    assert {row.recipient_user_id for row in _follow_up_alerts()} == set(admins)
