"""결재선 매핑 — 선택 규칙(`resolve_line`)과 관리 서비스 (S3-1 ADR-0061 / design-C C3).

■ 1행 = "이 (유형·통화)에서 금액이 `threshold_amount`를 **초과(strict >)** 하면 `approver_role`이 결재한다"(계단식 구간).
  임계 0 = 양(+)의 금액 전부. **경계 = 초과** — "임계 초과"의 직독이고, 임계와 같은 금액은 한 단계 아래 구간이다.
■ **선택 규칙은 `resolve_line` 하나**: 후보 = 활성 행 중 유형 일치 ∧ **같은 통화**(환산 없음 — 환율 규약이 미결이라 승인 라우팅이
  환율 원천에 종속되면 다른 결정이 뒤집힐 때 함께 뒤집힌다) ∧ `threshold < amount`. 후보 중 임계 최대 1행(동률은 DB 유니크가 배제).
■ **매핑 없음 = fail-closed**: 후보 0행이면 요청 생성 불가(422 `LINE.NOT_CONFIGURED`). 결재선에 "이 금액 미만은 자동 통과" 의미는
  없다 — 승인이 필요한지는 도메인 게이트가 정하고, 결재선은 **'누가'만** 정한다(자동 승인 경로 부재).
■ **요청 시점 동결**: `required_role`·`approval_line_id`를 approvals에 복사한다 — 결재선 변경이 진행 중 승인에 소급하지 않는다.
■ **초기 행은 시드·CLI·기본값 없이 ADMIN 화면/API로만** 공급한다(임계는 업무 정책 — 함정 ⑩). 등록 전 여신 초과 SO는 확정 불가가 정상이다.
■ 수정은 역할·메모만(유형·통화·임계는 삭제+신규 — 컬럼 UPDATE 권한이 DB로 강제), 삭제는 soft delete.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import CURRENCY_MINOR_UNITS, Money, parse_minor_amount
from app.core.time import utcnow
from app.modules.approvals.machine import APPROVAL_TYPES, APPROVER_ROLES
from app.modules.approvals.models import ApprovalLine
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser

LINE_CREATE_ENDPOINT = "POST /api/v1/approval-lines"

_UNIQUE_INDEX = "uq_approval_lines_threshold_active"


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def resolve_line(
    session: Session, *, approval_type: str, amount: int, currency: str
) -> ApprovalLine | None:
    """금액에 해당하는 결재선 1행 — 없으면 None(호출부가 요청 생성을 거부한다 — fail-closed).

    같은 통화만 본다(환산 없음). 임계 **초과**만 후보이며 그중 임계가 가장 큰 행이 이긴다.
    """
    return session.execute(
        select(ApprovalLine)
        .where(
            ApprovalLine.deleted_at.is_(None),
            ApprovalLine.approval_type == approval_type,
            ApprovalLine.threshold_currency == currency,
            ApprovalLine.threshold_amount < amount,
        )
        .order_by(ApprovalLine.threshold_amount.desc(), ApprovalLine.id)
        .limit(1)
    ).scalar_one_or_none()


def line_body(row: ApprovalLine) -> dict[str, Any]:
    return {
        "id": row.id,
        "approval_type": row.approval_type,
        "threshold_amount": row.threshold_amount,
        "threshold_currency": row.threshold_currency,
        "threshold_text": format(
            Money(row.threshold_amount, row.threshold_currency).to_decimal(), "f"
        ),
        "approver_role": row.approver_role,
        "note": row.note,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _clean_note(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > 200:
        raise _invalid("note", "메모는 200자 이내로 입력해 주세요.")
    return text


def _require_choice(field: str, value: object, allowed: tuple[str, ...], label: str) -> str:
    text = str(value)
    if text not in allowed:
        raise _invalid(field, f"{label}은(는) {', '.join(allowed)} 중 하나여야 합니다.")
    return text


def _require_currency(raw: object) -> str:
    code = str(raw).strip().upper()
    if code not in CURRENCY_MINOR_UNITS:
        raise _invalid("currency", "지원하지 않는 통화입니다. 통화 코드를 확인해 주세요.")
    return code


def _parse_threshold(raw: Decimal, currency: str) -> int:
    try:
        return parse_minor_amount(format(raw, "f"), currency, field="threshold")
    except ValueError as exc:
        # 메시지는 field·통화 안내뿐이다(입력 원문을 싣지 않는다).
        raise _invalid("threshold", str(exc).split(": ", 1)[-1]) from exc


def _duplicate_error(row_id: int | None = None) -> AppError:
    return AppError(
        ErrorCode.APPROVALS_LINE_DUPLICATE,
        detail={"threshold": "같은 유형·통화·임계 금액의 결재선이 이미 있습니다."},
        log_context={"existing_id": row_id},
    )


def _flush_or_duplicate(session: Session) -> None:
    """동시 등록 경합은 DB 유니크가 최종 방어한다 — 같은 안내(409)로 번역해 500을 막는다."""
    try:
        session.flush()
    except IntegrityError as exc:
        text = str(exc.orig) if exc.orig is not None else ""
        if _UNIQUE_INDEX in text:
            raise _duplicate_error() from exc
        raise


def create_approval_line(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=LINE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        approval_type = _require_choice(
            "approval_type", payload["approval_type"], APPROVAL_TYPES, "승인 유형"
        )
        role = _require_choice(
            "approver_role", payload["approver_role"], APPROVER_ROLES, "결재 역할"
        )
        currency = _require_currency(payload["currency"])
        amount = _parse_threshold(payload["threshold"], currency)
        existing = session.execute(
            select(ApprovalLine.id).where(
                ApprovalLine.deleted_at.is_(None),
                ApprovalLine.approval_type == approval_type,
                ApprovalLine.threshold_currency == currency,
                ApprovalLine.threshold_amount == amount,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise _duplicate_error(existing)
        row = ApprovalLine(
            approval_type=approval_type,
            threshold_amount=amount,
            threshold_currency=currency,
            approver_role=role,
            note=_clean_note(payload.get("note")),
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        _flush_or_duplicate(session)
        audit.record(
            session,
            action=AuditAction.APPROVAL_LINE_CREATED,
            actor_user_id=actor.id,
            entity_type="approval_lines",
            entity_id=row.id,
            detail={
                "approval_type": approval_type,
                "currency": currency,
                "threshold_amount": amount,
                "approver_role": role,
            },
        )
        body = line_body(row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=_json_safe(body))
        return 201, body


def _json_safe(body: dict[str, Any]) -> dict[str, Any]:
    """멱등 응답 저장용 — datetime을 ISO 문자열로(JSONB는 datetime을 직렬화하지 못한다)."""
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in body.items()}


def _load_for_update(session: Session, line_id: int) -> ApprovalLine:
    row = session.execute(
        select(ApprovalLine)
        .where(ApprovalLine.id == line_id, ApprovalLine.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"approval_line_id": line_id})
    return row


def update_approval_line(
    *, actor: AuthenticatedUser, line_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """역할·메모만 바꾼다(version 필수) — 이미 요청된 승인에는 소급하지 않는다(요청 시점 동결)."""
    with unit_of_work() as uow:
        session = uow.session
        row = _load_for_update(session, line_id)
        if int(payload["version"]) != row.version:
            raise VersionConflictError(log_context={"approval_line_id": line_id})
        before_role = row.approver_role
        changed: list[str] = []
        if "approver_role" in payload and payload["approver_role"] is not None:
            role = _require_choice(
                "approver_role", payload["approver_role"], APPROVER_ROLES, "결재 역할"
            )
            if role != row.approver_role:
                row.approver_role = role
                changed.append("approver_role")
        if "note" in payload:
            note = _clean_note(payload["note"])
            if note != row.note:
                row.note = note
                changed.append("note")
        if changed:
            row.updated_by_id = actor.id
            session.flush()
            audit.record(
                session,
                action=AuditAction.APPROVAL_LINE_UPDATED,
                actor_user_id=actor.id,
                entity_type="approval_lines",
                entity_id=row.id,
                detail={
                    "approval_type": row.approval_type,
                    "currency": row.threshold_currency,
                    "threshold_amount": row.threshold_amount,
                    "fields": changed,
                    "old_role": before_role,
                    "new_role": row.approver_role,
                },
            )
        return line_body(row)


def delete_approval_line(*, actor: AuthenticatedUser, line_id: int, version: int) -> None:
    """soft delete — 진행 중 승인이 FK로 참조해도 행은 남는다(매핑 계보 보존)."""
    with unit_of_work() as uow:
        session = uow.session
        row = _load_for_update(session, line_id)
        if version != row.version:
            raise VersionConflictError(log_context={"approval_line_id": line_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        session.flush()
        audit.record(
            session,
            action=AuditAction.APPROVAL_LINE_DELETED,
            actor_user_id=actor.id,
            entity_type="approval_lines",
            entity_id=row.id,
            detail={
                "approval_type": row.approval_type,
                "currency": row.threshold_currency,
                "threshold_amount": row.threshold_amount,
                "approver_role": row.approver_role,
            },
        )


def list_approval_lines(
    *, offset: int, limit: int, approval_type: str | None = None, currency: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[Any] = [ApprovalLine.deleted_at.is_(None)]
        if approval_type:
            conditions.append(ApprovalLine.approval_type == approval_type)
        if currency:
            conditions.append(ApprovalLine.threshold_currency == currency.upper())
        total = session.execute(
            select(func.count()).select_from(ApprovalLine).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(ApprovalLine)
            .where(*conditions)
            .order_by(
                ApprovalLine.approval_type,
                ApprovalLine.threshold_currency,
                ApprovalLine.threshold_amount,
                ApprovalLine.id,
            )
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [line_body(row) for row in rows], total


def coverage() -> dict[str, Any]:
    """결재선 공급 현황 — 등록 0행이면 확정 불가(정상 fail-closed)임을 서버 문구로 안내한다."""
    with unit_of_work() as uow:
        session = uow.session
        rows = session.execute(
            select(
                ApprovalLine.approval_type,
                ApprovalLine.threshold_currency,
                ApprovalLine.threshold_amount,
            )
            .where(ApprovalLine.deleted_at.is_(None))
            .order_by(
                ApprovalLine.approval_type,
                ApprovalLine.threshold_currency,
                ApprovalLine.threshold_amount,
            )
        ).all()
    messages: list[str] = []
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for approval_type, currency, amount in rows:
        entry = entries.setdefault(
            (approval_type, currency),
            {
                "approval_type": approval_type,
                "currency": currency,
                "lines": 0,
                "has_zero_threshold": False,
            },
        )
        entry["lines"] += 1
        if amount == 0:
            entry["has_zero_threshold"] = True
    if not rows:
        messages.append("결재선이 등록되지 않아 여신 초과 수주는 확정할 수 없습니다.")
    for entry in entries.values():
        if not entry["has_zero_threshold"]:
            messages.append(
                f"{entry['currency']}: 임계 0 결재선이 없어 최소 임계 이하 금액의 승인 요청은 만들 수 없습니다."
            )
    return {
        "configured": bool(rows),
        "entries": list(entries.values()),
        "messages": messages,
    }
