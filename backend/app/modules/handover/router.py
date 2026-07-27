"""담당 이관 엔드포인트 (관리자 전용 — DESIGN.md §2 / ADR-0015)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUser
from app.modules.handover import service
from app.modules.handover.schemas import HandoverRequest, HandoverResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/{user_id}/handover", summary="담당 일괄 이관 (관리자)")
def hand_over_assignments(
    user_id: int, payload: HandoverRequest, current: AdminUser
) -> HandoverResponse:
    """user_id가 담당하던 건 전부를 to_user_id에게 넘긴다(전부 아니면 0건)."""
    return HandoverResponse.of(
        service.reassign_all(
            from_user_id=user_id,
            to_user_id=payload.to_user_id,
            actor_user_id=current.id,
        )
    )
