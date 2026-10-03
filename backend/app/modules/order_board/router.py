"""오더 보드 엔드포인트 — 보드·열 드릴다운·CSV·벌크·저장 필터 (S3-1 PR-15a / design-D D6·D7 / ADR-0066·0067).

권한: 보드·드릴다운·CSV 조회는 **전 역할**(카드에 원가·마진·여신·게이트 필드가 없다), 벌크는 **무역**(관리자 상시 통과 — 서비스가 역할을 한 번 더 확인하고
각 건은 단일 통로가 다시 판정한다), 저장 필터는 **전 역할·본인 것만**(타인 id 404). 쿼리·본문 모델은 전부 `extra="forbid"`(모르는 키 422).
`GET /order-board`는 함수명이 `list_`로 시작하지 않는 **비-Page 단일 객체**다(의도된 예외 — 고정 4열·열당 50). 쓰기 POST는 `Idempotency-Key` 필수, PATCH·DELETE는 `version`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.order_board import bulk, saved_filters, service
from app.modules.order_board.schemas import (
    BoardCard,
    BoardExportQuery,
    BoardFilter,
    BoardItemsQuery,
    OrderBoardBulkOut,
    OrderBoardBulkRequest,
    OrderBoardOut,
    SavedFilterCreateRequest,
    SavedFilterOut,
    SavedFilterUpdateRequest,
)

CAN_BULK = (RoleCode.TRADE,)

router = APIRouter(prefix="/order-board", tags=["order-board"])

_FILTER_FIELDS = set(BoardFilter.model_fields)


def _filter_of(query: BoardFilter) -> BoardFilter:
    """하위 쿼리 모델(열·페이지 포함)에서 필터 부분만 떼어 낸다."""
    return BoardFilter.model_validate(query.model_dump(include=_FILTER_FIELDS))


@router.get(
    "",
    summary="오더 보드 (고정 4열 — 인테이크 대기·수주 접수·수주 보류·수주 확정, 열당 최대 50건+전체 건수·더 있음 표시, 전 역할)",
)
def get_order_board(
    current: CurrentUser, filters: Annotated[BoardFilter, Query()]
) -> OrderBoardOut:
    return OrderBoardOut.model_validate(service.get_order_board(filters))


@router.get("/items", summary="오더 보드 열 하나의 더 보기 (페이지 — 기본 50건, 전 역할)")
def list_order_board_items(
    current: CurrentUser, query: Annotated[BoardItemsQuery, Query()]
) -> Page[BoardCard]:
    params = PageParams(page=query.page, size=query.size)
    items, total = service.list_board_items(
        _filter_of(query), query.stage, offset=params.offset, limit=params.limit
    )
    return Page.of([BoardCard.model_validate(item) for item in items], total, params)


@router.get(
    "/export.csv",
    summary="오더 보드 CSV 내보내기 (같은 필터·UTF-8 BOM·최대 50,000행 — 원가·마진·게이트·여신 열 없음, 전 역할)",
)
def export_order_board_csv(
    current: CurrentUser, query: Annotated[BoardExportQuery, Query()]
) -> StreamingResponse:
    rows = service.export_rows(_filter_of(query), query.stage)
    return csv_response("오더보드.csv", service.EXPORT_HEADER, rows)


@router.post(
    "/bulk",
    summary="오더 보드 벌크 (인테이크 확정·수주 확정·담당자 지정 — 최대 50건, 건마다 독립 처리·결과 리포트. 승인·예외 승인은 벌크로 부여·우회할 수 없다)",
    dependencies=[require_roles(*CAN_BULK)],
)
def run_order_board_bulk(
    payload: OrderBoardBulkRequest, current: CurrentUser, key: IdempotencyKey
) -> OrderBoardBulkOut:
    body = bulk.run_bulk(
        actor=current,
        idempotency_key=key,
        action=payload.action,
        targets=[
            bulk.Target(kind=t.kind, id=t.id, expected_version=t.expected_version)
            for t in payload.targets
        ],
        assignee_id=payload.assignee_id,
    )
    return OrderBoardBulkOut.model_validate(body)


@router.get("/saved-filters", summary="내 저장 필터 목록 (본인 것만 — 전 역할)")
def list_saved_filters(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[SavedFilterOut]:
    items, total = saved_filters.list_saved_filters(
        actor=current, offset=params.offset, limit=params.limit
    )
    return Page.of([SavedFilterOut.model_validate(item) for item in items], total, params)


@router.post(
    "/saved-filters",
    summary="저장 필터 만들기 (본인 것 — 사람당 20개·이름 유일)",
    status_code=status.HTTP_201_CREATED,
)
def create_saved_filter(
    payload: SavedFilterCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SavedFilterOut:
    status_code, body = saved_filters.create_saved_filter(
        actor=current,
        idempotency_key=key,
        name=payload.name,
        filter_config=payload.filter_config,
    )
    response.status_code = status_code
    return SavedFilterOut.model_validate(body)


@router.patch(
    "/saved-filters/{filter_id}",
    summary="저장 필터 수정 (본인 것만 — 다른 사람의 필터는 404)",
)
def update_saved_filter(
    filter_id: Annotated[int, Path(ge=1)],
    payload: SavedFilterUpdateRequest,
    current: CurrentUser,
) -> SavedFilterOut:
    body = saved_filters.update_saved_filter(
        actor=current, filter_id=filter_id, payload=payload.model_dump(exclude_unset=True)
    )
    return SavedFilterOut.model_validate(body)


@router.delete(
    "/saved-filters/{filter_id}",
    summary="저장 필터 삭제 (본인 것만 — 다른 사람의 필터는 404)",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_saved_filter(
    filter_id: Annotated[int, Path(ge=1)],
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="화면이 본 version(낙관 잠금)")],
) -> Response:
    saved_filters.delete_saved_filter(actor=current, filter_id=filter_id, version=version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
