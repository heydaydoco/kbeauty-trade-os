"""거래처·품번 매핑·서명권자 엔드포인트 (§4.6·§4.7 / §12.2 CSV / §18.4 페이지네이션).

여신한도는 응답에 그대로 나간다 — 마스킹 비대상(마스킹 원장 2번, 웹 세션 판정
2026-08-05). 재판정 트리거가 오면(S3-1 여신 게이트·역할 세분화) ADR-0024의
스키마 분기 방식이 선례다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.money import Money
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode

# 헤더는 임포트 레지스트리와 한 상수다 — 내보내기와 왕복 양식이 갈라지면
# "내려받아 그대로 올리면 변화 0"(§12.2)이 그 자리에서 깨진다.
from app.modules.imports.registry import PARTNERS_CSV_HEADER as PARTNER_CSV_HEADER
from app.modules.partners import service
from app.modules.partners.schemas import (
    ItemCodeCreateRequest,
    ItemCodeSummary,
    PartnerCreateRequest,
    PartnerSummary,
    SignatoryCreateRequest,
    SignatorySummary,
)
from app.modules.partners.service import PartnerView

#: 마스터 등록은 무역이 한다(관리자는 항상 통과) — catalog·materials와 같은 규약.
CAN_REGISTER = (RoleCode.TRADE,)

router = APIRouter(prefix="/partners", tags=["partners"])
signatories_router = APIRouter(prefix="/signatories", tags=["signatories"])


def _credit_limit_text(view: PartnerView) -> str | None:
    """CSV용 사람 표기(12.34) — 등록 API와 같은 표기라 왕복(§12.2)이 대칭이다."""
    if view.credit_limit_amount is None or view.credit_limit_currency is None:
        return None
    return str(Money(view.credit_limit_amount, view.credit_limit_currency).to_decimal())


# ── 거래처 마스터 (§4.6) ───────────────────────────────────────────────────


@router.post(
    "",
    summary="거래처 등록 (다중 유형)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_partner(
    payload: PartnerCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PartnerSummary:
    status_code, body = service.create_partner(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return PartnerSummary.model_validate(body)


@router.get("", summary="거래처 목록")
def list_partners(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[PartnerSummary]:
    views, total = service.list_partners(offset=params.offset, limit=params.limit)
    return Page.of([PartnerSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="거래처 목록 CSV 내보내기 (UTF-8 BOM · 왕복 §12.2)")
def export_partners_csv(current: CurrentUser) -> StreamingResponse:
    """1열은 ID다 — 왕복 diff의 키(빈 ID=신규 / ID 있는 행=변경분만)."""
    views = service.all_partners_for_export()
    return csv_response(
        "거래처목록.csv",
        PARTNER_CSV_HEADER,
        [
            (
                view.id,
                view.partner_code,
                view.name_ko,
                "|".join(view.type_codes),
                _credit_limit_text(view),
                view.credit_limit_currency,
                view.dg_capable,
                view.strengths,
                view.weaknesses,
            )
            for view in views
        ],
    )


# ★ `/{partner_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{partner_id}", summary="거래처 상세")
def get_partner(partner_id: int, current: CurrentUser) -> PartnerSummary:
    return PartnerSummary.of(service.get_partner(partner_id))


# ── 바이어 품번 매핑 (§4.6 customer_item_codes) ────────────────────────────


@router.post(
    "/{partner_id}/item-codes",
    summary="바이어 품번 매핑 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def add_item_code(
    partner_id: int,
    payload: ItemCodeCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ItemCodeSummary:
    status_code, body = service.add_item_code(
        actor=current,
        idempotency_key=key,
        partner_id=partner_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return ItemCodeSummary.model_validate(body)


@router.get("/{partner_id}/item-codes", summary="바이어 품번 매핑 목록")
def list_item_codes(
    partner_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ItemCodeSummary]:
    views, total = service.list_item_codes(
        partner_id=partner_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ItemCodeSummary.of(view) for view in views], total, params)


# ── 서명권자 대장 (§4.7 — 최소 헤더) ───────────────────────────────────────


@signatories_router.post(
    "",
    summary="서명권자 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_signatory(
    payload: SignatoryCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SignatorySummary:
    status_code, body = service.create_signatory(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return SignatorySummary.model_validate(body)


@signatories_router.get("", summary="서명권자 목록")
def list_signatories(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[SignatorySummary]:
    views, total = service.list_signatories(offset=params.offset, limit=params.limit)
    return Page.of([SignatorySummary.of(view) for view in views], total, params)
