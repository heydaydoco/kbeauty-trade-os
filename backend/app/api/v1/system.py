"""시스템 엔드포인트 — 헬스체크·통화 자릿수.

인증이 붙는 S0-2에서 헬스 두 경로는 인증 예외 목록(allowlist)에 넣어야 한다.
그러지 않으면 컨테이너 헬스체크가 401을 받고 계속 재시작한다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core import health
from app.core.money import CURRENCY_MINOR_UNITS
from app.core.pagination import Page, PageParams

router = APIRouter(prefix="/system", tags=["system"])


class Currency(BaseModel):
    code: str
    #: 소수 자릿수. KRW=0, USD=2. 화면은 이 값으로만 최소단위↔표시값을 바꾼다.
    minor_units: int


@router.get("/healthz", summary="살아 있는지 (DB 비의존)")
def healthz() -> dict[str, Any]:
    return health.liveness()


@router.get("/readyz", summary="요청을 받아도 되는지 (DB·마이그레이션 확인)")
def readyz(response: Response) -> dict[str, Any]:
    ready, payload = health.readiness()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/currencies", summary="통화별 소수 자릿수")
def list_currencies(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[Currency]:
    """금액 표시의 유일한 출처 (§2 ADR-02 / 부채 #9).

    ★ 화면이 자릿수 표를 따로 갖지 않게 하려고 만든 경로다. 프런트에 같은 표를
      복사해 두면 통화가 추가될 때 한쪽만 갱신되고, 그러면 같은 금액이 서버와
      화면에서 다르게 보인다 — 그 차이는 정산 대사에서야 드러난다.
    """
    items = [
        Currency(code=code, minor_units=units)
        for code, units in sorted(CURRENCY_MINOR_UNITS.items())
    ]
    window = items[params.offset : params.offset + params.limit]
    return Page.of(window, len(items), params)
