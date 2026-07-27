"""쓰기 API 멱등 흡수 (DESIGN.md §17.4 / GC-A3 / ADR-0014).

사용법 — **업무 트랜잭션 안에서** 감싼다:

    with unit_of_work() as uow:
        claim = idempotency.claim(uow.session, actor_user_id=..., endpoint="POST /skus",
                                  key=header_value, request_body=payload)
        if claim.replay is not None:
            return claim.replay          # 최초 결과 그대로
        ... 업무 로직 ...
        idempotency.complete(uow.session, claim.record, status_code=201, body=response)

★ 왜 미들웨어가 아니라 서비스 안인가.
  "최초 결과 반환"이 성립하려면 **업무와 멱등 기록이 같은 트랜잭션에서 커밋**돼야
  한다. 바깥에서 감싸면 업무는 커밋됐는데 기록은 실패하는 창이 생기고, 그 창에
  들어온 재시도는 업무를 두 번 수행한다.

★ 동시 2요청은 UNIQUE 충돌 → 선점 행의 잠금 대기로 직렬화된다. 뒤늦은 요청은
  앞 트랜잭션이 커밋된 뒤에야 행을 읽으므로 저장된 결과를 그대로 본다.
  (§17.4 "코드에서 확인 후 INSERT는 race로 뚫린다 — ON CONFLICT를 쓴다")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import utcnow
from app.modules.idempotency.models import IdempotencyKey

#: 보존 기한 (ADR-0014). 재시도는 몇 초~몇 분 안에 오지, 하루 뒤에 오지 않는다.
KEY_TTL = timedelta(hours=24)

#: 클라이언트가 보내는 헤더 이름 (프런트 lib/api.ts와 동일해야 한다).
IDEMPOTENCY_HEADER = "Idempotency-Key"


@dataclass(frozen=True, slots=True)
class Replay:
    """이미 처리된 요청의 최초 결과."""

    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Claim:
    """이번 요청이 처음인지(record) 재수신인지(replay)."""

    record: IdempotencyKey | None
    replay: Replay | None


def fingerprint(request_body: Any) -> str:
    """요청 본문의 지문. 키 순서가 달라도 같은 내용이면 같은 값이 나온다."""
    canonical = json.dumps(request_body, sort_keys=True, ensure_ascii=False, default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def claim(
    session: Session,
    *,
    actor_user_id: int,
    endpoint: str,
    key: str,
    request_body: Any,
) -> Claim:
    """이 (액터, 엔드포인트, 키) 조합을 선점하거나, 이미 있으면 최초 결과를 돌려준다."""
    now = utcnow()
    digest = fingerprint(request_body)

    _purge_expired(session, actor_user_id, now)

    inserted = session.execute(
        insert(IdempotencyKey)
        .values(
            actor_user_id=actor_user_id,
            endpoint=endpoint,
            idempotency_key=key,
            request_fingerprint=digest,
            expires_at=now + KEY_TTL,
        )
        .on_conflict_do_nothing(index_elements=["actor_user_id", "endpoint", "idempotency_key"])
        .returning(IdempotencyKey.id)
    ).scalar_one_or_none()
    session.flush()

    if inserted is not None:
        record = session.get(IdempotencyKey, inserted)
        assert record is not None
        return Claim(record=record, replay=None)

    # 이미 누가 선점했다. 그 행을 잠근다 — 앞 트랜잭션이 진행 중이면 여기서
    # 기다리고, 커밋된 뒤에 저장된 결과를 본다.
    existing = session.execute(
        select(IdempotencyKey)
        .where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.idempotency_key == key,
        )
        .with_for_update()
    ).scalar_one()

    if existing.request_fingerprint != digest:
        # 같은 키로 다른 내용. 조용히 최초 결과를 돌려주면 사용자는 바뀐 값이
        # 저장됐다고 믿는다 — 그게 이 검사가 있는 이유다.
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
            log_context={"endpoint": endpoint, "actor_user_id": actor_user_id},
        )

    if existing.completed_at is not None and existing.status_code is not None:
        return Claim(record=None, replay=Replay(existing.status_code, existing.response_body or {}))

    # 선점 행은 있는데 결과가 없다 = 앞 요청이 결과를 남기지 못하고 끝났다.
    # 이번 요청이 이어받아 수행한다(영원히 막힌 키를 만들지 않는다).
    return Claim(record=existing, replay=None)


def complete(
    session: Session,
    record: IdempotencyKey,
    *,
    status_code: int,
    body: dict[str, Any],
) -> None:
    """업무가 끝난 뒤 최초 결과를 기록한다. 커밋은 호출부의 트랜잭션이 한다."""
    record.completed_at = utcnow()
    record.status_code = status_code
    record.response_body = body


def _purge_expired(session: Session, actor_user_id: int, now: Any) -> None:
    """만료된 키를 치운다 (ADR-0014 — 전역 청소 배치는 S2-3).

    해당 액터분만 지운다. 요청 경로에서 테이블 전체를 훑으면 사용자가 그 비용을
    기다린다.
    """
    session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.expires_at <= now,
        )
    )
