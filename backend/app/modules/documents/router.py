"""문서 보관소 엔드포인트 (§4.7·§4.8 / §18.1 다운로드 권한 검증 / ADR-0029).

다운로드는 이 라우터의 엔드포인트가 **유일한 통로**다(조건 4-②) — 저장 루트는
정적 서빙 경로 밖이라 nginx·StaticFiles로는 닿을 수 없고, 여기는 CurrentUser
인증을 지나야 한다(비인증 401은 아키텍처 테스트가 전 엔드포인트에 강제).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.documents import service
from app.modules.documents.schemas import (
    DocumentSummary,
    DocumentTypeSummary,
    LinkDocumentCreateRequest,
    ProfileDocumentTypeAddRequest,
    ProfileDocumentTypeSummary,
)
from app.modules.identity.models import RoleCode

#: 문서 등록·삭제는 무역+인증이 한다(관리자는 항상 통과) — CERT 역할 시드
#: 문면("인증 요건·원산지 판정·**문서**")이 문서를 명시한다. 마스터 등록이
#: TRADE 한정인 catalog·materials와 다른 점이고, 그 근거가 이 문면이다.
CAN_MANAGE = (RoleCode.TRADE, RoleCode.CERT)

router = APIRouter(prefix="/documents", tags=["documents"])
document_types_router = APIRouter(prefix="/document-types", tags=["documents"])
profile_document_types_router = APIRouter(prefix="/item-profiles", tags=["documents"])

DOCUMENT_CSV_HEADER = (
    "종류",
    "소유유형",
    "소유",
    "형태",
    "파일명",
    "링크",
    "크기(바이트)",
    "SHA-256",
    "발급일",
    "유효기간",
    "보존기한",
    "비고",
)


# ── 문서 종류 ──────────────────────────────────────────────────────────────


@document_types_router.get("", summary="문서 종류 목록 (§4.7 시드 — 추가는 마이그레이션)")
def list_document_types(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[DocumentTypeSummary]:
    views, total = service.list_document_types(offset=params.offset, limit=params.limit)
    return Page.of([DocumentTypeSummary.of(view) for view in views], total, params)


# ── 문서 (§4.7) ────────────────────────────────────────────────────────────


@router.post(
    "/links",
    summary="LINK형 문서 등록 (외부 URL 기록)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_MANAGE)],
)
def create_link_document(
    payload: LinkDocumentCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> DocumentSummary:
    status_code, body = service.create_link_document(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return DocumentSummary.model_validate(body)


@router.post(
    "/files",
    summary="FILE형 문서 업로드 (multipart)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_MANAGE)],
)
def create_file_document(
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
    file: Annotated[UploadFile, File(description="실물 파일 (최대 20MiB)")],
    owner_type: Annotated[str, Form()],
    owner_id: Annotated[int, Form()],
    document_type: Annotated[str, Form()],
    issued_on: Annotated[str | None, Form()] = None,
    valid_until: Annotated[str | None, Form()] = None,
    retention_until: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> DocumentSummary:
    status_code, body = service.create_file_document(
        actor=current,
        idempotency_key=key,
        payload={
            "owner_type": owner_type,
            "owner_id": owner_id,
            "document_type": document_type,
            "issued_on": issued_on,
            "valid_until": valid_until,
            "retention_until": retention_until,
            "note": note,
        },
        stream=file.file,
        filename=file.filename or "",
        declared_content_type=file.content_type,
    )
    response.status_code = status_code
    return DocumentSummary.model_validate(body)


@router.get("", summary="문서 목록")
def list_documents(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    owner_type: str | None = None,
    owner_id: int | None = None,
    document_type: str | None = None,
) -> Page[DocumentSummary]:
    views, total = service.list_documents(
        owner_type=owner_type,
        owner_id=owner_id,
        document_type=document_type,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([DocumentSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="문서 목록 CSV 내보내기 (UTF-8 BOM)")
def export_documents_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_documents_for_export()
    return csv_response(
        "문서목록.csv",
        DOCUMENT_CSV_HEADER,
        [
            (
                view.document_type_name,
                view.owner_type,
                view.owner_display,
                view.storage_kind,
                view.original_filename,
                view.url,
                view.size_bytes,
                view.sha256,
                view.issued_on,
                view.valid_until,
                view.retention_until,
                view.note,
            )
            for view in views
        ],
    )


# ★ `/{document_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{document_id}", summary="문서 상세")
def get_document(document_id: int, current: CurrentUser) -> DocumentSummary:
    return DocumentSummary.of(service.get_document(document_id))


@router.get("/{document_id}/download", summary="파일 다운로드 (attachment 고정)")
def download_document(document_id: int, current: CurrentUser) -> FileResponse:
    path, original_filename = service.resolve_download(document_id)
    return FileResponse(
        path,
        # 신고된 MIME으로 서빙하지 않는다 — 업로드물의 inline 렌더 금지(조건 4-③).
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(original_filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/{document_id}",
    summary="문서 삭제 (보존기한 내면 거부 — 파기 잠금)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_MANAGE)],
)
def delete_document(document_id: int, current: CurrentUser) -> None:
    service.delete_document(actor=current, document_id=document_id)


# ── 품목군 서류 세트 (§4.8) ────────────────────────────────────────────────


@profile_document_types_router.get(
    "/{profile_id}/document-types", summary="품목군 서류 세트 목록"
)
def list_item_profile_document_types(
    profile_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ProfileDocumentTypeSummary]:
    views, total = service.list_profile_document_types(
        profile_id=profile_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ProfileDocumentTypeSummary.of(view) for view in views], total, params)


@profile_document_types_router.post(
    "/{profile_id}/document-types",
    summary="품목군 서류 세트에 종류 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_MANAGE)],
)
def add_item_profile_document_type(
    profile_id: int,
    payload: ProfileDocumentTypeAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProfileDocumentTypeSummary:
    status_code, body = service.add_profile_document_type(
        actor=current,
        idempotency_key=key,
        profile_id=profile_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return ProfileDocumentTypeSummary.model_validate(body)


@profile_document_types_router.delete(
    "/{profile_id}/document-types/{link_id}",
    summary="품목군 서류 세트에서 종류 제거",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_MANAGE)],
)
def remove_item_profile_document_type(
    profile_id: int, link_id: int, current: CurrentUser
) -> None:
    service.remove_profile_document_type(actor=current, profile_id=profile_id, link_id=link_id)
