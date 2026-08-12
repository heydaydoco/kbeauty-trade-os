"""H. 운영 — 알림센터 왕복·재발송 0·담당 이관 후 라우팅 (WBS S2-3 DoD / §20 H).

★ 이 파일이 실 HTTP로 보는 것(조건 C — 전수는 서비스 층, e2e는 대표 소수):
  ① **담당자 지정 건은 담당자에게만**(DoD "전사 폭포 0건") — 관리자가 방에
    있어도 받지 않는다.
  ② **확인(ack) 후 재발송 0**(§20 H "확인 알림 재발송 0") — ack는 행을 지우지
    않으므로 같은 키가 계속 점유되고, 재디스패치가 새 알림을 못 만든다.
  ③ **담당 일괄 이관 후 라우팅 즉시 반영**(DoD·§20 H·ADR-0015) — 기존 미확인
    알림의 수신자가 넘어가고, 이관 뒤 발행된 사건은 새 담당자에게 간다.
    도구는 S0-2에 있었고 발송 채널이 서는 지금이 그 검증 자리다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.notifications import dispatcher
from app.modules.outbox import service as outbox
from app.modules.worklist.models import AlertRule
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_h

LOGIN = "/api/v1/auth/login"
ALERTS = "/api/v1/alerts"
EVENT = "worklist.task.created"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def owner() -> Iterator[TestClient]:
    yield from _client("alert-owner@example.com", RoleCode.CERT)


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("alert-admin@example.com", RoleCode.ADMIN)


def _user_id(client: TestClient) -> int:
    body = client.get("/api/v1/auth/me")
    assert body.status_code == 200, body.text
    return int(body.json()["id"])


def _rule_routing_to_assignee() -> None:
    """수신자를 비운 규칙 = '담당자에게'(모델 문면)."""
    with unit_of_work() as uow:
        uow.session.add(
            AlertRule(code="TASK-ASSIGNEE", name_ko="담당 태스크 알림", event_type=EVENT)
        )


def _publish_task_event(assignee_id: int, *, aggregate_id: int = 1) -> None:
    with unit_of_work() as uow:
        outbox.publish(
            uow.session,
            event_type=EVENT,
            aggregate_type="tasks",
            aggregate_id=aggregate_id,
            payload={"task_id": aggregate_id, "assignee_id": assignee_id},
        )


def _inbox(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(ALERTS)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


# ── ① 담당자에게만 (DoD 전사 폭포 0건) ───────────────────────────────────────


def test_only_the_assignee_receives(owner: TestClient, admin: TestClient) -> None:
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner))

    dispatcher.dispatch_pending()

    assert len(_inbox(owner)) == 1
    assert _inbox(admin) == []  # 관리자도 남의 담당 건을 받지 않는다


def test_unread_count_tracks_the_inbox(owner: TestClient) -> None:
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner))
    dispatcher.dispatch_pending()

    before = owner.get(f"{ALERTS}/unread-count")
    assert before.status_code == 200, before.text
    assert before.json()["count"] == 1

    alert_id = _inbox(owner)[0]["id"]
    acked = owner.post(f"{ALERTS}/{alert_id}/ack")
    assert acked.status_code == 200, acked.text
    assert acked.json()["acknowledged_at"] is not None

    after = owner.get(f"{ALERTS}/unread-count")
    assert after.json()["count"] == 0


# ── ② 확인 후 재발송 0 (§20 H) ───────────────────────────────────────────────


def test_an_acknowledged_alert_is_not_sent_again(owner: TestClient) -> None:
    """확인한 알림은 다시 오지 않는다 (§20 H "확인 알림 재발송 0").

    ★ ack는 행을 **지우지 않는다** — soft delete가 아니라 시각 기록이다. 그래서
      같은 사건의 dedup 키가 계속 점유돼 재전달이 새 알림을 만들지 못한다.
      (반대로 ack가 행을 지웠다면 재전달 때마다 알림이 되살아난다.)
    ★ 여기서 보는 것은 **같은 사건의 재전달**이다. 같은 업무 사실이 시간이 지나
      다시 조건에 걸리는 축(같은 인증의 같은 문턱을 다음 주기에 또 스캔)은 키가
      사건이 아니라 업무 사실로 잡히는 기일 스캔의 몫이고 PR-2에서 본다.
    """
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner))
    dispatcher.dispatch_pending()

    alert_id = _inbox(owner)[0]["id"]
    owner.post(f"{ALERTS}/{alert_id}/ack")

    dispatcher.dispatch_pending()  # 재전달 시도

    inbox = _inbox(owner)
    assert len(inbox) == 1
    assert inbox[0]["acknowledged_at"] is not None


def test_a_new_fact_is_a_new_alert_even_after_ack(owner: TestClient) -> None:
    """새 사건은 새 알림이다 — 재발송 금지가 '영원한 침묵'이 되지 않는다.

    dedup은 같은 사건의 반복을 막는 장치이지, 다음에 실제로 일어난 일을 막는
    장치가 아니다. 이 구분이 없으면 한 번 확인한 사용자는 같은 종류의 사건을
    두 번 다시 통지받지 못한다.
    """
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner), aggregate_id=1)
    dispatcher.dispatch_pending()
    owner.post(f"{ALERTS}/{_inbox(owner)[0]['id']}/ack")

    _publish_task_event(_user_id(owner), aggregate_id=2)
    dispatcher.dispatch_pending()

    inbox = _inbox(owner)
    assert len(inbox) == 2
    assert [item["acknowledged_at"] is None for item in inbox].count(True) == 1


def test_acking_twice_keeps_the_first_time(owner: TestClient) -> None:
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner))
    dispatcher.dispatch_pending()
    alert_id = _inbox(owner)[0]["id"]

    first = owner.post(f"{ALERTS}/{alert_id}/ack").json()["acknowledged_at"]
    second = owner.post(f"{ALERTS}/{alert_id}/ack").json()["acknowledged_at"]
    assert first == second


# ── ③ 담당 일괄 이관 후 라우팅 즉시 반영 (DoD·ADR-0015) ──────────────────────


def test_handover_moves_the_inbox_and_the_routing(owner: TestClient, admin: TestClient) -> None:
    successor_email = "alert-successor@example.com"
    successor_id = create_user(successor_email, roles=(RoleCode.CERT,))
    _rule_routing_to_assignee()
    _publish_task_event(_user_id(owner))
    dispatcher.dispatch_pending()
    assert len(_inbox(owner)) == 1

    moved = admin.post(
        f"/api/v1/users/{_user_id(owner)}/handover",
        json={"to_user_id": successor_id},
    )
    assert moved.status_code == 200, moved.text

    with TestClient(app) as successor:
        login = successor.post(LOGIN, json={"email": successor_email, "password": DEFAULT_PASSWORD})
        assert login.status_code == 200, login.text

        # 기존 미확인 알림이 새 담당자의 알림함으로 넘어왔다.
        assert len(_inbox(successor)) == 1
        assert _inbox(owner) == []

        # 이관 뒤 발행된 사건도 새 담당자에게 간다(라우팅 즉시 반영).
        _publish_task_event(successor_id, aggregate_id=2)
        dispatcher.dispatch_pending()
        assert len(_inbox(successor)) == 2
        assert _inbox(owner) == []
