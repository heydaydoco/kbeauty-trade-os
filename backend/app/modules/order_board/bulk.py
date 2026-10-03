"""오더 보드 벌크 — 인테이크 확정·수주 확정·담당자 지정 (S3-1 PR-15a / design-D D6 / ADR-0066 / DESIGN §7.4·§17.6).

■ **새 확정 경로가 아니다.** 각 건은 기존 단일 통로 함수를 **그대로** 부른다 — `CONFIRM_INTAKE`→`trade_chain.intake_flow.confirm_intake`,
  `CONFIRM_SO`→`trade_chain.confirm.confirm_sales_order`, `ASSIGN`→담당자 편집 통로(SO `sales_orders.service.update_meta`[FREE 열]·인테이크
  `order_intake.service.update_intake`). 그래서 게이트 재평가·승인 소비·override 결속·잠금 순서·역할 재검증·version 대조가 개별 처리와 **동일**하고,
  벌크는 override·승인을 부여하거나 기안하지 않는다(요청 스키마에 그런 필드가 없다). 보류·거부·취소·override·승인 요청은 벌크 제외(건별 사유·해시 필요).
  이 모듈은 상태를 대입하지 않고 전이 함수·게이트·승인 함수를 직접 부르지 않는다(no_auto_confirm·상태 통로 스캔).
■ **원자성 = 건별 독립 트랜잭션(§17.6) + 결과 리포트**: 단일 통로 함수가 각자 `unit_of_work()`를 연다. 바깥 트랜잭션 안에서 부르면 전부가 한 트랜잭션으로
  합류하므로(그러면 BLOCKED 증거 커밋·부분 성공이 깨진다) 건마다 `in_unit_of_work()`가 False임을 확인한다. 부분 성공이 정상이고(응답 200),
  **합계는 모든 건의 커밋·롤백이 끝난 뒤 결과 목록에서 센다**(건 트랜잭션 안에서 세지 않는다 — 커밋 실패한 건을 성공으로 세는 일이 없다).
■ **처리 순서 = (종류 순위, id) 오름차순** — 종류 순위는 전역 LOCK_ORDER(인테이크 (1) → SO (5))와 같은 방향이고 같은 종류 안에서는 id 오름차순이다.
  각 건 안의 잠금 순서는 단일 통로가 이미 지킨다.
■ **멱등**: 건별 키 = `sha256("{벌크 키}|{액션}|{종류}|{id}")` hex 64자(`idempotency_keys.idempotency_key` 128자 한도 회피). 확정 2종은 그 키를 단일 통로에
  그대로 넘긴다(성공은 최초 결과 재생, 거부는 키 미소비 — 단일 통로 규약 그대로). 담당자 지정은 단일 통로(PATCH)에 멱등 키가 없으므로 이 모듈이 **같은 트랜잭션**에서
  건별 claim/complete를 한다(재요청 시 낡은 version으로 CONFLICT가 나지 않고 최초 결과를 재생). 같은 벌크 키 재요청 = 건별 재생으로 같은 리포트.
■ **권한**: 벌크 전체는 무역·관리자(라우트+여기 사전 검증). 행별 역할 검증은 단일 통로가 하고(인테이크 편집·확정), SO 담당 편집 통로는 라우트 게이트만 있어
  이 모듈이 같은 역할 집합으로 행별 재검증한다. 권한 없는 행은 `FORBIDDEN`. 담당자는 **활성+무역/관리자 역할 보유자**만(ADR-0067 ③ — 사전 422+건별 재확인).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from app.core.db.uow import in_unit_of_work, unit_of_work
from app.core.errors.catalog import spec_for
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError
from app.core.errors.handlers import LOCK_BUSY_SQLSTATES
from app.core.logging import get_logger
from app.modules.idempotency import service as idempotency
from app.modules.identity import service as identity
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.order_board.constants import (
    BULK_MAX_TARGETS,
    KIND_RANK,
    BulkAction,
    BulkOutcome,
    CardKind,
)
from app.modules.order_intake import service as intake_service
from app.modules.sales_orders import service as sales_orders_service
from app.modules.trade_chain import confirm as so_confirm
from app.modules.trade_chain import intake_flow
from app.modules.trade_docs.validation import invalid

logger = get_logger(__name__)

#: 벌크를 요청할 수 있는 역할(라우트 게이트와 같은 집합 — 서비스가 한 번 더 확인한다). 관리자도 게이트·승인을 우회하지 못한다(단일 통로가 판정).
BULK_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})
#: 담당자 편집을 할 수 있는 역할 — SO 메타 라우트(무역, 관리자 상시 통과)·인테이크 편집과 같은 집합. 행별로 재검증한다.
ASSIGN_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})
#: 담당자가 될 수 있는 역할(ADR-0067 ③ — 활성 사용자이면서 무역·관리자 역할 보유).
ASSIGNEE_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})

#: 담당자 지정 건별 멱등 스코프(단일 통로 PATCH에는 멱등 키가 없다 — 이 모듈이 claim/complete한다).
ASSIGN_ITEM_ENDPOINT = "POST /api/v1/order-board/bulk#ASSIGN"

#: 리포트에 싣는 게이트 미해소 항목 필드 — 단일 확정 통로의 409 detail에서 그대로 옮긴다(basis·detail·판정 해시는 싣지 않는다).
BLOCKED_GATE_FIELDS: tuple[str, ...] = (
    "gate_code",
    "line_id",
    "line_no",
    "level",
    "resolution",
    "reason_code",
    "message_ko",
)

BLOCKED_SUFFIX = (
    " 이 건은 상세 화면에서 개별로 처리해 주세요(벌크로는 예외 승인·승인 요청을 할 수 없습니다)."
)

_OK_MESSAGES: dict[BulkAction, str] = {
    BulkAction.CONFIRM_INTAKE: "접수했습니다(수주가 만들어졌습니다).",
    BulkAction.CONFIRM_SO: "수주를 확정했습니다.",
    BulkAction.ASSIGN: "담당자를 바꿨습니다.",
}
SKIPPED_MESSAGE = "이미 그 담당자라 바꾸지 않았습니다."


@dataclass(frozen=True, slots=True)
class Target:
    kind: CardKind
    id: int
    expected_version: int


@dataclass(slots=True)
class ItemResult:
    """건 1개의 결과 — 그 건의 트랜잭션이 커밋(또는 롤백)된 **뒤에** 만들어진다."""

    kind: CardKind
    id: int
    outcome: BulkOutcome
    code: str | None
    message_ko: str
    blocked_gates: list[dict[str, Any]] = field(default_factory=list)
    version: int | None = None
    sales_order_id: int | None = None
    doc_number: str | None = None

    def body(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "outcome": self.outcome.value,
            "code": self.code,
            "message_ko": self.message_ko,
            "blocked_gates": list(self.blocked_gates),
            "version": self.version,
            "sales_order_id": self.sales_order_id,
            "doc_number": self.doc_number,
        }

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> ItemResult:
        return cls(
            kind=CardKind(body["kind"]),
            id=int(body["id"]),
            outcome=BulkOutcome(body["outcome"]),
            code=body.get("code"),
            message_ko=str(body["message_ko"]),
            blocked_gates=list(body.get("blocked_gates") or []),
            version=body.get("version"),
            sales_order_id=body.get("sales_order_id"),
            doc_number=body.get("doc_number"),
        )


def item_key(bulk_key: str, action: BulkAction, kind: CardKind, target_id: int) -> str:
    """건별 멱등 키 — 같은 벌크 키·액션·대상이면 같은 키(재요청=재생), 64자 hex(128자 한도 안)."""
    return sha256(f"{bulk_key}|{action.value}|{kind.value}|{target_id}".encode()).hexdigest()


def processing_order(targets: list[Target]) -> list[Target]:
    """처리 순서 — (종류 순위, id) 오름차순(전역 잠금 순서 방향·결정적 순서)."""
    return sorted(targets, key=lambda t: (KIND_RANK[t.kind], t.id))


def normalize_targets(targets: list[Target]) -> list[Target]:
    """같은 (종류, id)의 완전 중복은 하나로 합치고, version이 다른 중복은 모호하므로 422(fail-closed). 상한 초과는 422 `TOO_MANY`."""
    seen: dict[tuple[CardKind, int], Target] = {}
    conflicting: list[str] = []
    for target in targets:
        key = (target.kind, target.id)
        found = seen.get(key)
        if found is None:
            seen[key] = target
        elif found.expected_version != target.expected_version:
            conflicting.append(f"{target.kind.value}-{target.id}")
    if conflicting:
        raise invalid(
            "targets",
            f"같은 대상이 서로 다른 version으로 두 번 들어 있습니다({', '.join(sorted(set(conflicting)))}). 화면을 새로 고친 뒤 다시 선택해 주세요.",
        )
    unique = list(seen.values())
    if len(unique) > BULK_MAX_TARGETS:
        raise AppError(
            ErrorCode.ORDER_BOARD_BULK_TOO_MANY,
            detail={"limit": BULK_MAX_TARGETS, "requested": len(unique)},
        )
    return unique


def _assignee_problem(session: Any, assignee_id: int) -> str | None:
    roles = identity.active_roles_of(session, assignee_id)
    if roles is None:
        return "활성 사용자가 아닙니다. 담당자를 다시 선택해 주세요."
    if not roles & ASSIGNEE_ROLES:
        return "담당자는 무역 또는 관리자 역할 사용자만 지정할 수 있습니다."
    return None


def require_assignee(assignee_id: int) -> None:
    """벌크 ASSIGN의 담당자 사전 검증(422) — 활성+무역/관리자 보유. 건마다 같은 검사를 트랜잭션 안에서 다시 한다(TOCTOU)."""
    with unit_of_work() as uow:
        problem = _assignee_problem(uow.session, assignee_id)
    if problem is not None:
        raise invalid("assignee_id", problem)


# ── 예외 → 건 결과 ────────────────────────────────────────────────────────────


def _blocked_gates(detail: dict[str, Any]) -> list[dict[str, Any]]:
    gates = detail.get("blocked_gates")
    if not isinstance(gates, list):
        return []
    return [
        {name: gate.get(name) for name in BLOCKED_GATE_FIELDS}
        for gate in gates
        if isinstance(gate, dict)
    ]


def classify_exception(target: Target, exc: Exception) -> ItemResult:
    """단일 통로가 던진 거부를 건 결과로 옮긴다 — 판정을 다시 하지 않고 코드·상태만 매핑한다."""
    if isinstance(exc, AppError):
        if exc.code is ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED:
            return ItemResult(
                target.kind,
                target.id,
                BulkOutcome.BLOCKED,
                str(exc.code),
                exc.message + BLOCKED_SUFFIX,
                blocked_gates=_blocked_gates(exc.detail),
            )
        if exc.status_code == 403:
            outcome = BulkOutcome.FORBIDDEN
        elif exc.status_code == 409:
            outcome = BulkOutcome.CONFLICT
        else:
            outcome = BulkOutcome.FAILED
        return ItemResult(target.kind, target.id, outcome, str(exc.code), exc.message)
    if isinstance(exc, StaleDataError):
        code = ErrorCode.CONCURRENCY_VERSION_CONFLICT
        return ItemResult(
            target.kind, target.id, BulkOutcome.CONFLICT, str(code), spec_for(code).message_ko
        )
    if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in LOCK_BUSY_SQLSTATES:
        code = ErrorCode.CONCURRENCY_LOCK_BUSY
        return ItemResult(
            target.kind, target.id, BulkOutcome.CONFLICT, str(code), spec_for(code).message_ko
        )
    code = ErrorCode.INTERNAL_UNEXPECTED
    logger.error(
        "order_board_bulk_item_failed",
        kind=target.kind.value,
        target_id=target.id,
        exc_info=exc,
    )
    return ItemResult(
        target.kind, target.id, BulkOutcome.FAILED, str(code), spec_for(code).message_ko
    )


# ── 건 실행 ───────────────────────────────────────────────────────────────────


def _confirm_intake(actor: AuthenticatedUser, key: str, target: Target) -> ItemResult:
    _, body = intake_flow.confirm_intake(
        actor=actor, idempotency_key=key, intake_id=target.id, version=target.expected_version
    )
    return ItemResult(
        target.kind,
        target.id,
        BulkOutcome.OK,
        None,
        _OK_MESSAGES[BulkAction.CONFIRM_INTAKE],
        version=int(body["intake"]["version"]),
        sales_order_id=int(body["sales_order_id"]),
        doc_number=str(body["doc_number"]),
    )


def _confirm_so(actor: AuthenticatedUser, key: str, target: Target) -> ItemResult:
    _, body = so_confirm.confirm_sales_order(
        actor=actor, idempotency_key=key, so_id=target.id, version=target.expected_version
    )
    so = body["sales_order"]
    return ItemResult(
        target.kind,
        target.id,
        BulkOutcome.OK,
        None,
        _OK_MESSAGES[BulkAction.CONFIRM_SO],
        version=int(so["version"]),
        sales_order_id=int(so["id"]),
        doc_number=str(so["doc_number"]),
    )


def _assign(actor: AuthenticatedUser, key: str, target: Target, assignee_id: int) -> ItemResult:
    """담당자 지정 1건 — 한 트랜잭션: 건별 claim → (재생이면 최초 결과) → 담당자 재확인 → 단일 편집 통로 → complete."""
    # SO 담당 편집 통로는 라우트 게이트뿐 — 같은 역할 집합으로 행별 재검증한다(인테이크 편집은 통로가 스스로 다시 본다).
    if not actor.roles & ASSIGN_ROLES:
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "order_board_assign"})
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=ASSIGN_ITEM_ENDPOINT,
            key=key,
            request_body={
                "kind": target.kind.value,
                "id": target.id,
                "expected_version": target.expected_version,
                "assignee_id": assignee_id,
            },
        )
        if claim.replay is not None:
            return ItemResult.from_body(claim.replay.body)
        problem = _assignee_problem(session, assignee_id)
        if problem is not None:
            raise invalid("assignee_id", problem)
        payload = {"version": target.expected_version, "assignee_id": assignee_id}
        if target.kind is CardKind.SO:
            body = sales_orders_service.update_meta(actor=actor, so_id=target.id, payload=payload)
        else:
            body = intake_service.update_intake(actor=actor, intake_id=target.id, payload=payload)
        # 통로가 값을 반영하지 않았다면 성공으로 보고하지 않는다(fail-closed).
        if body["assignee_id"] != assignee_id:
            raise RuntimeError("담당자 편집 통로가 요청한 담당자를 반영하지 않았다")
        new_version = int(body["version"])
        unchanged = new_version == target.expected_version
        result = ItemResult(
            target.kind,
            target.id,
            BulkOutcome.SKIPPED if unchanged else BulkOutcome.OK,
            None,
            SKIPPED_MESSAGE if unchanged else _OK_MESSAGES[BulkAction.ASSIGN],
            version=new_version,
            sales_order_id=target.id if target.kind is CardKind.SO else None,
            doc_number=body.get("doc_number") if target.kind is CardKind.SO else None,
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=result.body())
    return result


def run_item(
    actor: AuthenticatedUser,
    bulk_key: str,
    action: BulkAction,
    target: Target,
    assignee_id: int | None,
) -> ItemResult:
    """건 1개 — 자기 트랜잭션에서 실행되고, 결과는 그 트랜잭션이 끝난 뒤 돌아온다(커밋 실패도 예외로 잡혀 FAILED가 된다)."""
    if in_unit_of_work():
        # 바깥 트랜잭션에 합류하면 건별 독립성(부분 성공·BLOCKED 증거 커밋)이 깨진다 — 프로그래밍 오류(fail-closed).
        raise RuntimeError(
            "벌크의 각 건은 독립 트랜잭션이어야 합니다 — 열린 트랜잭션 안에서 부를 수 없습니다."
        )
    key = item_key(bulk_key, action, target.kind, target.id)
    try:
        if action is BulkAction.CONFIRM_INTAKE:
            return _confirm_intake(actor, key, target)
        if action is BulkAction.CONFIRM_SO:
            return _confirm_so(actor, key, target)
        assert assignee_id is not None
        return _assign(actor, key, target, assignee_id)
    except Exception as exc:  # 거부·경합·예상 못 한 오류 → 건 결과(다음 건은 계속)
        return classify_exception(target, exc)


def report(action: BulkAction, results: list[ItemResult]) -> dict[str, Any]:
    """결과 리포트 — **모든 건이 끝난 뒤** 결과 목록에서 센다."""
    counts = Counter(result.outcome for result in results)
    ok = counts[BulkOutcome.OK]
    skipped = counts[BulkOutcome.SKIPPED]
    return {
        "action": action.value,
        "results": [result.body() for result in results],
        "total": len(results),
        "ok_count": ok,
        "skipped_count": skipped,
        "fail_count": len(results) - ok - skipped,
        "outcome_counts": {outcome.value: counts[outcome] for outcome in BulkOutcome},
    }


def run_bulk(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    action: BulkAction,
    targets: list[Target],
    assignee_id: int | None = None,
) -> dict[str, Any]:
    """벌크 실행 — 모듈 독스트링의 계약. 사람 1클릭(`POST /order-board/bulk`)의 유일한 서비스 진입점이다."""
    if not actor.roles & BULK_ROLES:
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "order_board_bulk"})
    unique = normalize_targets(targets)
    if action is BulkAction.ASSIGN:
        if assignee_id is None:
            raise invalid("assignee_id", "담당자를 선택해 주세요.")
        require_assignee(assignee_id)
    results: list[ItemResult] = []
    for target in processing_order(unique):
        result = run_item(actor, idempotency_key, action, target, assignee_id)
        logger.info(
            "order_board_bulk_item",
            action=action.value,
            kind=target.kind.value,
            target_id=target.id,
            outcome=result.outcome.value,
            code=result.code,
        )
        results.append(result)
    return report(action, results)
