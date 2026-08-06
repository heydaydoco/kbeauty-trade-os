"""엑셀 왕복 임포트 서비스 (DESIGN.md §12.2 / ADR-09 / §15 L2 / §17).

■ 흐름: 업로드 → 파싱 → diff → import_staging(+행) → 사람 검토 → 확정 1클릭

  diff 규칙(§12.2, 웹 세션 승인 문면 그대로):
    빈 ID = 신규 / ID 있는 행 = 변경분만 / 파일에 없는 행 = 무시(삭제 아님) /
    ID 훼손·검증 실패 = 오류 행 리포트(행번호+사유).

■ 확정은 원자적이다 — 부분 반영이 없다

  행 하나라도 스테이징 이후 먼저 수정·삭제됐으면(version 대조) 전체를 409로
  거부한다. 검토자가 본 diff와 다른 결과가 DB에 남는 것이, 아무것도 안 되는
  것보다 나쁘기 때문이다. 오류 행(ERROR)은 처음부터 반영 대상이 아니다 —
  리포트로 왕복해 파일을 고쳐 다시 올리는 것이 §12.2의 순환이다.

■ 자동 확정은 없다 (§15 자동화 4금 비저촉)

  ADR-09의 조건부 자동 확정 예외는 "인테이크 전표까지"이고 마스터는 그 범위
  밖이다. 확정 엔드포인트는 사람의 1클릭 + idempotency key뿐이다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.time import utcnow
from app.modules.documents.service import sanitize_filename
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.imports import parser
from app.modules.imports.models import ImportStaging, ImportStagingRow
from app.modules.imports.registry import IMPORT_TARGETS, ImportTarget
from app.modules.outbox import service as outbox

STAGE_ENDPOINT = "POST /api/v1/imports/{registry_code}/staging"
CONFIRM_ENDPOINT = "POST /api/v1/imports/staging/{staging_id}/confirm"

#: 업로드 상한 — documents와 같은 수치·같은 근거(ADR-0029, 운영 nginx 25m의 안쪽).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024

#: 임포트 행 상한 — 내보내기 상한(EXPORT_MAX_ROWS)과 대칭이다. 내보낸 것보다
#: 큰 파일은 왕복이 아니다.
IMPORT_MAX_ROWS = 50_000


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StagingView:
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


@dataclass(frozen=True, slots=True)
class StagingRowView:
    id: int
    row_no: int
    kind: str
    target_id: int | None
    payload: dict[str, Any]
    #: 표시용 diff — {CSV 헤더: [이전 표기, 이후 표기]}. DB에는 필드명으로
    #: 저장하고(확정이 소비), 응답 경계에서 헤더로 바꾼다(사람이 소비).
    changes: dict[str, list[str]] | None
    error_reason: str | None


def _serialize_staging(view: StagingView) -> dict[str, Any]:
    return {
        "id": view.id,
        "registry_code": view.registry_code,
        "registry_label": view.registry_label,
        "original_filename": view.original_filename,
        "sha256": view.sha256,
        "status": view.status,
        "total_data_rows": view.total_data_rows,
        "unchanged_rows": view.unchanged_rows,
        "new_rows": view.new_rows,
        "changed_rows": view.changed_rows,
        "error_rows": view.error_rows,
        "created_at": view.created_at.isoformat(),
        "confirmed_at": None if view.confirmed_at is None else view.confirmed_at.isoformat(),
    }


# ── 공통 ───────────────────────────────────────────────────────────────────


def require_target(registry_code: str) -> ImportTarget:
    target = IMPORT_TARGETS.get(registry_code)
    if target is None:
        raise NotFoundError(log_context={"registry_code": registry_code})
    return target


def _read_limited(stream: BinaryIO) -> bytes:
    """Content-Length를 믿지 않고 읽으면서 센다 — documents._store_stream과 같은 이유."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise AppError(ErrorCode.IMPORTS_FILE_TOO_LARGE, log_context={"read_bytes": size})
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_extension(filename: str) -> None:
    if not filename.lower().endswith(".csv"):
        raise AppError(
            ErrorCode.IMPORTS_FILE_TYPE_NOT_ALLOWED,
            log_context={"filename_suffix": filename.rsplit(".", 1)[-1][:20] if filename else ""},
        )


@dataclass(frozen=True, slots=True)
class _RowDraft:
    row_no: int
    kind: str
    target_id: int | None = None
    expected_version: int | None = None
    payload: dict[str, Any] | None = None
    changes: dict[str, list[str]] | None = None
    error_reason: str | None = None


def _diff_rows(
    target: ImportTarget,
    parsed: parser.ParsedFile,
    session: Any,
) -> tuple[list[_RowDraft], int]:
    """파일 행 → 신규/변경/오류 초안 + 무변경 행 수 (§12.2 diff)."""
    context = target.prepare(session, parsed.rows)

    # 1) ID 셀 해석 — 훼손은 여기서 오류가 된다.
    ids: list[int | None] = []
    id_problems: list[str | None] = []
    for raw in parsed.rows:
        cell = raw.cells["ID"].strip()
        if cell == "":
            ids.append(None)
            id_problems.append(None)
            continue
        # ★ isdigit() 판정 금지(리뷰 검출) — '²'는 isdigit()이 True인데 int()가
        #   거부해 오류 행 대신 500이 난다. isdecimal()은 십진 숫자(Nd)만 참이라
        #   int()와 도메인이 일치하고, int()가 몰래 받는 '1_0' 같은 표기도 거른다.
        if cell.isdecimal():
            ids.append(int(cell))
            id_problems.append(None)
        else:
            ids.append(None)
            id_problems.append(
                f"ID를 읽을 수 없습니다({cell}). 내려받은 ID를 그대로 두거나, "
                "신규 행이면 ID 칸을 비워 주세요."
            )
    id_counts: dict[int, int] = {}
    for parsed_id in ids:
        if parsed_id is not None:
            id_counts[parsed_id] = id_counts.get(parsed_id, 0) + 1
    snapshots = target.load_snapshots(session, [i for i in ids if i is not None])

    # 2) 값 파싱 + 파일 안 자연키 중복 집계.
    payloads: list[dict[str, Any] | None] = []
    parse_problems: list[str | None] = []
    code_counts: dict[str, int] = {}
    for raw in parsed.rows:
        payload, problem = target.parse_row(raw.cells, context)
        payloads.append(payload)
        parse_problems.append(problem)
        if payload is not None:
            code = payload[target.code_field]
            code_counts[code] = code_counts.get(code, 0) + 1
    code_owners = target.existing_code_owners(session, list(code_counts))

    field_to_header = {column.field: column.header for column in target.columns}
    code_label = field_to_header[target.code_field]

    drafts: list[_RowDraft] = []
    unchanged = 0
    for raw, target_id, id_problem, payload, parse_problem in zip(
        parsed.rows, ids, id_problems, payloads, parse_problems, strict=True
    ):
        if id_problem is not None:
            drafts.append(_RowDraft(row_no=raw.row_no, kind="ERROR", error_reason=id_problem))
            continue
        if target_id is not None and id_counts[target_id] > 1:
            drafts.append(
                _RowDraft(
                    row_no=raw.row_no,
                    kind="ERROR",
                    error_reason=f"같은 ID가 파일에 여러 번 있습니다(ID {target_id}). "
                    "한 행만 남기고 지워 주세요.",
                )
            )
            continue
        if target_id is not None and target_id not in snapshots:
            drafts.append(
                _RowDraft(
                    row_no=raw.row_no,
                    kind="ERROR",
                    error_reason=f"존재하지 않거나 삭제된 ID입니다({target_id}). "
                    "목록을 다시 내려받아 주세요.",
                )
            )
            continue
        if parse_problem is not None:
            drafts.append(_RowDraft(row_no=raw.row_no, kind="ERROR", error_reason=parse_problem))
            continue
        assert payload is not None
        code = payload[target.code_field]
        if code_counts[code] > 1:
            drafts.append(
                _RowDraft(
                    row_no=raw.row_no,
                    kind="ERROR",
                    error_reason=f"같은 {code_label}이(가) 파일에 여러 번 있습니다({code}). "
                    "한 행만 남기고 지워 주세요.",
                )
            )
            continue
        owner_id = code_owners.get(code)
        if owner_id is not None and owner_id != target_id:
            drafts.append(
                _RowDraft(
                    row_no=raw.row_no,
                    kind="ERROR",
                    error_reason=f"이미 등록된 {code_label}입니다({code}). "
                    "다른 코드를 쓰거나, 그 행을 수정하려면 해당 ID로 올려 주세요.",
                )
            )
            continue

        if target_id is None:
            drafts.append(_RowDraft(row_no=raw.row_no, kind="NEW", payload=payload))
            continue

        snapshot = snapshots[target_id]
        changed_fields = [
            column.field
            for column in target.columns
            if payload[column.field] != snapshot.payload[column.field]
        ]
        # 불변 칸(어댑터 선언 — 예: SKU 종류·제품코드)이 변경 diff에 잡히면
        # 오류 행이다(S1.5 판정 ① "종류 전환=오류 행"). 반영 단계가 아니라
        # 여기서 잡아야 검토자가 diff에서 보는 것과 반영이 일치한다.
        blocked = [field for field in changed_fields if field in target.immutable_fields]
        if blocked:
            labels = ", ".join(field_to_header[field] for field in blocked)
            drafts.append(
                _RowDraft(
                    row_no=raw.row_no,
                    kind="ERROR",
                    error_reason=f"{labels}은(는) 왕복 편집으로 변경할 수 없습니다. "
                    "목록을 다시 내려받아 해당 칸을 원래 값으로 되돌려 주세요.",
                )
            )
            continue
        if not changed_fields:
            unchanged += 1  # 파일과 같은 행 — 저장하지 않는다(§12.2 "변경분만").
            continue
        drafts.append(
            _RowDraft(
                row_no=raw.row_no,
                kind="CHANGED",
                target_id=target_id,
                expected_version=snapshot.version,
                payload=payload,
                changes={
                    field: [
                        target.display(field, snapshot.payload),
                        target.display(field, payload),
                    ]
                    for field in changed_fields
                },
            )
        )

    for structure_problem in parsed.problems:
        drafts.append(
            _RowDraft(
                row_no=structure_problem.row_no,
                kind="ERROR",
                error_reason=structure_problem.reason,
            )
        )
    drafts.sort(key=lambda draft: draft.row_no)
    return drafts, unchanged


# ── 스테이징 (업로드 → diff) ───────────────────────────────────────────────


def stage_import(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    registry_code: str,
    stream: BinaryIO,
    filename: str,
) -> tuple[int, dict[str, Any]]:
    target = require_target(registry_code)
    original = sanitize_filename(filename)
    _validate_extension(original)
    raw = _read_limited(stream)
    if not raw:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)
    sha256 = hashlib.sha256(raw).hexdigest()
    # 파싱은 CPU 작업이라 트랜잭션 밖이다(§17.1은 외부 호출을 금하지만, 여기서는
    # 실패가 흔한 검증을 DB 잠금 없이 끝내 두는 쪽이 낫다).
    parsed = parser.parse_csv(
        parser.decode_upload(raw), header=target.header, string_columns=target.string_columns
    )
    total_rows = len(parsed.rows) + len(parsed.problems)
    if total_rows == 0:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)
    if total_rows > IMPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "file": f"행이 너무 많습니다({total_rows:,}행). "
                f"{IMPORT_MAX_ROWS:,}행 이하로 나누어 올려 주세요."
            },
        )

    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=STAGE_ENDPOINT,
            key=idempotency_key,
            request_body={"registry_code": registry_code, "sha256": sha256, "filename": original},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        drafts, unchanged = _diff_rows(target, parsed, session)

        staging = ImportStaging(
            registry_code=registry_code,
            original_filename=original,
            sha256=sha256,
            status="PENDING",
            total_data_rows=total_rows,
            unchanged_rows=unchanged,
            created_by_id=actor.id,
        )
        session.add(staging)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.IMPORTS_STAGING_DUPLICATE_PENDING,
                log_context={"registry_code": registry_code, "sha256": sha256},
            ) from exc

        for draft in drafts:
            session.add(
                ImportStagingRow(
                    staging_id=staging.id,
                    row_no=draft.row_no,
                    kind=draft.kind,
                    target_id=draft.target_id,
                    expected_version=draft.expected_version,
                    payload=draft.payload or {},
                    changes=draft.changes,
                    error_reason=draft.error_reason,
                    created_by_id=actor.id,
                )
            )
        session.flush()

        view = StagingView(
            id=staging.id,
            registry_code=registry_code,
            registry_label=target.label_ko,
            original_filename=original,
            sha256=sha256,
            status="PENDING",
            total_data_rows=total_rows,
            unchanged_rows=unchanged,
            new_rows=sum(1 for draft in drafts if draft.kind == "NEW"),
            changed_rows=sum(1 for draft in drafts if draft.kind == "CHANGED"),
            error_rows=sum(1 for draft in drafts if draft.kind == "ERROR"),
            created_at=staging.created_at,
            confirmed_at=None,
        )
        body = _serialize_staging(view)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── 조회 ───────────────────────────────────────────────────────────────────


def _require_staging(session: Session, staging_id: int) -> ImportStaging:
    row = session.execute(
        select(ImportStaging).where(
            ImportStaging.id == staging_id, ImportStaging.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"staging_id": staging_id})
    return row


def _kind_counts(session: Any, staging_ids: list[int]) -> dict[int, dict[str, int]]:
    """스테이징별 행 분류 수 — 저장하지 않고 센다(행이 실체라 어긋날 수 없다)."""
    if not staging_ids:
        return {}
    rows = session.execute(
        select(ImportStagingRow.staging_id, ImportStagingRow.kind, func.count())
        .where(
            ImportStagingRow.staging_id.in_(staging_ids),
            ImportStagingRow.deleted_at.is_(None),
        )
        .group_by(ImportStagingRow.staging_id, ImportStagingRow.kind)
    ).all()
    grouped: dict[int, dict[str, int]] = {}
    for staging_id, kind, count in rows:
        grouped.setdefault(staging_id, {})[kind] = count
    return grouped


def _staging_view(row: ImportStaging, counts: dict[str, int]) -> StagingView:
    return StagingView(
        id=row.id,
        registry_code=row.registry_code,
        registry_label=IMPORT_TARGETS[row.registry_code].label_ko,
        original_filename=row.original_filename,
        sha256=row.sha256,
        status=row.status,
        total_data_rows=row.total_data_rows,
        unchanged_rows=row.unchanged_rows,
        new_rows=counts.get("NEW", 0),
        changed_rows=counts.get("CHANGED", 0),
        error_rows=counts.get("ERROR", 0),
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
    )


def list_stagings(*, offset: int, limit: int) -> tuple[list[StagingView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count())
            .select_from(ImportStaging)
            .where(ImportStaging.deleted_at.is_(None))
        ).scalar_one()
        rows = list(
            session.execute(
                select(ImportStaging)
                .where(ImportStaging.deleted_at.is_(None))
                .order_by(ImportStaging.id.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        counts = _kind_counts(session, [row.id for row in rows])
        return [_staging_view(row, counts.get(row.id, {})) for row in rows], total


def get_staging(staging_id: int) -> StagingView:
    with unit_of_work() as uow:
        session = uow.session
        row = _require_staging(session, staging_id)
        counts = _kind_counts(session, [row.id])
        return _staging_view(row, counts.get(row.id, {}))


def list_staging_rows(
    *, staging_id: int, offset: int, limit: int
) -> tuple[list[StagingRowView], int]:
    with unit_of_work() as uow:
        session = uow.session
        staging = _require_staging(session, staging_id)
        target = IMPORT_TARGETS[staging.registry_code]
        field_to_header = {column.field: column.header for column in target.columns}
        condition = (
            ImportStagingRow.staging_id == staging_id,
            ImportStagingRow.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(ImportStagingRow).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(ImportStagingRow)
            .where(*condition)
            .order_by(ImportStagingRow.row_no)
            .offset(offset)
            .limit(limit)
        ).scalars()
        views = [
            StagingRowView(
                id=row.id,
                row_no=row.row_no,
                kind=row.kind,
                target_id=row.target_id,
                payload=row.payload,
                changes=None
                if row.changes is None
                else {
                    field_to_header.get(field, field): [str(pair[0]), str(pair[1])]
                    for field, pair in row.changes.items()
                },
                error_reason=row.error_reason,
            )
            for row in rows
        ]
        return views, total


# ── 확정 (사람 1클릭 — §15 L2) ─────────────────────────────────────────────


def confirm_staging(
    *, actor: AuthenticatedUser, idempotency_key: str, staging_id: int
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CONFIRM_ENDPOINT,
            key=idempotency_key,
            request_body={"staging_id": staging_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        staging = session.execute(
            select(ImportStaging)
            .where(ImportStaging.id == staging_id, ImportStaging.deleted_at.is_(None))
            .with_for_update()
        ).scalar_one_or_none()
        if staging is None:
            raise NotFoundError(log_context={"staging_id": staging_id})
        if staging.status != "PENDING":
            raise AppError(
                ErrorCode.IMPORTS_STAGING_NOT_PENDING,
                log_context={"staging_id": staging_id, "status": staging.status},
            )

        target = IMPORT_TARGETS[staging.registry_code]
        rows = list(
            session.execute(
                select(ImportStagingRow)
                .where(
                    ImportStagingRow.staging_id == staging_id,
                    ImportStagingRow.deleted_at.is_(None),
                    ImportStagingRow.kind.in_(("NEW", "CHANGED")),
                )
                .order_by(ImportStagingRow.row_no)
            ).scalars()
        )

        # 1) 충돌 전수 검사 — 하나라도 어긋나면 아무것도 반영하지 않는다.
        changed = [row for row in rows if row.kind == "CHANGED"]
        current_targets = target.load_targets_for_update(
            session, [row.target_id for row in changed if row.target_id is not None]
        )
        conflicts: list[str] = []
        for row in changed:
            assert row.target_id is not None  # kind='CHANGED'는 DB CHECK가 보장한다
            current = current_targets.get(row.target_id)
            if current is None:
                conflicts.append(f"{row.row_no}행(대상이 삭제됨)")
            elif current.version != row.expected_version:
                conflicts.append(f"{row.row_no}행(다른 사용자가 먼저 수정함)")
        # 참조 재검증(리뷰 검출 TOCTOU) — 스테이징이 굳힌 참조가 그 사이 무효해졌으면
        # 같은 409로 전체 거부한다(부분 반영 없음의 같은 원칙).
        conflicts.extend(
            target.verify_references(session, [(row.row_no, row.payload) for row in rows])
        )
        if conflicts:
            raise AppError(
                ErrorCode.IMPORTS_CONFIRM_VERSION_CONFLICT,
                detail={"rows": ", ".join(conflicts)},
                log_context={"staging_id": staging_id, "conflicts": len(conflicts)},
            )

        # 2) 반영 — 실패(제약 위반)는 전체 롤백이다(한 업무 동작 = 한 트랜잭션).
        created = 0
        updated = 0
        for row in rows:
            # 실패 경로에서 쓸 값은 flush **전에** 평범한 변수로 빼 둔다(함정 ② —
            # 실패한 flush 뒤 ORM 속성을 읽으면 PendingRollbackError로 원인이 바뀐다).
            row_no = row.row_no
            try:
                if row.kind == "NEW":
                    target.create(session, actor.id, row.payload)
                    created += 1
                else:
                    assert row.target_id is not None and row.changes is not None
                    target.apply_changes(
                        session,
                        actor.id,
                        current_targets[row.target_id],
                        row.payload,
                        set(row.changes),
                    )
                    updated += 1
            except IntegrityError as exc:
                raise AppError(
                    ErrorCode.VALIDATION_INVALID_FIELD,
                    detail={
                        "row": f"{row_no}행을 반영하지 못했습니다(코드 중복 등 제약 위반). "
                        "목록을 다시 내려받아 변경분을 새로 올려 주세요."
                    },
                    log_context={"staging_id": staging_id, "row_no": row_no},
                ) from exc

        staging.status = "CONFIRMED"
        staging.confirmed_at = utcnow()
        staging.confirmed_by_id = actor.id
        session.flush()

        outbox.publish(
            session,
            event_type="imports.staging.confirmed",
            aggregate_type="import_staging",
            aggregate_id=staging.id,
            payload={
                "registry_code": staging.registry_code,
                "created_rows": created,
                "updated_rows": updated,
            },
        )

        body = {
            "id": staging.id,
            "status": "CONFIRMED",
            "created_rows": created,
            "updated_rows": updated,
        }
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def delete_staging(*, actor: AuthenticatedUser, staging_id: int) -> None:
    """검토 대기 스테이징 파기 — soft delete(§2 ADR-02). 확정본은 지울 수 없다."""
    with unit_of_work() as uow:
        session = uow.session
        staging = session.execute(
            select(ImportStaging)
            .where(ImportStaging.id == staging_id, ImportStaging.deleted_at.is_(None))
            .with_for_update()
        ).scalar_one_or_none()
        if staging is None:
            raise NotFoundError(log_context={"staging_id": staging_id})
        if staging.status != "PENDING":
            raise AppError(
                ErrorCode.IMPORTS_STAGING_NOT_PENDING,
                log_context={"staging_id": staging_id, "status": staging.status},
            )
        now = utcnow()
        staging.deleted_at = now
        staging.updated_by_id = actor.id
        session.execute(
            update(ImportStagingRow)
            .where(
                ImportStagingRow.staging_id == staging_id,
                ImportStagingRow.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_by_id=actor.id)
        )
        session.flush()
