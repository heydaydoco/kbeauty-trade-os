"""C·J — 날짜 스윕: 자동 부여·복귀·멱등·경합 (§5.2 / WBS S2-2 DoD / GC-C10 / 판정 ⑦).

★ GC-C10의 원 계약이 이 파일이다(골든 마커 3건 — 성공·미충족·멱등).
  스윕 함수 자체는 그대로이고, S2-3에서 실행 수단이 하나 늘었다 — CLI 수동
  호출 + `scheduled_jobs`의 `certification-sweep` 등록(daily@06:00). 함수는
  재작성하지 않았다(ADR-0039 "등록만" 문면 그대로).

★ 스윕-사람 경합(J)은 실제 동시 실행으로 본다(GC-F1 규율 — 순차 실행은
  경합 증거로 인정 안 됨). 행 잠금+상태 재확인(§17.2)이 시험 대상이다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import cli
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import VersionConflictError
from app.core.time import today_kst
from app.modules.certifications import service
from app.modules.certifications.models import Certification, CertificationStatusLog
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox.models import Event
from app.modules.requirements.models import RequirementTemplate
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_market, create_sku, create_user

pytestmark = pytest.mark.group_c

_TODAY = today_kst()


@pytest.fixture
def actor() -> AuthenticatedUser:
    user_id = create_user("cert-sweep@example.com", roles=(RoleCode.CERT,))
    return AuthenticatedUser(
        id=user_id,
        email="cert-sweep@example.com",
        display_name="인증 담당",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )


def _seeded_certification(
    actor: AuthenticatedUser,
    *,
    key: str,
    status: str,
    expires_on: date | None,
    renewal_lead_days: int | None = None,
) -> int:
    """스윕 대상 픽스처 — 서비스로 만들고 상태·날짜를 직접 심는다(픽스처 한정)."""
    with unit_of_work() as uow:
        template = RequirementTemplate(
            market_id=create_market("US"),
            name=f"US 등록 {key}",
            applies_to="SKU",
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 8, 1),
            status="CONFIRMED",
            renewal_lead_days=renewal_lead_days,
        )
        uow.session.add(template)
        uow.session.flush()
        template_id = template.id
    status_code, body = service.create_certification(
        actor=actor,
        idempotency_key=key,
        payload={
            "template_id": template_id,
            "target_type": "SKU",
            "target_id": create_sku(f"SKU-{key}"[:40]),
        },
    )
    assert status_code == 201, body
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Certification).where(Certification.id == body["id"])
        ).scalar_one()
        row.status = status
        row.approved_on = date(2020, 1, 1)
        row.valid_from = date(2020, 1, 1)
        row.expires_on = expires_on
    return int(body["id"])


def _status_of(certification_id: int) -> str:
    return service.get_certification(certification_id).status


def _last_log(certification_id: int) -> CertificationStatusLog | None:
    with unit_of_work() as uow:
        row = (
            uow.session.execute(
                select(CertificationStatusLog)
                .where(CertificationStatusLog.certification_id == certification_id)
                .order_by(CertificationStatusLog.id.desc())
            )
            .scalars()
            .first()
        )
        if row is not None:
            uow.session.expunge(row)
        return row


# ── GC-C10 — 만료임박 날짜 조건 자동 부여 (양방향+멱등) ─────────────────────


@pytest.mark.golden
def test_sweep_grants_expiring_within_lead_window(actor: AuthenticatedUser) -> None:
    """GC-C10 성공 방향 — 리드 창 안의 승인 인스턴스가 만료임박으로 전이된다"""
    certification_id = _seeded_certification(
        actor,
        key="gc10-grant",
        status="APPROVED",
        expires_on=_TODAY + timedelta(days=30),
        renewal_lead_days=90,
    )
    counts = service.sweep_date_transitions()
    assert counts["converged"] == 1
    assert _status_of(certification_id) == "EXPIRING"
    log = _last_log(certification_id)
    assert log is not None
    assert (log.from_status, log.to_status) == ("APPROVED", "EXPIRING")
    assert log.actor_user_id is None  # 자동 — actor NULL (§5.2 자동 부여)
    assert log.reason is not None  # 사유 자동 기록 (조건 B)


@pytest.mark.golden
def test_sweep_leaves_rows_outside_lead_window_untouched(actor: AuthenticatedUser) -> None:
    """GC-C10 미충족 방향(자기검사) — 리드 밖이면 변화 0 (과차단 방지)"""
    certification_id = _seeded_certification(
        actor,
        key="gc10-outside",
        status="APPROVED",
        expires_on=_TODAY + timedelta(days=365),
        renewal_lead_days=90,
    )
    counts = service.sweep_date_transitions()
    assert counts["converged"] == 0
    assert _status_of(certification_id) == "APPROVED"


@pytest.mark.golden
def test_sweep_rerun_changes_nothing(actor: AuthenticatedUser) -> None:
    """GC-C10 멱등 — 재실행 변화 0 (§20 J '동일 재실행 변화 0' 계보)"""
    certification_id = _seeded_certification(
        actor,
        key="gc10-idem",
        status="APPROVED",
        expires_on=_TODAY + timedelta(days=30),
        renewal_lead_days=90,
    )
    first = service.sweep_date_transitions()
    assert first["converged"] == 1
    rerun = service.sweep_date_transitions()
    assert rerun["converged"] == 0
    assert _status_of(certification_id) == "EXPIRING"


# ── 자동 6방향의 나머지 — 만료·직행·복귀 ────────────────────────────────────


def test_sweep_expires_overdue_rows(actor: AuthenticatedUser) -> None:
    """만료임박→만료(도과) + 승인→만료(리드 부재 직행 — 비대칭 결손 방지)"""
    expiring_id = _seeded_certification(
        actor,
        key="overdue-a",
        status="EXPIRING",
        expires_on=_TODAY - timedelta(days=1),
        renewal_lead_days=30,
    )
    approved_id = _seeded_certification(
        actor,
        key="overdue-b",
        status="APPROVED",
        expires_on=_TODAY - timedelta(days=10),
    )
    counts = service.sweep_date_transitions()
    assert counts["converged"] == 2
    assert counts["EXPIRING->EXPIRED"] == 1
    assert counts["APPROVED->EXPIRED"] == 1
    assert _status_of(expiring_id) == "EXPIRED"
    assert _status_of(approved_id) == "EXPIRED"


def test_sweep_restores_corrected_rows(actor: AuthenticatedUser) -> None:
    """만료 상태의 만료일이 정정돼 있으면 승인·임박으로 복귀 (전이 26·27 — #16 계보)"""
    to_approved = _seeded_certification(
        actor, key="restore-a", status="EXPIRED", expires_on=_TODAY + timedelta(days=365)
    )
    to_expiring = _seeded_certification(
        actor,
        key="restore-b",
        status="EXPIRED",
        expires_on=_TODAY + timedelta(days=30),
        renewal_lead_days=90,
    )
    counts = service.sweep_date_transitions()
    assert counts["converged"] == 2
    assert _status_of(to_approved) == "APPROVED"
    assert _status_of(to_expiring) == "EXPIRING"


def test_sweep_ignores_renewing_and_open_ended(actor: AuthenticatedUser) -> None:
    """갱신중=스윕 비대상(도과여도 불변 — 판정 ③), 무기한=후보 밖"""
    renewing_id = _seeded_certification(
        actor, key="skip-renewing", status="RENEWING", expires_on=_TODAY - timedelta(days=30)
    )
    open_ended_id = _seeded_certification(
        actor, key="skip-open", status="APPROVED", expires_on=None
    )
    counts = service.sweep_date_transitions()
    assert counts["converged"] == 0
    assert _status_of(renewing_id) == "RENEWING"
    assert _status_of(open_ended_id) == "APPROVED"


def test_sweep_base_date_parameter_is_the_calendar(actor: AuthenticatedUser) -> None:
    """기준일 인자가 달력이다 — 미래 기준일이면 오늘 유효한 행도 만료로 계산된다"""
    certification_id = _seeded_certification(
        actor, key="base-date", status="APPROVED", expires_on=_TODAY + timedelta(days=30)
    )
    counts = service.sweep_date_transitions(base_date=_TODAY + timedelta(days=31))
    assert counts["converged"] == 1
    assert _status_of(certification_id) == "EXPIRED"


def test_sweep_publishes_automatic_events(actor: AuthenticatedUser) -> None:
    """스윕 전이도 아웃박스에 남는다 — automatic 표식 (판정 ⑩ 발행 전건)"""
    _seeded_certification(
        actor, key="evt-auto", status="APPROVED", expires_on=_TODAY - timedelta(days=1)
    )
    service.sweep_date_transitions()
    with unit_of_work() as uow:
        last = uow.session.execute(select(Event).order_by(Event.id.desc())).scalars().first()
        assert last is not None
        assert last.event_type == "certifications.certification.status_changed"
        assert last.payload["automatic"] is True


# ── CLI (판정 ⑦ — 실행 수단은 수동 호출뿐) ──────────────────────────────────


def test_cli_certification_sweep(
    actor: AuthenticatedUser, capsys: pytest.CaptureFixture[str]
) -> None:
    certification_id = _seeded_certification(
        actor,
        key="cli-run",
        status="APPROVED",
        expires_on=_TODAY + timedelta(days=30),
        renewal_lead_days=90,
    )
    exit_code = cli.main(["certification-sweep"])
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "수렴 1건" in printed
    assert "APPROVED->EXPIRING" in printed
    assert _status_of(certification_id) == "EXPIRING"


def test_cli_certification_sweep_accepts_base_date(
    actor: AuthenticatedUser, capsys: pytest.CaptureFixture[str]
) -> None:
    certification_id = _seeded_certification(
        actor, key="cli-base", status="APPROVED", expires_on=_TODAY + timedelta(days=30)
    )
    future = (_TODAY + timedelta(days=31)).isoformat()
    exit_code = cli.main(["certification-sweep", "--base-date", future])
    assert exit_code == 0
    assert _status_of(certification_id) == "EXPIRED"


# ── 스윕-사람 경합 (J — §17.2 확인→기록 직렬화) ─────────────────────────────


@pytest.mark.group_j
@pytest.mark.concurrency
def test_sweep_vs_human_transition_race(actor: AuthenticatedUser) -> None:
    """도과한 만료임박 행을 스윕(→만료)과 사람(→갱신중)이 동시에 노린다.

    행 잠금+상태 재확인이 결과를 둘 중 하나로만 만든다: 사람이 먼저면 갱신중
    (스윕은 갱신중을 건너뛴다), 스윕이 먼저면 만료(사람은 version 409).
    두 전이가 모두 반영되는 소실 갱신은 어떤 타이밍에도 불가하다.
    """
    certification_id = _seeded_certification(
        actor,
        key="race",
        status="EXPIRING",
        expires_on=_TODAY - timedelta(days=1),
        renewal_lead_days=30,
    )
    version = service.get_certification(certification_id).version

    def worker(index: int) -> Any:
        if index == 0:
            return service.sweep_date_transitions()
        return service.transition_certification(
            actor=actor,
            idempotency_key="race-human",
            certification_id=certification_id,
            payload={"to": "RENEWING", "version": version},
        )

    outcomes = run_concurrently(worker, workers=2)
    sweep_outcome, human_outcome = outcomes[0], outcomes[1]
    assert sweep_outcome.ok  # 스윕은 어느 순서에서도 실패하지 않는다

    final_status = _status_of(certification_id)
    with unit_of_work() as uow:
        logs = (
            uow.session.execute(
                select(CertificationStatusLog)
                .where(CertificationStatusLog.certification_id == certification_id)
                .order_by(CertificationStatusLog.id)
            )
            .scalars()
            .all()
        )
        chain = [(log.from_status, log.to_status) for log in logs]

    if human_outcome.ok:
        # 사람 먼저 — 스윕은 갱신중을 건너뛰었다.
        assert final_status == "RENEWING"
        assert chain == [("EXPIRING", "RENEWING")]
    else:
        # 스윕 먼저 — 사람은 낡은 version으로 409를 받았다(소실 갱신 없음).
        assert isinstance(human_outcome.error, VersionConflictError)
        assert final_status == "EXPIRED"
        assert chain == [("EXPIRING", "EXPIRED")]
