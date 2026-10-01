"""SO 게이트 오케스트레이터 — 평가·조회·override 부여/철회 (S3-1 PR-11a / design-D D3·D5 / ADR-0069).

■ 이 파일(L2 trade_chain)이 **SO를 읽고 잠그고** `gates`(평가 틀·`clearance`·override 서비스)를 부른다 — `gates`는 도메인을 모른다(방향 반전 선례: PR-10a payments).
  `evaluate_sales_order`가 **단일 평가 진입점**이다: 조회(`GET /gates`)·override 부여(대상 1게이트만)·PR-12 확정 통로(`authoritative=True`)가 같은 함수를 쓴다 — 확정 가능 판정은 `gates.service.clearance`뿐이다.
■ **조회(`GET`)는 아무것도 저장하지 않는다**(조회 부작용 금지) — 잠금 없는 참고값(`authoritative=false`, CREDIT 포함)이다. 증거 스냅샷(`gate_evaluations`)은 확정 시도(PR-12)만 쓴다.
■ override는 **별도 액션**이다: 멱등 선점 → (권한 사전 검증) → **SO 행 `FOR UPDATE`**(전역 잠금 순서 (5)) → SO가 RECEIVED인지 → 서버가 **지금** 그 게이트만 평가 → `gates.service.grant_override`
  (역할·대상 결과·판정 해시 대조·불변 INSERT·audit·outbox) → `complete`. 실패(403/409/422)는 키를 소비하지 않는다(성공만 complete). 확정된·취소된 SO에는 부여·철회하지 않는다(409).
■ **PR-12 몫(이 파일에 없다)**: `confirm_sales_order`·승인 소비·`gate_evaluations` 기록 호출·`void_for_target` 훅. 여기서는 평가·저장 함수(`gates.service.record_evaluation`)와 단일 평가 진입점까지다.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.time import utcnow
from app.modules.approvals import service as approvals_service
from app.modules.approvals.machine import ApprovalType
from app.modules.gates import service as gates_service
from app.modules.gates.models import SUBJECT_SALES_ORDER, GateOverride
from app.modules.gates.registry import EvaluatorRegistry
from app.modules.gates.service import Clearance, Settlement
from app.modules.gates.types import (
    GateCode,
    GateOutcome,
    GatePhase,
    GateResolution,
    GateSubject,
)
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.policies.service import get_policy
from app.modules.readiness.rules import SCOPE_NOTE
from app.modules.sales_orders.digest import gate_input_digest
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_chain import gate_evaluators
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import SalesOrderStatus

OVERRIDE_ENDPOINT = "POST /api/v1/sales-orders/{so_id}/gate-overrides"
REVOKE_ENDPOINT = "POST /api/v1/sales-orders/{so_id}/gate-overrides/revoke"

#: 게이트 대상 상태 — 확정 전(접수)뿐(override 부여·철회 허용 상태).
OVERRIDE_OPEN_STATUS = SalesOrderStatus.RECEIVED.value

#: 응답에서 여신 수치(한도·노출 등)를 볼 수 있는 역할 — 그 외 역할 응답에서는 CREDIT 근거 필드가 없다(ADR-0024 방식, 여신 수치는 운영 필요 데이터).
CREDIT_VISIBLE_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})
#: 값 노출이 제한되는 자유 텍스트(override 사유)·표시 전용 상세(다른 문서번호)를 볼 수 있는 역할 — 그 외 역할은 null·빈 값.
DETAIL_VISIBLE_ROLES = CREDIT_VISIBLE_ROLES

NOTE_READINESS = gate_evaluators.NOTE_READINESS


@dataclass(frozen=True, slots=True)
class GateBundle:
    """한 번의 평가 결과 묶음 — 확정 통로(PR-12)가 그대로 증거 스냅샷에 쓴다."""

    subject: GateSubject
    outcomes: list[GateOutcome]
    overrides: dict[gates_service.OverrideKey, int]
    clearance: Clearance
    approval_available: bool
    approval_id: int | None
    input_digest: str
    policy_sources: dict[str, str]
    #: `only`로 일부 게이트만 평가했다 — 이 묶음의 `clearance.cleared`는 **항상 False**(부분 평가로 확정 가능을 판정할 수 없다).
    partial: bool = False


def evaluate_sales_order(
    session: Session,
    so_id: int,
    *,
    authoritative: bool = False,
    only: list[GateCode] | None = None,
    registry: EvaluatorRegistry | None = None,
) -> GateBundle:
    """SO의 게이트 평가 → 유효 override·승인 조회 → `clearance`. **쓰기 없음.**

    `authoritative=True`는 호출자가 거래처·SO를 이미 잠갔다는 계약이다(확정 통로). 조회·override는 False.
    승인은 CREDIT 결과가 승인 해소 대상일 때만 읽기 전용으로 조회한다(`peek_active_approved` — 소비·VOID 없음, 평가 불능이면 승인 없음으로 취급).
    """
    subject = gate_evaluators.subject_from_sales_order(session, so_id, authoritative=authoritative)
    outcomes = gates_service.evaluate_all(
        session, subject, GatePhase.CONFIRM, registry=registry, only=only
    )
    approval_id: int | None = None
    if any(
        o.gate_code == GateCode.CREDIT.value and o.resolution is GateResolution.APPROVAL
        for o in outcomes
    ):
        try:
            ref = approvals_service.peek_active_approved(
                session, approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value, target_id=so_id
            )
        except AppError:  # 평가 불능 등 — 승인 없음으로 취급(fail-closed)
            ref = None
        approval_id = ref.id if ref is not None else None
    overrides = gates_service.effective_overrides(session, SUBJECT_SALES_ORDER, so_id)
    clr = gates_service.clearance(outcomes, overrides, approval_id is not None)
    if (
        only is not None
    ):  # 부분 평가는 확정 가능 판정이 아니다 — PR-12가 only를 실수로 넘겨도 우회 불가(fail-closed)
        clr = dataclasses.replace(clr, cleared=False)
    policy_sources: dict[str, str] = {
        key: get_policy(session, key).source
        for key in (gate_evaluators.POLICY_PI_MODE, gate_evaluators.POLICY_PRICE_TOLERANCE)
    }
    return GateBundle(
        subject=subject,
        outcomes=outcomes,
        overrides=overrides,
        clearance=clr,
        approval_available=approval_id is not None,
        approval_id=approval_id,
        input_digest=gate_input_digest(session, so_id),
        policy_sources=policy_sources,
        partial=only is not None,
    )


# ── 조회 ─────────────────────────────────────────────────────────────────────


def _require_so(session: Session, so_id: int) -> SalesOrder:
    row = session.execute(
        select(SalesOrder).where(SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"sales_order_id": so_id})
    return row


def _override_infos(session: Session, ids: tuple[int, ...]) -> dict[int, GateOverride]:
    if not ids:
        return {}
    return {
        row.id: row
        for row in session.execute(select(GateOverride).where(GateOverride.id.in_(ids))).scalars()
    }


def gate_report_body(
    session: Session, so: SalesOrder, bundle: GateBundle, *, roles: frozenset[RoleCode]
) -> dict[str, Any]:
    """`GateReportOut` 본문 — 결과별 정산·override 정보·사용자별 `can_override`. 여신 수치는 TRADE·ADMIN에게만."""
    line_no = {line.line_id: line.line_no for line in bundle.subject.lines}
    infos = _override_infos(session, bundle.clearance.used_overrides)
    open_for_override = so.status == OVERRIDE_OPEN_STATUS
    show_credit = bool(roles & CREDIT_VISIBLE_ROLES)
    show_detail = bool(roles & DETAIL_VISIBLE_ROLES)
    revoked = gates_service.revoked_overrides(session, SUBJECT_SALES_ORDER, so.id)
    gates: list[dict[str, Any]] = []
    for item, state in bundle.clearance.settlements:
        override_id = bundle.overrides.get((item.gate_code, item.line_id, item.basis_hash))
        override_row = (
            infos.get(override_id) if state is Settlement.OVERRIDDEN and override_id else None
        )
        mask = item.gate_code == GateCode.CREDIT.value and not show_credit
        gates.append(
            {
                "gate_code": item.gate_code,
                "line_id": item.line_id,
                "line_no": line_no.get(item.line_id) if item.line_id is not None else None,
                "level": item.level.value,
                "resolution": item.resolution.value,
                "reason_code": item.reason_code,
                "message_ko": item.message_ko,
                "basis": {} if mask else dict(item.basis),
                "detail": dict(item.detail) if show_detail else {},
                "basis_hash": "" if mask else item.basis_hash,
                "override_roles": [role.value for role in item.override_roles],
                "settlement": state.value,
                "can_override": (
                    open_for_override
                    and state is Settlement.UNRESOLVED
                    and gates_service.is_overridable(item)
                    and gates_service.authorized_role(item.gate_code, roles) is not None
                    and gates_service.regrant_allowed(
                        (item.gate_code, item.line_id, item.basis_hash) in revoked, roles
                    )
                ),
                "override": (
                    {
                        "id": override_row.id,
                        "reason": override_row.reason if show_detail else None,
                        "authorized_role": override_row.authorized_role,
                        "granted_by_id": override_row.granted_by_id,
                        "created_at": override_row.created_at,
                    }
                    if override_row is not None
                    else None
                ),
            }
        )
    effective = {key: get_policy(session, key) for key in bundle.policy_sources}
    return {
        "subject_type": SUBJECT_SALES_ORDER,
        "subject_id": so.id,
        "status": so.status,
        "input_digest": bundle.input_digest,
        "authoritative": bundle.subject.authoritative,
        "evaluated_at": utcnow(),
        "note": NOTE_READINESS,
        "readiness_scope_note": SCOPE_NOTE,
        "clearance": {
            "cleared": bundle.clearance.cleared,
            "needs_approval": bundle.clearance.needs_approval,
            "unresolved_count": len(bundle.clearance.unresolved),
        },
        "approval": {"available": bundle.approval_available, "approval_id": bundle.approval_id},
        "policies": {
            key: {"value": policy.value, "source": policy.source}
            for key, policy in effective.items()
        },
        "gates": gates,
    }


def get_gates(*, so_id: int, roles: frozenset[RoleCode]) -> dict[str, Any]:
    """`GET /sales-orders/{id}/gates` — 참고값(잠금 없음·저장 없음)."""
    with unit_of_work() as uow:
        session = uow.session
        so = _require_so(session, so_id)
        bundle = evaluate_sales_order(session, so.id, authoritative=False)
        return gate_report_body(session, so, bundle, roles=roles)


# ── override 부여·철회 ───────────────────────────────────────────────────────


def override_body(row: GateOverride) -> dict[str, Any]:
    return {
        "id": row.id,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "line_id": row.line_id,
        "gate_code": row.gate_code,
        "action": row.action,
        "result_at_grant": row.result_at_grant,
        "reason": row.reason,
        "basis_hash": row.basis_hash,
        "granted_by_id": row.granted_by_id,
        "authorized_role": row.authorized_role,
        "created_at": row.created_at,
    }


def _lock_open_so(session: Session, so_id: int) -> SalesOrder:
    """SO 행 잠금(전역 잠금 순서 (5)) + 접수(RECEIVED) 상태 확인 — 확정·취소·보류 SO는 override를 받지 않는다(409)."""
    _require_so(session, so_id)  # 없는 SO·삭제 SO는 잠금 전에 404
    so = lock_document(session, SalesOrder, so_id)
    if so.status != OVERRIDE_OPEN_STATUS:
        raise AppError(
            ErrorCode.GATES_OVERRIDE_ORDER_NOT_OPEN,
            detail={"status": so.status},
            log_context={"sales_order_id": so_id, "op": "gate_override"},
        )
    return so


def _json_body(row: GateOverride) -> dict[str, Any]:
    """멱등 응답 본문 — JSON 직렬화 가능한 값만(시각은 ISO 문자열)."""
    body = override_body(row)
    created: datetime = body["created_at"]
    body["created_at"] = created.isoformat()
    return body


def grant_gate_override(
    *, actor: AuthenticatedUser, idempotency_key: str, so_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """override 부여 — 201. `payload`는 `GateOverrideRequest.model_dump(mode="json")`."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=OVERRIDE_ENDPOINT,
            key=idempotency_key,
            request_body={"so_id": so_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        gate_code: str = payload["gate_code"]
        line_id: int | None = payload.get("line_id")
        # 대상 게이트·역할은 잠금·평가 전에 거른다(대기 없이 거부)
        gates_service.require_override_authority(gate_code, actor.roles)
        _lock_open_so(session, so_id)
        bundle = evaluate_sales_order(session, so_id, only=[GateCode(gate_code)])
        if line_id is not None and line_id not in {line.line_id for line in bundle.subject.lines}:
            raise NotFoundError(log_context={"sales_order_id": so_id, "line_id": line_id})
        row = gates_service.grant_override(
            session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so_id,
            outcomes=bundle.outcomes,
            gate_code=gate_code,
            line_id=line_id,
            basis_hash=payload["basis_hash"],
            reason=payload["reason"],
            actor_user_id=actor.id,
            actor_roles=actor.roles,
        )
        body = _json_body(row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def revoke_gate_override(
    *, actor: AuthenticatedUser, idempotency_key: str, so_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """override 철회 — 201(REVOKE 행 추가). 부여자 본인 또는 ADMIN만. 현재 판정과 무관하게 해시로 부여 행을 찾는다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REVOKE_ENDPOINT,
            key=idempotency_key,
            request_body={"so_id": so_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        _lock_open_so(session, so_id)
        line_id: int | None = payload.get("line_id")
        row = gates_service.revoke_override(
            session,
            subject_type=SUBJECT_SALES_ORDER,
            subject_id=so_id,
            gate_code=payload["gate_code"],
            line_id=line_id,
            basis_hash=payload["basis_hash"],
            reason=payload["reason"],
            actor_user_id=actor.id,
            actor_roles=actor.roles,
        )
        body = _json_body(row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


__all__ = [
    "GateBundle",
    "evaluate_sales_order",
    "gate_report_body",
    "get_gates",
    "grant_gate_override",
    "revoke_gate_override",
]
