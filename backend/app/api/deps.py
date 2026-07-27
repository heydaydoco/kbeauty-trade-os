"""라우터가 쓰는 의존성.

★ 세션(Session)을 라우터에 직접 넘기지 않는다.
  라우터가 세션을 손에 쥐면 거기서 add/commit을 하게 되고, 그 순간
  §17.1의 "업무 동작 하나 = 트랜잭션 하나"가 화면 단위로 쪼개진다.
  쓰기는 unit_of_work()를 통해서만, 읽기는 읽기 전용 세션으로만 한다.

★ 인증은 `CurrentUser`로만 받는다.
  라우터가 쿠키를 직접 읽어 사용자를 찾는 코드가 생기면 그 경로마다 검증이
  달라진다. 여기 한 곳을 통과해야 app/api/public.py의 커버리지 검사가 의미를
  가진다(§18.1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.db.session import SessionFactory
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError, UnauthenticatedError
from app.core.logging.context import user_id_var, user_role_var
from app.modules.idempotency.service import IDEMPOTENCY_HEADER
from app.modules.identity import service as identity_service
from app.modules.identity.models import RoleCode
from app.modules.identity.service import SESSION_COOKIE_NAME, AuthenticatedUser


def get_read_session() -> Iterator[Session]:
    """조회 전용 세션. 커밋하지 않는다(GuardedSession이 막는다)."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request) -> AuthenticatedUser:
    """세션 쿠키로 지금 요청을 보낸 사람을 특정한다. 아니면 401."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise UnauthenticatedError()
    principal = identity_service.resolve_session(token)
    if principal is None:
        # 만료·폐기·계정 비활성 중 무엇인지 밝히지 않는다. 화면이 할 일은
        # 어느 경우든 "다시 로그인"으로 같다.
        raise UnauthenticatedError()

    # 이 요청의 모든 로그 줄에 누가 한 일인지 붙는다(§18.3 추적).
    user_id_var.set(principal.id)
    user_role_var.set(",".join(sorted(role.value for role in principal.roles)))
    return principal


#: 라우터에서 `current: CurrentUser`로 받는다.
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _role_guard(*allowed: RoleCode) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """지정한 역할 중 하나라도 있어야 통과하는 검사기를 만든다 (§18.1 API 레벨 인가).

    ADMIN은 언제나 통과한다 — 관리자를 개별 목록에 매번 적게 하면 언젠가
    빠뜨리고, 빠뜨린 화면은 관리자가 못 쓰는 화면이 된다.
    """

    def _guard(current: CurrentUser) -> AuthenticatedUser:
        if RoleCode.ADMIN in current.roles or current.has_any_role(*allowed):
            return current
        raise ForbiddenError(
            log_context={
                "required_roles": sorted(role.value for role in allowed),
                "actual_roles": sorted(role.value for role in current.roles),
            }
        )

    return _guard


def require_roles(*allowed: RoleCode) -> Any:
    """`dependencies=[...]`에 넣는 형태 — 행위자를 쓰지 않는 엔드포인트용.

    @router.get("/users", dependencies=[require_roles(RoleCode.ADMIN)])
    """
    return Depends(_role_guard(*allowed))


#: 행위자가 필요한 관리자 전용 엔드포인트에서 `current: AdminUser`로 받는다.
AdminUser = Annotated[AuthenticatedUser, Depends(_role_guard(RoleCode.ADMIN))]


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> str:
    """확정·생성 요청의 멱등 키 (§17.4).

    없으면 거절한다. "있으면 쓰고 없으면 그냥 실행"으로 두면 더블클릭 보호가
    클라이언트의 선의에 달리게 되고, 헤더를 빠뜨린 화면 하나가 조용히 이중 전표를
    만든다. 프런트 lib/api.ts는 모든 쓰기 요청에 자동으로 붙인다.
    """
    if not idempotency_key:
        raise AppError(ErrorCode.IDEMPOTENCY_KEY_REQUIRED)
    return idempotency_key


#: 라우터에서 `key: IdempotencyKey`로 받는다.
IdempotencyKey = Annotated[str, Depends(get_idempotency_key)]
