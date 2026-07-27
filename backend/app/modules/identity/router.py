"""인증 엔드포인트 (DESIGN.md §18.1 / ADR-0013).

/auth/login만 공개다. 나머지는 전부 인증을 요구한다 —
공개 경로 목록은 app/api/public.py 한 곳에 있고 메타 테스트가 그것을 강제한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentUser
from app.core.config import settings
from app.modules.identity import service
from app.modules.identity.schemas import LoginRequest, MeResponse
from app.modules.identity.service import SESSION_COOKIE_NAME, SESSION_LIFETIME

router = APIRouter(prefix="/auth", tags=["auth"])


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
