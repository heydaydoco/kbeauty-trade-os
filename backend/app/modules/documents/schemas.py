"""문서 보관소 요청·응답 스키마 (§4.7·§4.8).

파일 업로드(multipart)는 pydantic 본문 스키마가 아니라 라우터의 Form/File
파라미터로 받는다 — 여기 있는 것은 LINK 등록(JSON)과 응답 모양이다.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.modules.documents.service import (
    DocumentTypeView,
    DocumentView,
    ProfileDocumentTypeView,
)


class DocumentTypeSummary(BaseModel):
    id: int
    code: str
    name_ko: str
    #: 보존기한 산정 연수(§4.7). 값이 있으면 발급일 필수 + 파기 잠금.
    retention_years: int | None
    note: str | None

    @classmethod
    def of(cls, view: DocumentTypeView) -> DocumentTypeSummary:
        return cls(
            id=view.id,
            code=view.code,
            name_ko=view.name_ko,
            retention_years=view.retention_years,
            note=view.note,
        )


class LinkDocumentCreateRequest(BaseModel):
    """LINK형 문서 등록 — 외부 URL의 기록이다(실물 업로드는 /documents/files)."""

    owner_type: str = Field(min_length=2, max_length=13)
    owner_id: int = Field(gt=0)
    #: document_types.code — 열린 데이터라 값 검증은 서비스가 FK 실재로 한다.
    document_type: str = Field(min_length=1, max_length=40)
    #: 링크가 아니면 근거가 아니다 — http(s)로 제한한다(sku_hs_codes.source_url 선례).
    url: str = Field(max_length=500, pattern=r"^https?://")
    issued_on: date | None = None
    valid_until: date | None = None
    #: 보통은 비운다 — 종류의 retention_years가 발급일 기준으로 계산한다(§4.7).
    retention_until: date | None = None
    note: str | None = None


class DocumentSummary(BaseModel):
    id: int
    owner_type: str
    owner_id: int
    #: 표시용 소유자 텍스트(SKU 품번·라벨 식별) — 편집은 owner_id로 한다.
    owner_display: str | None
    document_type: str
    document_type_name: str
    storage_kind: str
    original_filename: str | None
    content_type: str | None
    size_bytes: int | None
    sha256: str | None
    url: str | None
    issued_on: date | None
    valid_until: date | None
    retention_until: date | None
    note: str | None

    @classmethod
    def of(cls, view: DocumentView) -> DocumentSummary:
        return cls(
            id=view.id,
            owner_type=view.owner_type,
            owner_id=view.owner_id,
            owner_display=view.owner_display,
            document_type=view.document_type_code,
            document_type_name=view.document_type_name,
            storage_kind=view.storage_kind,
            original_filename=view.original_filename,
            content_type=view.content_type,
            size_bytes=view.size_bytes,
            sha256=view.sha256,
            url=view.url,
            issued_on=view.issued_on,
            valid_until=view.valid_until,
            retention_until=view.retention_until,
            note=view.note,
        )


class ProfileDocumentTypeAddRequest(BaseModel):
    """품목군 서류 세트에 종류 추가 (§4.8)."""

    document_type: str = Field(min_length=1, max_length=40)


class ProfileDocumentTypeSummary(BaseModel):
    id: int
    item_profile_id: int
    document_type_id: int
    document_type: str
    document_type_name: str

    @classmethod
    def of(cls, view: ProfileDocumentTypeView) -> ProfileDocumentTypeSummary:
        return cls(
            id=view.id,
            item_profile_id=view.item_profile_id,
            document_type_id=view.document_type_id,
            document_type=view.document_type_code,
            document_type_name=view.document_type_name,
        )
