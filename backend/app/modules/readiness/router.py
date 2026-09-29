"""시장 준비도 매트릭스 엔드포인트 (§5.3 / §18.1 / §18.4).

읽기 전용이다 — 쓰기 메서드가 없고, 서비스도 아무것도 저장하지 않는다(계산값
미저장이 스펙 자체). 열람은 **전 역할**이다(계획 안건 ①(f) — 원가·마진 필드가 없어
마스킹 비대상이고, 준비도는 무역·물류·조회 모두의 판단 재료다).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser
from app.core.pagination import PageParams
from app.modules.readiness import service
from app.modules.readiness.schemas import MatrixPage

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("/matrix", summary="시장 준비도 매트릭스 (SKU × 시장 — 계산값, 저장하지 않는다)")
def get_readiness_matrix(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="SKU 코드·품명 부분 검색")
    ] = None,
    item_profile_id: Annotated[int | None, Query(ge=1, description="품목군으로 좁힘")] = None,
    kind: Annotated[Literal["SINGLE", "SET"] | None, Query(description="단품/세트")] = None,
) -> MatrixPage:
    view = service.get_matrix(
        offset=params.offset,
        limit=params.limit,
        q=q,
        item_profile_id=item_profile_id,
        kind=kind,
    )
    return MatrixPage.from_view(view, params)
