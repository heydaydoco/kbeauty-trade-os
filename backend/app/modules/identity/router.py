"""인증 엔드포인트 (DESIGN.md §18.1 / ADR-0013).

/auth/login만 공개다. 나머지는 전부 인증을 요구한다 —
공개 경로 목록은 app/api/public.py 한 곳에 있고 메타 테스트가 그것을 강제한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import AdminUser, CurrentUser, require_roles
from app.core.config import settings
from app.core.errors.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.identity import service
from app.modules.identity.models import RoleCode
from app.modules.identity.schemas import (
    AccountActiveRequest,
    LoginRequest,
    MeResponse,
    RoleAssignmentRequest,
    UserSummary,
)
from app.modules.identity.service import SESSION_COOKIE_NAME, SESSION_LIFETIME, UserView

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _set_session_cookie(response: Response, token: str) -> None:
    """세션 쿠키 속성 (ADR-0013).

    httponly  — 자바스크립트가 못 읽는다. XSS가 나도 토큰은 안 새어 나간다.
    secure    — 운영에서만 켠다. dev는 http라 켜면 쿠키가 아예 안 붙는다.
    samesite  — lax. 외부 사이트발 POST에 쿠키가 실리지 않아 CSRF의 주요 경로가
                막히고, 평범한 링크 이동은 그대로 동작한다.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path="/",
    )


@router.post("/login", summary="로그인")
def login(payload: LoginRequest, request: Request, response: Response) -> MeResponse:
    token, principal = service.login(
        email=payload.email,
        password=payload.password,
        # 이미 들고 있던 세션은 로그인 성공과 함께 폐기된다(세션 고정 차단).
        existing_token=request.cookies.get(SESSION_COOKIE_NAME),
    )
    _set_session_cookie(response, token)
    return MeResponse.of(principal)


@router.post("/logout", summary="로그아웃", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, current: CurrentUser) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        service.logout(token, actor_user_id=current.id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", summary="내 정보")
def me(current: CurrentUser) -> MeResponse:
    return MeResponse.of(current)


# ── 사용자 관리 (§2 권한·통제 / §18.1 API 레벨 인가·소유권) ──────────────────


def _summary(view: UserView) -> UserSummary:
    return UserSummary(
        id=view.id,
        email=view.email,
        display_name=view.display_name,
        is_active=view.is_active,
        roles=sorted(role.value for role in view.roles),
    )


@users_router.get(
    "",
    summary="사용자 목록 (관리자)",
    dependencies=[require_roles(RoleCode.ADMIN)],
)
def list_users(params: Annotated[PageParams, Depends()]) -> Page[UserSummary]:
    views, total = service.list_users(offset=params.offset, limit=params.limit)
    return Page.of([_summary(view) for view in views], total, params)


@users_router.get("/{user_id}", summary="사용자 상세 (관리자 또는 본인)")
def get_user(user_id: int, current: CurrentUser) -> UserSummary:
    # ★ 소유권 검증 (§18.1 IDOR 차단). 남의 id를 넣어 부르는 것이 가장 흔한
    #   공격이자 가장 흔한 실수다 — 역할 검사만으로는 막히지 않는다.
    if RoleCode.ADMIN not in current.roles and current.id != user_id:
        raise ForbiddenError(log_context={"requested_user_id": user_id, "actor_id": current.id})
    view = service.get_user(user_id)
    if view is None:
        raise NotFoundError(log_context={"user_id": user_id})
    return _summary(view)


@users_router.post("/{user_id}/roles", summary="역할 부여 (관리자)")
def grant_role(user_id: int, payload: RoleAssignmentRequest, current: AdminUser) -> UserSummary:
    return _summary(
        service.grant_role(user_id=user_id, role=payload.role, actor_user_id=current.id)
    )


@users_router.delete("/{user_id}/roles/{role}", summary="역할 회수 (관리자)")
def revoke_role(user_id: int, role: RoleCode, current: AdminUser) -> UserSummary:
    return _summary(service.revoke_role(user_id=user_id, role=role, actor_user_id=current.id))


@users_router.patch("/{user_id}/active", summary="계정 활성·비활성 (관리자)")
def set_account_active(
    user_id: int, payload: AccountActiveRequest, current: AdminUser
) -> UserSummary:
    return _summary(
        service.set_account_active(
            user_id=user_id, is_active=payload.is_active, actor_user_id=current.id
        )
    )


@users_router.post("/{user_id}/unlock", summary="로그인 잠금 해제 (관리자)")
def unlock_account(user_id: int, current: AdminUser) -> UserSummary:
    return _summary(service.unlock_account(user_id=user_id, actor_user_id=current.id))
