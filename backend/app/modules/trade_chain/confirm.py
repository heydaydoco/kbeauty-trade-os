"""수주 확정 — `confirm_sales_order` (S3-1 PR-12a / design-integrated §2.10 / design-D D5 · design-E E3·E4 / ADR-0070).

**SO가 RECEIVED→CONFIRMED가 되는 유일한 통로**다(`record_transition`의 동결 액션 엣지 호출처 = 이 함수). 사람 1클릭(`POST /sales-orders/{id}/confirm`) + `Idempotency-Key` 필수이고,
호출처는 라우터 1곳뿐이다(자동 확정 경로 부재 — `test_no_auto_confirm_code_path_exists`). 시그니처·요청 스키마에 `force`·`skip`·`bypass`·`override` 인자가 없고 **ADMIN 분기도 없다**
(승인·게이트 통과는 역할이 아니라 증거로 판정한다).

■ 한 트랜잭션의 순서(전역 `LOCK_ORDER`: 멱등 → 거래처 → QT → PI → SO → approvals):
  ① 역할 사전 검증(잠금 이전) → ② 멱등 claim(재수신 → 최초 결과 재생) → ③ 무잠금으로 SO의 거래처 id → **거래처 잠금(`lock_buyer_for_credit` — 여신 직렬화)** → 사슬 잠금(QT→PI→SO, version 대조 409)
  → 잠금 뒤 거래처 재확인 → ④ 상태(RECEIVED만)·입력 완결성(결제조건·Incoterms·환율·바이어 표기·라인 ≥ 1) 검증 — **동결 전 상태 검증**
  → ⑤ `gate_flow.evaluate_sales_order(authoritative=True)` — 게이트 7종을 **쓰기 없이** 잠금 하에서 재평가하고 `gates.service.clearance`(통과 판정의 유일한 정의)로 정산
  → ⑥-a **미해소면**: (승인 필요·승인 없음이면 우회 시도 audit) → `gate_evaluations(BLOCKED)` 증거 INSERT → **UoW를 정상 종료(커밋)한 뒤** 409 `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`
        (예외로 롤백되면 증거·우회 시도 audit이 사라진다 — PR-11a 인계 ⑦)
  → ⑥-b **해소면**: `consume_approval`(**항상 호출** — 권위 있는 재검증·1회 소비·digest 결속·NOT_REQUIRED 정리)
        · BLOCKED(소비 시점 경합 — STALE·REQUIRED)면 BLOCKED 증거를 남기고 커밋한 뒤 그 에러를 raise
  → ⑦ SO 증적 3열(여신·PI 판정·승인 id) + `record_transition(RECEIVED→CONFIRMED, approval_id)`(확정 시각·이력·outbox를 같은 flush에) → `gate_evaluations(CONFIRMED)` → 부모 QT 수렴(CONVERTED)
        → `AllocationPort.on_confirmed`(예외면 전체 롤백 — 소비도 되돌아간다) → ⑧ `idempotency.complete(200)`.
■ **미해소 시도에서는 승인을 소비하지 않는다**(소비는 해소가 확정된 뒤 마지막 부작용) · **거부 응답은 멱등 결과로 기록하지 않는다**(성공만 `complete` — 승인을 받은 뒤 같은 키로 재확정할 수 있어야 한다).
  커밋된 BLOCKED 경로의 claim 행은 결과 없는 선점으로 남고, 같은 키 재시도가 이어받는다(`idempotency.claim`).
■ SO 증적 3열 — DB CHECK는 **부분 채움·허용 값 집합 밖·승인 판정과 승인 id의 불일치만** 거부한다(`confirmed_at` ⇔ 두 판정 값, `APPROVED` ⇔ 승인 id, 1승인=1SO 유니크). 승인 **대상**이 맞는지·승인이 CONSUMED인지·확정 뒤 증적 값을 고치는 UPDATE는
  DB가 막지 못한다 — 앱 규율(쓰기 통로 1곳 AST 스캔·SYSTEM 열 분류)이 맡는다(트리거 미채택 ADR-0028·0040; 확정 후 증적 3열 UPDATE 차단 트리거는 부채 후보). 판정 값은 `clearance` 정산 결과
  (`Settlement`)와 결과 코드에서 **매핑만** 하고 통과를 다시 판정하지 않는다(통과 판정 복제 금지). 정산과 소비 결과가 어긋나면 fail-closed다: **거래처 잠금은 다른 SO의 취소·편집을 막지 않으므로**(취소 경로는 거래처를 잠그지 않는다 — 전역 순서의 부분수열)
  평가 뒤 소비 전에 노출이 줄어 승인이 불필요해질 수 있다 — 이 경우(NOT_REQUIRED인데 정산은 APPROVED)만 같은 트랜잭션에서 권위 평가를 **1회 재실행**하고, 그래도 어긋나면 전용 409로 전체 롤백한다.
■ 반복 시도 합산: 같은 입력·같은 미해소 결과의 BLOCKED 시도는 직전 증거 행을 **재사용**한다(새 행 미삽입 — 불변 표 규칙과 충돌 없음). 입력이 바뀌면(증거용 digest·승인 ref) 새 행이다. 우회 시도 audit도 (행위자·대상·승인 행·상태 표지) 단위로 합산한다.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import (
    AppError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.modules.approvals import service as approvals_service
from app.modules.approvals.machine import ApprovalType, ConsumeOutcome
from app.modules.credit import (
    spec as _credit_spec,  # noqa: F401 — SO_CREDIT_EXCEEDED TargetSpec 등록(소비 모듈이 확정 통로 임포트 시 항상 등록돼 있게 한다)
)
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.gates import service as gates_service
from app.modules.gates.models import (
    EVALUATION_BLOCKED,
    EVALUATION_CONFIRMED,
    SUBJECT_SALES_ORDER,
    GateEvaluation,
)
from app.modules.gates.service import Clearance, Settlement
from app.modules.gates.types import GateCode
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.sales_orders import service as sales_orders
from app.modules.sales_orders.models import (
    CREDIT_VERDICT_APPROVED,
    CREDIT_VERDICT_NOT_MANAGED,
    CREDIT_VERDICT_WITHIN_LIMIT,
    PI_GATE_NOT_APPLICABLE,
    PI_GATE_OVERRIDDEN,
    PI_GATE_PASS,
    PI_GATE_SKIPPED_OFF,
    PI_GATE_WARN,
    SalesOrder,
    SalesOrderLine,
)
from app.modules.sales_orders.ports import get_allocation_port
from app.modules.trade_chain import gate_flow
from app.modules.trade_chain.chain_ops import converge_parent, lock_chain
from app.modules.trade_chain.schemas import SalesOrderConfirmOut
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.transition import record_transition

KIND = DocKind.SALES_ORDER
CONFIRM_ENDPOINT = "POST /api/v1/sales-orders/{so_id}/confirm"

#: 확정을 요청할 수 있는 역할 — 라우트 게이트(무역·관리자)와 같은 집합을 서비스가 한 번 더 확인한다(잠금 이전 사전 검증). 관리자도 게이트·승인을 우회하지 못한다.
CONFIRM_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})

_CREDIT_PASS_REASONS = frozenset({CREDIT_VERDICT_WITHIN_LIMIT, CREDIT_VERDICT_NOT_MANAGED})
#: PI 입금 게이트 결과 코드 → 저장 판정. 입금 충족(`SUFFICIENT`·`ZERO_REQUIRED`)·비활성(`INACTIVE`)·모드 OFF(`SKIPPED_OFF`)는 각자의 값이고,
#: 미충족인데 통과한 결과(`SHORT`·`PI_MISSING`·`PI_NOT_USABLE` — 경고 모드)는 `WARN`으로 기록한다(스킵·경고 사실이 증적에 남는다).
_PI_VERDICT_BY_REASON: dict[str, str] = {
    "SUFFICIENT": PI_GATE_PASS,
    "ZERO_REQUIRED": PI_GATE_PASS,
    "INACTIVE": PI_GATE_NOT_APPLICABLE,
    "SKIPPED_OFF": PI_GATE_SKIPPED_OFF,
    "SHORT": PI_GATE_WARN,
    "PI_MISSING": PI_GATE_WARN,
    "PI_NOT_USABLE": PI_GATE_WARN,
}


#: 권위 평가 최대 횟수 — 평가 뒤 소비 전에 같은 거래처의 다른 SO가 취소·편집돼 승인이 불필요해진 경합에서 1회 재평가한다(2회째도 어긋나면 중단).
MAX_EVALUATIONS = 2

INCONSISTENT_MESSAGE = "확정하는 동안 거래처의 다른 수주가 변경되어 다시 평가해야 합니다. 화면을 새로고침한 뒤 다시 시도해 주세요. 입력한 내용은 저장되지 않았습니다."


def _inconsistent(so_id: int, what: str) -> AppError:
    """정산·소비 결과가 어긋났다 — 확정하지 않고 전체 롤백한다(fail-closed). 거래처 잠금은 다른 SO의 취소·편집을 막지 못하므로(전역 순서의 부분수열) 평가 뒤 상태가 바뀔 수 있고,
    그 원인을 사용자가 알 수 있게 전용 문구를 싣는다(409 version 충돌 코드 — 새로고침 후 재시도)."""
    return AppError(
        ErrorCode.CONCURRENCY_VERSION_CONFLICT,
        message_override=INCONSISTENT_MESSAGE,
        log_context={"sales_order_id": so_id, "op": "confirm", "inconsistent": what},
    )


def _settled(clr: Clearance, gate: GateCode, so_id: int) -> tuple[Any, Settlement]:
    """게이트 1종의 (결과, 정산) — 정확히 1건이어야 한다(없거나 여럿이면 확정 증적을 만들 수 없다)."""
    found = [(item, state) for item, state in clr.settlements if item.gate_code == gate.value]
    if len(found) != 1:
        raise _inconsistent(so_id, f"{gate.value}:{len(found)}")
    return found[0]


def credit_verdict_of(clr: Clearance, *, consumed: bool, so_id: int) -> str:
    """SO 증적 `credit_verdict` — 정산(`Settlement`)과 소비 결과의 **일치**를 확인하며 매핑한다. 어긋나면 fail-closed."""
    item, state = _settled(clr, GateCode.CREDIT, so_id)
    if state is Settlement.APPROVED and consumed:
        return CREDIT_VERDICT_APPROVED
    if (
        state is Settlement.NOT_REQUIRED
        and not consumed
        and item.reason_code in _CREDIT_PASS_REASONS
    ):
        return str(item.reason_code)
    raise _inconsistent(so_id, "credit")


def pi_gate_verdict_of(clr: Clearance, so_id: int) -> str:
    """SO 증적 `pi_gate_verdict` — override로 해소됐으면 OVERRIDDEN, 아니면 결과 코드에서 매핑(모르는 코드는 fail-closed)."""
    item, state = _settled(clr, GateCode.PI_DEPOSIT, so_id)
    if state is Settlement.OVERRIDDEN:
        return PI_GATE_OVERRIDDEN
    verdict = _PI_VERDICT_BY_REASON.get(item.reason_code)
    if state is not Settlement.NOT_REQUIRED or verdict is None:
        raise _inconsistent(so_id, "pi_gate")
    return verdict


def evaluation_digest(clr: Clearance, policy_sources: dict[str, str]) -> str:
    """**증거용** 게이트 평가 전체 digest(64자 hex) — 모든 결과(PASS 포함)의 `(게이트, 라인, basis_hash)`와 정책 출처의 canonical JSON sha256.

    `gate_evaluations.input_digest`는 **승인 결속용** `gate_input_digest`(거래처·통화·환율·라인 SKU/수량/단가)라 목적지 시장·결제조건·준비도·MOQ·PI 입금 같은 게이트 입력 전부를 덮지 않는다
    (PR-11a 인계 ⑧). 승인 결속 digest를 바꾸면 미소비 승인이 전부 무효가 되므로 **바꾸지 않고**, 확정 증거 `results.evaluation_digest`에 이 값을 별도로 남긴다 — 각 결과의 `basis_hash`가
    그 결과의 판정 입력 전부를 해시하므로 이 값이 같으면 "같은 입력으로 같은 판정"이다. 승인 결속·낡음 판정에는 쓰지 않는다(증거 전용).
    """
    parts = sorted(
        ([item.gate_code, item.line_id, item.basis_hash] for item, _ in clr.settlements),
        key=lambda p: (p[0], -1 if p[1] is None else p[1]),
    )
    canonical = json.dumps(
        {"gates": parts, "policy_source": dict(sorted(policy_sources.items()))},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _evidence_results(
    clr: Clearance, bundle: gate_flow.GateBundle, approval_id: int | None
) -> dict[str, Any]:
    """`gate_evaluations.results` — 비PASS 결과·정산·사용한 override·승인 ref·정책 출처(`gates` 공통) + 증거용 전체 digest."""
    return {
        **gates_service.evaluation_results(
            clr, policy_sources=bundle.policy_sources, approval_id=approval_id
        ),
        "evaluation_digest": evaluation_digest(clr, bundle.policy_sources),
    }


def _incomplete_fields(session: Any, so: SalesOrder) -> dict[str, str]:
    """동결 완결성 사전검사 — DB CHECK(`frozen_complete`)가 500이 되기 전에 422로 안내한다."""
    missing: dict[str, str] = {}
    if so.payment_type is None:
        missing["payment_terms"] = "결제조건을 입력해 주세요."
    if so.incoterm_code is None:
        missing["incoterm"] = "Incoterms를 입력해 주세요."
    if so.fx_rate is None:
        missing["fx_rate"] = "환율을 입력해 주세요."
    if not (so.buyer_name or "").strip():
        missing["buyer_name"] = "바이어 표기를 입력해 주세요."
    if editing.count_live_lines(session, KIND, so.id, SalesOrderLine) == 0:
        missing["lines"] = "라인을 1개 이상 추가해 주세요."
    return missing


def _blocked_detail(
    session: Any,
    so: SalesOrder,
    bundle: gate_flow.GateBundle,
    *,
    roles: frozenset[RoleCode],
    evaluation_id: int,
) -> dict[str, Any]:
    """409 `GATE_BLOCKED`의 detail — 미해소 게이트별 결과·해소 방식·사유 코드(판매 단가·수량·여신 수치만, 역할 마스킹은 조회와 같은 함수)."""
    report = gate_flow.gate_report_body(session, so, bundle, roles=roles)
    blocked = [
        {
            key: gate[key]
            for key in (
                "gate_code",
                "line_id",
                "line_no",
                "level",
                "resolution",
                "reason_code",
                "message_ko",
                "basis",
                "detail",
                "basis_hash",
                "override_roles",
                "can_override",
            )
        }
        for gate in report["gates"]
        if gate["settlement"] == Settlement.UNRESOLVED.value
    ]
    pending = approvals_service.peek_active_approval(
        session, approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value, target_id=so.id
    )
    return {
        "blocked_gates": blocked,
        "needs_approval": report["clearance"]["needs_approval"],
        # 승인 요청 버튼/링크 안내용 — 활성 승인이 있으면 id·상태(REQUESTED=결재 대기, APPROVED=낡아서 소비 불가).
        "pending_approval_id": pending[0] if pending is not None else None,
        "pending_approval_status": pending[1] if pending is not None else None,
        "evaluation_id": evaluation_id,
        "input_digest": bundle.input_digest,
    }


def _require_same_approval(consumed_id: int | None, settled_id: int | None, so_id: int) -> None:
    """정산이 본 승인 id = 소비한 승인 id여야 한다(같은 잠금 하의 평가·소비라 보통 같다) — 다르면 확정하지 않는다."""
    if consumed_id != settled_id:
        raise _inconsistent(so_id, "approval_id")


def _credit_settled_by_approval(clr: Clearance, so_id: int) -> bool:
    """CREDIT이 승인으로 해소된 정산인가 — 소비 결과가 NOT_REQUIRED와 맞물려 재평가 여부를 정한다."""
    _, state = _settled(clr, GateCode.CREDIT, so_id)
    return state is Settlement.APPROVED


def _record_blocked(
    session: Any,
    *,
    so_id: int,
    actor_id: int,
    clr: Clearance,
    bundle: gate_flow.GateBundle,
    approval_id: int | None,
) -> GateEvaluation:
    """BLOCKED 증거 — 직전 행이 같은 입력·같은 결과의 BLOCKED면 **재사용**한다(반복 시도가 증거 표를 무한히 늘리지 못한다). 아니면 새 행(INSERT-only)."""
    results = _evidence_results(clr, bundle, approval_id)
    last: GateEvaluation | None = session.execute(
        select(GateEvaluation)
        .where(
            GateEvaluation.subject_type == SUBJECT_SALES_ORDER, GateEvaluation.subject_id == so_id
        )
        .order_by(GateEvaluation.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if (
        last is not None
        and last.outcome == EVALUATION_BLOCKED
        and last.input_digest == bundle.input_digest
        and last.results == results
    ):
        return last
    return gates_service.record_evaluation(
        session,
        subject_type=SUBJECT_SALES_ORDER,
        subject_id=so_id,
        outcome_kind=EVALUATION_BLOCKED,
        results=results,
        input_digest=bundle.input_digest,
        actor_user_id=actor_id,
    )


def confirm_sales_order(
    *, actor: AuthenticatedUser, idempotency_key: str, so_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """수주 확정 — 모듈 독스트링의 순서. 성공 200(`SalesOrderConfirmOut`), 거부는 전부 예외(4xx)다."""
    if not (actor.roles & CONFIRM_ROLES):
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "confirm_sales_order"})
    deferred: AppError | None = None
    result: tuple[int, dict[str, Any]] | None = None
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CONFIRM_ENDPOINT,
            key=idempotency_key,
            request_body={"so_id": so_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        # 무잠금으로 거래처를 얻고(ORIGIN 불변) → (2) 거래처 잠금 → (3)~(5) 사슬 잠금 → 잠금 뒤 재확인
        buyer_id = session.execute(
            select(SalesOrder.buyer_partner_id).where(
                SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if buyer_id is None:
            raise NotFoundError(log_context={"sales_order_id": so_id})
        locked = lock_buyer_for_credit(session, buyer_id)
        so = lock_chain(session, KIND, so_id, expected_version=version)[KIND]
        if so.buyer_partner_id != locked.partner.id:  # ORIGIN 불변이라 정상 경로로는 불가능한 방어
            raise VersionConflictError(log_context={"sales_order_id": so_id})
        if so.status != "RECEIVED":
            raise AppError(
                ErrorCode.TRADE_DOCS_TRANSITION_NOT_ALLOWED,
                detail={"from": so.status, "to": "CONFIRMED"},
                log_context={"sales_order_id": so_id},
            )
        missing = _incomplete_fields(session, so)
        if missing:
            raise AppError(ErrorCode.TRADE_DOCS_DOCUMENT_INCOMPLETE, detail=missing)

        for attempt in range(MAX_EVALUATIONS):
            bundle = gate_flow.evaluate_sales_order(session, so_id, authoritative=True)
            clr = bundle.clearance
            if not clr.cleared:
                if clr.needs_approval:  # 승인 필요·승인 없음 — ADMIN 포함 우회 시도를 audit에 남긴다(BLOCKED 증거와 같은 커밋)
                    approvals_service.note_bypass_attempt(
                        session,
                        actor_user_id=actor.id,
                        approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value,
                        target_id=so_id,
                    )
                evidence = _record_blocked(
                    session,
                    so_id=so_id,
                    actor_id=actor.id,
                    clr=clr,
                    bundle=bundle,
                    approval_id=bundle.approval_id,
                )
                deferred = AppError(
                    ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED,
                    detail=_blocked_detail(
                        session, so, bundle, roles=actor.roles, evaluation_id=evidence.id
                    ),
                    log_context={"sales_order_id": so_id, "unresolved": len(clr.unresolved)},
                )
                break
            consumed = approvals_service.consume_approval(
                session,
                approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value,
                target_id=so_id,
                actor_user_id=actor.id,
            )
            if consumed.outcome is ConsumeOutcome.BLOCKED:
                # 소비 시점 경합(승인 상태·입력이 정산 뒤 바뀜): VOID·audit이 살아남도록 커밋한 뒤 그 에러를 raise
                _record_blocked(
                    session,
                    so_id=so_id,
                    actor_id=actor.id,
                    clr=clr,
                    bundle=bundle,
                    approval_id=consumed.approval_id,
                )
                assert consumed.error is not None
                deferred = consumed.error
                break
            if (
                consumed.outcome is ConsumeOutcome.NOT_REQUIRED
                and _credit_settled_by_approval(clr, so_id)
                and attempt + 1 < MAX_EVALUATIONS
            ):
                # 평가 뒤 다른 SO의 취소·편집으로 노출이 줄어 승인이 불필요해졌다(소비가 승인을 VOID 정리) — 같은 트랜잭션에서 권위 평가를 한 번 더 한다.
                continue
            credit_verdict = credit_verdict_of(
                clr, consumed=consumed.outcome is ConsumeOutcome.CONSUMED, so_id=so_id
            )
            _require_same_approval(consumed.approval_id, bundle.approval_id, so_id)
            pi_verdict = pi_gate_verdict_of(clr, so_id)
            # 증적 3열은 확정 시각과 **같은 flush**에 들어가야 CHECK를 지킨다 — 대입 후 `record_transition`까지 질의가 끼지 않게 한다.
            with session.no_autoflush:
                so.credit_verdict = credit_verdict
                so.credit_approval_id = consumed.approval_id
                so.pi_gate_verdict = pi_verdict
                record_transition(
                    session,
                    so,
                    "CONFIRMED",
                    actor_user_id=actor.id,
                    reason=None,
                    automatic=False,
                    approval_id=consumed.approval_id,
                    via_freeze_action=True,
                )
            evidence = gates_service.record_evaluation(
                session,
                subject_type=SUBJECT_SALES_ORDER,
                subject_id=so_id,
                outcome_kind=EVALUATION_CONFIRMED,
                results={
                    **_evidence_results(clr, bundle, consumed.approval_id),
                    "credit_verdict": credit_verdict,
                    "pi_gate_verdict": pi_verdict,
                },
                input_digest=bundle.input_digest,
                actor_user_id=actor.id,
            )
            converge_parent(session, KIND, so, actor_user_id=actor.id)  # QT → CONVERTED
            allocation = get_allocation_port().on_confirmed(session, so)  # 예외 = 전체 롤백
            report = gate_flow.gate_report_body(session, so, bundle, roles=actor.roles)
            body = SalesOrderConfirmOut.model_validate(
                {
                    "sales_order": sales_orders.detail_body(session, so),
                    "gates": report["gates"],
                    "allocation": {"status": allocation.status.value, "note": allocation.note},
                    "evaluation_id": evidence.id,
                }
            ).model_dump(mode="json")
            assert claim.record is not None
            idempotency.complete(session, claim.record, status_code=200, body=body)
            result = (200, body)
            break
    if deferred is not None:
        raise deferred  # UoW가 커밋된 뒤 — BLOCKED 증거·우회 시도 audit·소비 시점 VOID가 살아남는다(실패도 커밋)
    assert result is not None
    return result
