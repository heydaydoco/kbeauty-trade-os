"""TTL 기술 행 청크 삭제 (ADR-0013·0014 예약 청소 이행 — S3-1 PR-16 / ADR-0058 ⑤ / design-F F18).

각 소유 서비스(`idempotency.service.purge_expired_all`·`identity.service.purge_expired_sessions_all`)가
자기 테이블과 술어를 넘기고, 이 헬퍼는 **청크 반복 커밋**만 맡는다.

★ **청크마다 독립 트랜잭션이다**(§17.6 — 큰 삭제를 한 트랜잭션으로 묶으면 잠금·WAL·복제 지연이 길어진다).
  중간 실패 시 이미 커밋된 청크는 남고, 재실행이 나머지를 이어서 지운다(멱등 — 같은 술어).
★ **잠긴 행은 건너뛴다**(`FOR UPDATE SKIP LOCKED`). 지금 다른 트랜잭션이 쥐고 있는 행 — 진행 중인 멱등
  claim의 이어받기, 세션 `last_seen_at` 갱신 — 을 청소가 기다리지도(잡이 요청 경로에 끌려가지 않음),
  빼앗지도 않는다(fail-closed: 애매하면 남긴다). 남은 행은 다음 회차가 지운다.
★ **기준 시각은 호출 시작 시 한 번 고정한다** — 루프 도중 새로 만료되는 행 때문에 끝나지 않는 일이 없다.
★ 삭제 수는 `RETURNING`으로 실제 지운 행을 센다(rowcount는 드라이버마다 신뢰도가 다르다 — handover 선례).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, select

from app.core.db.uow import unit_of_work

#: 한 트랜잭션에서 지우는 최대 행 수(ADR-0058 ⑤ "1,000행 청크").
PURGE_BATCH = 1000


def purge_in_batches(
    model: Any, predicate: ColumnElement[bool], *, batch: int = PURGE_BATCH
) -> int:
    """`predicate`에 맞는 `model` 행을 `batch`개씩 독립 트랜잭션으로 지운다. 지운 총수를 돌려준다.

    청크가 `batch`보다 적게 지워지면 끝낸다 — 남은 후보가 없거나, 남은 것은 전부 잠긴 행이다.
    """
    if batch < 1:
        raise ValueError("batch는 1 이상이어야 합니다.")
    total = 0
    while True:
        with unit_of_work() as uow:
            chunk = (
                select(model.id)
                .where(predicate)
                .order_by(model.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            deleted = len(
                uow.session.execute(
                    delete(model).where(model.id.in_(chunk)).returning(model.id)
                ).all()
            )
        total += deleted
        if deleted < batch:
            return total


def expired_before(column: Any, moment: datetime) -> ColumnElement[bool]:
    """만료 술어 — 기존 사용자별 청소와 같은 `expires_at <= now`(유예 신설 없음, ADR-0058 ⑤)."""
    predicate: ColumnElement[bool] = column <= moment
    return predicate
