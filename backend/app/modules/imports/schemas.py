"""엑셀 왕복 임포트 응답 스키마 (§12.2).

업로드는 multipart(파일 + 경로의 레지스트리 코드)라 요청 스키마가 없다 —
documents와 같은 관례다. 확정·삭제도 본문이 없다(스테이징 행이 곧 내용이다).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.modules.imports.service import StagingRowView, StagingView


class ImportRegistryEntry(BaseModel):
    """임포트 대상 하나 — 표준 양식의 스키마 문서(§12.2)를 겸한다."""

    code: str
    label_ko: str
    headers: list[str]


class ImportRegistryResponse(BaseModel):
    items: list[ImportRegistryEntry]


class ImportStagingSummary(BaseModel):
    id: int
    registry_code: str
    registry_label: str
    original_filename: str
    sha256: str
    status: str
    total_data_rows: int
    unchanged_rows: int
    new_rows: int
    changed_rows: int
    error_rows: int
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def of(cls, view: StagingView) -> ImportStagingSummary:
        return cls(
            id=view.id,
            registry_code=view.registry_code,
            registry_label=view.registry_label,
            original_filename=view.original_filename,
            sha256=view.sha256,
            status=view.status,
            total_data_rows=view.total_data_rows,
            unchanged_rows=view.unchanged_rows,
            new_rows=view.new_rows,
            changed_rows=view.changed_rows,
            error_rows=view.error_rows,
            created_at=view.created_at,
            confirmed_at=view.confirmed_at,
        )


class ImportStagingRowSummary(BaseModel):
    id: int
    row_no: int
    kind: str
    target_id: int | None
    payload: dict[str, Any]
    #: {CSV 헤더: [이전 표기, 이후 표기]} — 변경 행에만 있다.
    changes: dict[str, list[str]] | None
    error_reason: str | None

    @classmethod
    def of(cls, view: StagingRowView) -> ImportStagingRowSummary:
        return cls(
            id=view.id,
            row_no=view.row_no,
            kind=view.kind,
            target_id=view.target_id,
            payload=view.payload,
            changes=view.changes,
            error_reason=view.error_reason,
        )


class ImportConfirmResult(BaseModel):
    id: int
    status: str
    created_rows: int
    updated_rows: int
