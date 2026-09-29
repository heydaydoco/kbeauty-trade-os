"""T1 시드 엔드포인트 (§5.5 / S2-4 PR-2). 투입=인증(관리자 상시 통과), 현황 열람=전 역할."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.modules.identity.models import RoleCode
from app.modules.seeds import service, wizard
from app.modules.seeds.schemas import (
    ApplyT1Request,
    ApplyT1Response,
    CatalogStatusSummary,
    WizardSummary,
)

CAN_EDIT = (RoleCode.CERT,)

router = APIRouter(prefix="/seeds", tags=["seeds"])
wizard_router = APIRouter(prefix="/market-wizard", tags=["seeds"])


@router.get("/t1", summary="T1 시드 카탈로그와 시장별 투입 현황")
def t1_status(current: CurrentUser) -> CatalogStatusSummary:
    return CatalogStatusSummary.of(service.catalog_status())


@router.post(
    "/t1/apply",
    summary="선택한 시장의 T1 요건 템플릿 초안 투입 (멱등 — 있는 템플릿은 건너뜀)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def apply_t1(payload: ApplyT1Request, current: CurrentUser, key: IdempotencyKey) -> ApplyT1Response:
    _, body = service.apply_t1(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    return ApplyT1Response.model_validate(body)


@wizard_router.get("/{code}", summary="신규 시장 위저드 — 5단계 진행 계산(읽기 전용·저장 없음)")
def market_wizard(code: str, current: CurrentUser) -> WizardSummary:
    return WizardSummary.model_validate(asdict(wizard.market_wizard(code)))
