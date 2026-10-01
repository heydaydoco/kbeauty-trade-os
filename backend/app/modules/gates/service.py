"""게이트 서비스 — 평가 틀·확정 가능 판정(`clearance`)·override 부여/철회·증거 스냅샷 (S3-1 PR-11a / design-D D3·D4 / ADR-0069).

■ **통과 판정은 이 파일의 `settle`/`clearance` 하나**다: PASS·WARN=통과 / BLOCK·UNKNOWN + `NONE`=미해소 / + `OVERRIDE`=`(gate_code, line_id, basis_hash)`가 **유효한 GRANT일 때만** 해소 /
  + `APPROVAL`=`approval_available`일 때만 해소. `GateLevel.PASS`·`'PASS'` 같은 리터럴로 통과를 판정하는 코드가 이 파일 밖에 있으면 아키텍처 스캔이 실패한다(복제 금지).
■ `evaluate_all`은 **쓰기 없이** 등록부의 평가기를 순회한다. **미등록 평가기·평가 중 예외는 UNKNOWN**(`EVALUATOR_NOT_REGISTERED`/`EVALUATION_ERROR`, 해소 NONE)이고 예외는 SAVEPOINT 안에서 잡아 세션
  오염을 막는다. **잠금 대기 초과(55P03)·교착(40P01)은 삼키지 않고 전파**한다(409 `LOCK_BUSY` — 평가 불능으로 둔갑시키지 않는다). 로그·응답에는 예외 클래스명만 싣는다(내부 메시지 금지).
■ override는 **별도 액션**이다(확정 요청에 첨부하는 방식 없음): 사유 5~500자·역할 제한(`OVERRIDE_ROLES` — 서비스 검증, 라우터는 상한만)·판정 해시 결속(낡으면 409)·불변 기록(`gate_overrides`).
  유효성 = `(subject, gate, line, basis_hash)`별 **최신 행이 GRANT**. 철회는 REVOKE 행 추가(삭제 없음). 부여·철회는 같은 트랜잭션에서 audit(id·게이트·역할만)와 outbox `gates.override.*`를 남긴다.
■ 이 모듈은 도메인 모듈을 임포트하지 않는다 — 평가 대상(SO 등)은 호출자가 잠그고 `GateSubject`로 어댑트해 `outcomes`를 넘긴다.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.errors.handlers import LOCK_BUSY_SQLSTATES
from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.gates.models import (
    ACTION_GRANT,
    ACTION_REVOKE,
    REASON_MAX,
    REASON_MIN,
    GateEvaluation,
    GateOverride,
)
from app.modules.gates.policy import (
    FRAMEWORK_RESULT,
    GATE_ORDER,
    GATE_SPECS,
    OVERRIDABLE_GATES,
    OVERRIDE_ROLES,
    outcome,
)
from app.modules.gates.registry import DEFAULT_REGISTRY, EvaluatorRegistry
from app.modules.gates.types import (
    GateCode,
    GateLevel,
    GateOutcome,
    GatePhase,
    GateResolution,
    GateSubject,
)
from app.modules.identity.models import RoleCode
from app.modules.outbox import service as outbox

logger = get_logger(__name__)

OverrideKey = tuple[str, int | None, str]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# ── 평가 ─────────────────────────────────────────────────────────────────────


def _is_declared(
    code: GateCode, level: GateLevel, resolution: GateResolution, phase: GatePhase
) -> bool:
    """(결과, 해소)가 이 게이트의 이 평가 시점에 명세된 조합인가 — 틀 결과(UNKNOWN/NONE)는 항상 허용."""
    if (level, resolution) == (FRAMEWORK_RESULT.level, FRAMEWORK_RESULT.resolution):
        return True
    return any(
        r.level is level and r.resolution is resolution and phase in r.phases
        for r in GATE_SPECS[code].results
    )


def _unknown(
    code: GateCode, reason_code: str, message_ko: str, error_class: str | None = None
) -> GateOutcome:
    basis = {"error_class": error_class} if error_class else {}
    return outcome(code, GateLevel.UNKNOWN, GateResolution.NONE, reason_code, message_ko, basis)


def _checked(
    code: GateCode, phase: GatePhase, outcomes: list[GateOutcome]
) -> list[GateOutcome] | None:
    """평가기 반환값의 계약 검사 — 비었거나 남의 게이트·명세 밖 조합·(게이트,라인) 중복이면 None."""
    if not outcomes:
        return None
    seen: set[tuple[str, int | None]] = set()
    for item in outcomes:
        if item.gate_code != code.value or item.key in seen:
            return None
        if not _is_declared(code, item.level, item.resolution, phase):
            return None
        seen.add(item.key)
    return outcomes


def _evaluate_one(
    session: Session,
    code: GateCode,
    subject: GateSubject,
    phase: GatePhase,
    registry: EvaluatorRegistry,
) -> list[GateOutcome]:
    evaluator = registry.get(code.value)
    if evaluator is None:
        logger.warning("gate_evaluator_not_registered", gate_code=code.value)
        return [
            _unknown(
                code,
                "EVALUATOR_NOT_REGISTERED",
                "이 게이트의 평가기가 등록되지 않아 판정할 수 없습니다. 관리자에게 문의해 주세요.",
            )
        ]
    try:
        # SAVEPOINT — 평가기의 DB 오류가 호출 트랜잭션을 aborted 상태로 만들지 않는다(이후 쿼리·증적 기록이 계속 가능).
        with session.begin_nested():
            produced = evaluator(session, subject, phase)
        checked = _checked(code, phase, list(produced))
        if checked is None:
            raise ValueError("gate evaluator contract violation")
        return checked
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in LOCK_BUSY_SQLSTATES:
            raise  # 잠금 경합은 평가 불능이 아니다 — 409 LOCK_BUSY로 전파
        error_class = type(exc).__name__
    except Exception as exc:  # 평가기 결함은 통과가 아니다(fail-closed) — 클래스명만 남긴다
        error_class = type(exc).__name__
    logger.warning("gate_evaluation_failed", gate_code=code.value, error_class=error_class)
    return [
        _unknown(
            code,
            "EVALUATION_ERROR",
            "판정 중 오류가 발생해 평가할 수 없습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요.",
            error_class,
        )
    ]


def evaluate_all(
    session: Session,
    subject: GateSubject,
    phase: GatePhase,
    *,
    registry: EvaluatorRegistry | None = None,
    only: Iterable[GateCode | str] | None = None,
) -> list[GateOutcome]:
    """이 시점에 해당하는 게이트 전건(또는 `only`)을 평가한다 — **쓰기 없음**. 표시 순서 = `GATE_ORDER`."""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    wanted = None if only is None else {GateCode(c).value for c in only}
    results: list[GateOutcome] = []
    for code in GATE_ORDER:
        if phase not in GATE_SPECS[code].phases:
            continue
        if wanted is not None and code.value not in wanted:
            continue
        results.extend(_evaluate_one(session, code, subject, phase, reg))
    return results


# ── 확정 가능 판정 (단일 정의) ───────────────────────────────────────────────


class Settlement(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"  # 통과·경고 — 해소가 필요 없다
    OVERRIDDEN = "OVERRIDDEN"  # 유효한 override 부여로 해소
    APPROVED = "APPROVED"  # 소비 가능한 승인으로 해소
    UNRESOLVED = "UNRESOLVED"  # 미해소 — 확정 불가


@dataclass(frozen=True, slots=True)
class Clearance:
    cleared: bool
    unresolved: tuple[GateOutcome, ...]
    #: 해소에 쓰인 override 행 id(확정 증거 스냅샷에 남긴다).
    used_overrides: tuple[int, ...]
    #: 미해소 중 승인으로 해소할 수 있는 것이 있다(여신 초과 — 승인 요청 안내).
    needs_approval: bool
    #: 결과별 정산 — (결과, 해소 상태), 입력 순서 그대로.
    settlements: tuple[tuple[GateOutcome, Settlement], ...]


def settle(
    item: GateOutcome,
    effective_overrides: Mapping[OverrideKey, int],
    approval_available: bool,
) -> tuple[Settlement, int | None]:
    """결과 1건의 해소 상태 — (상태, 사용한 override id). 모르는 값은 미해소(fail-closed)."""
    if item.level in (GateLevel.PASS, GateLevel.WARN):
        return Settlement.NOT_REQUIRED, None
    if item.level in (GateLevel.BLOCK, GateLevel.UNKNOWN):
        if item.resolution is GateResolution.OVERRIDE:
            override_id = effective_overrides.get((item.gate_code, item.line_id, item.basis_hash))
            if item.gate_code in OVERRIDABLE_GATES and override_id is not None:
                return Settlement.OVERRIDDEN, override_id
        elif item.resolution is GateResolution.APPROVAL and approval_available:
            return Settlement.APPROVED, None
    return Settlement.UNRESOLVED, None


def clearance(
    outcomes: Collection[GateOutcome],
    effective_overrides: Mapping[OverrideKey, int],
    approval_available: bool,
) -> Clearance:
    """**"확정 가능" 판정의 유일한 함수** — 결과가 없으면(아무것도 평가하지 않았으면) 통과가 아니다."""
    settlements: list[tuple[GateOutcome, Settlement]] = []
    unresolved: list[GateOutcome] = []
    used: list[int] = []
    for item in outcomes:
        state, override_id = settle(item, effective_overrides, approval_available)
        settlements.append((item, state))
        if state is Settlement.UNRESOLVED:
            unresolved.append(item)
        if override_id is not None:
            used.append(override_id)
    return Clearance(
        cleared=bool(settlements) and not unresolved,
        unresolved=tuple(unresolved),
        used_overrides=tuple(sorted(set(used))),
        needs_approval=any(i.resolution is GateResolution.APPROVAL for i in unresolved),
        settlements=tuple(settlements),
    )


def is_overridable(item: GateOutcome) -> bool:
    """override를 부여할 수 있는 결과인가 — 게이트가 override 가능 4종이고 BLOCK·UNKNOWN + OVERRIDE 해소일 때만."""
    return (
        item.gate_code in OVERRIDABLE_GATES
        and item.level in (GateLevel.BLOCK, GateLevel.UNKNOWN)
        and item.resolution is GateResolution.OVERRIDE
    )


def passed_gates(outcomes: Iterable[GateOutcome]) -> list[str]:
    """결과가 전부 PASS인 게이트 코드(표시 순서)."""
    by_gate: dict[str, list[GateOutcome]] = {}
    for item in outcomes:
        by_gate.setdefault(item.gate_code, []).append(item)
    return [
        code.value
        for code in GATE_ORDER
        if code.value in by_gate and all(i.level is GateLevel.PASS for i in by_gate[code.value])
    ]


# ── override 조회·권한 ───────────────────────────────────────────────────────


def effective_overrides(
    session: Session, subject_type: str, subject_id: int
) -> dict[OverrideKey, int]:
    """유효한 override — `(gate_code, line_id, basis_hash)`별 최신 행(max id)이 GRANT인 것 → 그 행 id."""
    rows = session.execute(
        select(GateOverride)
        .where(GateOverride.subject_type == subject_type, GateOverride.subject_id == subject_id)
        .distinct(GateOverride.gate_code, GateOverride.line_id, GateOverride.basis_hash)
        .order_by(
            GateOverride.gate_code,
            GateOverride.line_id,
            GateOverride.basis_hash,
            GateOverride.id.desc(),
        )
    ).scalars()
    return {
        (row.gate_code, row.line_id, row.basis_hash): row.id
        for row in rows
        if row.action == ACTION_GRANT
    }


def latest_override(
    session: Session,
    subject_type: str,
    subject_id: int,
    gate_code: str,
    line_id: int | None,
    basis_hash: str,
) -> GateOverride | None:
    return session.execute(
        select(GateOverride)
        .where(
            GateOverride.subject_type == subject_type,
            GateOverride.subject_id == subject_id,
            GateOverride.gate_code == gate_code,
            GateOverride.line_id.is_(None) if line_id is None else GateOverride.line_id == line_id,
            GateOverride.basis_hash == basis_hash,
        )
        .order_by(GateOverride.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def authorized_role(gate_code: str, roles: Collection[RoleCode]) -> RoleCode | None:
    """행위자가 보유한 역할 중 이 게이트의 override를 허용하는 역할(명세 순서) — 없으면 None."""
    try:
        allowed = OVERRIDE_ROLES.get(GateCode(gate_code), ())
    except ValueError:
        return None
    return next((role for role in allowed if role in roles), None)


def require_override_authority(gate_code: str, roles: Collection[RoleCode]) -> RoleCode:
    """override 대상 게이트이고 행위자가 허용 역할을 가졌는지 — 아니면 422(대상 아님)/403(권한 없음)."""
    if gate_code not in OVERRIDABLE_GATES:
        raise AppError(
            ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE, log_context={"gate_code": gate_code}
        )
    role = authorized_role(gate_code, roles)
    if role is None:
        raise AppError(ErrorCode.GATES_OVERRIDE_NOT_ALLOWED, log_context={"gate_code": gate_code})
    return role


def clean_reason(reason: str) -> str:
    """사유 검증 — 제어문자 불가, 앞뒤 공백을 자른 값이 5~500자(스키마와 DB CHECK의 서비스 계층 백스톱)."""
    if _CONTROL_CHARS.search(reason):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"reason": "사유에는 줄바꿈·탭 같은 제어문자를 쓸 수 없습니다."},
        )
    cleaned = reason.strip()
    if not REASON_MIN <= len(cleaned) <= REASON_MAX:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"reason": f"사유는 {REASON_MIN}~{REASON_MAX}자로 입력해 주세요."},
        )
    return cleaned


#: gate_overrides·gate_evaluations의 DB 제약 → 업무 오류. 서비스 선검증이 1차이고 DB 위반은 최후 방어선이다(완결성 테스트가 pg_constraint와 대사).
CONSTRAINT_ERRORS: dict[str, ErrorCode] = {
    "ck_gate_overrides_subject_type_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_gate_code_overridable": ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE,
    "ck_gate_overrides_action_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_result_at_grant_valid": ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE,
    "ck_gate_overrides_reason_length": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_reason_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_basis_hash_format": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_basis_is_object": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_line_id_positive": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_overrides_authorized_role_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "fk_gate_overrides_granted_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    "pk_gate_overrides": ErrorCode.INTERNAL_UNEXPECTED,
    "ck_gate_evaluations_subject_type_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_evaluations_outcome_valid": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_evaluations_input_digest_format": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_gate_evaluations_results_is_object": ErrorCode.VALIDATION_INVALID_FIELD,
    "fk_gate_evaluations_evaluated_by_id_users": ErrorCode.RESOURCE_NOT_FOUND,
    "pk_gate_evaluations": ErrorCode.INTERNAL_UNEXPECTED,
    # 확정 증거 1행 유니크 — PR-12가 확정을 두 번 기록하려는 결함을 번역 없이 드러낸다(500, fail-visible).
    "uq_gate_evaluations_confirmed_once": ErrorCode.INTERNAL_UNEXPECTED,
}


def _flush(session: Session) -> None:
    """INSERT를 확정(flush)하고 번역표에 있는 제약 위반만 업무 오류로 바꾼다 — 번역 불가는 그대로 전파(500)."""
    try:
        session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        code = CONSTRAINT_ERRORS.get(getattr(diag, "constraint_name", None) or "")
        if code is None or code is ErrorCode.INTERNAL_UNEXPECTED:
            raise
        raise AppError(code) from exc


# ── override 부여·철회 ───────────────────────────────────────────────────────


def _record(
    session: Session,
    *,
    action: str,
    subject_type: str,
    subject_id: int,
    line_id: int | None,
    gate_code: str,
    result_at_grant: str,
    reason: str,
    basis: Mapping[str, Any],
    basis_hash: str,
    actor_user_id: int,
    role: RoleCode,
) -> GateOverride:
    row = GateOverride(
        subject_type=subject_type,
        subject_id=subject_id,
        line_id=line_id,
        gate_code=gate_code,
        action=action,
        result_at_grant=result_at_grant,
        reason=reason,
        basis=dict(basis),
        basis_hash=basis_hash,
        granted_by_id=actor_user_id,
        authorized_role=role.value,
    )
    session.add(row)
    _flush(session)
    granted = action == ACTION_GRANT
    audit.record(
        session,
        action=AuditAction.GATE_OVERRIDE_GRANTED if granted else AuditAction.GATE_OVERRIDE_REVOKED,
        actor_user_id=actor_user_id,
        entity_type="gate_overrides",
        entity_id=row.id,
        detail={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "gate_code": gate_code,
            "line_id": line_id,
            "authorized_role": role.value,
        },
    )
    outbox.publish(
        session,
        event_type="gates.override.granted" if granted else "gates.override.revoked",
        aggregate_type="gate_overrides",
        aggregate_id=row.id,
        payload={
            "override_id": row.id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "gate_code": gate_code,
            "line_id": line_id,
        },
    )
    return row


def grant_override(
    session: Session,
    *,
    subject_type: str,
    subject_id: int,
    outcomes: Collection[GateOutcome],
    gate_code: str,
    line_id: int | None,
    basis_hash: str,
    reason: str,
    actor_user_id: int,
    actor_roles: Collection[RoleCode],
) -> GateOverride:
    """override 부여(GRANT) — 호출자가 대상을 잠그고 **지금** 평가한 `outcomes`를 넘긴다.

    검사 순서(먼저 걸린 것이 응답): ① 대상 게이트가 override 가능한 4종인가(422) ② 행위자 역할 허용(403) ③ 해당 (게이트, 라인) 결과가 BLOCK·UNKNOWN + OVERRIDE인가(422 —
    PASS·WARN·NONE·APPROVAL은 조용한 통과가 되지 않게 거부) ④ 서버 해시 == 요청 해시인가(409 — 사람이 본 판정이 낡음) ⑤ 사유.
    """
    role = require_override_authority(gate_code, actor_roles)
    target = next((o for o in outcomes if o.gate_code == gate_code and o.line_id == line_id), None)
    if target is None or not is_overridable(target):
        raise AppError(
            ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE,
            log_context={"gate_code": gate_code, "line_id": line_id},
        )
    if target.basis_hash != basis_hash:
        raise AppError(
            ErrorCode.GATES_OVERRIDE_STALE, log_context={"gate_code": gate_code, "line_id": line_id}
        )
    cleaned = clean_reason(reason)
    return _record(
        session,
        action=ACTION_GRANT,
        subject_type=subject_type,
        subject_id=subject_id,
        line_id=line_id,
        gate_code=gate_code,
        result_at_grant=target.level.value,
        reason=cleaned,
        basis=target.basis,
        basis_hash=target.basis_hash,
        actor_user_id=actor_user_id,
        role=role,
    )


def revoke_override(
    session: Session,
    *,
    subject_type: str,
    subject_id: int,
    gate_code: str,
    line_id: int | None,
    basis_hash: str,
    reason: str,
    actor_user_id: int,
    actor_roles: Collection[RoleCode],
) -> GateOverride:
    """override 철회(REVOKE 행 추가) — 해당 해시의 최신 행이 GRANT여야 한다(422). **부여자 본인 또는 ADMIN**만(403).

    철회는 현재 판정과 무관하다(판정 입력이 이미 바뀌어 낡은 부여도 정리할 수 있다) — 그래서 재평가하지 않고 해시로 부여 행을 찾는다.
    """
    if gate_code not in OVERRIDABLE_GATES:
        raise AppError(
            ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE, log_context={"gate_code": gate_code}
        )
    latest = latest_override(session, subject_type, subject_id, gate_code, line_id, basis_hash)
    if latest is None or latest.action != ACTION_GRANT:
        raise AppError(
            ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE,
            log_context={"gate_code": gate_code, "line_id": line_id},
        )
    if latest.granted_by_id != actor_user_id and RoleCode.ADMIN not in actor_roles:
        raise AppError(ErrorCode.GATES_OVERRIDE_NOT_ALLOWED, log_context={"gate_code": gate_code})
    role = authorized_role(gate_code, actor_roles) or RoleCode(latest.authorized_role)
    cleaned = clean_reason(reason)
    return _record(
        session,
        action=ACTION_REVOKE,
        subject_type=subject_type,
        subject_id=subject_id,
        line_id=line_id,
        gate_code=gate_code,
        result_at_grant=latest.result_at_grant,
        reason=cleaned,
        basis=latest.basis,
        basis_hash=latest.basis_hash,
        actor_user_id=actor_user_id,
        role=role,
    )


# ── 확정 증거 스냅샷 (PR-12 확정 통로가 쓴다) ───────────────────────────────


def evaluation_results(
    clr: Clearance,
    *,
    policy_sources: Mapping[str, str] | None = None,
    approval_id: int | None = None,
) -> dict[str, Any]:
    """`gate_evaluations.results` — 비PASS 결과(+해소 상태) · passed_gates · 사용한 override id · 승인 ref · 정책 출처.

    판매 단가·수량·준비 상태 요약 같은 `basis`만 싣는다(원가·마진·매입가·예외 문자열 없음).
    """
    outcomes = [item for item, _ in clr.settlements]
    return {
        "gates": [
            {
                "gate_code": item.gate_code,
                "line_id": item.line_id,
                "level": item.level.value,
                "resolution": item.resolution.value,
                "reason_code": item.reason_code,
                "basis_hash": item.basis_hash,
                "basis": dict(item.basis),
                "settlement": state.value,
            }
            for item, state in clr.settlements
            if item.level is not GateLevel.PASS
        ],
        "passed_gates": passed_gates(outcomes),
        "used_override_ids": list(clr.used_overrides),
        "approval_id": approval_id,
        "policy_source": dict(policy_sources or {}),
    }


def record_evaluation(
    session: Session,
    *,
    subject_type: str,
    subject_id: int,
    outcome_kind: str,
    results: Mapping[str, Any],
    input_digest: str,
    actor_user_id: int,
) -> GateEvaluation:
    """확정 시도 1건의 증거 스냅샷을 INSERT한다 — 차단(BLOCKED)·성공(CONFIRMED) 모두. 조회 경로는 부르지 않는다(조회 부작용 금지)."""
    row = GateEvaluation(
        subject_type=subject_type,
        subject_id=subject_id,
        outcome=outcome_kind,
        results=dict(results),
        input_digest=input_digest,
        evaluated_by_id=actor_user_id,
    )
    session.add(row)
    _flush(session)
    return row
