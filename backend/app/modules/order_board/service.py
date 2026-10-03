"""오더 보드 조회·CSV (S3-1 PR-15a / design-D D6 / ADR-0066).

■ `get_order_board` — **비-Page 단일 객체**: 고정 4열(인테이크 대기·수주 접수·수주 보류·수주 확정), 열마다 `total`·`has_more`·카드 최대 50장. 함수명에 `list_` 접두를 쓰지 않는다
  (Page 봉투 스캔은 `list_` 접두만 본다 — 의도된 예외이며 열 상한·건수는 별도 테스트가 고정한다). 열 '더 보기'는 `list_board_items`(Page)가 맡는다.
■ 읽기 트랜잭션은 첫 문장에서 `REPEATABLE READ, READ ONLY`(`read_snapshot`) — 6쿼리가 한 스냅샷을 본다(카드 중복·`total < len(items)` 방지).
■ **쿼리 수는 카드 수와 무관한 상수**다: 열 건수 2쿼리(인테이크 COUNT·SO 상태별 GROUP BY) + 열별 카드 4쿼리. 라인 수·인테이크 합계는 상관 서브쿼리, 담당자·거래처 이름은
  조인 — 카드마다 추가 질의가 없다(§18.4 N+1 금지). 게이트·여신은 평가하지 않는다(카드에 그 필드가 없다).
■ 보드는 **조회만** 한다(상태 대입 0). 취소 SO·확정/거부 인테이크는 보드에 없다. 원가·마진·매입가 열은 어느 쿼리도 읽지 않는다.
■ CSV(`export_rows`)는 같은 필터·같은 정렬로 `core.csv_export.render_csv` 통로(BOM·수식 이스케이프)를 지나며 최대 50,000행이다(초과 422 — 조용한 잘라내기 금지).
  감사 기록은 하지 않는다(목록 CSV 선례 — 관찰 D-D14).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Numeric, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.db.uow import in_unit_of_work, unit_of_work
from app.core.time import KST, to_kst, today_kst, utcnow
from app.modules.identity.models import User
from app.modules.order_board.constants import (
    BOARD_STAGE_STATUSES,
    COLUMN_LIMIT,
    EXPORT_MAX_ROWS,
    INTAKE_STAGE_STATUS,
    NEWEST_FIRST_STAGES,
    STAGE_LABELS_KO,
    STAGE_ORDER,
    BoardStage,
    CardKind,
)
from app.modules.order_board.schemas import BoardFilter
from app.modules.order_intake.models import OrderIntake, OrderIntakeLine
from app.modules.partners.models import Partner
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine
from app.modules.trade_docs.validation import invalid
from app.modules.trade_docs.views import money_text

INTAKE_REF_PREFIX = "IN-"

#: 보드 CSV 헤더 — 원가·마진·게이트·여신 열이 없다(테스트가 고정한다).
EXPORT_HEADER: tuple[str, ...] = (
    "구분",
    "문서번호",
    "거래처",
    "바이어PO번호",
    "통화",
    "합계금액",
    "라인수",
    "담당자",
    "접수일(KST)",
    "납기요청 최소일",
    "상태",
)

_KIND_LABEL_KO = {CardKind.INTAKE: "인테이크", CardKind.SO: "수주"}


# ── 읽기 스냅샷 ────────────────────────────────────────────────────────────────

SNAPSHOT_STATEMENT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


@contextmanager
def read_snapshot() -> Iterator[Session]:
    """보드·드릴다운·CSV 읽기 트랜잭션 — **첫 문장**에서 `REPEATABLE READ, READ ONLY`로 바꿔 열 건수·열 카드 6쿼리가 **한 스냅샷**을 본다.

    READ COMMITTED면 문장마다 스냅샷이 달라, 쿼리 사이에 커밋된 확정이 같은 SO를 '접수'·'확정' 두 열에 동시에 싣거나 `total < len(items)`를 만든다.
    `SET TRANSACTION`은 트랜잭션의 첫 문장이어야 하므로 바깥 UoW에 합류해서는 안 된다(합류면 이미 다른 문장이 실행됐을 수 있다 — 프로그래밍 오류로 멈춘다).
    """
    if in_unit_of_work():
        raise RuntimeError(
            "보드 읽기는 독립 트랜잭션이어야 합니다 — 열린 트랜잭션 안에서 부를 수 없습니다."
        )
    with unit_of_work() as uow:
        uow.session.execute(text(SNAPSHOT_STATEMENT))
        yield uow.session


# ── 필터 → 조건 ───────────────────────────────────────────────────────────────


def _kst_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=KST)


def _created_bounds(f: BoardFilter) -> list[tuple[str, datetime]]:
    """KST 날짜 범위(양끝 포함) → UTC 시각 경계 [(연산, 시각)]. 끝은 다음 날 KST 0시 미만이다."""
    bounds: list[tuple[str, datetime]] = []
    if f.created_from is not None:
        bounds.append((">=", _kst_day_start(f.created_from)))
    if f.created_to is not None:
        bounds.append(("<", _kst_day_start(f.created_to + timedelta(days=1))))
    return bounds


def _apply_bounds(column: Any, f: BoardFilter) -> list[Any]:
    out: list[Any] = []
    for op, moment in _created_bounds(f):
        out.append(column >= moment if op == ">=" else column < moment)
    return out


def _intake_conditions(f: BoardFilter) -> list[Any]:
    conditions: list[Any] = [
        OrderIntake.deleted_at.is_(None),
        OrderIntake.status == INTAKE_STAGE_STATUS,
    ]
    if f.buyer_partner_id is not None:
        conditions.append(OrderIntake.buyer_partner_id == f.buyer_partner_id)
    if f.assignee_id is not None:
        conditions.append(OrderIntake.assignee_id == f.assignee_id)
    if f.currency is not None:
        conditions.append(OrderIntake.currency == f.currency)
    if f.dest_market_code is not None:
        conditions.append(OrderIntake.dest_market_code == f.dest_market_code)
    if f.q is not None:
        conditions.append(
            or_(
                OrderIntake.buyer_po_no.icontains(f.q, autoescape=True),
                OrderIntake.buyer_partner_id.in_(
                    select(Partner.id).where(
                        or_(
                            Partner.name_ko.icontains(f.q, autoescape=True),
                            Partner.name_en.icontains(f.q, autoescape=True),
                        )
                    )
                ),
            )
        )
    conditions.extend(_apply_bounds(OrderIntake.created_at, f))
    return conditions


def _so_conditions(f: BoardFilter, statuses: tuple[str, ...]) -> list[Any]:
    conditions: list[Any] = [SalesOrder.deleted_at.is_(None), SalesOrder.status.in_(statuses)]
    if f.buyer_partner_id is not None:
        conditions.append(SalesOrder.buyer_partner_id == f.buyer_partner_id)
    if f.assignee_id is not None:
        conditions.append(SalesOrder.assignee_id == f.assignee_id)
    if f.currency is not None:
        conditions.append(SalesOrder.currency == f.currency)
    if f.dest_market_code is not None:
        conditions.append(SalesOrder.dest_market_code == f.dest_market_code)
    if f.q is not None:
        conditions.append(
            or_(
                SalesOrder.doc_number.icontains(f.q, autoescape=True),
                SalesOrder.buyer_name.icontains(f.q, autoescape=True),
                SalesOrder.buyer_po_no.icontains(f.q, autoescape=True),
            )
        )
    conditions.extend(_apply_bounds(SalesOrder.created_at, f))
    return conditions


def _all_so_statuses() -> tuple[str, ...]:
    return tuple(status for stage in STAGE_ORDER for status in BOARD_STAGE_STATUSES.get(stage, ()))


# ── 카드 쿼리 ─────────────────────────────────────────────────────────────────


def _intake_line_count() -> Any:
    return (
        select(func.count())
        .where(OrderIntakeLine.intake_id == OrderIntake.id, OrderIntakeLine.deleted_at.is_(None))
        .correlate(OrderIntake)
        .scalar_subquery()
    )


def _intake_total() -> Any:
    return (
        select(
            func.coalesce(
                func.sum(
                    cast(OrderIntakeLine.quantity, Numeric) * OrderIntakeLine.unit_price_amount
                ),
                0,
            )
        )
        .where(OrderIntakeLine.intake_id == OrderIntake.id, OrderIntakeLine.deleted_at.is_(None))
        .correlate(OrderIntake)
        .scalar_subquery()
    )


def _intake_min_delivery() -> Any:
    return (
        select(func.min(OrderIntakeLine.requested_delivery_date))
        .where(OrderIntakeLine.intake_id == OrderIntake.id, OrderIntakeLine.deleted_at.is_(None))
        .correlate(OrderIntake)
        .scalar_subquery()
    )


def _so_line_count() -> Any:
    return (
        select(func.count())
        .where(SalesOrderLine.so_id == SalesOrder.id, SalesOrderLine.deleted_at.is_(None))
        .correlate(SalesOrder)
        .scalar_subquery()
    )


def _so_min_delivery() -> Any:
    return (
        select(func.min(SalesOrderLine.requested_delivery_date))
        .where(SalesOrderLine.so_id == SalesOrder.id, SalesOrderLine.deleted_at.is_(None))
        .correlate(SalesOrder)
        .scalar_subquery()
    )


def _intake_select(*, with_min_delivery: bool = False) -> Any:
    buyer_name = func.coalesce(func.nullif(Partner.name_en, ""), Partner.name_ko)
    columns: list[Any] = [
        OrderIntake.id,
        OrderIntake.buyer_partner_id,
        buyer_name.label("buyer_name"),
        OrderIntake.buyer_po_no,
        _intake_line_count().label("line_count"),
        _intake_total().label("total_amount"),
        OrderIntake.currency,
        OrderIntake.assignee_id,
        User.display_name.label("assignee_name"),
        OrderIntake.created_at,
        OrderIntake.updated_at,
        OrderIntake.version,
    ]
    if with_min_delivery:
        columns.append(_intake_min_delivery().label("min_delivery"))
    return (
        select(*columns)
        .join(Partner, Partner.id == OrderIntake.buyer_partner_id)
        .outerjoin(User, User.id == OrderIntake.assignee_id)
    )


def _so_select(*, with_min_delivery: bool = False) -> Any:
    columns: list[Any] = [
        SalesOrder.id,
        SalesOrder.doc_number,
        SalesOrder.buyer_partner_id,
        SalesOrder.buyer_name,
        SalesOrder.buyer_po_no,
        _so_line_count().label("line_count"),
        SalesOrder.total_amount,
        SalesOrder.currency,
        SalesOrder.assignee_id,
        User.display_name.label("assignee_name"),
        SalesOrder.created_at,
        SalesOrder.updated_at,
        SalesOrder.version,
    ]
    if with_min_delivery:
        columns.append(_so_min_delivery().label("min_delivery"))
    return select(*columns).outerjoin(User, User.id == SalesOrder.assignee_id)


def _so_order(stage: BoardStage) -> tuple[Any, ...]:
    """열 정렬 — 확정 열은 최근 확정 순, 나머지는 접수(생성) 오래된 순. id는 동률 깨기(페이지 안정성)."""
    if stage in NEWEST_FIRST_STAGES:
        return (SalesOrder.confirmed_at.desc().nulls_last(), SalesOrder.id.desc())
    return (SalesOrder.created_at.asc(), SalesOrder.id.asc())


_INTAKE_ORDER = (OrderIntake.created_at.asc(), OrderIntake.id.asc())


def _age_days(created_at: datetime, today: date) -> int:
    return max((today - to_kst(created_at).date()).days, 0)


def _intake_card(row: Any, today: date) -> dict[str, Any]:
    total = int(row.total_amount)
    return {
        "kind": CardKind.INTAKE.value,
        "id": row.id,
        "ref_label": f"{INTAKE_REF_PREFIX}{row.id}",
        "buyer_partner_id": row.buyer_partner_id,
        "buyer_name": row.buyer_name,
        "buyer_po_no": row.buyer_po_no,
        "line_count": int(row.line_count),
        "total_amount": total,
        "total_text": money_text(total, row.currency) or "0",
        "currency": row.currency,
        "age_days": _age_days(row.created_at, today),
        "assignee_id": row.assignee_id,
        "assignee_name": row.assignee_name,
        "updated_at": row.updated_at,
        "version": row.version,
    }


def _so_card(row: Any, today: date) -> dict[str, Any]:
    total = int(row.total_amount)
    return {
        "kind": CardKind.SO.value,
        "id": row.id,
        "ref_label": row.doc_number,
        "buyer_partner_id": row.buyer_partner_id,
        "buyer_name": row.buyer_name,
        "buyer_po_no": row.buyer_po_no,
        "line_count": int(row.line_count),
        "total_amount": total,
        "total_text": money_text(total, row.currency) or "0",
        "currency": row.currency,
        "age_days": _age_days(row.created_at, today),
        "assignee_id": row.assignee_id,
        "assignee_name": row.assignee_name,
        "updated_at": row.updated_at,
        "version": row.version,
    }


def _intake_cards(
    session: Session, f: BoardFilter, *, offset: int, limit: int, today: date
) -> list[dict[str, Any]]:
    rows = session.execute(
        _intake_select()
        .where(*_intake_conditions(f))
        .order_by(*_INTAKE_ORDER)
        .offset(offset)
        .limit(limit)
    ).all()
    return [_intake_card(row, today) for row in rows]


def _so_cards(
    session: Session,
    f: BoardFilter,
    stage: BoardStage,
    *,
    offset: int,
    limit: int,
    today: date,
) -> list[dict[str, Any]]:
    rows = session.execute(
        _so_select()
        .where(*_so_conditions(f, BOARD_STAGE_STATUSES[stage]))
        .order_by(*_so_order(stage))
        .offset(offset)
        .limit(limit)
    ).all()
    return [_so_card(row, today) for row in rows]


def _intake_total_count(session: Session, f: BoardFilter) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(OrderIntake).where(*_intake_conditions(f))
        ).scalar_one()
    )


def _so_counts_by_stage(session: Session, f: BoardFilter) -> dict[BoardStage, int]:
    """SO 열 건수를 **한 쿼리**(상태별 GROUP BY)로 — 상태→열 매핑은 `BOARD_STAGE_STATUSES` 하나."""
    by_status = {
        str(status): int(n)
        for status, n in session.execute(
            select(SalesOrder.status, func.count())
            .where(*_so_conditions(f, _all_so_statuses()))
            .group_by(SalesOrder.status)
        )
    }
    return {
        stage: sum(by_status.get(status, 0) for status in statuses)
        for stage, statuses in BOARD_STAGE_STATUSES.items()
    }


def _stage_totals(session: Session, f: BoardFilter) -> dict[BoardStage, int]:
    totals = _so_counts_by_stage(session, f)
    totals[BoardStage.INTAKE_PENDING] = _intake_total_count(session, f)
    return totals


# ── 공개 함수 ─────────────────────────────────────────────────────────────────


def get_order_board(f: BoardFilter) -> dict[str, Any]:
    """보드 전체 — 고정 4열 × (전체 건수·`has_more`·카드 ≤ `COLUMN_LIMIT`). 쿼리 수 상수(6)."""
    generated_at = utcnow()
    today = today_kst()
    with read_snapshot() as session:
        totals = _stage_totals(session, f)
        columns: list[dict[str, Any]] = []
        for stage in STAGE_ORDER:
            if stage is BoardStage.INTAKE_PENDING:
                items = _intake_cards(session, f, offset=0, limit=COLUMN_LIMIT, today=today)
            else:
                items = _so_cards(session, f, stage, offset=0, limit=COLUMN_LIMIT, today=today)
            total = totals[stage]
            columns.append(
                {
                    "stage": stage.value,
                    "label_ko": STAGE_LABELS_KO[stage],
                    "total": total,
                    "has_more": total > len(items),
                    "items": items,
                }
            )
        return {"columns": columns, "generated_at": generated_at}


def list_board_items(
    f: BoardFilter, stage: BoardStage, *, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """한 열의 페이지(드릴다운 — Page 봉투). 정렬은 보드 열과 같다."""
    today = today_kst()
    with read_snapshot() as session:
        if stage is BoardStage.INTAKE_PENDING:
            total = _intake_total_count(session, f)
            items = _intake_cards(session, f, offset=offset, limit=limit, today=today)
        else:
            total = int(
                session.execute(
                    select(func.count())
                    .select_from(SalesOrder)
                    .where(*_so_conditions(f, BOARD_STAGE_STATUSES[stage]))
                ).scalar_one()
            )
            items = _so_cards(session, f, stage, offset=offset, limit=limit, today=today)
        return items, total


def _csv_row(kind: CardKind, row: Any, stage: BoardStage) -> tuple[Any, ...]:
    ref = f"{INTAKE_REF_PREFIX}{row.id}" if kind is CardKind.INTAKE else row.doc_number
    total = int(row.total_amount)
    return (
        _KIND_LABEL_KO[kind],
        ref,
        row.buyer_name or "",
        row.buyer_po_no or "",
        row.currency,
        money_text(total, row.currency) or "0",
        int(row.line_count),
        row.assignee_name or "",
        to_kst(row.created_at).date().isoformat(),
        row.min_delivery.isoformat() if row.min_delivery is not None else "",
        STAGE_LABELS_KO[stage],
    )


def export_rows(f: BoardFilter, stage: BoardStage | None = None) -> list[tuple[Any, ...]]:
    """보드 CSV 행 — 같은 필터·열 순서·열 정렬. 상한 초과는 422(조건을 좁히게 한다 — 조용한 잘라내기 금지)."""
    stages = (stage,) if stage is not None else STAGE_ORDER
    with read_snapshot() as session:
        totals = _stage_totals(session, f)
        count = sum(totals[s] for s in stages)
        if count > EXPORT_MAX_ROWS:
            raise invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({count:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        out: list[tuple[Any, ...]] = []
        for s in stages:
            if s is BoardStage.INTAKE_PENDING:
                rows = session.execute(
                    _intake_select(with_min_delivery=True)
                    .where(*_intake_conditions(f))
                    .order_by(*_INTAKE_ORDER)
                ).all()
                out.extend(_csv_row(CardKind.INTAKE, row, s) for row in rows)
            else:
                rows = session.execute(
                    _so_select(with_min_delivery=True)
                    .where(*_so_conditions(f, BOARD_STAGE_STATUSES[s]))
                    .order_by(*_so_order(s))
                ).all()
                out.extend(_csv_row(CardKind.SO, row, s) for row in rows)
        return out
