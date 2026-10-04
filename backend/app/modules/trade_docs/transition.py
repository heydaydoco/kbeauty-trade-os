"""상태 대입의 단일 통로 — `record_birth`·`record_transition` (S3-1 ADR-0051 / design-B B1·B6).

`status`를 대입하는 코드는 이 파일의 두 함수뿐이다(전이 통로 스캔이 고정) — 생성자·PATCH·`update().values(status=…)`
어느 것도 status를 넘길 수 없다. 함수 하나가 (엣지 조회 → RESERVED 거부 → 사유 검사 → 상태 대입 → 이력 INSERT →
아웃박스 발행)을 **한 트랜잭션의 한 동작**으로 한다.

■ 검사 순서(호출부가 잠금·version 대조를 이미 했다는 전제): 후속 생존(취소일 때, 엣지 검사보다 먼저) → 엣지 존재 →
  동결 액션 전용 여부 → 사유. `automatic=False`면 사람 엣지 집합, True면 자동 엣지 집합에 속해야 한다 — 공개 API는
  automatic을 전달할 수 없다(L2 오케스트레이션 함수만 자동 전이를 부른다).
■ 아웃박스 payload는 화이트리스트 키뿐이다 — 금액·단가·원가·마진·여신·사유 원문·메모 금지(외부 채널로 나갈 수 있다).
■ 4종 전표의 생성·전이는 audit_log에 이중 기록하지 않는다(상태이력+아웃박스가 정본 — X-24).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import utcnow
from app.modules.outbox import service as outbox
from app.modules.trade_docs.chain import live_children_numbers
from app.modules.trade_docs.constants import (
    DOC_TABLES,
    EVENT_PREFIX,
    FREEZE_COLUMN,
    PARTNER_COLUMN,
    STATUS_LOG_FK,
    DocKind,
)
from app.modules.trade_docs.machine import (
    AUTO_TRANSITIONS,
    FREEZE_ACTION_EDGES,
    HUMAN_TRANSITIONS,
    INITIAL_STATUS,
    REASON_REQUIRED_TO,
    RESERVED,
    is_terminal,
)
from app.modules.trade_docs.models import STATUS_LOG_MODELS

REASON_MAX = 500

#: 이벤트 payload 허용 키 — 단일 빌더 `_payload`가 강제한다(테스트가 키 집합·금지 문자열을 고정).
PAYLOAD_KEYS = (
    "doc_type",
    "doc_id",
    "doc_number",
    "from_status",
    "to_status",
    "automatic",
    "assignee_id",
    "partner_id",
    # S3-2 PR-3a — SO 선적 수렴(CONFIRMED↔IN_SHIPMENT)을 일으킨 선적 id(자동 전이에만 실린다 — 그 밖의 이벤트에는 키가 없다).
    "cause_shipment_id",
)


def _kind(doc: Any) -> DocKind:
    kind = getattr(type(doc), "DOC_KIND", None)
    if not isinstance(kind, DocKind):
        raise TypeError(f"{type(doc).__name__}에 DOC_KIND가 없습니다 — 전표 모델이 아닙니다.")
    return kind


def _payload(
    doc: Any, kind: DocKind, from_status: str | None, to_status: str, automatic: bool
) -> dict[str, Any]:
    return {
        "doc_type": kind.value,
        "doc_id": doc.id,
        "doc_number": doc.doc_number,
        "from_status": from_status,
        "to_status": to_status,
        "automatic": automatic,
        "assignee_id": doc.assignee_id,
        "partner_id": getattr(doc, PARTNER_COLUMN[kind]),
    }


def record_birth(session: Session, doc: Any, *, actor_user_id: int) -> None:
    """생성 시 초기 상태 대입 + 탄생 이력 행 + `.created` 이벤트.

    `doc`은 status가 비어 있어야 한다(생성자에 status를 넘기지 않는다). 채번(doc_number)은 호출부가 마지막
    단계에서 끝낸 뒤 부른다 — flush가 여기서 일어나 INSERT가 나간다.
    """
    kind = _kind(doc)
    doc.status = INITIAL_STATUS[kind]
    session.add(doc)
    session.flush()
    session.add(
        STATUS_LOG_MODELS[kind](
            **{
                STATUS_LOG_FK[kind]: doc.id,
                "from_status": None,
                "to_status": doc.status,
                "reason": None,
                "actor_user_id": actor_user_id,
                "automatic": False,
            }
        )
    )
    outbox.publish(
        session,
        event_type=f"{EVENT_PREFIX[kind]}.created",
        aggregate_type=DOC_TABLES[kind],
        aggregate_id=doc.id,
        payload=_payload(doc, kind, None, doc.status, False),
    )


def _not_allowed(kind: DocKind, from_status: str, to_status: str) -> AppError:
    return AppError(
        ErrorCode.TRADE_DOCS_TRANSITION_NOT_ALLOWED,
        detail={"from": from_status, "to": to_status},
        log_context={"kind": kind.value},
    )


def record_transition(
    session: Session,
    doc: Any,
    to: str,
    *,
    actor_user_id: int | None,
    reason: str | None,
    automatic: bool,
    approval_id: int | None = None,
    via_freeze_action: bool = False,
    cause_shipment_id: int | None = None,
) -> str:
    """상태 전이 1건을 검사·기록한다. 돌려주는 값은 이전 상태(from).

    `cause_shipment_id`는 SO 선적 수렴(자동 전이)의 원인 선적 — 이벤트 payload에만 싣는다(화이트리스트 키, 금액 없음).
    """
    kind = _kind(doc)
    from_status: str = doc.status
    if to in RESERVED[kind] or from_status in RESERVED[kind]:
        raise _not_allowed(kind, from_status, to)

    # 역순 취소 — 엣지 검사보다 **먼저** 살아 있는 후속을 본다(CONVERTED QT 취소가 '전이 불가'가 아니라
    # '먼저 취소할 후속 전표' 안내가 되도록).
    if to == "CANCELLED" and not is_terminal(kind, from_status):
        successors = live_children_numbers(session, kind, doc.id)
        if successors:
            raise AppError(
                ErrorCode.TRADE_DOCS_CANCEL_SUCCESSOR_ALIVE,
                detail={"successors": successors},
                log_context={"kind": kind.value, "doc_id": doc.id},
            )

    pair = (from_status, to)
    edges = AUTO_TRANSITIONS[kind] if automatic else HUMAN_TRANSITIONS[kind]
    if pair not in edges:
        raise _not_allowed(kind, from_status, to)
    is_freeze_edge = pair in FREEZE_ACTION_EDGES[kind]
    if is_freeze_edge != via_freeze_action:
        raise _not_allowed(
            kind, from_status, to
        )  # 동결 엣지는 동결 액션으로만, 그 밖은 동결 액션으로 못 넘는다

    clean_reason = reason.strip() if reason is not None else None
    if to in REASON_REQUIRED_TO[kind] and not clean_reason:
        raise AppError(
            ErrorCode.TRADE_DOCS_TRANSITION_REASON_REQUIRED,
            detail={"to": to},
            log_context={"kind": kind.value},
        )
    if clean_reason and len(clean_reason) > REASON_MAX:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"reason": f"사유는 {REASON_MAX}자 이내로 입력해 주세요."},
        )

    log_model = STATUS_LOG_MODELS[kind]
    log_fields: dict[str, Any] = {
        STATUS_LOG_FK[kind]: doc.id,
        "from_status": from_status,
        "to_status": to,
        "reason": clean_reason or None,
        "actor_user_id": actor_user_id,
        "automatic": automatic,
    }
    if approval_id is not None:
        if not hasattr(log_model, "approval_id"):
            raise TypeError(f"{log_model.__tablename__}에는 approval_id가 없습니다.")
        log_fields["approval_id"] = approval_id

    doc.status = to
    if via_freeze_action:
        setattr(
            doc, FREEZE_COLUMN[kind], utcnow()
        )  # 동결 시각은 동결 전이와 같은 flush에 함께 쓴다
    if actor_user_id is not None:
        doc.updated_by_id = actor_user_id
    session.add(log_model(**log_fields))
    payload = _payload(doc, kind, from_status, to, automatic)
    if cause_shipment_id is not None:
        if not automatic:
            raise TypeError("cause_shipment_id는 자동 수렴 전이에만 싣는다.")
        payload["cause_shipment_id"] = cause_shipment_id
    outbox.publish(
        session,
        event_type=f"{EVENT_PREFIX[kind]}.status_changed",
        aggregate_type=DOC_TABLES[kind],
        aggregate_id=doc.id,
        payload=payload,
    )
    session.flush()
    return from_status
