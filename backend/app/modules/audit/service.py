"""감사 기록 (DESIGN.md §18.1 "로그인·권한변경은 audit 필수").

호출부는 반드시 **업무 트랜잭션과 같은 세션**으로 부른다. 별도 트랜잭션으로
남기면 업무가 롤백돼도 감사 기록만 살아남아 "일어나지 않은 일"이 장부에 남는다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging.context import request_id_var
from app.modules.audit.models import AuditLog


def record(
    session: Session,
    *,
    action: str,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """감사 로그 한 줄을 현재 트랜잭션에 추가한다.

    request_id는 컨텍스트에서 자동으로 가져온다 — 호출부가 매번 넘기게 하면
    빠뜨린 곳이 생기고, 빠진 줄은 사용자 신고와 이어 붙일 수 없다(§18.3).

    detail에 비밀번호·토큰 같은 민감값을 넣지 않는다. 여기 담긴 값은 로그
    마스킹 프로세서를 거치지 않고 DB에 그대로 남는다.
    """
    entry = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id_var.get(),
        detail=detail or {},
    )
    session.add(entry)
    return entry
