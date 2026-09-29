"""C·H·J — 기일 스캔: 문턱·지각·도과·문서·갱신중 도과·에스컬레이션·라우팅·멱등 (GC-C1 / S2-3 PR-2 안건 ②·⑦).

★ GC-C1의 원 계약이 이 파일이다(골든 마커 — D-88: D-90분 실재 + D-30분 미발송 +
  재스캔 중복 0).
★ 스캔은 아웃박스를 거치지 않고 알림 생성 코어를 직접 부른다(판정 요청 3 (가)) —
  여기서 보는 알림은 전부 `alerts` 행이고 events는 늘지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.core.time import today_kst, utcnow
from app.modules.certifications import service as certifications
from app.modules.certifications.models import Certification
from app.modules.deadlines import service as deadlines
from app.modules.deadlines.service import CERTIFICATION_EVENT, DOCUMENT_EVENT
from app.modules.documents.models import Document, DocumentType
from app.modules.handover import service as handover
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox.models import Event
from app.modules.requirements.models import RequirementTemplate
from app.modules.worklist.models import Alert, AlertRule
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_market, create_sku, create_user

pytestmark = pytest.mark.group_c

BASE = date(2026, 7, 4)  # GC-C1 "오늘"
EXPIRES = date(2026, 9, 30)  # GC-C1 만료일 — D-88


@pytest.fixture
def actor() -> AuthenticatedUser:
    user_id = create_user("deadline-cert@example.com", roles=(RoleCode.CERT,))
    return AuthenticatedUser(
        id=user_id,
        email="deadline-cert@example.com",
        display_name="인증 담당",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )


@pytest.fixture
def admins() -> Iterator[tuple[int, int]]:
    yield (
        create_user("deadline-admin-a@example.com", roles=(RoleCode.ADMIN,)),
        create_user("deadline-admin-b@example.com", roles=(RoleCode.ADMIN,)),
    )


def _certification(
    actor: AuthenticatedUser,
    *,
    key: str,
    status: str = "APPROVED",
    expires_on: date | None = EXPIRES,
    assignee_id: int | None = None,
) -> int:
    """스캔 대상 인증 — 서비스로 만들고 상태·만료일을 직접 심는다(픽스처 한정)."""
    with unit_of_work() as uow:
        template = RequirementTemplate(
            market_id=create_market("US"),
            name=f"US 등록 {key}",
            applies_to="SKU",
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 6, 1),
            status="CONFIRMED",
        )
        uow.session.add(template)
        uow.session.flush()
        template_id = template.id
    status_code, body = certifications.create_certification(
        actor=actor,
        idempotency_key=key,
        payload={
            "template_id": template_id,
            "target_type": "SKU",
            "target_id": create_sku(f"SKU-{key}"[:40]),
            "assignee_id": assignee_id,
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


def _document(*, valid_until: date | None, owner_id: int = 1) -> int:
    with unit_of_work() as uow:
        type_id = uow.session.execute(
            select(DocumentType.id).where(
                DocumentType.code == "CFS", DocumentType.deleted_at.is_(None)
            )
        ).scalar_one()
        row = Document(
            owner_type="SKU",
            owner_id=owner_id,
            document_type_id=type_id,
            storage_kind="LINK",
            url=f"https://example.com/cfs-{owner_id}.pdf",
            issued_on=date(2025, 1, 1),
            valid_until=valid_until,
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _rule(event_type: str, **overrides: Any) -> int:
    columns: dict[str, Any] = {
        "code": f"RULE-{event_type.split('.')[0].upper()}",
        "name_ko": "기일 알림 규칙",
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


def _keys(prefix: str = "") -> set[str]:
    return {row.dedup_key for row in _alerts(prefix)}


def _event_count() -> int:
    with unit_of_work() as uow:
        total: int = uow.session.execute(select(func.count()).select_from(Event)).scalar_one()
        return total


def _ack_all(user_id: int) -> None:
    with unit_of_work() as uow:
        for row in uow.session.execute(
            select(Alert).where(Alert.recipient_user_id == user_id)
        ).scalars():
            row.acknowledged_at = utcnow()


# ── GC-C1 — 만료 알림 리드타임 (골든) ────────────────────────────────────────


@pytest.mark.golden
def test_gc_c1_d90_already_sent_and_d30_not_yet(actor: AuthenticatedUser, admins: Any) -> None:
    """GC-C1 — 만료 2026-09-30·오늘 2026-07-04(D-88): D-90 발송 상태, D-30 미발송"""
    certification_id = _certification(actor, key="gc-c1", assignee_id=actor.id)
    events_before = _event_count()

    counts = deadlines.scan_deadlines(base_date=BASE)

    keys = _keys(f"deadline:certifications:{certification_id}:")
    assert keys == {
        f"deadline:certifications:{certification_id}:D-180@2026-09-30:{actor.id}",
        f"deadline:certifications:{certification_id}:D-90@2026-09-30:{actor.id}",
    }
    assert counts["threshold"] == 2 and counts["overdue"] == 0 and counts["escalated"] == 0
    # 담당자 지정 건은 담당자에게만 — 관리자 2인 알림 0 (배타 라우팅, 조건 E)
    assert all(row.recipient_user_id == actor.id for row in _alerts("deadline:"))
    # 아웃박스 비경유(판정 요청 3 (가)) — events 불변
    assert _event_count() == events_before


@pytest.mark.golden
def test_gc_c1_rescan_sends_nothing_twice(actor: AuthenticatedUser, admins: Any) -> None:
    """GC-C1 — 동일 기일 재스캔 → 중복 발송 0 (발송 이력 기반 dedup, DoD)"""
    _certification(actor, key="gc-c1-dedup", assignee_id=actor.id)
    first = deadlines.scan_deadlines(base_date=BASE)
    assert first["threshold"] == 2
    again = deadlines.scan_deadlines(base_date=BASE)
    assert again["threshold"] == 0 and again["overdue"] == 0 and again["escalated"] == 0
    assert len(_alerts("deadline:")) == 2


@pytest.mark.golden
def test_gc_c1_next_threshold_arrives_on_its_day(actor: AuthenticatedUser, admins: Any) -> None:
    """GC-C1 — D-30 도달 시 그 문턱만 새로 나간다(앞선 문턱은 점유된 채)"""
    certification_id = _certification(actor, key="gc-c1-next", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)
    later = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=30))
    assert later["threshold"] == 1
    assert f"deadline:certifications:{certification_id}:D-30@2026-09-30:{actor.id}" in _keys()


# ── 지각·도과·갱신중 도과·무기한 ──────────────────────────────────────────────


def test_a_late_scan_still_sends_the_passed_threshold(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """정각을 놓쳐도(스캔 하루 결번) 지난 문턱은 나간다 — 정각 발송이 아니다"""
    certification_id = _certification(actor, key="late", assignee_id=actor.id)
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=29))
    assert counts["threshold"] == 3
    assert f"deadline:certifications:{certification_id}:D-30@2026-09-30:{actor.id}" in _keys()


def test_overdue_sends_one_overdue_alert_per_expiry(actor: AuthenticatedUser, admins: Any) -> None:
    """도과 — 만료일이 지난 건은 도과 알림 1건(재스캔 0)"""
    certification_id = _certification(actor, key="overdue", status="EXPIRED", assignee_id=actor.id)
    counts = deadlines.scan_deadlines(base_date=EXPIRES + timedelta(days=5))
    assert counts["overdue"] == 1 and counts["threshold"] == 0  # 도과 건에 문턱 소급 없음(§0-3 ⑨)
    assert _keys() == {f"deadline:certifications:{certification_id}:overdue@2026-09-30:{actor.id}"}
    assert deadlines.scan_deadlines(base_date=EXPIRES + timedelta(days=6))["overdue"] == 0


def test_renewing_past_expiry_is_overdue_without_a_transition(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """갱신중 도과(안건 ⑦) — 도과 알림은 나가되 상태는 RENEWING 그대로(전이 없음)"""
    certification_id = _certification(
        actor, key="renewing", status="RENEWING", assignee_id=actor.id
    )
    counts = deadlines.scan_deadlines(base_date=EXPIRES + timedelta(days=1))
    assert counts["overdue"] == 1
    assert certifications.get_certification(certification_id).status == "RENEWING"


def test_indefinite_and_terminal_rows_are_not_scanned(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """무기한(NULL)은 기일이 없고, 반려·중단은 종결이라 후보 밖이다"""
    _certification(actor, key="indef", expires_on=None, assignee_id=actor.id)
    _certification(actor, key="rejected", status="REJECTED", assignee_id=actor.id)
    _certification(actor, key="suspended", status="SUSPENDED", assignee_id=actor.id)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["certifications"] == 0
    assert _alerts() == []


def test_a_corrected_expiry_is_a_new_deadline(actor: AuthenticatedUser, admins: Any) -> None:
    """만료일 정정·갱신 = 새 기일 → 새 알림(키가 만료일을 품는다 — 조용한 도과 방지)"""
    certification_id = _certification(actor, key="renewed", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.expires_on = date(2027, 9, 30)
    counts = deadlines.scan_deadlines(base_date=date(2027, 7, 4))
    assert counts["threshold"] == 2
    assert f"deadline:certifications:{certification_id}:D-90@2027-09-30:{actor.id}" in _keys()


# ── 문서 valid_until (§4.7 "유효기간 있는 모든 문서") ────────────────────────


def test_documents_with_validity_are_scanned(admins: tuple[int, int]) -> None:
    """문서 유효기간 스캔 — 담당자 컬럼이 없어 규칙 부재 시 ADMIN 폴백(각 1건)"""
    document_id = _document(valid_until=EXPIRES)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["documents"] == 1 and counts["threshold"] == 4  # 문턱 2 × 관리자 2
    assert _keys(f"deadline:documents:{document_id}:") == {
        f"deadline:documents:{document_id}:D-180@2026-09-30:{admins[0]}",
        f"deadline:documents:{document_id}:D-180@2026-09-30:{admins[1]}",
        f"deadline:documents:{document_id}:D-90@2026-09-30:{admins[0]}",
        f"deadline:documents:{document_id}:D-90@2026-09-30:{admins[1]}",
    }
    assert all("규칙이 없어" in (row.body or "") for row in _alerts("deadline:documents"))


def test_deleted_documents_are_not_scanned(admins: Any) -> None:
    """스캔은 활성 문서만 본다(요청 19 동석 문면) — soft delete 행은 후보 밖"""
    document_id = _document(valid_until=EXPIRES)
    with unit_of_work() as uow:
        row = uow.session.get(Document, document_id)
        assert row is not None
        row.deleted_at = utcnow()
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["documents"] == 0 and _alerts() == []


def test_document_rule_routes_to_its_recipient_and_sets_thresholds(admins: Any) -> None:
    """규칙이 있으면 규칙 수신자·규칙 문턱(config.thresholds)이 정본 — 폴백 비발동"""
    recipient = create_user("doc-owner@example.com", roles=(RoleCode.TRADE,))
    _rule(DOCUMENT_EVENT, recipient_user_id=recipient, config={"thresholds": [60]})
    document_id = _document(valid_until=EXPIRES)
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=61))
    assert counts["threshold"] == 0  # D-61 — 문턱 60 아직
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=60))
    assert counts["threshold"] == 1
    assert _keys() == {f"deadline:documents:{document_id}:D-60@2026-09-30:{recipient}"}


# ── 수신자 결정 (ADR-0045 — 담당자 → 규칙 → ADMIN 폴백) ─────────────────────


def test_assignee_beats_rule_recipient(actor: AuthenticatedUser, admins: tuple[int, int]) -> None:
    """담당자 지정 건은 규칙 수신자·관리자 모두 받지 않는다(배타 라우팅 — 조건 E)"""
    _rule(CERTIFICATION_EVENT, recipient_role="ADMIN")
    _certification(actor, key="exclusive", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)
    assert {row.recipient_user_id for row in _alerts("deadline:")} == {actor.id}


def test_unassigned_certification_falls_back_to_admins(
    actor: AuthenticatedUser, admins: tuple[int, int]
) -> None:
    """담당자·규칙 모두 없으면 ADMIN 전원 폴백 — 조용한 미발송 0(fail-visible)"""
    _certification(actor, key="fallback", assignee_id=None)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["threshold"] == 4
    assert {row.recipient_user_id for row in _alerts("deadline:")} == set(admins)


def test_certification_rule_role_fans_out_when_unassigned(
    actor: AuthenticatedUser, admins: tuple[int, int]
) -> None:
    """담당자 없음 + 규칙 recipient_role → 역할 보유자 전원(각 1건), 관리자 폴백 0"""
    traders = (
        create_user("trade-a@example.com", roles=(RoleCode.TRADE,)),
        create_user("trade-b@example.com", roles=(RoleCode.TRADE,)),
    )
    _rule(CERTIFICATION_EVENT, recipient_role="TRADE")
    _certification(actor, key="role-rule", assignee_id=None)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["threshold"] == 4  # 문턱 2 × TRADE 2
    assert {row.recipient_user_id for row in _alerts("deadline:")} == set(traders)
    assert all("규칙이 없어" not in (row.body or "") for row in _alerts("deadline:"))


def test_the_lowest_id_rule_is_the_policy_when_two_match(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """같은 사건 규칙이 둘이면 id가 작은 것이 정본 — 문턱도 그 규칙의 것"""
    _rule(CERTIFICATION_EVENT, code="R-FIRST", config={"thresholds": [120]})
    _rule(CERTIFICATION_EVENT, code="R-SECOND", config={"thresholds": [45]})
    certification_id = _certification(actor, key="two-rules", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)  # D-88 — 120만 지났다(45는 무시)
    assert _keys() == {f"deadline:certifications:{certification_id}:D-120@2026-09-30:{actor.id}"}


def test_thresholds_come_from_the_certification_rule(actor: AuthenticatedUser, admins: Any) -> None:
    """문턱은 데이터다(ADR-11) — 인증 규칙 config가 기본 D-180/90/30을 덮는다"""
    _rule(CERTIFICATION_EVENT, config={"thresholds": [120, 45]})
    certification_id = _certification(actor, key="custom", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)  # D-88 — 120만 지났다
    assert _keys() == {f"deadline:certifications:{certification_id}:D-120@2026-09-30:{actor.id}"}


# ── D-3 에스컬레이션 (DoD "미확인 에스컬레이션 발동" / 판정 요청 8) ────────────


def test_unacknowledged_alert_escalates_to_all_admins_at_d3(
    actor: AuthenticatedUser, admins: tuple[int, int]
) -> None:
    """미확인 기일 알림이 D-3에 남아 있으면 ADMIN 전원에게 esc: 알림(각 1건)"""
    certification_id = _certification(actor, key="esc", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))  # 담당자에게 D-30 등
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=3))
    assert counts["escalated"] == 2
    assert _keys("esc:") == {
        f"esc:certifications:{certification_id}:D-3@2026-09-30:{admins[0]}",
        f"esc:certifications:{certification_id}:D-3@2026-09-30:{admins[1]}",
    }
    # 재스캔 — 에스컬레이션도 dedup
    assert deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=2))["escalated"] == 0


def test_acknowledged_alerts_do_not_escalate(actor: AuthenticatedUser, admins: Any) -> None:
    """담당자가 전부 확인했으면 에스컬레이션 0 — 확인이 곧 대응 신호다"""
    _certification(actor, key="esc-acked", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))
    _ack_all(actor.id)
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=3))
    assert counts["escalated"] == 0 and _keys("esc:") == set()


def test_partial_acknowledgement_still_escalates(actor: AuthenticatedUser, admins: Any) -> None:
    """문턱 셋 중 하나만 확인했으면 미확인이 남은 것 — 에스컬레이션 발동"""
    _certification(actor, key="esc-partial", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Alert).where(Alert.dedup_key.like("%:D-30@%"))
        ).scalar_one()
        row.acknowledged_at = utcnow()
    assert deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=3))["escalated"] == 2


def test_documents_escalate_too(admins: tuple[int, int]) -> None:
    """문서도 같은 판정 — 폴백 수신자(관리자)가 안 읽으면 관리자 에스컬레이션(각 1건)"""
    document_id = _document(valid_until=EXPIRES)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=3))
    assert counts["escalated"] == 2
    assert _keys("esc:") == {
        f"esc:documents:{document_id}:D-3@2026-09-30:{admins[0]}",
        f"esc:documents:{document_id}:D-3@2026-09-30:{admins[1]}",
    }


def test_escalation_is_judged_before_this_scans_own_alerts(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """같은 스캔이 방금 만든 알림은 미확인으로 치지 않는다 — 받을 틈이 없었다"""
    _certification(actor, key="esc-fresh", assignee_id=actor.id)
    counts = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=2))
    assert counts["threshold"] == 3 and counts["escalated"] == 0


def test_old_cycle_alerts_do_not_escalate_the_new_deadline(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """지난 주기(옛 만료일)의 미확인 알림은 새 만료일의 D-3 판정 근거가 아니다

    관통 실측 발견: 만료일 정정 직후 옛 주기의 미확인 D-30이 새 주기 에스컬레이션을
    즉시 발동시켰다. 판정은 현재 만료일 스탬프의 알림만 센다.
    """
    certification_id = _certification(actor, key="esc-cycle", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))  # 옛 주기 D-30 미확인
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.expires_on = date(2027, 9, 30)  # 갱신 — 새 주기
    counts = deadlines.scan_deadlines(base_date=date(2027, 9, 28))  # 새 주기 D-2
    assert counts["escalated"] == 0 and counts["threshold"] == 3
    # 새 주기의 D-30이 미확인으로 남은 다음 날에는 발동한다
    assert deadlines.scan_deadlines(base_date=date(2027, 9, 29))["escalated"] == 2


def test_escalation_does_not_fire_before_d3(actor: AuthenticatedUser, admins: Any) -> None:
    _certification(actor, key="esc-early", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=10))
    assert deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=4))["escalated"] == 0


# ── 건별 트랜잭션·기준일 기본값 ────────────────────────────────────────────────


def test_default_base_date_is_kst_today(
    actor: AuthenticatedUser, admins: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기준일 생략 = KST 오늘(§22 렌즈 6) — today_kst()가 기준일의 유일 출처"""
    certification_id = _certification(actor, key="today", assignee_id=actor.id)
    monkeypatch.setattr(deadlines, "today_kst", lambda: BASE)
    counts = deadlines.scan_deadlines()
    assert counts["threshold"] == 2  # D-88 — D-180·D-90
    assert f"deadline:certifications:{certification_id}:D-90@2026-09-30:{actor.id}" in _keys()
    assert today_kst() != BASE  # 실제 오늘과 다름을 확인 — 패치가 실효했다는 증거


@pytest.mark.group_j
@pytest.mark.concurrency
def test_two_scans_at_once_send_once(actor: AuthenticatedUser, admins: Any) -> None:
    """두 스캔이 동시에 돌아도 알림은 1벌 — 멱등 장치는 DB 유니크뿐이라 실제 경합으로 본다"""
    _certification(actor, key="race", assignee_id=actor.id)
    outcomes = run_concurrently(lambda _i: deadlines.scan_deadlines(base_date=BASE), workers=2)
    assert all(outcome.ok for outcome in outcomes), [o.error for o in outcomes]
    assert sum(o.value["threshold"] for o in outcomes) == 2
    assert sum(o.value["failed"] for o in outcomes) == 0
    assert len(_alerts("deadline:")) == 2


@pytest.mark.group_h
def test_handover_moves_deadline_alerts_without_resending(
    actor: AuthenticatedUser, admins: Any
) -> None:
    """담당 일괄 이관 → 기존 알림의 키 꼬리가 새 담당자로, 재스캔 재발송 0 (§20 H·ADR-0015)"""
    successor = create_user("deadline-successor@example.com", roles=(RoleCode.CERT,))
    certification_id = _certification(actor, key="handover", assignee_id=actor.id)
    deadlines.scan_deadlines(base_date=BASE)
    handover.reassign_all(from_user_id=actor.id, to_user_id=successor, actor_user_id=admins[0])

    assert deadlines.scan_deadlines(base_date=BASE)["threshold"] == 0
    rows = _alerts("deadline:")
    assert {row.recipient_user_id for row in rows} == {successor}
    assert {row.dedup_key for row in rows} == {
        f"deadline:certifications:{certification_id}:D-180@2026-09-30:{successor}",
        f"deadline:certifications:{certification_id}:D-90@2026-09-30:{successor}",
    }
    # 이관 뒤의 새 문턱은 새 담당자에게만
    later = deadlines.scan_deadlines(base_date=EXPIRES - timedelta(days=30))
    assert later["threshold"] == 1
    assert {row.recipient_user_id for row in _alerts("deadline:")} == {successor}


def test_nobody_to_notify_is_a_silent_gap(actor: AuthenticatedUser) -> None:
    """담당자·규칙·활성 관리자 전부 없으면 0건 — 폴백의 끝은 관리자다(관찰 등재 근거)"""
    _certification(actor, key="nobody", assignee_id=None)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["certifications"] == 1 and counts["threshold"] == 0
    assert _alerts() == []


def test_one_failing_subject_does_not_stop_the_scan(
    actor: AuthenticatedUser, admins: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """건별 독립 트랜잭션(§17.6) — 한 건이 터져도 나머지는 처리된다"""
    _certification(actor, key="fail-a", assignee_id=actor.id)
    good_id = _certification(actor, key="fail-b", assignee_id=actor.id)
    original = deadlines._certification_subject

    def boom(row: Certification) -> deadlines.Subject:
        if row.id != good_id:
            raise RuntimeError("의도한 실패")
        return original(row)

    monkeypatch.setattr(deadlines, "_certification_subject", boom)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["failed"] == 1 and counts["threshold"] == 2
    assert all(f":{good_id}:" in key for key in _keys())


def test_a_rollback_does_not_leak_into_the_counts(
    actor: AuthenticatedUser, admins: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """건의 뒤쪽 INSERT가 실패하면 앞서 센 알림도 롤백된다 — 집계는 커밋 뒤에만 합산"""
    _certification(actor, key="rollback", assignee_id=actor.id)
    original = deadlines.notifications.notify
    calls = {"n": 0}

    def flaky(session: Any, **kwargs: Any) -> list[int]:
        calls["n"] += 1
        if calls["n"] == 2:  # D-180은 성공, D-90에서 실패
            raise RuntimeError("의도한 실패")
        return original(session, **kwargs)

    monkeypatch.setattr(deadlines.notifications, "notify", flaky)
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["failed"] == 1 and counts["threshold"] == 0
    assert _alerts() == []


def test_a_soft_deleted_certification_is_not_scanned(actor: AuthenticatedUser, admins: Any) -> None:
    certification_id = _certification(actor, key="softdel", assignee_id=actor.id)
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.deleted_at = utcnow()
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["certifications"] == 0 and _alerts() == []


def test_an_over_long_template_name_still_notifies(actor: AuthenticatedUser, admins: Any) -> None:
    """템플릿명 200자 — 제목이 컬럼 길이를 넘어도 잘려서 나간다(영구 미발송 방지)"""
    certification_id = _certification(actor, key="long", assignee_id=actor.id)
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.template_name = "가" * 200
    counts = deadlines.scan_deadlines(base_date=BASE)
    assert counts["failed"] == 0 and counts["threshold"] == 2
    assert all(len(row.title) <= 200 and row.title.endswith("…") for row in _alerts())
