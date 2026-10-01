"""승인 코어 서비스 — 요청·결정·소비·무효·우회 시도 기록 (S3-1 ADR-0060·0061 / design-C C2·C4·C6·C8·X1).

■ **상태를 바꾸는 통로는 `_record_transition` 하나**다 — 상태 UPDATE + `approval_events` INSERT + 아웃박스가 한 함수·한 트랜잭션이다
  (생성도 같은 함수: `NULL→REQUESTED`). 이 파일 밖에서 `approval.status`를 대입하는 코드는 없다(아키텍처 스캔이 고정한다).
■ 사람이 승인 상태를 바꾸는 통로는 `decide_approval` 하나(승인·반려·회수 — 자기 UoW). 시스템 통로 둘은 승인을 **부여하지 않고 좁히기만** 한다:
  `consume_approval`(APPROVED→CONSUMED, 확정 트랜잭션 안) · `void_for_target`(REQUESTED·APPROVED→VOIDED, 대상 편집·취소 트랜잭션 안).
■ **자동 승인 경로 부재**: `decide_approval`·`request_approval`·`consume_approval`·`void_for_target`은 `force`·`override`·`admin` 같은 우회·자동 표식
  파라미터가 없고, `actor`(실 사용자)가 시그니처에 있다. 이벤트 `actor_user_id`는 NOT NULL이다(시스템 행위자 없음).
■ **승인 후 불변**: 결속은 **digest**(대상 판정 입력 내용 — version 아님)로 한다. 결정(APPROVE) 시점과 소비 시점에 digest·통화·상한을 **다시 본다**
  (지연 검증 = fail-closed 안전망). `void_for_target`은 편집·취소 경로의 즉시 청소(결재함 위생) 훅이다 — 둘 다 둔다.
■ **소비는 결과값으로 돌려준다(예외 금지)**: 소비는 확정 UoW에 *합류*해 돌기 때문에 UoW 안에서 raise하면 VOID·감사까지 롤백되어 "재시도해도 같은
  결과"가 사라진다. `ConsumeResult.BLOCKED`면 호출자가 **UoW를 정상 종료(커밋)한 뒤 `raise result.error`** 한다. 같은 이유로 거부된 결정 시도의 감사와
  stale 감지 VOID도 UoW 안에서 기록하고 **UoW 밖에서 raise**한다(함정 ⑨ — 실패도 커밋).
■ 잠금 순서(전역 LOCK_ORDER): (0)멱등 키 → 거래처·대상 전표(TargetSpec이 확정 통로와 같은 순서로 잠금) → (7)approvals. 결정은 무잠금으로 승인 id →
  대상을 알아내고 대상을 먼저 잠근 뒤 approvals를 `FOR UPDATE`한다.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import (
    AppError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.core.logging.redaction import is_sensitive_key
from app.core.time import utcnow
from app.modules.approvals import alerts, lines
from app.modules.approvals.authority import (
    Authority,
    decision_authority,
    eligible_user_ids,
    kst_today,
)
from app.modules.approvals.machine import (
    ACTIVE_STATUSES,
    ALLOWED,
    EVENT_NAME,
    HUMAN,
    REASON_MAX,
    REASON_REQUIRED_TO,
    TYPE_TARGET,
    VERB_TO,
    ApprovalStatus,
    AuthorityKind,
    ConsumeOutcome,
    DecisionVerb,
    VoidReasonCode,
)
from app.modules.approvals.models import Approval, ApprovalEvent
from app.modules.approvals.registry import TargetSnapshot, TargetSpec, get_target_spec
from app.modules.approvals.views import _may_view, approval_body
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser, active_roles_of
from app.modules.outbox import service as outbox

DECISION_ENDPOINT = "POST /api/v1/approvals/{approval_id}/decisions"

#: 표시용 snapshot 상세의 한도(design-C C1) — 스칼라만·키 20개·4KB.
SNAPSHOT_MAX_KEYS = 20
SNAPSHOT_MAX_BYTES = 4096

_ACTIVE_VALUES = tuple(s.value for s in ACTIVE_STATUSES)


# ── 결과 값 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RequestResult:
    approval: Approval
    #: False = 같은 스냅샷의 기존 활성 승인을 그대로 돌려줬다(멱등 재요청 — 알림·이벤트 재발생 없음).
    created: bool


@dataclass(frozen=True, slots=True)
class ApprovalRef:
    """`peek_active_approved`가 돌려주는 읽기 전용 참조 — "지금 소비 가능한 승인"이 있다는 사실뿐이다."""

    id: int
    approval_type: str
    target_id: int
    basis_amount: int
    basis_currency: str
    required_role: str


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """소비 결과 — **예외를 던지지 않는다**. BLOCKED면 호출자가 커밋한 뒤 `error`를 raise한다."""

    outcome: ConsumeOutcome
    approval_id: int | None = None
    error: AppError | None = None


# ── 단일 상태 대입 통로 ────────────────────────────────────────────────────────


def _clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = reason.strip()
    return text or None


def _record_transition(
    session: Session,
    approval: Approval,
    *,
    to: ApprovalStatus,
    actor_user_id: int,
    reason: str | None = None,
    reason_code: VoidReasonCode | None = None,
    on_behalf_of_id: int | None = None,
    delegation_id: int | None = None,
) -> None:
    """**승인 상태를 바꾸는 유일한 함수** — 상태 UPDATE + 이력 INSERT + 아웃박스가 한 트랜잭션이다.

    생성(`approval.id is None`)도 같은 함수다(`NULL→REQUESTED`). 허용 7방향 밖은 409, 반려·회수는 사유 필수(422),
    무효는 사유 코드 필수. 사람 결정 컬럼(decided_*)은 승인·반려일 때만, 소비 컬럼은 소비일 때만 채운다.
    """
    birth = approval.id is None
    from_status: ApprovalStatus | None = None if birth else ApprovalStatus(approval.status)
    if birth:
        if to is not ApprovalStatus.REQUESTED:
            raise ValueError("승인 행은 REQUESTED로만 태어난다.")
    elif (from_status, to) not in ALLOWED:
        raise AppError(
            ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED,
            detail={"current": str(from_status), "attempted": to.value},
        )
    cleaned = _clean_reason(reason)
    if to in REASON_REQUIRED_TO:
        if cleaned is None:
            raise AppError(ErrorCode.APPROVALS_TRANSITION_REASON_REQUIRED)
        if len(cleaned) > REASON_MAX:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"reason": f"사유는 {REASON_MAX}자 이내로 입력해 주세요."},
            )
    if (to is ApprovalStatus.VOIDED) != (reason_code is not None):
        raise ValueError("무효(VOIDED)에는 사유 코드가 필수이고, 그 밖의 전이에는 없다.")

    now = utcnow()
    approval.status = to.value
    approval.updated_by_id = actor_user_id
    if to in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        approval.decided_by_id = actor_user_id
        approval.decided_on_behalf_of_id = on_behalf_of_id
        approval.decided_delegation_id = delegation_id
        approval.decided_at = now
    elif to is ApprovalStatus.CONSUMED:
        approval.consumed_at = now
        approval.consumed_by_id = actor_user_id
    if birth:
        approval.created_by_id = actor_user_id
        session.add(approval)
    session.flush()

    session.add(
        ApprovalEvent(
            approval_id=approval.id,
            from_status=from_status.value if from_status is not None else None,
            to_status=to.value,
            actor_user_id=actor_user_id,
            on_behalf_of_user_id=on_behalf_of_id,
            delegation_id=delegation_id,
            reason=cleaned,
            reason_code=reason_code.value if reason_code is not None else None,
        )
    )
    # 페이로드는 id·유형·대상·from/to·행위자뿐 — 금액·digest·사유를 싣지 않는다(외부 채널로 나갈 수 있다).
    outbox.publish(
        session,
        event_type=f"approvals.approval.{EVENT_NAME[to]}",
        aggregate_type="approvals",
        aggregate_id=approval.id,
        payload={
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "target_type": approval.target_type,
            "target_id": approval.target_id,
            "from_status": from_status.value if from_status is not None else None,
            "to_status": to.value,
            "actor_user_id": actor_user_id,
        },
    )
    session.flush()


def _void(
    session: Session,
    approval: Approval,
    *,
    actor_user_id: int,
    reason_code: VoidReasonCode,
    notify: bool = True,
) -> None:
    _record_transition(
        session,
        approval,
        to=ApprovalStatus.VOIDED,
        actor_user_id=actor_user_id,
        reason_code=reason_code,
    )
    if notify:
        alerts.notify_result(session, approval, outcome="voided", actor_user_id=actor_user_id)


# ── 보조 ─────────────────────────────────────────────────────────────────────


def _validate_detail(detail: dict[str, Any]) -> None:
    """표시용 snapshot 상세 검증 — 스칼라만·키 20개·4KB·민감 키 금지(원가·마진·비밀이 승인 이력에 들어가지 못한다)."""
    if len(detail) > SNAPSHOT_MAX_KEYS:
        raise RuntimeError("승인 snapshot detail의 키가 너무 많습니다.")
    for key, value in detail.items():
        if is_sensitive_key(key):
            raise RuntimeError(f"승인 snapshot detail에 민감 키가 있습니다: {key!r}")
        if not isinstance(value, int | str | bool | None):
            raise RuntimeError(f"승인 snapshot detail은 스칼라만 허용합니다: {key!r}")
    if len(json.dumps(detail, ensure_ascii=False).encode("utf-8")) > SNAPSHOT_MAX_BYTES:
        raise RuntimeError("승인 snapshot detail이 4KB를 넘습니다.")


def _stale_error(reason_code: VoidReasonCode) -> AppError:
    return AppError(ErrorCode.APPROVALS_APPROVAL_STALE, detail={"reason": reason_code.value})


def _stale_reason(snapshot: TargetSnapshot | None, approval: Approval) -> VoidReasonCode | None:
    """승인이 더는 유효하지 않은 이유 — 유효하면 None. **소비·결정 시점 공통 재검증**(digest·통화·상한·게이트 대상)."""
    if snapshot is None or not snapshot.gate_open:
        return VoidReasonCode.TARGET_CANCELLED
    if snapshot.amount <= 0:
        return VoidReasonCode.NOT_REQUIRED
    if snapshot.digest != approval.snapshot_digest or snapshot.currency != approval.basis_currency:
        return VoidReasonCode.TARGET_CHANGED
    if snapshot.amount > approval.basis_amount:
        return VoidReasonCode.CAP_EXCEEDED
    return None


def _active_approval(
    session: Session, approval_type: str, target_id: int, *, lock: bool
) -> Approval | None:
    statement = select(Approval).where(
        Approval.approval_type == approval_type,
        Approval.target_type == TYPE_TARGET[approval_type],
        Approval.target_id == target_id,
        Approval.status.in_(_ACTIVE_VALUES),
    )
    if lock:
        statement = statement.with_for_update()
    statement = statement.execution_options(
        populate_existing=True
    )  # 세션 캐시의 옛 상태를 쓰지 않는다(읽기·잠금 공통)
    return session.execute(statement).scalar_one_or_none()


def _spec_for(approval_type: str) -> TargetSpec:
    if approval_type not in TYPE_TARGET:
        raise RuntimeError(f"알 수 없는 승인 유형입니다: {approval_type!r}")
    return get_target_spec(approval_type)


# ── 요청 ─────────────────────────────────────────────────────────────────────


def request_approval(
    session: Session,
    *,
    approval_type: str,
    target_id: int,
    actor: AuthenticatedUser,
    today: date | None = None,
) -> RequestResult:
    """승인 요청 — **호출 트랜잭션에 합류**한다(대상 전표 엔드포인트가 부른다). HTTP로 노출하지 않는다.

    요청은 SO 확정 시도의 부작용이 아니라 **사람의 명시 동작**이다(승인 폭주·알림 스팸 방지) — 호출부 규약은 ADR-0060.
    순서: 대상 잠금 + 스냅샷 → (활성 승인 있으면 같은 스냅샷은 그대로 반환·다르면 VOID(TARGET_CHANGED)) → 결재선 해석(없으면 422 fail-closed)
    → 승인 행 + `NULL→REQUESTED` 이벤트 → 자격자 공집합 검사(422 — 롤백) → 알림.
    """
    if not any(role is not RoleCode.VIEWER for role in actor.roles):
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "request_approval"})
    spec = _spec_for(approval_type)
    snapshot = spec.snapshot(session, target_id, lock=True)
    if snapshot is None:
        raise NotFoundError(log_context={"approval_type": approval_type, "target_id": target_id})
    if not snapshot.gate_open:
        raise _stale_error(VoidReasonCode.TARGET_CANCELLED)
    if snapshot.amount <= 0:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "approval": "승인이 필요한 금액이 없습니다. 승인을 요청할 필요가 없는 건입니다."
            },
        )
    _validate_detail(snapshot.detail)

    existing = _active_approval(session, approval_type, target_id, lock=True)
    if existing is not None:
        same = (
            existing.snapshot_digest == snapshot.digest
            and existing.basis_currency == snapshot.currency
            # 같은 digest에서 현재 초과분이 승인 상한 이하면 기존 승인을 유지한다(노출이 줄었다고 타인이 멀쩡한 승인을 VOID하지 못하게 —
            # 상한 초과 때만 새 요청). 정확한 재검증은 결정·소비 시점이 한다.
            and snapshot.amount <= existing.basis_amount
        )
        if same:
            return RequestResult(existing, created=False)
        _void(session, existing, actor_user_id=actor.id, reason_code=VoidReasonCode.TARGET_CHANGED)

    line = lines.resolve_line(
        session, approval_type=approval_type, amount=snapshot.amount, currency=snapshot.currency
    )
    if line is None:
        raise AppError(
            ErrorCode.APPROVALS_LINE_NOT_CONFIGURED,
            log_context={"approval_type": approval_type, "currency": snapshot.currency},
        )

    approval = Approval(
        approval_type=approval_type,
        target_type=TYPE_TARGET[approval_type],
        target_id=target_id,
        target_label=snapshot.label[:200],
        requested_by_id=actor.id,
        basis_amount=snapshot.amount,
        basis_currency=snapshot.currency,
        snapshot_digest=snapshot.digest,
        snapshot=dict(snapshot.detail),
        required_role=line.approver_role,
        approval_line_id=line.id,
    )
    try:
        _record_transition(session, approval, to=ApprovalStatus.REQUESTED, actor_user_id=actor.id)
    except IntegrityError as exc:  # 대상 잠금이 직렬화하므로 정상 경로로는 도달하지 않는 안전망
        if "uq_approvals_active_target" not in str(exc.orig or ""):
            raise
        raise AppError(ErrorCode.APPROVALS_APPROVAL_ALREADY_ACTIVE) from exc

    today = today or kst_today()
    if not eligible_user_ids(session, approval, today=today):
        # 호출 트랜잭션이 롤백된다 — 자격자 없는 요청은 만들지 않는다(조용한 정체 금지).
        raise AppError(
            ErrorCode.APPROVALS_APPROVAL_NO_ELIGIBLE_APPROVER,
            log_context={"approval_type": approval_type, "required_role": line.approver_role},
        )
    alerts.notify_requested(session, approval, today=today)
    return RequestResult(approval, created=True)


# ── 결정 ─────────────────────────────────────────────────────────────────────


def _deny(
    session: Session,
    approval: Approval,
    *,
    actor: AuthenticatedUser,
    verb: DecisionVerb,
    code: ErrorCode,
    blocked: str,
) -> AppError:
    """거부된 결정 시도를 audit에 남기고(커밋은 호출부 UoW) 던질 에러를 돌려준다 — 막힌 시도야말로 남아야 하는 기록이다.

    같은 (행위자·승인·동사·사유)의 거부는 **한 건으로 합산**한다(재시도·반복 호출이 audit를 소모하지 못한다). 승인과 무관한(열람 불가) 사용자의
    호출은 여기까지 오지 않고 404로 끝난다 — audit를 소모할 수 있는 것은 그 승인의 당사자(기안자·결재 자격자·ADMIN)뿐이다.
    """
    already = session.execute(
        select(AuditLog.id)
        .where(
            AuditLog.action == AuditAction.APPROVAL_DECISION_DENIED,
            AuditLog.actor_user_id == actor.id,
            AuditLog.entity_type == "approvals",
            AuditLog.entity_id == approval.id,
            AuditLog.detail["verb"].astext == verb.value,
            AuditLog.detail["blocked"].astext == blocked,
        )
        .limit(1)
    ).first()
    if already is None:
        audit.record(
            session,
            action=AuditAction.APPROVAL_DECISION_DENIED,
            actor_user_id=actor.id,
            entity_type="approvals",
            entity_id=approval.id,
            detail={"approval_id": approval.id, "verb": verb.value, "blocked": blocked},
        )
    return AppError(code, log_context={"approval_id": approval.id, "actor_id": actor.id})


def _withdraw_denied(session: Session, approval: Approval, actor: AuthenticatedUser) -> bool:
    """회수 자격 — 기안자 본인 또는 ADMIN(결정 시점에 역할을 다시 읽는다). True면 **거부**."""
    roles = active_roles_of(session, actor.id)
    if roles is None:
        return True
    return not (approval.requested_by_id == actor.id or RoleCode.ADMIN in roles)


def decide_approval(
    *,
    approval_id: int,
    actor: AuthenticatedUser,
    verb: DecisionVerb,
    reason: str | None,
    version: int,
    idempotency_key: str,
) -> tuple[int, dict[str, Any]]:
    """승인·반려·회수의 **유일한 사람 통로**(T1·T2·T3·T7) — 자기 UoW.

    처리 순서(한 트랜잭션): 멱등 claim → 승인 무잠금 조회(404) → 대상 잠금(+스냅샷) → 승인 `FOR UPDATE` → **상태 확인(409)** → version 확인(409)
    → **결재 자격 판정(403 — 거부는 audit 기록 후 커밋·UoW 밖에서 raise)** → 사유 검증(반려·회수 422) → APPROVE는 스냅샷 재검증(불일치면 VOID 커밋 후
    409 STALE) → `_record_transition` → 결과 알림 → 멱등 complete.
    `actor`는 항상 실 사용자다(시스템·None 불가). 우회·자동 표식 파라미터는 없다.
    """
    deferred: AppError | None = None
    result: tuple[int, dict[str, Any]] | None = None
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=DECISION_ENDPOINT,
            key=idempotency_key,
            request_body={
                "approval_id": approval_id,
                "verb": verb.value,
                "reason": reason,
                "version": version,
            },
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        approval, deferred = _decide(
            session, approval_id=approval_id, actor=actor, verb=verb, reason=reason, version=version
        )
        if deferred is None:
            assert approval is not None and claim.record is not None
            body = approval_body(session, approval, actor=actor)
            idempotency.complete(session, claim.record, status_code=200, body=body)
            result = (200, body)
    if deferred is not None:
        raise deferred  # UoW가 커밋된 뒤 — 거부 audit·stale VOID가 살아남는다(실패도 커밋)
    assert result is not None
    return result


def _check_authority(
    session: Session, approval: Approval, actor: AuthenticatedUser, verb: DecisionVerb
) -> tuple[AppError | None, Authority | None]:
    """결정·회수 자격 판정 — `(거부 에러, 대결 정보)`. 잠금 전(무잠금 조회)과 잠금 후(재확인) **두 번** 같은 함수로 부른다."""
    if verb is DecisionVerb.WITHDRAW:
        if _withdraw_denied(session, approval, actor):
            return (
                _deny(
                    session,
                    approval,
                    actor=actor,
                    verb=verb,
                    code=ErrorCode.APPROVALS_DECISION_NOT_APPROVER,
                    blocked="WITHDRAW_NOT_ALLOWED",
                ),
                None,
            )
        return None, None
    authority = decision_authority(session, actor_id=actor.id, approval=approval)
    if authority.kind is AuthorityKind.DENIED_SELF:
        return (
            _deny(
                session,
                approval,
                actor=actor,
                verb=verb,
                code=ErrorCode.APPROVALS_DECISION_SELF_APPROVAL,
                blocked="SELF_APPROVAL",
            ),
            None,
        )
    if not authority.can_decide:
        return (
            _deny(
                session,
                approval,
                actor=actor,
                verb=verb,
                code=ErrorCode.APPROVALS_DECISION_NOT_APPROVER,
                blocked="NOT_APPROVER",
            ),
            None,
        )
    return None, authority


def _decide(
    session: Session,
    *,
    approval_id: int,
    actor: AuthenticatedUser,
    verb: DecisionVerb,
    reason: str | None,
    version: int,
) -> tuple[Approval | None, AppError | None]:
    peek = session.get(Approval, approval_id)  # 무잠금 조회 — 대상 id를 얻는 용도
    if peek is None:
        raise NotFoundError(log_context={"approval_id": approval_id})
    # ★ 권한을 **잠금·평가보다 먼저** 무잠금으로 판정한다 — 무권한 호출이 거래처·SO·approvals 잠금을 잡거나 노출을 재계산하게 하지 않는다(잠금 경합·DoS 표면 제거).
    #   그 승인과 무관한(열람 불가) 사용자에게는 **존재 여부도 밝히지 않는다**(없는 id와 같은 404 — 상태·version 정보 비노출, IDOR 규칙 `_may_view` 일치).
    #   잠금 뒤에는 TOCTOU 방어로 DB에서 한 번 더 판정한다(아래).
    roles = active_roles_of(session, actor.id)
    if roles is None or not _may_view(session, peek, actor, roles):
        raise NotFoundError(log_context={"approval_id": approval_id})
    early_denial, _ = _check_authority(session, peek, actor, verb)
    if early_denial is not None:
        return None, early_denial
    approval_type, target_id = peek.approval_type, peek.target_id
    spec = _spec_for(approval_type)

    # (1) 대상 → (2) 대상이 정한 직렬화 잠금 — 확정 통로와 같은 순서(교착 방지). 반려·회수는 스냅샷 평가 실패(평가 불능)가 와도 막지 않는다.
    snapshot: TargetSnapshot | None = None
    if verb is DecisionVerb.APPROVE:
        snapshot = spec.snapshot(
            session, target_id, lock=True
        )  # 평가 불능 AppError는 그대로 전파(fail-closed)
    else:
        with contextlib.suppress(AppError):
            spec.snapshot(session, target_id, lock=True)

    # (3) approvals 행 잠금 + 재조회(잠금 뒤 재확인 — 확인→기록 창 방어)
    approval = session.execute(
        select(Approval)
        .where(Approval.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    # 잠금 뒤 자격 재확인(TOCTOU) — **상태·version 409보다 먼저**라 자격을 잃은 사용자에게 현재 상태가 새지 않는다.
    denial, authority = _check_authority(session, approval, actor, verb)
    if denial is not None:
        return None, denial
    on_behalf_of = authority.on_behalf_of_id if authority is not None else None
    delegation_id = authority.delegation_id if authority is not None else None

    to = VERB_TO[verb]
    current = ApprovalStatus(approval.status)
    if (current, to) not in HUMAN:
        raise AppError(
            ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED,
            detail={"current": current.value, "attempted": to.value},
        )
    if approval.version != version:
        raise VersionConflictError(log_context={"approval_id": approval_id})

    if to in REASON_REQUIRED_TO and _clean_reason(reason) is None:
        raise AppError(ErrorCode.APPROVALS_TRANSITION_REASON_REQUIRED)

    if verb is DecisionVerb.APPROVE:
        problem = _stale_reason(snapshot, approval)
        if problem is not None:
            _void(session, approval, actor_user_id=actor.id, reason_code=problem)
            return None, _stale_error(problem)

    _record_transition(
        session,
        approval,
        to=to,
        actor_user_id=actor.id,
        reason=reason,
        on_behalf_of_id=on_behalf_of,
        delegation_id=delegation_id,
    )
    alerts.notify_result(session, approval, outcome=EVENT_NAME[to], actor_user_id=actor.id)
    if delegation_id is not None and on_behalf_of is not None:
        alerts.notify_delegated(session, approval, delegator_id=on_behalf_of)
    return approval, None


# ── 소비·무효·참조 (도메인 통로 — 확정·편집·취소 트랜잭션에 합류) ────────────────────


def note_bypass_attempt(
    session: Session,
    *,
    actor_user_id: int,
    approval_type: str,
    target_id: int,
) -> None:
    """승인 필요·승인 없음 상태의 확정 시도(우회 시도)를 audit에 남긴다 — ADMIN 포함. 커밋은 호출부 몫(BLOCKED 기록과 같은 커밋)."""
    active = _active_approval(session, approval_type, target_id, lock=False)
    audit.record(
        session,
        action=AuditAction.APPROVAL_BYPASS_BLOCKED,
        actor_user_id=actor_user_id,
        entity_type="approvals",
        entity_id=active.id if active is not None else None,
        detail={
            "approval_type": approval_type,
            "target_type": TYPE_TARGET[approval_type],
            "target_id": target_id,
            "pending_request": active is not None
            and active.status == ApprovalStatus.REQUESTED.value,
        },
    )


def peek_active_approved(
    session: Session, *, approval_type: str, target_id: int
) -> ApprovalRef | None:
    """**읽기 전용** — 지금 바로 소비 가능한 승인이 있으면 참조를 돌려준다(어떤 쓰기·VOID도 없음).

    조건: APPROVED ∧ digest·통화 일치 ∧ 0 < 현재 금액 ≤ 승인 상한 ∧ 대상이 게이트 대상. 권위 있는 재검증·소비는 `consume_approval`이 한다
    (clearance가 미해소 시도에서 승인을 소비하지 않으려고 쓴다 — 승인은 해소가 확정된 뒤에만 소비된다).
    """
    spec = _spec_for(approval_type)
    snapshot = spec.snapshot(session, target_id, lock=False)
    active = _active_approval(session, approval_type, target_id, lock=False)
    if (
        active is None
        or active.status != ApprovalStatus.APPROVED.value
        or _stale_reason(snapshot, active) is not None
    ):
        return None
    return ApprovalRef(
        id=active.id,
        approval_type=active.approval_type,
        target_id=active.target_id,
        basis_amount=active.basis_amount,
        basis_currency=active.basis_currency,
        required_role=active.required_role,
    )


def peek_active_approval(
    session: Session, *, approval_type: str, target_id: int
) -> tuple[int, str] | None:
    """**읽기 전용** — 대상의 활성(요청됨·승인됨) 승인의 `(id, 상태)`. 없으면 None. 확정 거부 응답이 "승인 대기 중(요청 #n)"을 안내하는 용도다(판정에 쓰지 않는다)."""
    if approval_type not in TYPE_TARGET:
        raise RuntimeError(f"알 수 없는 승인 유형입니다: {approval_type!r}")
    active = _active_approval(session, approval_type, target_id, lock=False)
    return (active.id, active.status) if active is not None else None


def consume_approval(
    session: Session,
    *,
    approval_type: str,
    target_id: int,
    actor_user_id: int,
) -> ConsumeResult:
    """승인 1회 소비 — **확정 트랜잭션 안에서**(대상 행 잠금 후) 호출한다. **예외를 던지지 않고 결과를 돌려준다**(모듈 독스트링).

    1. 대상 스냅샷(lock) — 없거나 게이트 대상이 아니면 활성 승인을 VOID(TARGET_CANCELLED) → BLOCKED(STALE). 평가 불능은 spec의 AppError가 전파된다(통과 취급 없음).
    2. 현재 금액 ≤ 0 → 활성 승인이 있으면 VOID(NOT_REQUIRED) → NOT_REQUIRED(확정 진행 가능, 승인 불필요).
    3. 활성 APPROVED가 없으면 우회 시도를 audit에 남기고 BLOCKED(REQUIRED — REQUESTED 대기 여부 안내).
    4. digest·통화 불일치 → VOID(TARGET_CHANGED) / 현재 금액 > 승인 상한 → VOID(CAP_EXCEEDED) → BLOCKED(STALE).
    5. 통과 → APPROVED→CONSUMED. **ADMIN 포함 예외 없음** — `force`·`admin` 파라미터가 존재하지 않는다.
    소비된 승인은 CONSUMED 종결이고 활성 유니크에서 빠지며 target_id가 고정이라 다른 대상에 재사용될 수 없다.
    """
    spec = _spec_for(approval_type)
    snapshot = spec.snapshot(session, target_id, lock=True)
    active = _active_approval(session, approval_type, target_id, lock=True)

    if snapshot is None or not snapshot.gate_open:
        if active is not None:
            _void(
                session,
                active,
                actor_user_id=actor_user_id,
                reason_code=VoidReasonCode.TARGET_CANCELLED,
            )
        return ConsumeResult(
            ConsumeOutcome.BLOCKED,
            error=_stale_error(VoidReasonCode.TARGET_CANCELLED),
        )

    if snapshot.amount <= 0:
        if active is not None:
            _void(
                session,
                active,
                actor_user_id=actor_user_id,
                reason_code=VoidReasonCode.NOT_REQUIRED,
            )
        return ConsumeResult(ConsumeOutcome.NOT_REQUIRED)

    if active is None or active.status != ApprovalStatus.APPROVED.value:
        note_bypass_attempt(
            session,
            actor_user_id=actor_user_id,
            approval_type=approval_type,
            target_id=target_id,
        )
        return ConsumeResult(
            ConsumeOutcome.BLOCKED,
            approval_id=active.id if active is not None else None,
            error=AppError(
                ErrorCode.APPROVALS_APPROVAL_REQUIRED,
                detail={"pending_request": active is not None},
            ),
        )

    problem = _stale_reason(snapshot, active)
    if problem is not None:
        _void(session, active, actor_user_id=actor_user_id, reason_code=problem)
        return ConsumeResult(
            ConsumeOutcome.BLOCKED, approval_id=active.id, error=_stale_error(problem)
        )

    _record_transition(session, active, to=ApprovalStatus.CONSUMED, actor_user_id=actor_user_id)
    return ConsumeResult(ConsumeOutcome.CONSUMED, approval_id=active.id)


def void_for_target(
    session: Session,
    *,
    approval_type: str,
    target_id: int,
    actor_user_id: int,
    reason_code: VoidReasonCode,
) -> int:
    """대상 수정(게이트 입력 필드)·취소 트랜잭션이 **같은 트랜잭션에서** 부르는 훅 — 활성(요청됨·승인됨) 승인을 VOIDED로. 없으면 무동작(0).

    훅이 빠진 경로(임포트·벌크)가 있어도 지연 검증(결정·소비 시점 digest·통화·상한 재검증)이 백스톱이다 — eager(결재함 위생)+lazy(안전망) 둘 다 둔다.
    """
    if (
        approval_type not in TYPE_TARGET
    ):  # 알 수 없는 유형은 거부 — 단 TargetSpec 등록은 요구하지 않는다(무효화는 승인을 좁히기만 하고 대상 스냅샷을 쓰지 않는다)
        raise RuntimeError(f"알 수 없는 승인 유형입니다: {approval_type!r}")
    active = _active_approval(session, approval_type, target_id, lock=True)
    if active is None:
        return 0
    _void(session, active, actor_user_id=actor_user_id, reason_code=reason_code)
    return 1
