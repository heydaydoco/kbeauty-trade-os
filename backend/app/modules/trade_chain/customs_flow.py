"""통관 기록 쓰기 — 추가·정정·삭제 (S3-2 PR-4a / ADR-0074 / design-A A8 / design-C T9·C9 / design-integrated X-02·X-03·R-16·R-18·R-26).

통관 기록은 관세사 신고 결과의 **사실 기록**이다(세율·과세가격·세액·HS 열 없음 — §15 법적 판정 금지). 수리일(`accepted_on`)은 신고수리 실적·
적재의무(+30) 산식의 **유일 원천**이고 마일스톤으로 복사하지 않는다(읽기 시 MIN 파생 — `milestone_view`).

■ 잠금 순서(T9 — R-08): 멱등 claim(추가만) → (관세사) partners `FOR KEY SHARE` → 선적 `FOR UPDATE` → 통관 기록 행 `FOR UPDATE`(+version).
  선적 취소도 같은 헤더를 `FOR UPDATE`로 잡으므로 "통관 기록 추가 vs 취소"는 직렬화된다(기록이 먼저면 취소 409 CUSTOMS_RECORD_ALIVE).
■ 오류 우선순위(ADR-0079 ⑧): 선적·기록 404(무잠금 peek) → 취소 선적 409 NOT_ACTIVE → version 409 → 입력 422(구분·날짜·번호·메모·관세사 유형·
  사유) → 잠금 뒤 재확인. (구분, 신고번호) 중복 409는 INSERT의 부분 유니크가 판정한다(번역표 — 입력 422 뒤에 드러남: 부채 R-4a-3).
■ 날짜: 신고일·수리일 ≤ **KST 오늘(여유 0 — R-18)**, 수리일 ≥ 신고일(서비스 선검증 + CHECK — R-26).
■ 정정: 신고번호·신고일 변경과 **기존 수리일의 변경·삭제**는 사유 필수(422 REASON_REQUIRED), 수리일 첫 입력(미수리 → 수리)은 기록이라 사유 불요
  (마일스톤 실적 '기록 vs 정정'과 같은 규율 — 자율 확정). 사실 열이 바뀌면 audit_log `shipments.customs.corrected`(전후 날짜·번호·사유),
  삭제는 `shipments.customs.deleted`(사유). 아웃박스 `shipments.customs.recorded`(추가·사실 정정 — payload: 선적·기록·구분·수리 유무).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.core.text import invisible_char_problem, is_invisible_char
from app.core.time import today_kst
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.partners import service as partners
from app.modules.shipments import service as shipments
from app.modules.shipments.models import CustomsRecord, Shipment
from app.modules.trade_chain.milestone_view import RECORD_EDITABLE_STATES, customs_body
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.validation import invalid

CREATE_ENDPOINT = "POST /api/v1/shipments/{id}/customs-records"
RECORDED_EVENT = "shipments.customs.recorded"
#: 정정 시 사유가 필요한 사실 열(수리일은 '기존 값이 있을 때'만 — 첫 입력은 기록).
_FACT_FIELDS = ("declaration_no", "declared_on", "accepted_on")
_NOTE_LINE_BREAKS = frozenset("\t\n\r")


def _require_active(row: Shipment) -> None:
    if row.status not in RECORD_EDITABLE_STATES:
        raise AppError(
            ErrorCode.SHIPMENTS_SHIPMENT_NOT_ACTIVE,
            log_context={"shipment_id": row.id, "status": row.status},
        )


def _declaration_no(raw: object) -> str:
    """신고번호 — strip 전 원문의 보이지 않는 글자는 422, 앞뒤 공백 제거·대문자 정규화(같은 번호의 대소문자 이중 등록 차단), 안쪽 공백 422."""
    text = str(raw or "")
    problem = invisible_char_problem(text, label="신고번호")
    if problem is not None:
        raise invalid("declaration_no", problem)
    cleaned = text.strip().upper()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        raise invalid("declaration_no", "신고번호는 공백 없이 입력해 주세요.")
    return cleaned


def _note(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    if any(is_invisible_char(ch) for ch in text if ch not in _NOTE_LINE_BREAKS):
        raise invalid(
            "note",
            "메모에 보이지 않는 글자(제어·서식·채움 문자)가 있습니다 — 줄바꿈·탭만 쓸 수 있습니다.",
        )
    return text.strip() or None


def _reason(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    problem = invisible_char_problem(text, label="사유")
    if problem is not None:
        raise invalid("reason", problem)
    return text.strip() or None


def _check_dates(declared_on: date, accepted_on: date | None) -> None:
    today = today_kst()
    future = {
        field: "오늘(한국 날짜) 이후일 수 없습니다."
        for field, value in (("declared_on", declared_on), ("accepted_on", accepted_on))
        if value is not None and value > today
    }
    if future:
        raise AppError(ErrorCode.SHIPMENTS_CUSTOMS_DATE_IN_FUTURE, detail=future)
    if accepted_on is not None and accepted_on < declared_on:
        raise AppError(
            ErrorCode.SHIPMENTS_CUSTOMS_ACCEPT_BEFORE_DECLARE,
            detail={"accepted_on": "수리일이 신고일보다 앞섭니다."},
        )


def _require_broker(session: Session, partner_id: int | None) -> None:
    if partner_id is not None:
        partners.require_partner_of_any_type(
            session,
            int(partner_id),
            ("CUSTOMS_BROKER",),
            field="customs_broker_partner_id",
            type_label="관세사",
            lock=True,  # partners FOR KEY SHARE — 선적 잠금보다 먼저(R-08)
        )


def _publish(session: Session, row: Shipment, record: CustomsRecord) -> None:
    outbox.publish(
        session,
        event_type=RECORDED_EVENT,
        aggregate_type="shipments",
        aggregate_id=row.id,
        payload={
            "shipment_id": row.id,
            "customs_record_id": record.id,
            "declaration_kind": record.declaration_kind,
            "accepted": record.accepted_on is not None,
        },
    )


# ── 추가 (S17) ────────────────────────────────────────────────────────────────


def create_customs_record(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        peek = shipments.require_shipment(session, shipment_id)  # 무잠금 peek(404)
        _require_active(peek)  # 409
        kind = str(payload["declaration_kind"])
        if kind != peek.shipment_kind:
            raise AppError(
                ErrorCode.SHIPMENTS_CUSTOMS_KIND_MISMATCH,
                detail={"declaration_kind": f"이 선적은 {peek.shipment_kind} 구분입니다."},
            )
        values: dict[str, Any] = {
            "declaration_kind": kind,
            "declaration_no": _declaration_no(payload["declaration_no"]),
            "declared_on": payload["declared_on"],
            "accepted_on": payload.get("accepted_on"),
            "customs_broker_partner_id": payload.get("customs_broker_partner_id"),
            "note": _note(payload.get("note")),
        }
        _check_dates(values["declared_on"], values["accepted_on"])
        _require_broker(session, values["customs_broker_partner_id"])
        row = lock_document(session, Shipment, shipment_id)  # shipments FOR UPDATE
        _require_active(row)  # 잠금 뒤 재확인(TOCTOU)
        record = shipments.insert_customs_record(session, row, values, actor_id=actor.id)
        _publish(session, row, record)
        body = customs_body(session, record)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── 정정 (S18) ────────────────────────────────────────────────────────────────


def _changes(record: CustomsRecord, payload: dict[str, Any]) -> dict[str, Any]:
    """보낸 필드 중 실제로 바뀌는 것만(같은 값은 무변경). 신고번호·신고일은 null 불가(422)."""
    changes: dict[str, Any] = {}
    for field in ("declaration_no", "declared_on"):
        if field in payload:
            if payload[field] is None:
                raise invalid(field, "비울 수 없습니다.")
            value = _declaration_no(payload[field]) if field == "declaration_no" else payload[field]
            if value != getattr(record, field):
                changes[field] = value
    if "accepted_on" in payload and payload["accepted_on"] != record.accepted_on:
        changes["accepted_on"] = payload["accepted_on"]
    if (
        "customs_broker_partner_id" in payload
        and payload["customs_broker_partner_id"] != record.customs_broker_partner_id
    ):
        changes["customs_broker_partner_id"] = payload["customs_broker_partner_id"]
    if "note" in payload:
        note = _note(payload["note"])
        if note != record.note:
            changes["note"] = note
    return changes


def _needs_reason(record: CustomsRecord, changes: dict[str, Any]) -> bool:
    if "declaration_no" in changes or "declared_on" in changes:
        return True
    return (
        "accepted_on" in changes and record.accepted_on is not None
    )  # 기존 수리일의 변경·삭제 = 정정


def _audit_detail(
    row: Shipment, record: CustomsRecord, changes: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    facts = {
        field: {
            "before": _text(getattr(record, field)),
            "after": _text(changes[field]),
        }
        for field in _FACT_FIELDS
        if field in changes
    }
    return {
        "doc_number": row.doc_number,
        "customs_record_id": record.id,
        "declaration_kind": record.declaration_kind,
        "changed": sorted(changes),
        "facts": facts,
        "reason": reason,
    }


def _text(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def update_customs_record(
    *, actor: AuthenticatedUser, shipment_id: int, record_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """통관 기록 정정 — 보낸 필드만. 바뀌는 것이 없으면 무변경(version·audit·이벤트 0)."""
    with unit_of_work() as uow:
        session = uow.session
        peek = shipments.require_shipment(session, shipment_id)
        record = shipments.require_customs_record(session, peek.id, record_id)  # 부모-자식 404
        _require_active(peek)
        if record.version != payload["version"]:
            raise VersionConflictError(log_context={"customs_record_id": record_id})
        changes = _changes(record, payload)
        declared = changes.get("declared_on", record.declared_on)
        accepted = changes.get("accepted_on", record.accepted_on)
        _check_dates(declared, accepted)
        reason = _reason(payload.get("reason"))
        if _needs_reason(record, changes) and reason is None:
            raise AppError(
                ErrorCode.SHIPMENTS_CUSTOMS_REASON_REQUIRED,
                detail={"reason": "신고번호·신고일·수리일 정정 사유를 입력해 주세요."},
            )
        if "customs_broker_partner_id" in changes:
            _require_broker(session, changes["customs_broker_partner_id"])
        row = lock_document(session, Shipment, shipment_id)
        record = shipments.require_customs_record(session, row.id, record_id, for_update=True)
        _require_active(row)
        if record.version != payload["version"]:  # 잠금 뒤 재확인(peek와 잠금 사이 정정)
            raise VersionConflictError(log_context={"customs_record_id": record_id})
        if changes:
            detail = _audit_detail(row, record, changes, reason)
            shipments.apply_customs_update(record, changes, actor_id=actor.id)
            shipments.flush_translated(session)
            audit.record(
                session,
                action=AuditAction.SHIPMENTS_CUSTOMS_CORRECTED,
                actor_user_id=actor.id,
                entity_type="shipments",
                entity_id=row.id,
                detail=detail,
            )
            if set(changes) & set(_FACT_FIELDS):
                _publish(session, row, record)
        return customs_body(session, record)


# ── 삭제 (S19) ────────────────────────────────────────────────────────────────


def delete_customs_record(
    *, actor: AuthenticatedUser, shipment_id: int, record_id: int, payload: dict[str, Any]
) -> None:
    """통관 기록 삭제(soft delete) — 사유 필수(audit). 선적 취소 전 탈출로(CUSTOMS_RECORD_ALIVE 해소)."""
    with unit_of_work() as uow:
        session = uow.session
        peek = shipments.require_shipment(session, shipment_id)
        record = shipments.require_customs_record(session, peek.id, record_id)
        _require_active(peek)
        if record.version != payload["version"]:
            raise VersionConflictError(log_context={"customs_record_id": record_id})
        reason = _reason(payload.get("reason"))
        if reason is None:
            raise AppError(
                ErrorCode.SHIPMENTS_CUSTOMS_REASON_REQUIRED,
                detail={"reason": "통관 기록 삭제 사유를 입력해 주세요."},
            )
        row = lock_document(session, Shipment, shipment_id)
        record = shipments.require_customs_record(session, row.id, record_id, for_update=True)
        _require_active(row)
        if record.version != payload["version"]:
            raise VersionConflictError(log_context={"customs_record_id": record_id})
        shipments.remove_customs_record(record, actor_id=actor.id)
        shipments.flush_translated(session)
        audit.record(
            session,
            action=AuditAction.SHIPMENTS_CUSTOMS_DELETED,
            actor_user_id=actor.id,
            entity_type="shipments",
            entity_id=row.id,
            detail={
                "doc_number": row.doc_number,
                "customs_record_id": record.id,
                "declaration_kind": record.declaration_kind,
                "declaration_no": record.declaration_no,
                "accepted_on": _text(record.accepted_on),
                "reason": reason,
            },
        )
