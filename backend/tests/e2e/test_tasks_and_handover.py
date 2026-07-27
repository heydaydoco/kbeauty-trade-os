"""K. 보안·품질 — 태스크 소유권 + 담당 일괄 이관.

DESIGN.md §18.1(IDOR) / §17.4(멱등) / §2 담당 이관 / §20 H / ADR-0015.
S0-2 DoD "타 사용자 리소스 403"을 계획대로 **tasks로 재확인**하고,
"담당 이관 후 원담당자 잔여 0건"(WBS v1.3)을 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k

LOGIN = "/api/v1/auth/login"
TASKS = "/api/v1/tasks"
USERS = "/api/v1/users"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login_as(client: TestClient, email: str) -> Response:
    response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text
    return response


def _create_task(
    client: TestClient, title: str, *, key: str, assignee_id: int | None = None
) -> Response:
    return client.post(
        TASKS,
        json={"title": title, "assignee_id": assignee_id},
        headers={"Idempotency-Key": key},
    )


def _error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def _count(sql: str, **params: object) -> int:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


# ── 소유권 (DoD "타 사용자 리소스 403" — tasks 재확인) ──────────────────────


def test_creator_can_read_own_task(client: TestClient) -> None:
    """만든 사람은 자기 태스크를 볼 수 있다"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    task_id = _create_task(client, "내 태스크", key="k1").json()["id"]

    assert client.get(f"{TASKS}/{task_id}").status_code == 200


def test_another_users_task_is_forbidden(client: TestClient) -> None:
    """남의 태스크 URL을 직접 부르면 403이다 (S0-2 DoD — tasks 재확인)"""
    create_user("owner@example.com", roles=(RoleCode.TRADE,))
    create_user("stranger@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as owner:
        _login_as(owner, "owner@example.com")
        task_id = _create_task(owner, "남의 태스크", key="k1").json()["id"]

    _login_as(client, "stranger@example.com")
    response = client.get(f"{TASKS}/{task_id}")

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.FORBIDDEN"


def test_forbidden_task_response_does_not_leak_the_title(client: TestClient) -> None:
    """403 응답에 남의 태스크 내용이 섞이지 않는다"""
    create_user("owner@example.com", roles=(RoleCode.TRADE,))
    create_user("stranger@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as owner:
        _login_as(owner, "owner@example.com")
        task_id = _create_task(owner, "대외비 협상 메모", key="k1").json()["id"]

    _login_as(client, "stranger@example.com")
    assert "대외비 협상 메모" not in client.get(f"{TASKS}/{task_id}").text


def test_assignee_can_read_the_task(client: TestClient) -> None:
    """담당자로 지정되면 볼 수 있다"""
    create_user("owner@example.com", roles=(RoleCode.TRADE,))
    assignee_id = create_user("assignee@example.com", roles=(RoleCode.LOGISTICS,))

    with TestClient(app) as owner:
        _login_as(owner, "owner@example.com")
        task_id = _create_task(owner, "담당 배정", key="k1", assignee_id=assignee_id).json()["id"]

    _login_as(client, "assignee@example.com")
    assert client.get(f"{TASKS}/{task_id}").status_code == 200


def test_task_list_shows_only_my_tasks(client: TestClient) -> None:
    """목록에도 남의 태스크는 안 나온다 (상세만 막으면 목록으로 샌다)"""
    create_user("owner@example.com", roles=(RoleCode.TRADE,))
    create_user("stranger@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as owner:
        _login_as(owner, "owner@example.com")
        _create_task(owner, "남의 태스크", key="k1")

    _login_as(client, "stranger@example.com")
    body = client.get(TASKS).json()

    assert body["total"] == 0
    assert body["items"] == []
    assert body["size"] == 50


def test_admin_sees_every_task(client: TestClient) -> None:
    """관리자는 전부 본다"""
    create_user("owner@example.com", roles=(RoleCode.TRADE,))
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))

    with TestClient(app) as owner:
        _login_as(owner, "owner@example.com")
        _create_task(owner, "누군가의 태스크", key="k1")

    _login_as(client, "admin@example.com")
    assert client.get(TASKS).json()["total"] == 1


def test_viewer_cannot_create_a_task(client: TestClient) -> None:
    """조회 역할은 태스크를 만들 수 없다 (§2 조회는 읽기 전용)"""
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    _login_as(client, "viewer@example.com")

    assert _create_task(client, "만들면 안 됨", key="k1").status_code == 403


# ── 멱등 (§17.4) — HTTP 표면에서 ────────────────────────────────────────────


def test_missing_idempotency_key_is_rejected(client: TestClient) -> None:
    """생성 요청에 멱등 키가 없으면 거절한다

    "있으면 쓰고 없으면 그냥 실행"으로 두면 더블클릭 보호가 클라이언트의 선의에
    달리고, 헤더를 빠뜨린 화면 하나가 조용히 이중 전표를 만든다.
    """
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    response = client.post(TASKS, json={"title": "키 없음"})

    assert response.status_code == 400
    assert _error_code(response) == "COMMON.IDEMPOTENCY.KEY_REQUIRED"


def test_double_click_creates_one_task(client: TestClient) -> None:
    """같은 키로 두 번 눌러도 태스크는 1건이고, 2차 응답이 1차와 같다 (GC-A3)"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    first = _create_task(client, "더블클릭", key="same-key")
    second = _create_task(client, "더블클릭", key="same-key")

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert _count("SELECT count(*) FROM tasks") == 1


# ── 담당 일괄 이관 (§2 / ADR-0015 / WBS v1.3) ──────────────────────────────


def test_handover_moves_every_assignment(client: TestClient) -> None:
    """이관 후 원담당자에게 남은 담당 건이 0이다 (S0-2 DoD — WBS v1.3)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as leaver:
        _login_as(leaver, "leaver@example.com")
        for index in range(3):
            _create_task(leaver, f"업무 {index}", key=f"k{index}", assignee_id=leaver_id)

    _login_as(client, "admin@example.com")
    response = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id})

    assert response.status_code == 200
    body = response.json()
    assert body["moved"]["tasks"] == 3
    assert body["total"] == 3

    assert _count("SELECT count(*) FROM tasks WHERE assignee_id = :id", id=leaver_id) == 0, (
        "이관했는데 원담당자에게 담당 건이 남았다"
    )
    assert _count("SELECT count(*) FROM tasks WHERE assignee_id = :id", id=successor_id) == 3


def test_handover_reports_every_target_table(client: TestClient) -> None:
    """0건인 대상도 결과에 나온다 (빠진 항목이 누락처럼 보이지 않게)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "admin@example.com")

    body = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id}).json()

    assert set(body["moved"]) == {"tasks", "alert_rules", "alerts"}
    assert body["total"] == 0


def test_handover_is_audited(client: TestClient) -> None:
    """이관이 audit_log에 남는다 (누가 누구에게 넘겼는지)"""
    admin_id = create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "admin@example.com")

    client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id})

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT actor_user_id, entity_id, detail->>'to_user_id' AS to_id "
                "FROM audit_log WHERE action = 'identity.assignments.handed_over'"
            )
        ).one()
    assert row.actor_user_id == admin_id
    assert row.entity_id == leaver_id
    assert int(row.to_id) == successor_id


def test_handover_publishes_an_event_for_the_notification_engine(client: TestClient) -> None:
    """이관 사실이 아웃박스에 실린다 (§20 H의 라우팅 즉시 반영은 S2-3이 받는다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "admin@example.com")

    client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id})

    assert (
        _count("SELECT count(*) FROM events WHERE event_type = 'identity.assignments.handed_over'")
        == 1
    )


def test_non_admin_cannot_hand_over(client: TestClient) -> None:
    """비관리자는 담당 이관을 실행할 수 없다 (ADR-0015 관리자 전용)"""
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "trade@example.com")

    response = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id})

    assert response.status_code == 403
    assert _error_code(response) == "COMMON.AUTH.FORBIDDEN"


def test_handover_to_self_is_rejected(client: TestClient) -> None:
    """자기 자신에게 넘길 수는 없다"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    _login_as(client, "admin@example.com")

    response = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": leaver_id})

    assert response.status_code == 422


def test_handover_to_an_inactive_user_is_rejected(client: TestClient) -> None:
    """비활성 계정에는 넘길 수 없다

    로그인도 못 하는 사람이 담당자가 되면, 이관은 성공했는데 아무도 못 보는
    상태가 된다 — 이관 전보다 나쁘다.
    """
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    sleeping_id = create_user("sleeping@example.com", is_active=False)
    _login_as(client, "admin@example.com")

    response = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": sleeping_id})

    assert response.status_code == 422


def test_handover_from_a_deactivated_user_is_allowed(client: TestClient) -> None:
    """이미 비활성화된 퇴사자의 담당 건도 넘길 수 있다 (실무 순서가 그렇다)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    successor_id = create_user("successor@example.com", roles=(RoleCode.TRADE,))

    with TestClient(app) as leaver:
        _login_as(leaver, "leaver@example.com")
        _create_task(leaver, "남긴 일", key="k1", assignee_id=leaver_id)

    _login_as(client, "admin@example.com")
    client.patch(f"{USERS}/{leaver_id}/active", json={"is_active": False})

    response = client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": successor_id})

    assert response.status_code == 200
    assert response.json()["moved"]["tasks"] == 1


def test_failed_handover_moves_nothing(client: TestClient) -> None:
    """이관이 거절되면 한 건도 움직이지 않는다 (전부 아니면 0건 — §17.1)"""
    create_user("admin@example.com", roles=(RoleCode.ADMIN,))
    leaver_id = create_user("leaver@example.com", roles=(RoleCode.TRADE,))
    sleeping_id = create_user("sleeping@example.com", is_active=False)

    with TestClient(app) as leaver:
        _login_as(leaver, "leaver@example.com")
        _create_task(leaver, "남긴 일", key="k1", assignee_id=leaver_id)

    _login_as(client, "admin@example.com")
    client.post(f"{USERS}/{leaver_id}/handover", json={"to_user_id": sleeping_id})

    assert _count("SELECT count(*) FROM tasks WHERE assignee_id = :id", id=leaver_id) == 1
    assert (
        _count("SELECT count(*) FROM audit_log WHERE action = 'identity.assignments.handed_over'")
        == 0
    )
