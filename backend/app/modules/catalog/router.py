"""SKU 엔드포인트 — 등록→목록→CSV 관통 (DESIGN.md §19 Phase 0 / §12.2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.catalog import service
from app.modules.catalog.schemas import SkuCreateRequest, SkuSummary
from app.modules.identity.models import RoleCode

router = APIRouter(prefix="/skus", tags=["skus"])

#: 마스터 등록은 무역이 한다(관리자는 항상 통과 — require_roles 규약).
CAN_REGISTER = (RoleCode.TRADE,)

CSV_HEADER = ("품번", "품명(국문)", "품명(영문)", "상태")


@router.post(
    "",
    summary="SKU 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_sku(
    payload: SkuCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SkuSummary:
    status_code, body = service.create_sku(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return SkuSummary.model_validate(body)


@router.get("", summary="SKU 목록")
def list_skus(current: CurrentUser, params: Annotated[PageParams, Depends()]) -> Page[SkuSummary]:
    views, total = service.list_skus(offset=params.offset, limit=params.limit)
    return Page.of([SkuSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="SKU 목록 CSV 내보내기 (UTF-8 BOM)")
def export_skus_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_skus_for_export()
    return csv_response(
        "SKU목록.csv",
        CSV_HEADER,
        [(view.sku_code, view.name_ko, view.name_en, view.status) for view in views],
    )


@router.get("/{sku_id}", summary="SKU 상세")
def get_sku(sku_id: int, current: CurrentUser) -> SkuSummary:
    return SkuSummary.of(service.get_sku(sku_id))
