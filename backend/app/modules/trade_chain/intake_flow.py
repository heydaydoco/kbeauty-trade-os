"""오더 인테이크 확정 오케스트레이터 — `confirm_intake`(SO 접수 생성)·인테이크 게이트 평가(INTAKE phase) (S3-1 PR-13a / design-D D4·D5 / ADR-0071).

■ **인테이크가 PENDING→CONFIRMED가 되는 유일한 통로**다(`apply_intake_transition(CONFIRMED)`의 호출처 = 이 함수). 사람 1클릭(`POST /order-intakes/{id}/confirm`) + `Idempotency-Key` 필수이고
  호출처는 trade_chain 라우터 1곳뿐이다(자동 확정 경로 부재 — `test_no_auto_confirm_code_path_exists`). 시그니처에 `force`·`skip`·`bypass`·`override` 인자가 없고 ADMIN 분기도 없다.
  **오케스트레이터가 trade_chain에 있는 이유**: SO 생성 단일 착지(`create_received_sales_order`)를 부르고 게이트 평가기(여러 L1 모듈을 읽는다)를 쓰기 때문이다 —
  `order_intake`는 trade_chain을 임포트하지 못한다(PR-10a/11a 방향 반전 선례: 하위 모듈은 상위를 임포트하지 않는다).
■ **한 트랜잭션의 순서**(전역 `LOCK_ORDER`: 멱등 → 인테이크 → 거래처 → … → SO → … → 채번):
  ① 역할 사전 검증(잠금 이전) → ② 멱등 claim(재수신 → 최초 결과 재생) → ③ **인테이크 행 `FOR UPDATE`**(잠금 순서 (1))+version 대조(409)+PENDING 확인(409)
  → ④ 입력 완결성(거래처 BUYER·**`FOR KEY SHARE`**, 시장·통화, 라인 ≥1, 같은 SKU 유상 라인 중복 422, 금액 상한) → ⑤ **하드 게이트 재평가**(품번 매핑·중복 PO — `evaluate_all(phase=INTAKE, only=…)`와
  **`gates.service.clearance`**[통과 판정의 유일한 정의 — 복제 금지]): 미매핑·삭제 SKU 422 / **저장본과 해석이 다르면 409 `STALE_MAPPING`** / 중복 PO(PENDING 인테이크·비취소 SO) 409 / 평가 불능 409
  → ⑥ `create_received_sales_order`(SO 생성 단일 착지 — RECEIVED로만, 채번은 마지막, `copied_from_so_id`→`copied_from_id`) → ⑦ 인테이크 CONFIRMED+백링크+`status_changed` 이벤트 → ⑧ `complete(201)`.
■ **가격 편차·MOQ·시장 준비도·여신·PI 입금은 접수를 막지 않는다**(§7.2 접수는 미확정 — 확정 시점 판정, GC-C2). 인테이크 시점 평가(`GET /order-intakes/{id}/gates`)는 **정보**이며 override는 SO에서만 부여된다.
  단종 SKU는 접수를 허용한다(WARN, A11-2 — SO 확정에서 차단).
■ **어떤 거부도 커밋 보존 대상이 아니다**: 인테이크 확정의 실패(422·409)는 전부 롤백이며 멱등 키를 소비하지 않는다(재시도 가능). 증거 저장(`gate_evaluations`)은 SO 확정 시도의 몫이다(`subject_type`=SALES_ORDER만).
  SO 생성·인테이크 전이·이벤트·채번은 **같은 트랜잭션**이라 중간 실패는 인테이크·SO·번호 카운터·이벤트를 전부 원복한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError
from app.core.time import today_kst, utcnow
from app.modules.catalog.models import Sku
from app.modules.catalog.pricing import prices_at
from app.modules.gates import service as gates_service
from app.modules.gates.service import Clearance
from app.modules.gates.types import (
    SUBJECT_INTAKE,
    GateCode,
    GateLine,
    GateOutcome,
    GatePhase,
    GateSubject,
)
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets import service as markets
from app.modules.order_intake import machine
from app.modules.order_intake import service as intake_service
from app.modules.order_intake.models import OrderIntake, OrderIntakeLine
from app.modules.partners import service as partners
from app.modules.readiness.rules import SCOPE_NOTE
from app.modules.sales_orders import service as sales_orders
from app.modules.trade_chain import gate_evaluators
from app.modules.trade_chain.gate_flow import DETAIL_VISIBLE_ROLES
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind, PriceBasis
from app.modules.trade_docs.fx import require_known_currency
from app.modules.trade_docs.lines import require_sellable_sku
from app.modules.trade_docs.snapshot import LineSnapshot, line_amount, master_list_price

CONFIRM_ENDPOINT = "POST /api/v1/order-intakes/{intake_id}/confirm"

#: 인테이크 확정을 요청할 수 있는 역할 — 라우트 게이트(무역·관리자)와 같은 집합을 서비스가 한 번 더 확인한다.
CONFIRM_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})

#: **접수 확정의 하드 게이트** — 데이터 무결성 2종(품번 매핑·중복 PO)뿐이다. 나머지 게이트는 SO 확정 시점에 판정한다(D5-①-4).
HARD_GATES: tuple[GateCode, ...] = (GateCode.ITEM_MAPPING, GateCode.DUPLICATE_PO)

_UNMAPPED_REASONS = frozenset({"ITEM_UNMAPPED", "SKU_DELETED", "BUYER_LOST"})
_STALE_REASONS = frozenset({"MAPPING_CHANGED"})
_DUPLICATE_REASONS = frozenset({"DUPLICATE_PO_NO"})


# ── 평가 대상 어댑터·평가 ───────────────────────────────────────────────────────


def subject_from_intake(
    session: Session, intake: OrderIntake, lines: list[OrderIntakeLine]
) -> GateSubject:
    """인테이크(헤더+살아 있는 라인)를 평가 대상으로 어댑트한다 — 호출자가 잠근/읽은 값 그대로.

    기준가(`list_price_amount`)는 **인테이크 단계에서 마스터 판가를 지금 조회**한다(`prices_at` — 한 번의 쿼리, 부재는 None → 평가기가 UNKNOWN 처리, 0·환산 대체 없음).
    라인 `sku_id`는 검토자가 본 저장본이다(현재 해석과의 비교는 품번 평가기가 한다).
    """
    sku_ids = sorted({line.sku_id for line in lines if line.sku_id is not None})
    reference = (
        prices_at(
            session,
            sku_ids=sku_ids,
            price_type="SALES",
            currency=intake.currency,
            on=today_kst(),
        )
        if sku_ids
        else {}
    )
    return GateSubject(
        kind=SUBJECT_INTAKE,
        id=intake.id,
        buyer_partner_id=intake.buyer_partner_id,
        currency=intake.currency,
        dest_market_code=intake.dest_market_code,
        lines=tuple(
            GateLine(
                line_id=line.id,
                line_no=line.line_no,
                sku_id=line.sku_id,
                buyer_item_code=line.buyer_item_code,
                quantity=line.quantity,
                unit_price_amount=line.unit_price_amount,
                list_price_amount=(
                    reference[line.sku_id].amount
                    if line.sku_id is not None and line.sku_id in reference
                    else None
                ),
                is_free=False,
            )
            for line in lines
        ),
        total_amount=sum(line.quantity * line.unit_price_amount for line in lines),
        po_no_key=intake.buyer_po_no_key,
        authoritative=False,
    )


@dataclass(frozen=True, slots=True)
class IntakeGateBundle:
    subject: GateSubject
    outcomes: list[GateOutcome]
    clearance: Clearance


def evaluate_intake(
    session: Session,
    intake: OrderIntake,
    lines: list[OrderIntakeLine],
    *,
    only: tuple[GateCode, ...] | None = None,
) -> IntakeGateBundle:
    """INTAKE phase 평가 — **쓰기 없음**. 평가기는 SO와 같은 등록부의 것(품번·중복 PO·가격·준비도·MOQ)이고 통과 판정은 `gates.service.clearance`뿐이다(override·승인 없음 → `{}`·False).

    `only`로 일부만 평가하면 호출자가 그 부분집합에 대한 판정임을 안다(확정 통로는 하드 게이트만 본다).
    """
    subject = subject_from_intake(session, intake, lines)
    outcomes = gates_service.evaluate_all(session, subject, GatePhase.INTAKE, only=only)
    return IntakeGateBundle(
        subject=subject,
        outcomes=outcomes,
        clearance=gates_service.clearance(outcomes, {}, False),
    )


def gate_report_body(
    intake: OrderIntake, bundle: IntakeGateBundle, *, roles: frozenset[RoleCode]
) -> dict[str, Any]:
    """`IntakeGateReportOut` 본문 — 결과별 정산·접수 확정 차단 여부. 점유 문서 식별 등 표시 전용 상세는 무역·관리자에게만."""
    line_no = {line.line_id: line.line_no for line in bundle.subject.lines}
    show_detail = bool(roles & DETAIL_VISIBLE_ROLES)
    hard = {code.value for code in HARD_GATES}
    gates: list[dict[str, Any]] = []
    for item, state in bundle.clearance.settlements:
        gates.append(
            {
                "gate_code": item.gate_code,
                "line_id": item.line_id,
                "line_no": line_no.get(item.line_id) if item.line_id is not None else None,
                "level": item.level.value,
                "resolution": item.resolution.value,
                "reason_code": item.reason_code,
                "message_ko": item.message_ko,
                "basis": dict(item.basis),
                "detail": dict(item.detail) if show_detail else {},
                "settlement": state.value,
                "blocks_intake_confirm": (
                    item.gate_code in hard and state is gates_service.Settlement.UNRESOLVED
                ),
            }
        )
    hard_outcomes = [o for o in bundle.outcomes if o.gate_code in hard]
    return {
        "intake_id": intake.id,
        "status": intake.status,
        "phase": GatePhase.INTAKE.value,
        "evaluated_at": utcnow(),
        "note": gate_evaluators.NOTE_READINESS,
        "readiness_scope_note": SCOPE_NOTE,
        # 하드 게이트만의 정산 — 통과 판정은 `clearance` 한 곳(복제 금지). 하드 결과가 비어 있으면(평가 안 됨) 확정 가능이 아니다.
        "intake_confirmable": gates_service.clearance(hard_outcomes, {}, False).cleared,
        "gates": gates,
    }


def get_intake_gates(*, intake_id: int, roles: frozenset[RoleCode]) -> dict[str, Any]:
    """`GET /order-intakes/{id}/gates` — 인테이크 시점 평가(정보 — 잠금·저장 없음). 확정·거부된 인테이크는 평가하지 않는다(점유가 자기 자신의 SO라 중복 PO로 오독되지 않게)."""
    with unit_of_work() as uow:
        session = uow.session
        intake = intake_service.require_intake(session, intake_id)
        if intake.status != machine.PENDING:
            empty = IntakeGateBundle(
                subject=GateSubject(
                    kind=SUBJECT_INTAKE,
                    id=intake.id,
                    buyer_partner_id=intake.buyer_partner_id,
                    currency=intake.currency,
                    dest_market_code=intake.dest_market_code,
                    lines=(),
                    total_amount=0,
                    po_no_key=intake.buyer_po_no_key,
                ),
                outcomes=[],
                clearance=gates_service.clearance([], {}, False),
            )
            return gate_report_body(intake, empty, roles=roles)
        lines = intake_service.live_lines(session, intake.id)
        return gate_report_body(intake, evaluate_intake(session, intake, lines), roles=roles)


# ── 접수 확정 ────────────────────────────────────────────────────────────────


def _line_numbers(subject: GateSubject, outcomes: list[GateOutcome]) -> list[dict[str, Any]]:
    by_id = {line.line_id: line for line in subject.lines}
    out: list[dict[str, Any]] = []
    for item in outcomes:
        line = by_id.get(item.line_id) if item.line_id is not None else None
        out.append(
            {
                "line_no": line.line_no if line is not None else None,
                "buyer_item_code": line.buyer_item_code if line is not None else None,
                "reason_code": item.reason_code,
            }
        )
    return out


def _raise_for_unresolved(subject: GateSubject, unresolved: tuple[GateOutcome, ...]) -> NoReturn:
    """하드 게이트 미해소 → 지정 에러(우선순위: 미매핑 422 → STALE 409 → 중복 PO 409 → 평가 불능 409). 통과 판정이 아니라 **이미 미해소로 정산된 결과의 코드 매핑**이다."""
    unmapped = [
        o
        for o in unresolved
        if o.gate_code == "ITEM_MAPPING" and o.reason_code in _UNMAPPED_REASONS
    ]
    if unmapped:
        raise AppError(
            ErrorCode.ORDER_INTAKE_LINE_UNMAPPED_ITEMS,
            detail={"lines": _line_numbers(subject, unmapped)},
        )
    stale = [
        o for o in unresolved if o.gate_code == "ITEM_MAPPING" and o.reason_code in _STALE_REASONS
    ]
    if stale:
        raise AppError(
            ErrorCode.ORDER_INTAKE_LINE_STALE_MAPPING,
            detail={"lines": _line_numbers(subject, stale)},
        )
    duplicate = [
        o
        for o in unresolved
        if o.gate_code == "DUPLICATE_PO" and o.reason_code in _DUPLICATE_REASONS
    ]
    if duplicate:
        det = duplicate[0].detail
        found: dict[str, Any] = {"status": det.get("other_status")}
        if det.get("other_doc_number") is not None:
            found["doc_number"] = det["other_doc_number"]
        if det.get("other_intake_id") is not None:
            found["intake_id"] = det["other_intake_id"]
        raise intake_service.duplicate_po_error(found)
    raise AppError(
        ErrorCode.ORDER_INTAKE_GATE_UNRESOLVED,
        detail={"gates": sorted({o.gate_code for o in unresolved})},
    )


def _duplicate_sku_lines(lines: list[OrderIntakeLine]) -> list[list[int]]:
    """같은 SKU로 해석된 라인들의 번호 묶음(2줄 이상인 SKU만) — 인테이크 라인은 모두 유상이다."""
    groups: dict[int, list[int]] = defaultdict(list)
    for line in lines:
        if line.sku_id is not None:
            groups[line.sku_id].append(line.line_no)
    return [nos for nos in groups.values() if len(nos) > 1]


def _new_so_lines(
    session: Session, intake: OrderIntake, lines: list[OrderIntakeLine]
) -> list[sales_orders.NewSoLine]:
    """인테이크 라인 → SO 라인 스냅샷(`price_basis='BUYER_PO'`) — 단가는 바이어 PO 값, `list_price_amount`는 **접수 시점 마스터 판가**(없으면 NULL). 이후 인테이크·마스터와 독립(ADR-05 참조 복사)."""
    doc_date = today_kst()
    out: list[sales_orders.NewSoLine] = []
    for line in lines:
        if line.sku_id is None:  # 하드 게이트가 막은 뒤라 도달 불가 — 방어(fail-closed)
            raise AppError(ErrorCode.ORDER_INTAKE_LINE_UNMAPPED_ITEMS)
        sku: Sku = require_sellable_sku(session, line.sku_id, DocKind.SALES_ORDER)
        out.append(
            sales_orders.NewSoLine(
                snapshot=LineSnapshot(
                    sku_id=sku.id,
                    sku_code=sku.sku_code,
                    sku_name_ko=sku.name_ko,
                    sku_name_en=sku.name_en,
                    sku_kind=sku.kind,
                    buyer_item_code=line.buyer_item_code,
                    unit_price_amount=line.unit_price_amount,
                    list_price_amount=master_list_price(session, sku.id, intake.currency, doc_date),
                    price_basis=PriceBasis.BUYER_PO.value,
                    is_free=False,
                    price_reason=None,
                ),
                quantity=line.quantity,
                requested_delivery_date=line.requested_delivery_date,
            )
        )
    return out


def confirm_intake(
    *, actor: AuthenticatedUser, idempotency_key: str, intake_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """인테이크 확정 = SO 접수 생성 — 모듈 독스트링의 순서. 성공 201, 거부는 전부 예외(4xx) — 롤백이며 키를 소비하지 않는다."""
    if not (actor.roles & CONFIRM_ROLES):
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "confirm_intake"})
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CONFIRM_ENDPOINT,
            key=idempotency_key,
            request_body={"intake_id": intake_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        intake = intake_service.lock_intake(session, intake_id, version=version)  # 잠금 순서 (1)
        intake_service.require_pending(intake, machine.CONFIRMED)

        # 입력 완결성(게이트가 아니라 422) — 거래처는 FOR KEY SHARE로 잡아 유형 해제 임포트와 직렬화한다(잠금 순서 (2)).
        partners.require_partner_of_any_type(
            session,
            intake.buyer_partner_id,
            ("BUYER",),
            field="buyer_partner_id",
            type_label="바이어",
            lock=True,
        )
        markets.require_active_market_code(
            session, intake.dest_market_code, field="dest_market_code"
        )
        require_known_currency(intake.currency)
        lines = intake_service.live_lines(session, intake.id)
        if not lines:
            raise AppError(
                ErrorCode.ORDER_INTAKE_LINE_LIMIT_EXCEEDED,
                detail={"lines": "라인이 없는 오더는 접수할 수 없습니다."},
            )
        duplicates = _duplicate_sku_lines(lines)
        if duplicates:
            raise AppError(ErrorCode.ORDER_INTAKE_LINE_DUPLICATE_SKU, detail={"lines": duplicates})
        editing.compute_total(
            [line_amount(line.quantity, line.unit_price_amount) for line in lines]
        )

        # 하드 게이트 재평가(잠금 하) — 통과 판정은 `clearance` 하나. 미해소는 예외(롤백·키 미소비).
        bundle = evaluate_intake(session, intake, lines, only=HARD_GATES)
        hard = gates_service.clearance(bundle.outcomes, {}, False)
        if not hard.cleared:
            _raise_for_unresolved(bundle.subject, hard.unresolved)

        so = sales_orders.create_received_sales_order(
            session,
            actor=actor,
            draft=sales_orders.SalesOrderDraft(
                buyer_partner_id=intake.buyer_partner_id,
                currency=intake.currency,
                dest_market_code=intake.dest_market_code,
                lines=_new_so_lines(session, intake, lines),
                doc_date=today_kst(),
                buyer_po_no=intake.buyer_po_no,
                buyer_po_date=intake.buyer_po_date,
                assignee_id=intake.assignee_id,
                copied_from_id=intake.copied_from_so_id,
            ),
        )
        machine.apply_intake_transition(
            session, intake, machine.CONFIRMED, actor_id=actor.id, sales_order_id=so.id
        )
        session.refresh(intake)
        body: dict[str, Any] = {
            "intake_id": intake.id,
            "sales_order_id": so.id,
            "doc_number": so.doc_number,
            "intake": intake_service.detail_body(session, intake),
        }
        body = intake_service.jsonable(body)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body
