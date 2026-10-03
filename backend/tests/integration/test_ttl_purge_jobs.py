"""J·H. TTL 기술 행 청소 잡 2종 — `idempotency-purge`·`session-purge` (S3-1 PR-16 / ADR-0013·0014 예약 이행 / ADR-0058 ⑤ / design-F F18).

경계 3종을 고정한다.
  ① **TTL 정확히**: `expires_at <= now`면 지우고, 1마이크로초라도 남았으면 남긴다(기존 사용자별 청소와 같은 술어 — 유예 신설 없음).
  ② **진행 중인 claim**: 미만료 claim(결과 미기록)은 남기고, 만료됐더라도 지금 다른 트랜잭션이 잠근 행(이어받기 중)은
     기다리지도 빼앗지도 않고 건너뛴다(SKIP LOCKED — fail-closed). 잠금이 풀린 뒤 다음 회차가 지운다.
  ③ **활성 세션**: 미만료 세션은 폐기 여부와 무관하게 남기고, 청소 뒤에도 그 토큰으로 계속 인증된다.
재실행은 변화 0이고(멱등), 동시 실행은 합계가 정확히 후보 수다(이중 삭제·교착·오류 0).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import purge
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.idempotency import service as idempotency
from app.modules.idempotency.models import IdempotencyKey
from app.modules.identity import service as identity
from app.modules.identity.models import RoleCode, UserSession
from app.modules.platform import scheduler
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_j

TICK = timedelta(microseconds=1)


def _count(table: str, where: str = "TRUE", **params: Any) -> int:
    with owner_engine.connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"), params
            ).scalar_one()
        )


def _key(actor: int, key: str, *, expires_at: datetime, completed: bool = True) -> int:
    with unit_of_work() as uow:
        row = IdempotencyKey(
            actor_user_id=actor,
            endpoint="POST /api/v1/tasks",
            idempotency_key=key,
            request_fingerprint="0" * 64,
            expires_at=expires_at,
            completed_at=utcnow() if completed else None,
            status_code=201 if completed else None,
            response_body={"id": 1} if completed else None,
        )
        uow.session.add(row)
        uow.session.flush()
        return int(row.id)


def _session(user: int, *, expires_at: datetime, revoked: bool = False) -> int:
    with unit_of_work() as uow:
        row = UserSession(
            user_id=user,
            token_hash=f"{user:08d}{expires_at.timestamp():.6f}{revoked}".ljust(64, "x")[:64],
            expires_at=expires_at,
            last_seen_at=utcnow(),
            revoked_at=utcnow() if revoked else None,
        )
        uow.session.add(row)
        uow.session.flush()
        return int(row.id)


def _bulk_expired_keys(actor: int, n: int, *, now: datetime) -> None:
    """만료 키 n행을 한 문장으로 넣는다(청크 경계 시험용)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO idempotency_keys (actor_user_id, endpoint, idempotency_key, request_fingerprint, expires_at)"
                " SELECT :a, 'POST /bulk', 'k-' || g, repeat('0', 64), :e FROM generate_series(1, :n) g"
            ),
            {"a": actor, "e": now - timedelta(hours=1), "n": n},
        )


@pytest.fixture
def actor() -> int:
    return create_user("purge-actor@example.com", roles=(RoleCode.TRADE,))


# ── 멱등 키 ─────────────────────────────────────────────────────────────────


def test_idempotency_purge_boundary_is_exactly_the_ttl(actor: int) -> None:
    """① claim이 찍은 expires_at(=생성+24h)에서 1µs 전에는 남고, 정확히 그 순간 지워진다"""
    with unit_of_work() as uow:
        claim = idempotency.claim(
            uow.session, actor_user_id=actor, endpoint="POST /x", key="ttl", request_body={"a": 1}
        )
        assert claim.record is not None
        idempotency.complete(uow.session, claim.record, status_code=201, body={"ok": True})
        expires_at, created_at = claim.record.expires_at, claim.record.created_at
    assert abs((expires_at - created_at) - idempotency.KEY_TTL) < timedelta(seconds=5)

    assert idempotency.purge_expired_all(now=expires_at - TICK) == 0
    assert _count("idempotency_keys") == 1
    assert idempotency.purge_expired_all(now=expires_at) == 1
    assert _count("idempotency_keys") == 0


def test_idempotency_purge_keeps_unexpired_rows_completed_or_in_progress(actor: int) -> None:
    """만료분만 지운다 — 미만료는 완료·진행 중(결과 미기록) 모두 남는다. 다른 액터의 만료분도 지운다(전역)"""
    now = utcnow()
    other = create_user("purge-other@example.com", roles=(RoleCode.TRADE,))
    keep_done = _key(actor, "keep-done", expires_at=now + TICK)
    keep_open = _key(actor, "keep-open", expires_at=now + timedelta(hours=23), completed=False)
    _key(actor, "gone-done", expires_at=now)
    _key(actor, "gone-open", expires_at=now - timedelta(days=3), completed=False)
    _key(other, "gone-other", expires_at=now - timedelta(seconds=1))

    assert idempotency.purge_expired_all(now=now) == 3
    with owner_engine.connect() as connection:
        left = set(connection.execute(text("SELECT id FROM idempotency_keys")).scalars())
    assert left == {keep_done, keep_open}
    # 재실행 변화 0(멱등).
    assert idempotency.purge_expired_all(now=now) == 0
    assert _count("idempotency_keys") == 2


def test_idempotency_purge_runs_in_chunks_until_nothing_is_left(actor: int) -> None:
    """1,000행 청크를 넘는 양도 반복 커밋으로 잔여 0 — 청크 크기의 정확한 배수도 끝난다"""
    now = utcnow()
    _bulk_expired_keys(actor, 2500, now=now)
    keep = _key(actor, "keep", expires_at=now + timedelta(hours=1))
    assert idempotency.purge_expired_all(now=now) == 2500
    assert _count("idempotency_keys") == 1
    assert _count("idempotency_keys", "id = :i", i=keep) == 1

    _bulk_expired_keys(actor, 20, now=now)
    assert idempotency.purge_expired_all(now=now, batch=10) == 20  # 정확한 배수 — 빈 청크로 끝난다
    assert idempotency.purge_expired_all(now=now, batch=10) == 0


def test_each_chunk_commits_on_its_own_so_a_mid_run_failure_keeps_earlier_chunks(
    actor: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """청크마다 독립 트랜잭션 — 2번째 청크에서 실패해도 1번째 청크 삭제는 커밋돼 남고 나머지는 남는다(§17.6)"""
    now = utcnow()
    _bulk_expired_keys(actor, 25, now=now)
    original = purge.unit_of_work
    entries = [0]

    @contextmanager
    def fail_on_second_chunk() -> Iterator[Any]:
        entries[0] += 1
        if entries[0] == 2:
            raise RuntimeError("주입 실패")
        with original() as uow:
            yield uow

    monkeypatch.setattr(purge, "unit_of_work", fail_on_second_chunk)
    with pytest.raises(RuntimeError, match="주입 실패"):
        idempotency.purge_expired_all(now=now, batch=10)
    assert _count("idempotency_keys") == 15


def test_purge_refuses_to_join_an_open_unit_of_work(actor: int) -> None:
    """열린 UoW 안에서 부르면 거부 — 합류하면 전 청크가 한 트랜잭션으로 묶인다"""
    now = utcnow()
    _bulk_expired_keys(actor, 3, now=now)
    with pytest.raises(RuntimeError, match="unit_of_work 밖"), unit_of_work():
        idempotency.purge_expired_all(now=now)
    assert _count("idempotency_keys") == 3


def test_a_purged_key_is_processed_as_new_on_retry(actor: int) -> None:
    """지워진 뒤 같은 키 재요청은 재생이 아니라 신규 처리(그 뒤의 이중 확정은 상태 검사가 막는다 — ADR-0014 부기)"""
    with unit_of_work() as uow:
        first = idempotency.claim(
            uow.session, actor_user_id=actor, endpoint="POST /x", key="again", request_body={}
        )
        assert first.record is not None
        idempotency.complete(uow.session, first.record, status_code=201, body={"n": 1})
        expires_at = first.record.expires_at
    assert idempotency.purge_expired_all(now=expires_at) == 1
    with unit_of_work() as uow:
        second = idempotency.claim(
            uow.session, actor_user_id=actor, endpoint="POST /x", key="again", request_body={}
        )
        assert second.replay is None and second.record is not None


def test_a_locked_claim_is_skipped_not_waited_on_and_purged_after_release(actor: int) -> None:
    """② 지금 다른 트랜잭션이 잠근 만료 행(이어받기 중인 claim)은 기다리지도 지우지도 않는다 — 풀리면 다음 회차가 지운다"""
    now = utcnow()
    locked = _key(actor, "held", expires_at=now - timedelta(minutes=1), completed=False)
    _key(actor, "free", expires_at=now - timedelta(minutes=1))

    holder = owner_engine.connect()
    try:
        transaction = holder.begin()
        holder.execute(text("SET LOCAL lock_timeout = '30s'"))
        holder.execute(
            text("SELECT id FROM idempotency_keys WHERE id = :i FOR UPDATE"), {"i": locked}
        )
        started = utcnow()
        assert idempotency.purge_expired_all(now=now) == 1
        assert utcnow() - started < timedelta(seconds=5), "잠긴 행을 기다렸다(요청 경로에 끌려감)"
        assert _count("idempotency_keys", "id = :i", i=locked) == 1
        transaction.rollback()
    finally:
        holder.close()
    assert idempotency.purge_expired_all(now=now) == 1
    assert _count("idempotency_keys") == 0


def test_the_purge_touches_no_other_table(actor: int) -> None:
    """원장·감사 무접촉 — audit_log 행 수 불변"""
    now = utcnow()
    _bulk_expired_keys(actor, 5, now=now)
    before = _count("audit_log")
    idempotency.purge_expired_all(now=now)
    identity.purge_expired_sessions_all(now=now)
    assert _count("audit_log") == before


# ── 로그인 세션 ─────────────────────────────────────────────────────────────


def test_session_purge_boundary_and_active_sessions(actor: int) -> None:
    """① 정확히 만료 시각에 지운다 ③ 미만료는 폐기 여부와 무관하게 남는다 — 로그인 시 사용자별 청소와 같은 결과"""
    now = utcnow()
    other = create_user("purge-sess-other@example.com", roles=(RoleCode.CERT,))
    keep_active = _session(actor, expires_at=now + TICK)
    keep_revoked = _session(actor, expires_at=now + timedelta(hours=2), revoked=True)
    _session(actor, expires_at=now)
    _session(actor, expires_at=now - timedelta(days=1), revoked=True)
    _session(other, expires_at=now - timedelta(seconds=1))

    assert identity.purge_expired_sessions_all(now=now) == 3
    with owner_engine.connect() as connection:
        left = set(connection.execute(text("SELECT id FROM user_sessions")).scalars())
    assert left == {keep_active, keep_revoked}
    assert identity.purge_expired_sessions_all(now=now) == 0


def test_session_purge_matches_the_per_user_login_cleanup(actor: int) -> None:
    """같은 술어 — 같은 만료 분포를 가진 두 사용자에 대해 로그인 청소와 전역 청소가 남기는 만료 시각 집합이 같다"""
    now = utcnow()
    twin = create_user("purge-twin@example.com", roles=(RoleCode.TRADE,))
    offsets = (-3600, -1, 0, 1, 3600)
    for user in (actor, twin):
        for offset in offsets:
            _session(user, expires_at=now + timedelta(seconds=offset))

    def survivors(user: int) -> set[int]:
        with owner_engine.connect() as connection:
            rows = connection.execute(
                text("SELECT expires_at FROM user_sessions WHERE user_id = :u"), {"u": user}
            ).scalars()
            return {round((row - now).total_seconds()) for row in rows}

    with unit_of_work() as uow:
        identity._purge_expired_sessions(uow.session, actor, now)  # 로그인 경로 — actor만
    by_login = survivors(actor)
    identity.purge_expired_sessions_all(now=now)  # 전역 — twin 포함
    assert survivors(twin) == by_login == {1, 3600}


def test_an_active_session_still_authenticates_after_the_purge(actor: int) -> None:
    """③ 청소 직후에도 활성 세션 토큰은 그대로 인증된다(로그아웃 유발 0)"""
    now = utcnow()
    with unit_of_work() as uow:
        token, _row = identity._issue_session(uow.session, actor, now)
    _session(actor, expires_at=now - timedelta(minutes=5))
    assert identity.purge_expired_sessions_all() == 1
    resolved = identity.resolve_session(token)
    assert resolved is not None and resolved.id == actor


# ── 잡 배선 ─────────────────────────────────────────────────────────────────


def test_the_registered_jobs_call_the_purges(actor: int) -> None:
    """레지스트리의 두 잡이 실제 청소를 돌리고 지운 수를 돌려준다"""
    now = utcnow()
    _bulk_expired_keys(actor, 3, now=now)
    _session(actor, expires_at=now - timedelta(minutes=1))
    assert scheduler.JOBS_BY_CODE["idempotency-purge"].run() == {"deleted": 3}
    assert scheduler.JOBS_BY_CODE["session-purge"].run() == {"deleted": 1}
    assert scheduler.JOBS_BY_CODE["idempotency-purge"].run() == {"deleted": 0}


def test_a_failing_purge_marks_the_job_failed_and_alerts_admins(
    actor: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패는 기존 실행기 경로로 FAILED + 관리자 알림(fail-visible)"""
    admin = create_user("purge-admin@example.com", roles=(RoleCode.ADMIN,))
    scheduler.register_jobs()

    def boom(**_: Any) -> int:
        raise RuntimeError("청소 실패 시험")

    monkeypatch.setattr(idempotency, "purge_expired_all", boom)
    with owner_engine.begin() as connection:
        # 다른 잡은 방금 돈 것으로 둬서 이번 틱에 청소 잡만 돌게 한다.
        connection.execute(
            text("UPDATE scheduled_jobs SET last_run_at = now() WHERE code <> 'idempotency-purge'")
        )
    counts = scheduler.run_due_jobs()
    assert counts["failed"] == 1
    with owner_engine.connect() as connection:
        status = connection.execute(
            text("SELECT last_status FROM scheduled_jobs WHERE code = 'idempotency-purge'")
        ).scalar_one()
    assert status == "FAILED"
    assert (
        _count(
            "alerts",
            "recipient_user_id = :a AND entity_type = 'scheduled_jobs' AND severity = 'CRITICAL'",
            a=admin,
        )
        == 1
    )


# ── H. 동시 실행 ────────────────────────────────────────────────────────────


@pytest.mark.group_h
@pytest.mark.concurrency
def test_concurrent_purges_delete_each_row_exactly_once(actor: int) -> None:
    """H — 청소 4개를 동시에 출발: 합계 = 후보 수, 오류·교착 0, 미만료 보존(SKIP LOCKED로 서로 기다리지 않는다)"""
    now = utcnow()
    _bulk_expired_keys(actor, 3000, now=now)
    keep = _key(actor, "keep", expires_at=now + timedelta(hours=1))
    outcomes = run_concurrently(
        lambda _i: idempotency.purge_expired_all(now=now, batch=200), workers=4
    )
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert sum(int(o.value) for o in outcomes) == 3000
    assert _count("idempotency_keys") == 1
    assert _count("idempotency_keys", "id = :i", i=keep) == 1


@pytest.mark.group_h
@pytest.mark.concurrency
def test_concurrent_session_purge_and_login_cleanup_agree(actor: int) -> None:
    """H — 전역 청소와 사용자 로그인 청소가 같은 순간에 겹쳐도 오류 0·만료분 0·활성 세션 보존"""
    now = utcnow()
    users = [actor] + [
        create_user(f"purge-h-{i}@example.com", roles=(RoleCode.TRADE,)) for i in range(3)
    ]
    keep = {u: _session(u, expires_at=now + timedelta(hours=1)) for u in users}
    for u in users:
        for minutes in range(1, 6):
            _session(u, expires_at=now - timedelta(minutes=minutes))

    def work(i: int) -> int:
        if i == 0:
            return identity.purge_expired_sessions_all(now=now, batch=3)
        with unit_of_work() as uow:
            identity._purge_expired_sessions(uow.session, users[i], now)
        return 0

    outcomes = run_concurrently(work, workers=len(users))
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert _count("user_sessions", "expires_at <= :n", n=now) == 0
    with owner_engine.connect() as connection:
        left = set(connection.execute(text("SELECT id FROM user_sessions")).scalars())
    assert left == set(keep.values())
