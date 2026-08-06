"""문서 보관소 서비스 (§4.7·§4.8 / §17.4 멱등 / §18.1 파일 보안 / ADR-0028·0029).

■ 파일 보안 4항 (웹 세션 승인 조건 4 / ADR-0029)

  ① 저장 파일명 = 서버 생성(uuid hex) — 사용자 파일명은 경로에 절대 쓰지 않는다
    (path traversal 원천 차단, DB CHECK stored_name_format이 이중 안전).
  ② 저장 위치 = 정적 서빙 경로 밖(settings.file_storage_root) — 접근 통로는
    권한 검증 다운로드 엔드포인트 하나뿐이다.
  ③ 다운로드는 Content-Disposition: attachment 고정 + octet-stream + nosniff —
    업로드물이 브라우저에서 inline 렌더되는 일이 없다.
  ④ 크기 상한 = MAX_UPLOAD_BYTES(20MiB, 명시 수치) — 테스트가 수치와 거부를
    함께 고정한다. 운영 nginx의 client_max_body_size 25m가 1차 방어선이고
    이 값은 그 안쪽이다(멀티파트 오버헤드 여유 포함).

■ 파기 잠금 (§4.7)

  보존기한(retention_until)이 오늘(KST 업무 날짜) 이후면 soft delete를
  거부한다. 강제 삭제 액션은 만들지 않는다(웹 세션 승인 문면). 보존기한은
  종류의 retention_years(원산지 증빙=5년)가 발급일 기준으로 계산한다.

■ SET SKU에는 MSDS 문서를 연결할 수 없다 (웹 세션 승인 — 서비스 가드)

  구 skus.msds_url 시절에는 set_has_no_dg CHECK가 막았지만, 문서는 다른
  테이블이라 CHECK로 표현할 수 없다 — 여기가 판정 지점이고 테스트가 지킨다
  (set_components의 "다른 행을 봐야 하는 조건" 선례와 같은 규율).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.time import today_kst, utcnow
from app.modules.catalog.models import Sku
from app.modules.catalog.profiles import require_profile
from app.modules.documents.models import (
    DOCUMENT_OWNER_TYPES,
    Document,
    DocumentType,
    ItemProfileDocumentType,
)
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.materials.models import Label
from app.modules.outbox import service as outbox

LINK_CREATE_ENDPOINT = "POST /api/v1/documents/links"
FILE_CREATE_ENDPOINT = "POST /api/v1/documents/files"
PROFILE_DOC_TYPE_ADD_ENDPOINT = "POST /api/v1/item-profiles/{profile_id}/document-types"

#: CSV 내보내기 상한 — catalog·ingredients·materials·partners와 같은 값·같은 이유.
EXPORT_MAX_ROWS = 50_000

#: 업로드 크기 상한(조건 4-④ — 명시 수치, 테스트가 고정). 20MiB.
#: 운영 nginx client_max_body_size 25m 안쪽이다(멀티파트 오버헤드 여유).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

#: 허용 확장자(§18.1 "업로드 MIME·크기 검증 + 실행 불가"). 실행물(.exe·.sh·
#: .bat·.js)과 인라인 렌더 가능물(.html·.svg)은 목록에 없어서 거부된다 —
#: attachment 고정(③)에 더한 이중 안전이다.
ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".txt",
        ".csv",
        ".xls",
        ".xlsx",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".zip",
        ".ai",
    }
)

_READ_CHUNK_BYTES = 1024 * 1024


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DocumentTypeView:
    id: int
    code: str
    name_ko: str
    retention_years: int | None
    note: str | None


@dataclass(frozen=True, slots=True)
class DocumentView:
    id: int
    owner_type: str
    owner_id: int
    #: 표시용 소유자 텍스트. 소유자가 이미 삭제됐으면 None(화면은 — 표시).
    owner_display: str | None
    document_type_code: str
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


@dataclass(frozen=True, slots=True)
class ProfileDocumentTypeView:
    id: int
    item_profile_id: int
    document_type_id: int
    document_type_code: str
    document_type_name: str


def _type_view(row: DocumentType) -> DocumentTypeView:
    return DocumentTypeView(
        id=row.id,
        code=row.code,
        name_ko=row.name_ko,
        retention_years=row.retention_years,
        note=row.note,
    )


def _document_view(
    row: Document, doc_type: DocumentType, owner_display: str | None
) -> DocumentView:
    return DocumentView(
        id=row.id,
        owner_type=row.owner_type,
        owner_id=row.owner_id,
        owner_display=owner_display,
        document_type_code=doc_type.code,
        document_type_name=doc_type.name_ko,
        storage_kind=row.storage_kind,
        original_filename=row.original_filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        url=row.url,
        issued_on=row.issued_on,
        valid_until=row.valid_until,
        retention_until=row.retention_until,
        note=row.note,
    )


def _serialize_document(view: DocumentView) -> dict[str, Any]:
    return {
        "id": view.id,
        "owner_type": view.owner_type,
        "owner_id": view.owner_id,
        "owner_display": view.owner_display,
        "document_type": view.document_type_code,
        "document_type_name": view.document_type_name,
        "storage_kind": view.storage_kind,
        "original_filename": view.original_filename,
        "content_type": view.content_type,
        "size_bytes": view.size_bytes,
        "sha256": view.sha256,
        "url": view.url,
        "issued_on": None if view.issued_on is None else view.issued_on.isoformat(),
        "valid_until": None if view.valid_until is None else view.valid_until.isoformat(),
        "retention_until": (
            None if view.retention_until is None else view.retention_until.isoformat()
        ),
        "note": view.note,
    }


def _serialize_profile_doc_type(view: ProfileDocumentTypeView) -> dict[str, Any]:
    return {
        "id": view.id,
        "item_profile_id": view.item_profile_id,
        "document_type_id": view.document_type_id,
        "document_type": view.document_type_code,
        "document_type_name": view.document_type_name,
    }


# ── 공통 ───────────────────────────────────────────────────────────────────


def require_document_type(session: Session, code: str) -> DocumentType:
    normalized = str(code).strip().upper()
    row = session.execute(
        select(DocumentType).where(
            DocumentType.code == normalized, DocumentType.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"document_type": f"등록되지 않은 문서 종류입니다: {normalized}"},
            log_context={"document_type": normalized},
        )
    return row


def _require_owner(
    session: Session, owner_type: str, owner_id: int, document_type_code: str
) -> str:
    """소유자 실재 검증 + 표시 텍스트. 폴리모픽이라 FK 대신 여기가 판정 지점이다."""
    if owner_type == "SKU":
        sku = session.execute(
            select(Sku).where(Sku.id == owner_id, Sku.deleted_at.is_(None))
        ).scalar_one_or_none()
        if sku is None:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"owner_id": "존재하지 않는 SKU입니다. 목록에서 다시 선택해 주세요."},
                log_context={"owner_type": owner_type, "owner_id": owner_id},
            )
        # SET SKU에는 MSDS 금지 — 웹 세션 승인이 DB CHECK 대신 지정한 서비스 가드.
        if document_type_code == "MSDS" and sku.kind == "SET":
            raise AppError(
                ErrorCode.DOCUMENTS_SET_SKU_MSDS_FORBIDDEN,
                log_context={"owner_id": owner_id},
            )
        return sku.sku_code
    if owner_type == "LABEL":
        row = session.execute(
            select(Label, Sku.sku_code)
            .join(Sku, Label.sku_id == Sku.id)
            .where(Label.id == owner_id, Label.deleted_at.is_(None))
        ).one_or_none()
        if row is None:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"owner_id": "존재하지 않는 라벨입니다. SKU 상세에서 다시 선택해 주세요."},
                log_context={"owner_type": owner_type, "owner_id": owner_id},
            )
        label, sku_code = row
        return _label_display(sku_code, label.country_code, label.label_version, label.language)
    raise AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={
            "owner_type": "알 수 없는 소유자 유형입니다. "
            f"{', '.join(DOCUMENT_OWNER_TYPES)} 중에서 선택해 주세요."
        },
        log_context={"owner_type": owner_type},
    )


def _label_display(sku_code: str, country_code: str, label_version: int, language: str) -> str:
    return f"{sku_code} · {country_code} v{label_version}({language})"


def _owner_displays(
    session: Session, keys: set[tuple[str, int]]
) -> dict[tuple[str, int], str]:
    """페이지 분량의 소유자 표시를 한 번에 가져온다 — 행마다 조회하면 N+1이다(§18.4).

    소유자가 soft delete된 뒤에도 문서는 남으므로 삭제 필터를 걸지 않는다 —
    표시는 조회일 뿐 판정이 아니다.
    """
    displays: dict[tuple[str, int], str] = {}
    sku_ids = [owner_id for owner_type, owner_id in keys if owner_type == "SKU"]
    if sku_ids:
        for sku_id, sku_code in session.execute(
            select(Sku.id, Sku.sku_code).where(Sku.id.in_(sku_ids))
        ):
            displays[("SKU", sku_id)] = sku_code
    label_ids = [owner_id for owner_type, owner_id in keys if owner_type == "LABEL"]
    if label_ids:
        for label_id, sku_code, country, version, language in session.execute(
            select(
                Label.id, Sku.sku_code, Label.country_code, Label.label_version, Label.language
            )
            .join(Sku, Label.sku_id == Sku.id)
            .where(Label.id.in_(label_ids))
        ):
            displays[("LABEL", label_id)] = _label_display(sku_code, country, version, language)
    return displays


def _parse_date(payload: dict[str, Any], field: str, label: str) -> date | None:
    value = payload.get(field)
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={field: f"{label}은(는) YYYY-MM-DD 형식의 날짜여야 합니다."},
            log_context={field: value},
        ) from exc


def _add_years(day: date, years: int) -> date:
    """달력 연수 더하기. 2/29 출발이면 평년에는 2/28로 내린다(보수적 — 더 이르게)."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, month=2, day=28)


def _resolve_retention(
    doc_type: DocumentType, issued_on: date | None, explicit: date | None
) -> date | None:
    """보존기한 결정 (§4.7 "원산지 증빙은 보존기한(발급일+5년)" — '기본'이므로
    명시값이 오면 그것을 쓴다)."""
    if explicit is not None:
        return explicit
    if doc_type.retention_years is None:
        return None
    if issued_on is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "issued_on": f"'{doc_type.name_ko}' 종류는 보존기한 산정(발급일+"
                f"{doc_type.retention_years}년)을 위해 발급일이 필요합니다."
            },
        )
    return _add_years(issued_on, doc_type.retention_years)


def _validate_dates(issued_on: date | None, valid_until: date | None) -> None:
    """기간 순서 — DB CHECK가 최종 판정자이고, 여기는 한국어 안내다."""
    if issued_on is not None and valid_until is not None and valid_until < issued_on:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"valid_until": "유효기간 만료일이 발급일보다 빠릅니다. 날짜를 확인해 주세요."},
        )


def _guard_export_size(total: int) -> None:
    if total > EXPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
            },
        )


# ── 파일 저장 (ADR-0029) ───────────────────────────────────────────────────


def storage_root() -> Path:
    """저장 루트 — settings가 유일 출처(정적 서빙 경로 밖, ②)."""
    return Path(settings.file_storage_root)


def sanitize_filename(raw: str) -> str:
    """원본 파일명 정규화(§18.1) — **표시·헤더 전용**이다. 경로에는 절대 안 쓴다(①).

    경로 구분자 앞부분을 버리고(traversal 문자열 무력화) 제어 문자를 걷어낸다.
    """
    name = str(raw).replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if not name:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"file": "파일 이름이 비어 있습니다. 이름이 있는 파일을 올려 주세요."},
        )
    return name[:255]


def _validate_extension(filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise AppError(
            ErrorCode.DOCUMENTS_FILE_TYPE_NOT_ALLOWED,
            log_context={"suffix": suffix or "(없음)"},
        )


def _store_stream(stream: BinaryIO) -> tuple[str, int, str]:
    """스트림을 서버 생성 이름으로 저장하고 (저장명, 크기, sha256)을 돌려준다.

    Content-Length를 믿지 않는다 — 읽으면서 세고, 상한을 넘는 순간 쓰다 만
    파일을 지우고 거부한다(④).
    """
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    stored_name = uuid4().hex
    target = root / stored_name
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := stream.read(_READ_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise AppError(
                        ErrorCode.DOCUMENTS_FILE_TOO_LARGE,
                        log_context={"limit_bytes": MAX_UPLOAD_BYTES},
                    )
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"file": "빈 파일은 업로드할 수 없습니다."},
        )
    return stored_name, size, digest.hexdigest()


def discard_stored_file(stored_name: str) -> None:
    """저장 실패·멱등 재생 경로의 뒷정리 — 행이 없는 파일을 남기지 않는다."""
    (storage_root() / stored_name).unlink(missing_ok=True)


# ── 문서 종류 ──────────────────────────────────────────────────────────────


def list_document_types(*, offset: int, limit: int) -> tuple[list[DocumentTypeView], int]:
    with unit_of_work() as uow:
        session = uow.session
        condition = (DocumentType.deleted_at.is_(None),)
        total = session.execute(
            select(func.count()).select_from(DocumentType).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(DocumentType)
            .where(*condition)
            .order_by(DocumentType.code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_type_view(row) for row in rows], total


# ── 문서 (§4.7) ────────────────────────────────────────────────────────────


def _create_document(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    endpoint: str,
    request_body: dict[str, Any],
    payload: dict[str, Any],
    file_fields: dict[str, Any] | None,
) -> tuple[int, dict[str, Any], bool]:
    """LINK·FILE 공통 생성 경로 — 검증·멱등·아웃박스가 한 트랜잭션이다(§17.1).

    반환 3번째 값은 "멱등 재생이었는가" — FILE 경로가 방금 쓴 실물을 버릴지
    판단하는 데 쓴다(재생이면 실물의 진본은 최초 요청의 것이다).
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=endpoint,
            key=idempotency_key,
            request_body=request_body,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body, True

        doc_type = require_document_type(session, str(payload["document_type"]))
        owner_type = str(payload["owner_type"]).strip().upper()
        owner_id = int(payload["owner_id"])
        owner_display = _require_owner(session, owner_type, owner_id, doc_type.code)

        issued_on = _parse_date(payload, "issued_on", "발급일")
        valid_until = _parse_date(payload, "valid_until", "유효기간 만료일")
        _validate_dates(issued_on, valid_until)
        retention_until = _resolve_retention(
            doc_type, issued_on, _parse_date(payload, "retention_until", "보존기한")
        )

        row = Document(
            owner_type=owner_type,
            owner_id=owner_id,
            document_type_id=doc_type.id,
            storage_kind="FILE" if file_fields is not None else "LINK",
            issued_on=issued_on,
            valid_until=valid_until,
            retention_until=retention_until,
            note=payload.get("note"),
            created_by_id=actor.id,
            **(file_fields or {"url": str(payload["url"]).strip()}),
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "file": "같은 파일이 이 대상에 이미 등록되어 있습니다(해시 일치). "
                    "새 판이라면 내용이 바뀐 파일인지 확인해 주세요."
                },
                log_context={"owner_type": owner_type, "owner_id": owner_id},
            ) from exc

        outbox.publish(
            session,
            event_type="documents.document.created",
            aggregate_type="documents",
            aggregate_id=row.id,
            payload={
                "document_id": row.id,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "document_type": doc_type.code,
            },
        )

        body = _serialize_document(_document_view(row, doc_type, owner_display))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body, False


def create_link_document(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    status_code, body, _ = _create_document(
        actor=actor,
        idempotency_key=idempotency_key,
        endpoint=LINK_CREATE_ENDPOINT,
        request_body=payload,
        payload=payload,
        file_fields=None,
    )
    return status_code, body


def create_file_document(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    payload: dict[str, Any],
    stream: BinaryIO,
    filename: str,
    declared_content_type: str | None,
) -> tuple[int, dict[str, Any]]:
    """FILE형 등록. 파일 IO는 트랜잭션 밖(§17.1 — DB 트랜잭션에 IO를 묶지 않는다),
    실패·멱등 재생이면 방금 쓴 파일을 지운다."""
    original = sanitize_filename(filename)
    _validate_extension(original)
    stored_name, size, sha256 = _store_stream(stream)
    content_type = (declared_content_type or "application/octet-stream").strip()[:100]
    try:
        status_code, body, replayed = _create_document(
            actor=actor,
            idempotency_key=idempotency_key,
            endpoint=FILE_CREATE_ENDPOINT,
            # 멱등 재생 판정에 파일 내용(sha256)이 들어가야 "같은 요청"이 성립한다.
            request_body={**payload, "original_filename": original, "sha256": sha256},
            payload=payload,
            file_fields={
                "stored_name": stored_name,
                "original_filename": original,
                "content_type": content_type,
                "size_bytes": size,
                "sha256": sha256,
            },
        )
    except BaseException:
        discard_stored_file(stored_name)
        raise
    if replayed:
        # 멱등 재생(최초 결과 반환) — 실물의 진본은 최초 요청의 것이다.
        discard_stored_file(stored_name)
    return status_code, body


def list_documents(
    *,
    owner_type: str | None,
    owner_id: int | None,
    document_type: str | None,
    offset: int,
    limit: int,
) -> tuple[list[DocumentView], int]:
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[Any] = [Document.deleted_at.is_(None)]
        if owner_type is not None:
            normalized = owner_type.strip().upper()
            if normalized not in DOCUMENT_OWNER_TYPES:
                raise AppError(
                    ErrorCode.VALIDATION_INVALID_FIELD,
                    detail={
                        "owner_type": "알 수 없는 소유자 유형입니다. "
                        f"{', '.join(DOCUMENT_OWNER_TYPES)} 중에서 선택해 주세요."
                    },
                )
            conditions.append(Document.owner_type == normalized)
        if owner_id is not None:
            conditions.append(Document.owner_id == owner_id)
        if document_type is not None:
            doc_type = require_document_type(session, document_type)
            conditions.append(Document.document_type_id == doc_type.id)

        total = session.execute(
            select(func.count()).select_from(Document).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(Document, DocumentType)
            .join(DocumentType, Document.document_type_id == DocumentType.id)
            .where(*conditions)
            .order_by(Document.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        displays = _owner_displays(
            session, {(row.owner_type, row.owner_id) for row, _ in rows}
        )
        return [
            _document_view(row, doc_type, displays.get((row.owner_type, row.owner_id)))
            for row, doc_type in rows
        ], total


def all_documents_for_export() -> list[DocumentView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(Document, DocumentType)
            .join(DocumentType, Document.document_type_id == DocumentType.id)
            .where(Document.deleted_at.is_(None))
            .order_by(Document.id.desc())
        ).all()
        displays = _owner_displays(
            session, {(row.owner_type, row.owner_id) for row, _ in rows}
        )
        return [
            _document_view(row, doc_type, displays.get((row.owner_type, row.owner_id)))
            for row, doc_type in rows
        ]


def _require_document(session: Session, document_id: int) -> tuple[Document, DocumentType]:
    row = session.execute(
        select(Document, DocumentType)
        .join(DocumentType, Document.document_type_id == DocumentType.id)
        .where(Document.id == document_id, Document.deleted_at.is_(None))
    ).one_or_none()
    if row is None:
        raise NotFoundError(log_context={"document_id": document_id})
    return row[0], row[1]


def get_document(document_id: int) -> DocumentView:
    with unit_of_work() as uow:
        session = uow.session
        row, doc_type = _require_document(session, document_id)
        displays = _owner_displays(session, {(row.owner_type, row.owner_id)})
        return _document_view(row, doc_type, displays.get((row.owner_type, row.owner_id)))


def delete_document(*, actor: AuthenticatedUser, document_id: int) -> None:
    """soft delete — 보존기한 내면 거부한다(파기 잠금, §4.7).

    강제 삭제 액션은 없다(웹 세션 승인 문면). 저장 실물은 지우지 않는다 —
    soft delete는 복원 가능해야 하고(§17.4), 실물 정리는 보존·백업 정책(S2-4)의
    일이다.
    """
    with unit_of_work() as uow:
        session = uow.session
        row, doc_type = _require_document(session, document_id)
        if row.retention_until is not None and today_kst() <= row.retention_until:
            raise AppError(
                ErrorCode.DOCUMENTS_RETENTION_LOCKED,
                log_context={
                    "document_id": document_id,
                    "retention_until": row.retention_until.isoformat(),
                },
            )
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        outbox.publish(
            session,
            event_type="documents.document.deleted",
            aggregate_type="documents",
            aggregate_id=row.id,
            payload={"document_id": row.id, "document_type": doc_type.code},
        )


def resolve_download(document_id: int) -> tuple[Path, str]:
    """다운로드 대상 실물 경로와 원본 파일명. LINK형은 내려받을 실물이 없다."""
    with unit_of_work() as uow:
        session = uow.session
        row, _ = _require_document(session, document_id)
        if row.storage_kind != "FILE":
            raise AppError(
                ErrorCode.DOCUMENTS_DOWNLOAD_NOT_A_FILE,
                log_context={"document_id": document_id},
            )
        assert row.stored_name is not None and row.original_filename is not None
        path = storage_root() / row.stored_name
        if not path.is_file():
            # 행은 있는데 실물이 없다 — 저장소 유실은 진단 대상이지 404가 아니다.
            raise AppError(
                ErrorCode.INTERNAL_UNEXPECTED,
                log_context={"document_id": document_id, "stored_name": row.stored_name},
            )
        return path, row.original_filename


# ── 품목군 서류 세트 (§4.8) ────────────────────────────────────────────────


def list_profile_document_types(
    *, profile_id: int, offset: int, limit: int
) -> tuple[list[ProfileDocumentTypeView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_profile(session, profile_id)
        condition = (
            ItemProfileDocumentType.item_profile_id == profile_id,
            ItemProfileDocumentType.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(ItemProfileDocumentType).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(ItemProfileDocumentType, DocumentType)
            .join(DocumentType, ItemProfileDocumentType.document_type_id == DocumentType.id)
            .where(*condition)
            .order_by(DocumentType.code)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            ProfileDocumentTypeView(
                id=row.id,
                item_profile_id=row.item_profile_id,
                document_type_id=row.document_type_id,
                document_type_code=doc_type.code,
                document_type_name=doc_type.name_ko,
            )
            for row, doc_type in rows
        ], total


def add_profile_document_type(
    *, actor: AuthenticatedUser, idempotency_key: str, profile_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PROFILE_DOC_TYPE_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "profile_id": profile_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        profile = require_profile(session, profile_id)
        doc_type = require_document_type(session, str(payload["document_type"]))
        # 실패 경로에서 쓸 값은 flush 전에 빼 둔다(함정 ②).
        profile_pk = profile.id
        type_pk = doc_type.id
        type_code = doc_type.code
        type_name = doc_type.name_ko

        row = ItemProfileDocumentType(
            item_profile_id=profile_pk, document_type_id=type_pk, created_by_id=actor.id
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"document_type": "이 품목군의 서류 세트에 이미 있는 종류입니다."},
                log_context={"profile_id": profile_pk, "document_type": type_code},
            ) from exc

        body = _serialize_profile_doc_type(
            ProfileDocumentTypeView(
                id=row.id,
                item_profile_id=profile_pk,
                document_type_id=type_pk,
                document_type_code=type_code,
                document_type_name=type_name,
            )
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def remove_profile_document_type(
    *, actor: AuthenticatedUser, profile_id: int, link_id: int
) -> None:
    """세트에서 종류 제거 = soft delete. 재추가는 부활이 아니라 신규다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        require_profile(session, profile_id)
        row = session.execute(
            select(ItemProfileDocumentType).where(
                ItemProfileDocumentType.id == link_id,
                ItemProfileDocumentType.item_profile_id == profile_id,
                ItemProfileDocumentType.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"profile_id": profile_id, "link_id": link_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
