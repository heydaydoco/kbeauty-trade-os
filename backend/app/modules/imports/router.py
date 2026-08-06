"""엑셀 왕복 임포트 엔드포인트 (DESIGN.md §12.2 / ADR-09 / §15 L2).

경로 규칙: 정적 경로(/registry, /staging…)를 먼저 선언하고, 레지스트리 코드
경로(/{registry_code}/…)는 마지막이다 — 앞에 두면 "staging"이 코드로 읽힌다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.imports import service
from app.modules.imports.registry import IMPORT_TARGETS
from app.modules.imports.schemas import (
    ImportConfirmResult,
    ImportRegistryEntry,
    ImportRegistryResponse,
    ImportStagingRowSummary,
    ImportStagingSummary,
)

#: 마스터를 만드는 경로이므로 등록과 같은 역할이다(관리자는 항상 통과).
CAN_IMPORT = (RoleCode.TRADE,)

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/registry", summary="임포트 레지스트리 (§12.2 — 대상·표준 양식 목록)")
def import_registry(current: CurrentUser) -> ImportRegistryResponse:
    """고정 레지스트리다 — DB 목록이 아니라 코드에 결합된 어댑터 목록(ADR-11)."""
    return ImportRegistryResponse(
        items=[
            ImportRegistryEntry(
                code=target.code, label_ko=target.label_ko, headers=list(target.header)
            )
            for target in IMPORT_TARGETS.values()
        ]
    )


@router.get("/staging", summary="임포트 스테이징 목록")
def list_import_stagings(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ImportStagingSummary]:
    views, total = service.list_stagings(offset=params.offset, limit=params.limit)
    return Page.of([ImportStagingSummary.of(view) for view in views], total, params)


@router.get("/staging/{staging_id}", summary="임포트 스테이징 상세")
def get_import_staging(staging_id: int, current: CurrentUser) -> ImportStagingSummary:
    return ImportStagingSummary.of(service.get_staging(staging_id))


@router.get("/staging/{staging_id}/rows", summary="스테이징 행 목록 (신규·변경·오류)")
def list_import_staging_rows(
    staging_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ImportStagingRowSummary]:
    views, total = service.list_staging_rows(
        staging_id=staging_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ImportStagingRowSummary.of(view) for view in views], total, params)


@router.post(
    "/staging/{staging_id}/confirm",
    summary="스테이징 확정 — 사람 1클릭 (§15 L2, 자동 확정 없음)",
    dependencies=[require_roles(*CAN_IMPORT)],
)
def confirm_import_staging(
    staging_id: int,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ImportConfirmResult:
    status_code, body = service.confirm_staging(
        actor=current, idempotency_key=key, staging_id=staging_id
    )
    response.status_code = status_code
    return ImportConfirmResult.model_validate(body)


@router.delete(
    "/staging/{staging_id}",
    summary="검토 대기 스테이징 파기 (soft delete — 확정본은 불가)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_IMPORT)],
)
def delete_import_staging(staging_id: int, current: CurrentUser) -> None:
    service.delete_staging(actor=current, staging_id=staging_id)


@router.post(
    "/{registry_code}/staging",
    summary="왕복 CSV 업로드 → 변경분 diff 스테이징 (§12.2)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_IMPORT)],
)
def stage_import(
    registry_code: str,
    file: Annotated[UploadFile, File(description="왕복 CSV 파일 (목록 내보내기 형식)")],
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ImportStagingSummary:
    status_code, body = service.stage_import(
        actor=current,
        idempotency_key=key,
        registry_code=registry_code,
        stream=file.file,
        filename=file.filename or "",
    )
    response.status_code = status_code
    return ImportStagingSummary.model_validate(body)


@router.get(
    "/{registry_code}/template.csv",
    summary="표준 양식 다운로드 — 헤더 행 = 스키마 문서 (§12.2)",
)
def download_import_template(registry_code: str, current: CurrentUser) -> StreamingResponse:
    target = service.require_target(registry_code)
    return csv_response(f"{target.label_ko}_임포트양식.csv", target.header, [])
