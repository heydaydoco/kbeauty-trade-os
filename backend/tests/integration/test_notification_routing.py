"""H. 운영 — 수신자 결정과 dedup 규약 (ADR-07 / ADR-0012 / 판정 요청 6·7·조건 E).

★ 이 파일이 고정하는 것:
  ① **이원화**(요청 7) — 기일 계열은 규칙·담당자가 없을 때 ADMIN 폴백,
    디스패처 일반 이벤트는 미발송.
  ② **배타 라우팅**(조건 E) — 담당자가 있으면 ADMIN은 받지 않는다. 이것이
    DoD "담당자 지정 건은 담당자에게만(전사 폭포 0건)"의 서비스 층 실측이다.
  ③ **수신자 포함 dedup 키**(요청 6) — 역할 팬아웃 2인이 각자 1건을 받고,
    같은 사건의 재호출은 0건이다. 키에 수신자가 없으면 두 번째 사람이 조용히
    사라지는데, 그 실패 모양은 "중복 발송 0"을 통과하면서 알림만 안 간다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.modules.identity.models import RoleCode
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing
from app.modules.worklist.models import Alert, AlertRule
from tests.support.factories import create_user

pytestmark = pytest.mark.group_h

EVENT = "worklist.task.created"


@pytest.fixture
def admins() -> Iterator[tuple[int, int]]:
    """관리자 2인 — 폴백이 '전원'인지(1인이 아니라) 보려면 둘이어야 한다."""
    yield (
        create_user("admin-a@example.com", roles=(RoleCode.ADMIN,)),
        create_user("admin-b@example.com", roles=(RoleCode.ADMIN,)),
    )


def _rule(**overrides: object) -> AlertRule:
    columns: dict[str, object] = {
        "code": "RULE-1",
        "name_ko": "태스크 생성 알림",
        "event_type": EVENT,
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = AlertRule(**columns)
        uow.session.add(row)
        uow.session.flush()
        uow.session.expunge(row)
        return row


def _notify(
    *,
    rule: AlertRule | None = None,
    assignee_id: int | None = None,
    routing: Routing = Routing.EVENT,
    subject_key: str = "test:tasks:1:D-30",
) -> list[int]:
    with unit_of_work() as uow:
        merged = uow.session.merge(rule) if rule is not None else None
        return notifications.notify(
            uow.session,
            subject_key=subject_key,
            title="기일 임박",
            rule=merged,
            assignee_id=assignee_id,
            routing=routing,
        )


def _recipients_of(subject_key: str = "test:tasks:1:D-30") -> set[int]:
    with unit_of_work() as uow:
        rows = uow.session.execute(
            select(Alert.recipient_user_id).where(Alert.dedup_key.like(f"{subject_key}:%"))
        ).scalars()
        return set(rows)


# ── ① 이원화 (요청 7) ────────────────────────────────────────────────────────


def test_a_rule_with_an_explicit_user_decides_when_there_is_no_assignee() -> None:
    target = create_user("named@example.com", roles=(RoleCode.CERT,))
    assert len(_notify(rule=_rule(recipient_user_id=target))) == 1
    assert _recipients_of() == {target}


def test_the_assignee_outranks_an_explicit_rule_recipient() -> None:
    """담당자가 규칙보다 앞선다 (판정 요청 7 (i) — 담당자 → 규칙 → 폴백).

    ★ 순서가 뒤집히면 규칙에 수신자를 한 명 적어 두는 순간 **전 건의 담당자
      라우팅이 그 한 사람으로 덮인다** — DoD "담당자 지정 건은 담당자에게만"의
      정반대다. 이 조합(규칙 수신자 + 담당자 동시 실재)이 그 역전을 잡는다.
    """
    rule_target = create_user("rule-target@example.com", roles=(RoleCode.CERT,))
    assignee = create_user("real-owner@example.com", roles=(RoleCode.CERT,))

    created = _notify(rule=_rule(recipient_user_id=rule_target), assignee_id=assignee)
    assert len(created) == 1
    assert _recipients_of() == {assignee}


def test_the_assignee_outranks_a_role_rule() -> None:
    create_user("role-holder@example.com", roles=(RoleCode.CERT,))
    assignee = create_user("role-owner@example.com", roles=(RoleCode.TRADE,))

    _notify(rule=_rule(recipient_role=RoleCode.CERT.value), assignee_id=assignee)
    assert _recipients_of() == {assignee}


def test_a_role_rule_fans_out_to_every_holder() -> None:
    """역할 지정은 보유자 전원에게 — 각자 1건(수신자 포함 키라 유실 0)"""
    first = create_user("cert-a@example.com", roles=(RoleCode.CERT,))
    second = create_user("cert-b@example.com", roles=(RoleCode.CERT,))
    created = _notify(rule=_rule(recipient_role=RoleCode.CERT.value))
    assert len(created) == 2
    assert _recipients_of() == {first, second}


def test_an_empty_rule_routes_to_the_assignee() -> None:
    """수신자 2필드가 빈 규칙 = '담당자에게'(모델 문면)"""
    assignee = create_user("owner@example.com", roles=(RoleCode.TRADE,))
    assert len(_notify(rule=_rule(), assignee_id=assignee)) == 1
    assert _recipients_of() == {assignee}


def test_deadline_routing_falls_back_to_every_admin(admins: tuple[int, int]) -> None:
    """기일 계열은 규칙·담당자가 없으면 관리자 전원 — 조용한 도과보다 낫다"""
    created = _notify(routing=Routing.DEADLINE)
    assert len(created) == 2
    assert _recipients_of() == set(admins)


def test_the_fallback_says_why_it_arrived(admins: tuple[int, int]) -> None:
    _notify(routing=Routing.DEADLINE)
    with unit_of_work() as uow:
        body = uow.session.execute(select(Alert.body)).scalars().first()
    assert body is not None and notifications.FALLBACK_NOTE in body


def test_event_routing_stays_silent_without_a_rule(admins: tuple[int, int]) -> None:
    """디스패처 일반 이벤트는 규칙이 곧 구독 의사 — 폭을 넓히지 않는다"""
    assert _notify(routing=Routing.EVENT) == []
    assert _recipients_of() == set()


def test_admin_routing_is_not_a_fallback(admins: tuple[int, int]) -> None:
    """운영 알림은 관리자가 정본 수신자 — 폴백 꼬리말이 붙지 않는다"""
    created = _notify(routing=Routing.ADMIN)
    assert len(created) == 2
    with unit_of_work() as uow:
        bodies = uow.session.execute(select(Alert.body)).scalars().all()
    assert all(body is None or notifications.FALLBACK_NOTE not in body for body in bodies)


# ── ② 배타 라우팅 (조건 E — DoD "전사 폭포 0건") ─────────────────────────────


def test_an_assignee_excludes_the_admin_fallback(admins: tuple[int, int]) -> None:
    """담당자가 있으면 관리자는 받지 않는다 — 폴백은 공백을 메울 때만 돈다"""
    assignee = create_user("assignee@example.com", roles=(RoleCode.CERT,))
    created = _notify(assignee_id=assignee, routing=Routing.DEADLINE)
    assert len(created) == 1
    assert _recipients_of() == {assignee}
    assert set(admins).isdisjoint(_recipients_of())


def test_inactive_users_never_receive() -> None:
    """비활성 계정은 어느 경로에서도 수신자가 아니다 (§2 계정 비활성 절차)"""
    retired = create_user("retired@example.com", roles=(RoleCode.CERT,), is_active=False)
    assert _notify(rule=_rule(recipient_user_id=retired)) == []
    assert _notify(assignee_id=retired, routing=Routing.EVENT) == []


def test_a_rule_pointing_at_a_retired_user_still_falls_back(admins: tuple[int, int]) -> None:
    """규칙이 퇴사자를 가리켜도 기일 알림은 사라지지 않는다 (fail-visible).

    ★ '아무도 못 짚었다'와 '규칙이 지목했는데 그 사람이 없다'는 결과가 같아야
      한다. 후자에서 멈추면 규칙 하나가 기일 축 전체를 조용히 침묵시킨다 —
      이 축의 존재 이유(조용한 도과 방지)와 정반대다.
    """
    retired = create_user("gone@example.com", roles=(RoleCode.CERT,), is_active=False)
    created = _notify(rule=_rule(recipient_user_id=retired), routing=Routing.DEADLINE)
    assert len(created) == 2
    assert _recipients_of() == set(admins)


def test_a_role_rule_with_no_holders_still_falls_back(admins: tuple[int, int]) -> None:
    """보유자 0명인 역할을 가리키는 규칙도 마찬가지다"""
    created = _notify(rule=_rule(recipient_role=RoleCode.LOGISTICS.value), routing=Routing.DEADLINE)
    assert len(created) == 2
    assert _recipients_of() == set(admins)


def test_an_empty_rule_stays_silent_for_events_without_an_assignee(
    admins: tuple[int, int],
) -> None:
    """일반 이벤트는 폴백이 없다 — 규칙이 아무도 못 짚으면 미발송이다"""
    assert _notify(rule=_rule(), routing=Routing.EVENT) == []
    assert _recipients_of() == set()


# ── ③ dedup 규약 (요청 6) ────────────────────────────────────────────────────


def test_the_same_subject_is_not_sent_twice() -> None:
    assignee = create_user("dedup@example.com", roles=(RoleCode.CERT,))
    assert len(_notify(rule=_rule(), assignee_id=assignee)) == 1
    assert _notify(rule=_rule(code="RULE-2"), assignee_id=assignee) == []
    with unit_of_work() as uow:
        total = uow.session.execute(select(func.count()).select_from(Alert)).scalar_one()
    assert total == 1


def test_a_different_threshold_is_a_different_alert() -> None:
    """문턱이 키에 들어가므로 D-90과 D-30은 서로를 막지 않는다"""
    assignee = create_user("thresholds@example.com", roles=(RoleCode.CERT,))
    assert len(_notify(assignee_id=assignee, subject_key="cert:CERTIFICATION:7:D-90")) == 1
    assert len(_notify(assignee_id=assignee, subject_key="cert:CERTIFICATION:7:D-30")) == 1


def test_the_dedup_key_carries_the_recipient() -> None:
    """키 규약 자체의 실측 — 수신자가 빠지면 팬아웃이 조용히 잘린다"""
    first = create_user("keyed-a@example.com", roles=(RoleCode.CERT,))
    second = create_user("keyed-b@example.com", roles=(RoleCode.CERT,))
    _notify(rule=_rule(recipient_role=RoleCode.CERT.value), subject_key="cert:SKU:3:D-30")
    with unit_of_work() as uow:
        keys = set(uow.session.execute(select(Alert.dedup_key)).scalars())
    assert keys == {f"cert:SKU:3:D-30:{first}", f"cert:SKU:3:D-30:{second}"}
