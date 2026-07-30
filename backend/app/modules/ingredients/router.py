"""성분 엔드포인트 (DESIGN.md §4.3 / §12.2 CSV / §18.4 페이지네이션).

★ 여기에 "성분 자동 판정"·"규제 자동 조회" 엔드포인트를 추가하지 않는다
  (§1 비범위 — HS와 같은 지위다). 스크리닝은 판정이 아니라 등록된 규칙과의
  기계적 대조 리포트이고 screening.py가 맡는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.ingredients import screening, service
from app.modules.ingredients.schemas import (
    IngredientCreateRequest,
    IngredientRuleCreateRequest,
    IngredientRuleSummary,
    IngredientSummary,
    ProductIngredientCreateRequest,
    ProductIngredientSummary,
    ScreeningReportSummary,
)

#: 마스터 등록은 무역이 한다(관리자는 항상 통과) — catalog와 같은 규약.
CAN_REGISTER = (RoleCode.TRADE,)

router = APIRouter(prefix="/ingredients", tags=["ingredients"])
#: 전성분은 제품(처방)의 하위 자원이다 — 경로는 /products 밑, 코드는 이 모듈.
product_ingredients_router = APIRouter(prefix="/products", tags=["products"])

INGREDIENT_CSV_HEADER = ("INCI명", "표시명(국문)", "비고")


# ── 성분 마스터 ─────────────────────────────────────────────────────────────


@router.post(
    "",
    summary="성분 등록 (자사 취급 성분만 — §4.3)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_ingredient(
    payload: IngredientCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> IngredientSummary:
    status_code, body = service.create_ingredient(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return IngredientSummary.model_validate(body)


@router.get("", summary="성분 목록")
def list_ingredients(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[IngredientSummary]:
    views, total = service.list_ingredients(offset=params.offset, limit=params.limit)
    return Page.of([IngredientSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="성분 목록 CSV 내보내기 (UTF-8 BOM)")
def export_ingredients_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_ingredients_for_export()
    return csv_response(
        "성분목록.csv",
        INGREDIENT_CSV_HEADER,
        [(view.inci_name, view.name_ko, view.note) for view in views],
    )


# ★ `/{ingredient_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다. 앞에 두면
#   FastAPI가 "export.csv"를 ingredient_id로 해석해 422를 낸다.
@router.get("/{ingredient_id}", summary="성분 상세")
def get_ingredient(ingredient_id: int, current: CurrentUser) -> IngredientSummary:
    return IngredientSummary.of(service.get_ingredient(ingredient_id))


# ── 성분×국가 규칙 (§4.3 / ADR-03) ─────────────────────────────────────────


@router.post(
    "/{ingredient_id}/rules",
    summary="성분 국가별 규칙 등록 (사람이 확인한 규칙의 기록)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_ingredient_rule(
    ingredient_id: int,
    payload: IngredientRuleCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> IngredientRuleSummary:
    status_code, body = service.create_ingredient_rule(
        actor=current,
        idempotency_key=key,
        ingredient_id=ingredient_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return IngredientRuleSummary.model_validate(body)


@router.get("/{ingredient_id}/rules", summary="성분 국가별 규칙 목록")
def list_ingredient_rules(
    ingredient_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[IngredientRuleSummary]:
    views, total = service.list_ingredient_rules(
        ingredient_id=ingredient_id, offset=params.offset, limit=params.limit
    )
    return Page.of([IngredientRuleSummary.of(view) for view in views], total, params)


# ── 전성분 (제품×성분 — §4.3 "함량·표시순서") ──────────────────────────────


@product_ingredients_router.post(
    "/{product_id}/ingredients",
    summary="제품 전성분에 성분 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def add_product_ingredient(
    product_id: int,
    payload: ProductIngredientCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProductIngredientSummary:
    status_code, body = service.add_product_ingredient(
        actor=current,
        idempotency_key=key,
        product_id=product_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return ProductIngredientSummary.model_validate(body)


@product_ingredients_router.get("/{product_id}/ingredients", summary="제품 전성분 목록 (표시순서)")
def list_product_ingredients(
    product_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ProductIngredientSummary]:
    views, total = service.list_product_ingredients(
        product_id=product_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ProductIngredientSummary.of(view) for view in views], total, params)


# ── 스크리닝 (§4.3 — 판정 아님·게이트 아님·미저장) ─────────────────────────


@product_ingredients_router.get(
    "/{product_id}/screening",
    summary="성분 스크리닝 리포트 — 등록된 규칙과의 기계적 대조 (참고용·근거 확인)",
)
def screen_product(
    product_id: int,
    current: CurrentUser,
    country: Annotated[
        list[str], Query(description="대상국 코드(반복 지정). 예: ?country=US&country=EU")
    ],
) -> ScreeningReportSummary:
    report = screening.screen_product(product_id=product_id, countries=country)
    return ScreeningReportSummary.model_validate(screening.serialize_report(report))
