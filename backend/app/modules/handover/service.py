"""담당 일괄 이관 (DESIGN.md §2 "담당 이관" / §20 H / ADR-0015).

퇴사·휴가·담당 변경 시 그 사람이 쥐고 있던 담당 건을 한 번에 넘긴다.

★ 전부 아니면 0건이다. 태스크는 넘어갔는데 알림 수신자는 그대로인 중간 상태가
  가장 위험하다 — 사람은 "이관했다"고 믿고, 남은 건은 아무에게도 안 보인 채로
  기일을 넘긴다. 그래서 대상 전체가 한 트랜잭션이다(§17.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.handover.targets import ASSIGNMENT_TARGETS
from app.modules.identity.models import User
from app.modules.outbox import service as outbox


@dataclass(frozen=True, slots=True)
class HandoverResult:
    from_user_id: int
    to_user_id: int
    moved: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.moved.values())


def reassign_all(*, from_user_id: int, to_user_id: int, actor_user_id: int) -> HandoverResult:
    """from_user의 담당 건 전부를 to_user에게 넘긴다."""
    if from_user_id == to_user_id:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"to_user_id": "인계자와 인수자가 같습니다. 다른 사용자를 선택해 주세요."},
        )

    with unit_of_work() as uow:
        session = uow.session
        _require_user(session, from_user_id, allow_inactive=True)
        # 인수자는 **활성**이어야 한다. 비활성 계정에 넘기면 로그인도 못 하는
        # 사람이 담당자가 되어, 이관했는데 아무도 못 보는 상태가 된다.
        _require_user(session, to_user_id, allow_inactive=False)

        moved: dict[str, int] = {}
        for target in ASSIGNMENT_TARGETS:
            # RETURNING으로 옮긴 행을 실제로 받아 센다. rowcount는 드라이버마다
            # 의미가 갈리고 타입도 흐릿해서, "몇 건 옮겼다"는 보고의 근거로는 약하다.
            moved_ids = (
                session.execute(
                    update(target.model)
                    .where(target.column == from_user_id)
                    .values({target.column.key: to_user_id})
                    .returning(target.model.id)
                )
                .scalars()
                .all()
            )
            moved[target.label] = len(moved_ids)

        audit.record(
            session,
            action=AuditAction.ASSIGNMENTS_HANDED_OVER,
            actor_user_id=actor_user_id,
            entity_type="users",
            entity_id=from_user_id,
            detail={"to_user_id": to_user_id, "moved": moved},
        )
        outbox.publish(
            session,
            event_type="identity.assignments.handed_over",
            aggregate_type="users",
            aggregate_id=from_user_id,
            payload={"from_user_id": from_user_id, "to_user_id": to_user_id, "moved": moved},
        )
        return HandoverResult(from_user_id=from_user_id, to_user_id=to_user_id, moved=moved)


def _require_user(session: Session, user_id: int, *, allow_inactive: bool) -> User:
    user = session.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError(log_context={"user_id": user_id})
    if not allow_inactive and not user.is_active:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"to_user_id": "비활성 계정에는 담당을 넘길 수 없습니다."},
            log_context={"user_id": user_id},
        )
    return user
