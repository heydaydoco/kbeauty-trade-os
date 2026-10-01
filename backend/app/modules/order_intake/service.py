"""오더 인테이크 서비스 — 착지(`register_intake`)·편집·해석(resolve)·거부·조회 (S3-1 PR-13a / design-D D1·D2·D4-①·D7 / ADR-0071).

L2(order_intake): **trade_chain을 임포트하지 않는다**(역방향 금지 — 허용 간선은 order_intake→{L1 sales_orders, gates, partners}). 인테이크 **확정(`confirm_intake`)은 오케스트레이터**라
trade_chain(`intake_flow`)에 있다(SO 생성 단일 착지 `create_received_sales_order`를 부르고 인테이크 CONFIRMED로 전이 — PR-10a/11a 방향 반전 선례).

■ **`register_intake` = 착지의 단일 통로**: 거래처 BUYER 재확인(`FOR KEY SHARE`) → 통화·시장·PO일자·담당자 선검사 → PO 키 산출·길이 검사 → **중복 PO 선조회**(PENDING 인테이크·비취소 SO — 409) →
  라인 검증(수량·단가·금액 상한·납기) → 라인 **품번 해석**(`resolve_buyer_items` — 정확 일치, 자동 매칭 없음, 서버 해석만) → INSERT(SAVEPOINT — 선조회를 빠져나간 동시 요청은 DB 부분 유니크가 잡고 제약명으로 같은 409로 번역) →
  `order_intakes.order_intake.created`. **`status` 인자가 없다**(항상 PENDING — 서버 기본값). 트랜잭션·멱등 claim은 호출자(엔드포인트 서비스) 몫.
■ 모든 쓰기의 순서 — 멱등 claim → 인테이크 행 `FOR UPDATE`(잠금 순서 (1)) → `version` 대조(409) → 상태 PENDING 검사(409) → 변경 → **라인만 바뀌어도 헤더 version +1**. 편집은 PENDING에서만, **삭제 엔드포인트는 없다**(폐기=거부).
■ SKU 해석 저장본(`order_intake_lines.sku_id`)은 **검토자가 본 값**이다 — 등록·수정·`resolve`마다 서버가 다시 해석해 대입하고, 확정 시 현재 해석과 다르면 409 `STALE_MAPPING`이다.
■ 응답·이벤트 payload에 금액·사유 원문을 싣지 않는다(이벤트) / 원가·마진·매입가 필드는 어느 역할에도 없다(응답).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.money import AmountFormatError, parse_minor_amount
from app.core.time import today_kst, utcnow
from app.modules.catalog.models import Sku
from app.modules.gates.text import reason_problem
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets import service as markets
from app.modules.order_intake import machine
from app.modules.order_intake.models import (
    MAX_INTAKE_LINES,
    IntakeSourceKind,
    IntakeStatus,
    OrderIntake,
    OrderIntakeLine,
)
from app.modules.order_intake.schemas import IntakeHeaderIn, IntakeLineIn
from app.modules.outbox import service as outbox
from app.modules.partners import service as partners
from app.modules.partners.models import Partner
from app.modules.partners.service import ResolvedBuyerItem
from app.modules.sales_orders import service as sales_orders
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_docs import editing
from app.modules.trade_docs.buyer_po import po_columns
from app.modules.trade_docs.fx import require_known_currency
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.machine import SalesOrderStatus
from app.modules.trade_docs.snapshot import line_amount, validate_quantity
from app.modules.trade_docs.validation import invalid, require_active_user
from app.modules.trade_docs.views import money_text

logger = get_logger(__name__)

#: 인테이크를 만들고 고치고 거부·확정하는 역할 — 라우트 게이트(무역·관리자)와 같은 집합을 서비스가 한 번 더 확인한다(이중 방어).
INTAKE_WRITE_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})

CREATE_ENDPOINT = "POST /api/v1/order-intakes"
RESOLVE_ENDPOINT = "POST /api/v1/order-intakes/{intake_id}/resolve"
REJECT_ENDPOINT = "POST /api/v1/order-intakes/{intake_id}/reject"

#: 품번 해석 파생 상태(응답 전용 — 저장 아님).
MAPPING_MAPPED = "MAPPED"
MAPPING_UNMAPPED = "UNMAPPED"
MAPPING_STALE = "STALE"

PO_UNIQUE = "uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending"
COPY_UNIQUE = "uq_order_intakes_copied_from_so_id_pending"


def require_intake_writer(actor: AuthenticatedUser) -> None:
    """서비스 층 역할 사전 검증 — 무역·관리자만(라우트 게이트와 이중 방어)."""
    if not (actor.roles & INTAKE_WRITE_ROLES):
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "order_intake_write"})


# ── 제약명 → HTTP 번역 (단일 지점) ─────────────────────────────────────────────


def _constraint_name(exc: IntegrityError) -> str:
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return str(name) if name else str(exc.orig)


def map_integrity_error(
    session: Session, exc: IntegrityError, *, buyer_partner_id: int | None, po_key: str | None
) -> AppError | None:
    """인테이크 제약 위반 → 지정 코드(제약명으로 분기) — 미등록 제약이면 None(호출자가 500을 유지한다: 값 없이 제약명만 로그, 삼키면 fail-open).

    PENDING 점유 중복 PO → 409 `TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO`(점유 문서 id·상태만, 금액 없음) / 같은 원본 SO의 PENDING 복제 → 409 `COPY.SOURCE_NOT_ELIGIBLE`.
    """
    name = _constraint_name(exc)
    if PO_UNIQUE in name and buyer_partner_id is not None and po_key is not None:
        found = occupant(session, buyer_partner_id, po_key)
        return duplicate_po_error(found or {})
    if COPY_UNIQUE in name:
        return AppError(ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE)
    logger.error("order_intake_unmapped_constraint", constraint=name)
    return None


@contextmanager
def guarded_flush(
    session: Session, *, buyer_partner_id: int | None = None, po_key: str | None = None
) -> Iterator[None]:
    """SAVEPOINT 안에서 실행해 DB 유니크 경합(선조회를 빠져나간 동시 요청)을 서비스 검사와 **같은 409**로 번역한다(500 금지, `PendingRollbackError` 방지)."""
    try:
        with session.begin_nested():
            yield
    except IntegrityError as exc:
        mapped = map_integrity_error(session, exc, buyer_partner_id=buyer_partner_id, po_key=po_key)
        if mapped is None:
            raise
        raise mapped from None


# ── 중복 바이어 PO ────────────────────────────────────────────────────────────


def duplicate_po_error(found: dict[str, Any]) -> AppError:
    return AppError(ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO, detail=found)


def occupant(
    session: Session, buyer_partner_id: int, key: str, *, exclude_intake_id: int | None = None
) -> dict[str, Any] | None:
    """(바이어, 정규화 PO 키)를 점유한 문서 — **PENDING 인테이크**(다른 것) 또는 **살아 있는 비취소 SO**. 없으면 None.

    detail은 점유 문서의 식별(인테이크 id 또는 SO 문서번호)과 상태뿐이다(금액 미기재). REJECTED 인테이크·취소 SO는 키를 점유하지 않는다.
    """
    query = select(OrderIntake.id, OrderIntake.status).where(
        OrderIntake.buyer_partner_id == buyer_partner_id,
        OrderIntake.buyer_po_no_key == key,
        OrderIntake.status == IntakeStatus.PENDING.value,
        OrderIntake.deleted_at.is_(None),
    )
    if exclude_intake_id is not None:
        query = query.where(OrderIntake.id != exclude_intake_id)
    pending = session.execute(query.limit(1)).first()
    if pending is not None:
        return {"intake_id": int(pending[0]), "status": str(pending[1])}
    so = sales_orders.po_occupant(session, buyer_partner_id, key)
    if so is not None:
        return {"doc_number": so[0], "status": so[1]}
    return None


def require_po_free(
    session: Session, buyer_partner_id: int, key: str, *, exclude_intake_id: int | None = None
) -> None:
    found = occupant(session, buyer_partner_id, key, exclude_intake_id=exclude_intake_id)
    if found is not None:
        raise duplicate_po_error(found)


# ── 입력 검증 ────────────────────────────────────────────────────────────────


def _parse_price(raw: str, currency: str, field: str) -> int:
    try:
        amount = parse_minor_amount(raw, currency, field=field, max_digits=15)
    except AmountFormatError as exc:
        raise invalid(field, exc.reason) from None
    if amount < 1:
        raise invalid(
            field, "단가는 0보다 커야 합니다(무상 라인은 수주 접수 후 수주 편집에서 추가합니다)."
        )
    return amount


def _check_delivery(value: date | None, field: str) -> date | None:
    if value is not None and value < today_kst():
        raise invalid(field, "요청납기는 오늘(KST)보다 앞설 수 없습니다.")
    return value


def _clean_code(raw: str, field: str) -> str:
    code = raw.strip()
    if not code:
        raise invalid(field, "바이어 품번을 입력해 주세요.")
    if len(code) > 100:
        raise invalid(field, "바이어 품번은 100자 이내로 입력해 주세요.")
    return code


def _check_po_date(value: date | None) -> date | None:
    if value is not None and value > today_kst():
        raise invalid("buyer_po_date", "바이어 PO 일자는 오늘(KST)보다 미래일 수 없습니다.")
    return value


def _require_line_count(count: int) -> None:
    if not 1 <= count <= MAX_INTAKE_LINES:
        raise AppError(
            ErrorCode.ORDER_INTAKE_LINE_LIMIT_EXCEEDED,
            detail={
                "lines": f"라인은 1개 이상 {MAX_INTAKE_LINES}개 이하여야 합니다(현재 {count}개)."
            },
        )


def _normalize_po(raw: str) -> tuple[str, str]:
    po_no, key = po_columns(raw)
    if po_no is None or key is None:
        raise invalid("buyer_po_no", "바이어 PO번호를 입력해 주세요.")
    return po_no, key


def _validated_line(
    item: IntakeLineIn, currency: str, index: int
) -> tuple[str, int, int, date | None]:
    """(품번, 수량, 단가 최소단위, 요청납기) — 라인 금액 상한(2^53−1)까지 검사한다."""
    prefix = f"lines[{index}]."
    code = _clean_code(item.buyer_item_code, f"{prefix}buyer_item_code")
    quantity = validate_quantity(item.quantity, field=f"{prefix}quantity")
    price = _parse_price(item.unit_price, currency, f"{prefix}unit_price")
    line_amount(quantity, price)  # 상한 초과는 422(TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE)
    delivery = _check_delivery(item.requested_delivery_date, f"{prefix}requested_delivery_date")
    return code, quantity, price, delivery


# ── 품번 해석 ────────────────────────────────────────────────────────────────


def resolve_codes(
    session: Session, buyer_partner_id: int, codes: Sequence[str]
) -> dict[str, ResolvedBuyerItem]:
    """품번 → SKU 해석(한 번의 쿼리, strip 정확 일치 — 자동 매칭·수동 SKU 지정 없음)."""
    return partners.resolve_buyer_items(session, buyer_partner_id, list(codes))


def live_lines(session: Session, intake_id: int) -> list[OrderIntakeLine]:
    return list(
        session.execute(
            select(OrderIntakeLine)
            .where(OrderIntakeLine.intake_id == intake_id, OrderIntakeLine.deleted_at.is_(None))
            .order_by(OrderIntakeLine.line_no, OrderIntakeLine.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def _apply_resolution(
    session: Session, intake: OrderIntake, lines: Sequence[OrderIntakeLine], *, actor_id: int
) -> int:
    """전 라인을 지금 매핑으로 재해석해 저장본(`sku_id`)에 대입한다 — 바뀐 라인 수를 돌려준다."""
    resolved = resolve_codes(
        session, intake.buyer_partner_id, [line.buyer_item_code for line in lines]
    )
    changed = 0
    for line in lines:
        hit = resolved.get(line.buyer_item_code.strip())
        new_sku = hit.sku_id if hit is not None else None
        if line.sku_id != new_sku:
            line.sku_id = new_sku
            line.updated_by_id = actor_id
            changed += 1
    return changed


# ── 착지 (단일 통로) ──────────────────────────────────────────────────────────


def _check_copy_source(session: Session, source_id: int, buyer_partner_id: int) -> None:
    """복제 재접수 원본 SO 자격(X-13) — 존재·같은 바이어·**취소(CANCELLED)** 상태. 살아 있는 복제본이 있으면 거부한다(복제=중복 생성 차단).

    확정 시점에 `create_received_sales_order`가 같은 검사를 원본 행 잠금 하에서 다시 한다(여기는 조기 거부).
    """
    source = session.execute(
        select(SalesOrder.buyer_partner_id, SalesOrder.status).where(
            SalesOrder.id == source_id, SalesOrder.deleted_at.is_(None)
        )
    ).first()
    if (
        source is None
        or source[0] != buyer_partner_id
        or source[1] != SalesOrderStatus.CANCELLED.value
        or sales_orders.has_live_copy(session, source_id)
    ):
        raise AppError(
            ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE, log_context={"source_id": source_id}
        )


def register_intake(
    session: Session,
    *,
    actor: AuthenticatedUser,
    source_kind: IntakeSourceKind,
    buyer_partner_id: int,
    header: IntakeHeaderIn,
    lines: Sequence[IntakeLineIn],
    extracted_snapshot: dict[str, Any],
    source_sha256: str | None = None,
    source_group_key: str | None = None,
    original_filename: str | None = None,
) -> OrderIntake:
    """인테이크 1건 착지 — 모듈 독스트링의 순서. 항상 PENDING이다(`status` 인자 없음). 호출자의 트랜잭션에 합류한다(자체 커밋 없음)."""
    require_intake_writer(actor)
    if not isinstance(extracted_snapshot, dict):
        raise TypeError("extracted_snapshot은 dict여야 합니다.")
    if source_kind is IntakeSourceKind.MANUAL and (
        source_sha256 or source_group_key or original_filename
    ):
        raise ValueError("MANUAL 입구는 파일 출처 값을 받지 않는다.")
    partner = partners.require_partner_of_any_type(
        session,
        buyer_partner_id,
        ("BUYER",),
        field="buyer_partner_id",
        type_label="바이어",
        lock=True,
    )
    currency = require_known_currency(header.currency)
    dest = str(header.dest_market_code).strip().upper()
    markets.require_active_market_code(session, dest, field="dest_market_code")
    po_no, po_key = _normalize_po(header.buyer_po_no)
    _check_po_date(header.buyer_po_date)
    assignee_id = header.assignee_id or actor.id
    if header.assignee_id is not None:
        require_active_user(session, assignee_id)
    if header.copied_from_so_id is not None:
        _check_copy_source(session, header.copied_from_so_id, partner.id)
    _require_line_count(len(lines))
    parsed = [_validated_line(item, currency, index) for index, item in enumerate(lines)]
    editing.compute_total([line_amount(q, p) for _, q, p, _ in parsed])
    require_po_free(session, partner.id, po_key)
    resolved = resolve_codes(session, partner.id, [code for code, _, _, _ in parsed])

    row = OrderIntake(
        source_kind=source_kind.value,
        source_sha256=source_sha256,
        source_group_key=source_group_key,
        original_filename=original_filename,
        extracted_snapshot=extracted_snapshot,
        buyer_partner_id=partner.id,
        buyer_po_no=po_no,
        buyer_po_no_key=po_key,
        buyer_po_date=header.buyer_po_date,
        currency=currency,
        dest_market_code=dest,
        assignee_id=assignee_id,
        last_line_no=len(parsed),
        copied_from_so_id=header.copied_from_so_id,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    with guarded_flush(session, buyer_partner_id=partner.id, po_key=po_key):
        session.add(row)
        session.flush()
        for line_no, (item, (code, quantity, price, delivery)) in enumerate(
            zip(lines, parsed, strict=True), start=1
        ):
            hit = resolved.get(code)
            session.add(
                OrderIntakeLine(
                    intake_id=row.id,
                    currency=currency,
                    line_no=line_no,
                    buyer_item_code=code,
                    sku_id=hit.sku_id if hit is not None else None,
                    quantity=quantity,
                    unit_price_amount=price,
                    requested_delivery_date=delivery,
                    source_row_no=item.source_row_no,
                    created_by_id=actor.id,
                    updated_by_id=actor.id,
                )
            )
        session.flush()
    outbox.publish(
        session,
        event_type=machine.EVENT_CREATED,
        aggregate_type=machine.AGGREGATE_TYPE,
        aggregate_id=row.id,
        payload={
            "intake_id": row.id,
            "buyer_partner_id": row.buyer_partner_id,
            "assignee_id": row.assignee_id,
            "source_kind": row.source_kind,
        },
    )
    session.refresh(row)  # 서버 기본값(status=PENDING)·version 적재
    return row


# ── 조회 ─────────────────────────────────────────────────────────────────────


def require_intake(session: Session, intake_id: int) -> OrderIntake:
    row = session.execute(
        select(OrderIntake).where(OrderIntake.id == intake_id, OrderIntake.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"intake_id": intake_id})
    return row


def _buyer_names(session: Session, partner_ids: set[int]) -> dict[int, str]:
    if not partner_ids:
        return {}
    return {
        int(pid): (name_en or name_ko)
        for pid, name_ko, name_en in session.execute(
            select(Partner.id, Partner.name_ko, Partner.name_en).where(Partner.id.in_(partner_ids))
        )
    }


def _line_body(
    line: OrderIntakeLine,
    currency: str,
    skus: dict[int, Sku],
    resolved: dict[str, ResolvedBuyerItem],
) -> dict[str, Any]:
    sku = skus.get(line.sku_id) if line.sku_id is not None else None
    hit = resolved.get(line.buyer_item_code.strip())
    if hit is None:
        state = MAPPING_UNMAPPED
    elif line.sku_id == hit.sku_id:
        state = MAPPING_MAPPED
    else:
        state = MAPPING_STALE
    return {
        "id": line.id,
        "line_no": line.line_no,
        "buyer_item_code": line.buyer_item_code,
        "sku_id": line.sku_id,
        "sku_code": sku.sku_code if sku is not None else None,
        "sku_name_ko": sku.name_ko if sku is not None else None,
        "sku_status": (
            None
            if line.sku_id is None
            else ("DELETED" if sku is None or sku.deleted_at is not None else sku.status)
        ),
        "mapping_state": state,
        "quantity": line.quantity,
        "unit_price_amount": line.unit_price_amount,
        "unit_price_text": money_text(line.unit_price_amount, currency) or "0",
        "line_amount": line.quantity * line.unit_price_amount,
        "requested_delivery_date": line.requested_delivery_date,
        "source_row_no": line.source_row_no,
    }


def detail_body(session: Session, intake: OrderIntake) -> dict[str, Any]:
    """`IntakeDetail` 본문 — 현재 값 + 불변 원본(`original`). 라인별 품번 해석 파생 상태는 지금 매핑 기준이다(쿼리 상수: 라인 수와 무관)."""
    lines = live_lines(session, intake.id)
    sku_ids = {line.sku_id for line in lines if line.sku_id is not None}
    skus = (
        {s.id: s for s in session.execute(select(Sku).where(Sku.id.in_(sku_ids))).scalars()}
        if sku_ids
        else {}
    )
    resolved = resolve_codes(
        session, intake.buyer_partner_id, [line.buyer_item_code for line in lines]
    )
    total = sum(line.quantity * line.unit_price_amount for line in lines)
    return {
        "id": intake.id,
        "version": intake.version,
        "source_kind": intake.source_kind,
        "status": intake.status,
        "buyer_partner_id": intake.buyer_partner_id,
        "buyer_name": _buyer_names(session, {intake.buyer_partner_id}).get(intake.buyer_partner_id),
        "buyer_po_no": intake.buyer_po_no,
        "buyer_po_date": intake.buyer_po_date,
        "currency": intake.currency,
        "dest_market_code": intake.dest_market_code,
        "assignee_id": intake.assignee_id,
        "reject_reason": intake.reject_reason,
        "decided_at": intake.decided_at,
        "decided_by_id": intake.decided_by_id,
        "sales_order_id": intake.sales_order_id,
        "copied_from_so_id": intake.copied_from_so_id,
        "last_line_no": intake.last_line_no,
        "created_at": intake.created_at,
        "updated_at": intake.updated_at,
        "total_amount": total,
        "total_text": money_text(total, intake.currency) or "0",
        "lines": [_line_body(line, intake.currency, skus, resolved) for line in lines],
        "original": intake.extracted_snapshot,
    }


def get_intake(intake_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return detail_body(uow.session, require_intake(uow.session, intake_id))


def list_intakes(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """목록(페이지) — 라인 수·합계는 한 번의 집계 쿼리로(N+1 없음)."""
    conditions: list[Any] = [OrderIntake.deleted_at.is_(None)]
    if status:
        conditions.append(OrderIntake.status == status)
    if buyer_partner_id:
        conditions.append(OrderIntake.buyer_partner_id == buyer_partner_id)
    if assignee_id:
        conditions.append(OrderIntake.assignee_id == assignee_id)
    if q and q.strip():
        needle = q.strip()
        conditions.append(
            or_(
                OrderIntake.buyer_po_no.icontains(needle, autoescape=True),
                OrderIntake.buyer_partner_id.in_(
                    select(Partner.id).where(
                        or_(
                            Partner.name_ko.icontains(needle, autoescape=True),
                            Partner.name_en.icontains(needle, autoescape=True),
                        )
                    )
                ),
            )
        )
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(OrderIntake).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(OrderIntake)
                .where(*conditions)
                .order_by(OrderIntake.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        ids = [r.id for r in rows]
        sums: dict[int, tuple[int, int]] = {}
        if ids:
            sums = {
                int(i): (int(n), int(t))
                for i, n, t in session.execute(
                    select(
                        OrderIntakeLine.intake_id,
                        func.count(),
                        func.coalesce(
                            func.sum(OrderIntakeLine.quantity * OrderIntakeLine.unit_price_amount),
                            0,
                        ),
                    )
                    .where(OrderIntakeLine.intake_id.in_(ids), OrderIntakeLine.deleted_at.is_(None))
                    .group_by(OrderIntakeLine.intake_id)
                )
            }
        names = _buyer_names(session, {r.buyer_partner_id for r in rows})
        return [
            {
                "id": r.id,
                "version": r.version,
                "source_kind": r.source_kind,
                "status": r.status,
                "buyer_partner_id": r.buyer_partner_id,
                "buyer_name": names.get(r.buyer_partner_id),
                "buyer_po_no": r.buyer_po_no,
                "buyer_po_date": r.buyer_po_date,
                "currency": r.currency,
                "dest_market_code": r.dest_market_code,
                "assignee_id": r.assignee_id,
                "line_count": sums.get(r.id, (0, 0))[0],
                "total_amount": sums.get(r.id, (0, 0))[1],
                "total_text": money_text(sums.get(r.id, (0, 0))[1], r.currency) or "0",
                "sales_order_id": r.sales_order_id,
                "copied_from_so_id": r.copied_from_so_id,
                "created_at": r.created_at,
                "decided_at": r.decided_at,
            }
            for r in rows
        ], total


# ── 엔드포인트 서비스 (멱등·잠금·낙관 잠금) ───────────────────────────────────────


def create_manual_intake(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """수동 입구 — 한 트랜잭션: 멱등 claim → `register_intake`(MANUAL) → 상세 → `complete(201)`. 최초 제출 본문이 불변 스냅샷이 된다."""
    require_intake_writer(actor)
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        header = IntakeHeaderIn(
            buyer_po_no=payload["buyer_po_no"],
            currency=payload["currency"],
            dest_market_code=payload["dest_market_code"],
            buyer_po_date=payload.get("buyer_po_date"),
            assignee_id=payload.get("assignee_id"),
            copied_from_so_id=payload.get("copied_from_so_id"),
        )
        lines = [
            IntakeLineIn(
                buyer_item_code=item["buyer_item_code"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                requested_delivery_date=item.get("requested_delivery_date"),
            )
            for item in payload["lines"]
        ]
        row = register_intake(
            session,
            actor=actor,
            source_kind=IntakeSourceKind.MANUAL,
            buyer_partner_id=payload["buyer_partner_id"],
            header=header,
            lines=lines,
            extracted_snapshot=jsonable(_manual_snapshot(payload)),
        )
        body = detail_body(session, row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=jsonable(body))
        return 201, jsonable(body)


def _manual_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """MANUAL 불변 원본 — 최초 제출 본문 `{kind, header, lines}`(요청 본문 그대로 — 서버 해석값·키는 싣지 않는다)."""
    return {
        "kind": IntakeSourceKind.MANUAL.value,
        "header": {
            key: payload.get(key)
            for key in (
                "buyer_partner_id",
                "buyer_po_no",
                "buyer_po_date",
                "currency",
                "dest_market_code",
                "assignee_id",
                "copied_from_so_id",
            )
        },
        "lines": [
            {
                "buyer_item_code": item["buyer_item_code"],
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "requested_delivery_date": item.get("requested_delivery_date"),
            }
            for item in payload["lines"]
        ],
    }


def jsonable(body: dict[str, Any]) -> dict[str, Any]:
    """멱등 저장·JSONB용 — date·datetime을 ISO 문자열로(응답 모델이 같은 값을 다시 파싱한다)."""
    import json

    return dict(json.loads(json.dumps(body, default=str)))


def require_pending(intake: OrderIntake, to: str) -> None:
    if intake.status != IntakeStatus.PENDING.value:
        raise AppError(
            ErrorCode.ORDER_INTAKE_STATE_NOT_PENDING,
            detail={"from": intake.status, "to": to},
            log_context={"intake_id": intake.id},
        )


def lock_intake(session: Session, intake_id: int, *, version: int) -> OrderIntake:
    """인테이크 행 `FOR UPDATE`(잠금 순서 (1)) + 낙관 잠금 대조(409) — 존재·삭제 검증 포함."""
    return lock_document(session, OrderIntake, intake_id, expected_version=version)


def update_intake(
    *, actor: AuthenticatedUser, intake_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """대기(PENDING) 인테이크 편집 — 헤더(PO번호·PO일자·시장·담당자)와 라인 전체. 라인만 바뀌어도 헤더 version +1. 거래처·통화·상태·스냅샷은 건드릴 수 없다."""
    require_intake_writer(actor)
    with unit_of_work() as uow:
        session = uow.session
        intake = lock_intake(session, intake_id, version=payload["version"])
        require_pending(intake, intake.status)
        changed = False
        po_key: str | None = None
        if "buyer_po_no" in payload:
            if payload["buyer_po_no"] is None:
                raise invalid("buyer_po_no", "바이어 PO번호는 비울 수 없습니다.")
            po_no, po_key = _normalize_po(payload["buyer_po_no"])
            if po_key != intake.buyer_po_no_key:
                require_po_free(
                    session, intake.buyer_partner_id, po_key, exclude_intake_id=intake.id
                )
                intake.buyer_po_no_key = po_key
                changed = True
            if po_no != intake.buyer_po_no:
                intake.buyer_po_no = po_no
                changed = True
        if "buyer_po_date" in payload and payload["buyer_po_date"] != intake.buyer_po_date:
            intake.buyer_po_date = _check_po_date(payload["buyer_po_date"])
            changed = True
        if "dest_market_code" in payload:
            if payload["dest_market_code"] is None:
                raise invalid("dest_market_code", "목적지 시장은 비울 수 없습니다.")
            dest = str(payload["dest_market_code"]).strip().upper()
            if dest != intake.dest_market_code:
                markets.require_active_market_code(session, dest, field="dest_market_code")
                intake.dest_market_code = dest
                changed = True
        if "assignee_id" in payload:
            if payload["assignee_id"] is None:
                raise invalid("assignee_id", "담당자는 비울 수 없습니다.")
            if payload["assignee_id"] != intake.assignee_id:
                require_active_user(session, payload["assignee_id"])
                intake.assignee_id = payload["assignee_id"]
                changed = True
        if payload.get("lines") is not None:
            _edit_lines(session, intake, payload["lines"], actor.id)
            changed = True
        if changed:
            intake.updated_by_id = actor.id
            editing.bump_header_version(intake)  # 라인만 바뀌어도 헤더 version +1
            with guarded_flush(session, buyer_partner_id=intake.buyer_partner_id, po_key=po_key):
                session.flush()
        session.refresh(intake)
        return detail_body(session, intake)


def _edit_lines(
    session: Session, intake: OrderIntake, items: list[dict[str, Any]], actor_id: int
) -> None:
    """라인 전체 교체 의미 — `id` 있는 항목은 제자리 수정, 없으면 신규(번호는 `last_line_no`+1, 재사용 금지), 목록에 없는 기존 라인은 제외(soft delete).

    **다른 인테이크의 라인 id는 404**(IDOR — 존재 여부도 알리지 않는다). 수정 후 전 라인을 다시 해석한다(저장본 갱신).
    """
    _require_line_count(len(items))
    existing = {line.id: line for line in live_lines(session, intake.id)}
    ids = [item["id"] for item in items if item.get("id") is not None]
    if len(ids) != len(set(ids)):
        raise invalid("lines", "같은 라인이 두 번 들어 있습니다.")
    for line_id in ids:
        if line_id not in existing:
            raise NotFoundError(log_context={"intake_id": intake.id, "line_id": line_id})
    parsed = [
        _validated_line(
            IntakeLineIn(
                buyer_item_code=item["buyer_item_code"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                requested_delivery_date=item.get("requested_delivery_date"),
            ),
            intake.currency,
            index,
        )
        for index, item in enumerate(items)
    ]
    editing.compute_total([line_amount(q, p) for _, q, p, _ in parsed])
    keep = set(ids)
    now = utcnow()
    for line_id, line in existing.items():
        if line_id not in keep:
            line.deleted_at = now
            line.updated_by_id = actor_id
    session.flush()
    for item, (code, quantity, price, delivery) in zip(items, parsed, strict=True):
        if item.get("id") is not None:
            line = existing[item["id"]]
            line.buyer_item_code = code
            line.quantity = quantity
            line.unit_price_amount = price
            line.requested_delivery_date = delivery
            line.updated_by_id = actor_id
        else:
            intake.last_line_no += 1
            session.add(
                OrderIntakeLine(
                    intake_id=intake.id,
                    currency=intake.currency,
                    line_no=intake.last_line_no,
                    buyer_item_code=code,
                    sku_id=None,
                    quantity=quantity,
                    unit_price_amount=price,
                    requested_delivery_date=delivery,
                    created_by_id=actor_id,
                    updated_by_id=actor_id,
                )
            )
    session.flush()
    _apply_resolution(session, intake, live_lines(session, intake.id), actor_id=actor_id)


def resolve_intake(
    *, actor: AuthenticatedUser, idempotency_key: str, intake_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """전 라인 품번을 **지금 매핑으로 재해석**해 저장본을 갱신한다(품번 등록 유도 뒤의 '다시 확인'). 바뀐 것이 있으면 헤더 version +1, 없으면 불변."""
    require_intake_writer(actor)
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=RESOLVE_ENDPOINT,
            key=idempotency_key,
            request_body={"intake_id": intake_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        intake = lock_intake(session, intake_id, version=version)
        require_pending(intake, intake.status)
        if _apply_resolution(session, intake, live_lines(session, intake.id), actor_id=actor.id):
            intake.updated_by_id = actor.id
            editing.bump_header_version(intake)
        session.flush()
        session.refresh(intake)
        body = jsonable(detail_body(session, intake))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def reject_intake(
    *, actor: AuthenticatedUser, idempotency_key: str, intake_id: int, version: int, reason: str
) -> tuple[int, dict[str, Any]]:
    """거부 — 사유 필수(5~500자). 종결이며 행·스냅샷·사유·행위자는 영구 보존된다(삭제 경로 없음). `PENDING→REJECTED`의 유일한 호출처다."""
    require_intake_writer(actor)
    problem = reason_problem(reason)
    if problem is not None:
        raise invalid("reason", problem)
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REJECT_ENDPOINT,
            key=idempotency_key,
            request_body={"intake_id": intake_id, "version": version, "reason": reason},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        intake = lock_intake(session, intake_id, version=version)
        require_pending(intake, machine.REJECTED)
        machine.apply_intake_transition(
            session, intake, machine.REJECTED, actor_id=actor.id, reason=reason
        )
        session.refresh(intake)
        body = jsonable(detail_body(session, intake))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


__all__ = [
    "create_manual_intake",
    "detail_body",
    "get_intake",
    "list_intakes",
    "register_intake",
    "reject_intake",
    "resolve_intake",
    "update_intake",
]
