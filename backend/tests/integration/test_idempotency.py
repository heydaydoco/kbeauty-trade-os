"""J. 안전 계약 — 쓰기 멱등 (DESIGN.md §17.4 / **GC-A3** / ADR-0014).

GC-A3: 같은 idempotency key로 두 번 요청하면 업무는 한 번만 일어나고,
2차 요청은 **에러가 아니라 최초 결과를 그대로** 받는다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.worklist.models import Task
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_j

ENDPOINT = "POST /api/v1/tasks"


def _create_task_once(actor_id: int, key: str, body: dict[str, object]) -> tuple[int, dict]:
    """업무(태스크 생성) 한 번을 멱등으로 감싼 것 — 실제 서비스가 쓸 모양 그대로."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor_id,
            endpoint=ENDPOINT,
            key=key,
            request_body=body,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        task = Task(title=str(body["title"]), created_by_id=actor_id)
        session.add(task)
        session.flush()

        response = {"id": task.id, "title": task.title}
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=response)
        return 201, response


def _task_count() -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM tasks")).scalar_one()


@pytest.fixture
def actor_id() -> int:
    return create_user("actor@example.com", roles=(RoleCode.TRADE,))


@pytest.mark.golden
def test_gc_a3_same_key_twice_creates_one_row_and_replays_the_first_result(
    actor_id: int,
) -> None:
    """GC-A3 — 같은 키로 2회 요청: 행 1개, 2차는 최초 결과 그대로(에러 아님)"""
    body = {"title": "샘플 발송 준비"}

    first_status, first_body = _create_task_once(actor_id, "key-1", body)
    second_status, second_body = _create_task_once(actor_id, "key-1", body)

    assert _task_count() == 1, "같은 키인데 업무가 두 번 일어났다"
    assert second_status == first_status == 201, "2차 요청이 에러가 됐다(멱등 성공이어야 한다)"
    assert second_body == first_body, "2차 요청이 최초 결과와 다른 값을 돌려줬다"


def test_different_keys_create_separate_rows(actor_id: int) -> None:
    """키가 다르면 별개의 업무다"""
    _create_task_once(actor_id, "key-1", {"title": "첫 번째"})
    _create_task_once(actor_id, "key-2", {"title": "두 번째"})
    assert _task_count() == 2


def test_same_key_with_different_body_is_rejected(actor_id: int) -> None:
    """같은 키로 다른 내용을 보내면 409로 거절한다

    조용히 최초 결과를 돌려주면 사용자는 바뀐 값이 저장됐다고 믿는다 —
    금액을 고쳐 재전송한 경우가 정확히 이 상황이다.
    """
    _create_task_once(actor_id, "key-1", {"title": "원래 내용"})

    with pytest.raises(AppError) as caught:
        _create_task_once(actor_id, "key-1", {"title": "바뀐 내용"})

    assert caught.value.code.value == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    assert _task_count() == 1


def test_key_is_scoped_to_the_actor(actor_id: int) -> None:
    """다른 사람이 같은 키를 써도 남의 결과를 받지 않는다 (ADR-0014 스코프)"""
    other_id = create_user("other@example.com", roles=(RoleCode.TRADE,))

    _create_task_once(actor_id, "shared-key", {"title": "내 태스크"})
    _, other_body = _create_task_once(other_id, "shared-key", {"title": "남의 태스크"})

    assert other_body["title"] == "남의 태스크"
    assert _task_count() == 2


def test_key_is_scoped_to_the_endpoint(actor_id: int) -> None:
    """같은 키라도 엔드포인트가 다르면 별개다"""
    body = {"title": "같은 내용"}
    _create_task_once(actor_id, "key-1", body)

    with unit_of_work() as uow:
        claim = idempotency.claim(
            uow.session,
            actor_user_id=actor_id,
            endpoint="POST /api/v1/skus",
            key="key-1",
            request_body=body,
        )
        assert claim.replay is None, "다른 엔드포인트인데 최초 결과가 재생됐다"


def test_key_order_in_body_does_not_change_the_fingerprint() -> None:
    """본문의 키 순서가 달라도 같은 내용이면 같은 지문이다"""
    assert idempotency.fingerprint({"a": 1, "b": 2}) == idempotency.fingerprint({"b": 2, "a": 1})


def test_rolled_back_work_leaves_no_claim(actor_id: int) -> None:
    """업무가 롤백되면 멱등 선점도 함께 사라진다 (키가 영구히 막히지 않는다)"""
    with pytest.raises(RuntimeError), unit_of_work() as uow:
        idempotency.claim(
            uow.session,
            actor_user_id=actor_id,
            endpoint=ENDPOINT,
            key="key-1",
            request_body={"title": "실패할 일"},
        )
        raise RuntimeError("업무 실패")

    status, body = _create_task_once(actor_id, "key-1", {"title": "다시 시도"})
    assert status == 201
    assert body["title"] == "다시 시도"


@pytest.mark.concurrency
def test_concurrent_requests_with_the_same_key_create_one_row(actor_id: int) -> None:
    """동일 키 **동시** 2요청 → 행 1개, 응답 동일

    더블클릭은 순차가 아니라 동시에 도착한다. UNIQUE 충돌 후 선점 행의 잠금에서
    대기했다가 커밋된 결과를 읽는 경로가 여기서 시험된다.
    """
    body = {"title": "동시 더블클릭"}
    outcomes = run_concurrently(lambda _: _create_task_once(actor_id, "same-key", body), workers=2)

    failures = [outcome.error for outcome in outcomes if not outcome.ok]
    assert not failures, f"동시 요청이 실패했다: {failures}"

    assert _task_count() == 1, "동시 요청이 업무를 두 번 일으켰다"
    first, second = (outcome.value for outcome in outcomes)
    assert first == second, f"두 응답이 다르다: {first} vs {second}"
