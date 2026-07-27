"""담당 이관 요청·응답."""

from __future__ import annotations

from pydantic import BaseModel

from app.modules.handover.service import HandoverResult


class HandoverRequest(BaseModel):
    to_user_id: int


class HandoverResponse(BaseModel):
    from_user_id: int
    to_user_id: int
    #: 테이블별 이관 건수. 0건도 그대로 보여 준다 — "안 나온 항목"이 있으면
    #: 사람이 누락을 의심하게 되는데, 그 의심이 매번 헛수고가 된다.
    moved: dict[str, int]
    total: int

    @classmethod
    def of(cls, result: HandoverResult) -> HandoverResponse:
        return cls(
            from_user_id=result.from_user_id,
            to_user_id=result.to_user_id,
            moved=result.moved,
            total=result.total,
        )
