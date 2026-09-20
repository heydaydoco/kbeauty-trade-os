"""H·J — 데일리 브리핑: 담당 건 보유 사용자만 각자 1통·KST 날짜 dedup (판정 요청 6·9 / §20 J).

★ 키는 `briefing:{KST 날짜}:{수신자id}`다 — PR-1 관찰 등재("코어가 수신자를 뒤에
  붙인다")의 첫 판단: 코어를 넓히지 않고 표기를 맞췄다. 담당 이관의 키 재작성이
  "꼬리=수신자"를 전제하므로 이 순서가 이관 경로와도 정합이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.certifications import service as certifications
from app.modules.certifications.models import Certification
from app.modules.deadlines import service as deadlines
from app.modules.identity.models import RoleCode, User
from app.modules.identity.service import AuthenticatedUser
from app.modules.requirements.models import RequirementTemplate
from app.modules.worklist.models import Alert
from tests.support.factories import create_market, create_sku, create_user

pytestmark = pytest.mark.group_h

TODAY = date(2026, 8, 20)


@pytest.fixture
def actor() -> AuthenticatedUser:
    user_id = create_user("briefing-actor@example.com", roles=(RoleCode.CERT,))
    return AuthenticatedUser(
        id=user_id,
        email="briefing-actor@example.com",
        display_name="인증 담당",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )


def _certification(
    actor: AuthenticatedUser, *, key: str, assignee_id: int | None, status: str = "PREPARING"
) -> int:
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
        row = uow.session.get(Certification, body["id"])
        assert row is not None
        row.status = status
        if status in ("EXPIRING", "EXPIRED", "APPROVED", "RENEWING"):
            row.approved_on = date(2020, 1, 1)
            row.valid_from = date(2020, 1, 1)
            row.expires_on = (
                date(2026, 8, 1) if status in ("EXPIRED", "RENEWING") else date(2026, 9, 1)
            )
    return int(body["id"])


def _alerts() -> list[Any]:
    with unit_of_work() as uow:
        rows = list(uow.session.execute(select(Alert).order_by(Alert.id)).scalars())
        for row in rows:
            uow.session.expunge(row)
        return rows


def test_each_assignee_gets_one_briefing(actor: AuthenticatedUser) -> None:
    """담당 건 보유 사용자 2인 → 각자 1통, 담당 없는 사용자·관리자는 0통(판정 요청 9)"""
    other = create_user("briefing-other@example.com", roles=(RoleCode.TRADE,))
    create_user("briefing-idle@example.com", roles=(RoleCode.CERT,))
    create_user("briefing-admin@example.com", roles=(RoleCode.ADMIN,))
    _certification(actor, key="b1", assignee_id=actor.id)
    _certification(actor, key="b2", assignee_id=other)

    counts = deadlines.send_daily_briefing(base_date=TODAY)

    assert counts == {"recipients": 2, "sent": 2}
    rows = _alerts()
    assert {row.recipient_user_id for row in rows} == {actor.id, other}
    assert {row.dedup_key for row in rows} == {
        f"briefing:2026-08-20:{actor.id}",
        f"briefing:2026-08-20:{other}",
    }
    assert all("규칙이 없어" not in (row.body or "") for row in rows)  # 폴백 비발동


def test_briefing_is_deduped_per_kst_date(actor: AuthenticatedUser) -> None:
    """같은 날 두 번 돌려도 1통(§20 J 더블 실행), 다음 날은 새 1통"""
    _certification(actor, key="b-dedup", assignee_id=actor.id)
    assert deadlines.send_daily_briefing(base_date=TODAY)["sent"] == 1
    assert deadlines.send_daily_briefing(base_date=TODAY)["sent"] == 0
    assert deadlines.send_daily_briefing(base_date=date(2026, 8, 21))["sent"] == 1
    assert len(_alerts()) == 2


def test_briefing_body_summarises_the_assignees_load(actor: AuthenticatedUser) -> None:
    """본문 = 진행·만료임박·만료/도과·미확인 알림 건수(계산값)"""
    _certification(actor, key="s1", assignee_id=actor.id, status="PREPARING")
    _certification(actor, key="s2", assignee_id=actor.id, status="EXPIRING")
    _certification(actor, key="s3", assignee_id=actor.id, status="RENEWING")  # 갱신중 도과
    _certification(actor, key="s4", assignee_id=actor.id, status="REJECTED")  # 종결 — 제외
    deadlines.scan_deadlines(base_date=TODAY)  # 미확인 기일 알림이 생긴다

    deadlines.send_daily_briefing(base_date=TODAY)

    briefing = next(row for row in _alerts() if row.dedup_key.startswith("briefing:"))
    assert briefing.title == "데일리 브리핑 — 2026-08-20"
    assert "· 담당 인증(진행 중): 3건" in briefing.body
    assert "· 만료임박: 1건" in briefing.body
    assert "· 만료·도과: 1건" in briefing.body
    assert "· 미확인 알림: " in briefing.body
    unread = int(briefing.body.split("· 미확인 알림: ")[1].split("건")[0])
    assert unread >= 1


def test_inactive_assignees_get_no_briefing_and_no_fallback(actor: AuthenticatedUser) -> None:
    """비활성 담당자 → 0통이고 관리자 폴백도 없다(브리핑은 개인 요약이다)"""
    create_user("briefing-admin2@example.com", roles=(RoleCode.ADMIN,))
    _certification(actor, key="b-inactive", assignee_id=actor.id)
    with unit_of_work() as uow:
        user = uow.session.get(User, actor.id)
        assert user is not None
        user.is_active = False
    counts = deadlines.send_daily_briefing(base_date=TODAY)
    assert counts == {"recipients": 0, "sent": 0}
    assert _alerts() == []
