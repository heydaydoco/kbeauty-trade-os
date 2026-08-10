"""시장 마스터 엔드포인트 (§5.1 / §18.4 페이지네이션 / §12.2 CSV).

권한(S2-1 판정 ⑤): 편집(등록·정식화)=**인증**(관리자는 상시 통과 —
require_roles 규약), 무역·물류·조회=열람. 근거: §2 역할 문면 "인증 —
인증 요건·원산지 판정·문서"(PR #10 CERT 판정 계열).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.markets import service
from app.modules.markets.schemas import (
    MarketCreateRequest,
    MarketSummary,
    MarketUpdateRequest,
)

#: 시장·요건 편집은 인증 역할이 한다(관리자는 항상 통과) — S2-1 판정 ⑤.
CAN_EDIT = (RoleCode.CERT,)

router = APIRouter(prefix="/markets", tags=["markets"])


@router.post(
    "",
    summary="시장 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def create_market(
    payload: MarketCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MarketSummary:
    status_code, body = service.create_market(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return MarketSummary.model_validate(body)


@router.get("", summary="시장 목록")
def list_markets(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[MarketSummary]:
    views, total = service.list_markets(offset=params.offset, limit=params.limit)
    return Page.of([MarketSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="시장 목록 CSV 내보내기 (UTF-8 BOM — §12.2)")
def export_markets_csv(current: CurrentUser) -> StreamingResponse:
    """표시용 목록 CSV다 — 왕복 대상이 아니라 ID 열이 없다(라벨·규칙 CSV 선례)."""
    views = service.all_markets_for_export()
    return csv_response(
        "시장목록.csv",
        ("시장코드", "시장명", "영문명", "메모"),
        [(view.code, view.name_ko, view.name_en, view.note) for view in views],
    )


# ★ `/{market_id}`는 `/export.csv`보다 뒤에 선언해야 한다(앞에 두면 422).
@router.get("/{market_id}", summary="시장 상세")
def get_market(market_id: int, current: CurrentUser) -> MarketSummary:
    return MarketSummary.of(service.get_market(market_id))


@router.patch(
    "/{market_id}",
    summary="시장 정식화 편집 (이름·메모 — 코드는 불변)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_market(
    market_id: int, payload: MarketUpdateRequest, current: CurrentUser
) -> MarketSummary:
    view = service.update_market(
        actor=current, market_id=market_id, payload=payload.model_dump(mode="json")
    )
    return MarketSummary.of(view)
