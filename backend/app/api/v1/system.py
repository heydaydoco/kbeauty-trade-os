"""시스템 엔드포인트 — 헬스체크.

인증이 붙는 S0-2에서 이 두 경로는 인증 예외 목록(allowlist)에 넣어야 한다.
그러지 않으면 컨테이너 헬스체크가 401을 받고 계속 재시작한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.core import health

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/healthz", summary="살아 있는지 (DB 비의존)")
def healthz() -> dict[str, Any]:
    return health.liveness()


@router.get("/readyz", summary="요청을 받아도 되는지 (DB·마이그레이션 확인)")
def readyz(response: Response) -> dict[str, Any]:
    ready, payload = health.readiness()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload
