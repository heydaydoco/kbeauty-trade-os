"""선적 마일스톤 보드·변경 이력·통관 기록 조립 — 읽기 시점 계산(S3-2 PR-4a / ADR-0080·0082·0083 / design-D D3 / design-B B3·B7·B8·B12).

■ **파생값은 저장하지 않는다** — 적재기한·대금만기·제시기한은 매 조회마다 `trade_docs.schedule` 순수 함수가 다시 계산한다(재계산 = 읽기 결과의
  변화, 쓰기 연쇄 0). 기일 스캔(PR-6)도 같은 함수를 부른다(정의 이원화 금지).
■ **수리일 = 통관 기록 유일 원천**(X-02): 신고수리 행의 실적 = 구분 일치·살아 있는 통관 기록 `MIN(accepted_on)`(`customs_clearance`),
  미수리 1건↑이면 `customs_state=PARTIAL`+미수리 건수(산식은 MIN 유지 — R-06).
■ **휴일 경고는 ETA(도착국)만**(R-09) — `holidays.calc.holiday_flag` 3값(HOLIDAY·CLEAR·UNVERIFIED — 미선언 ≠ 평일). 날짜는 옮기지 않는다(ADR-0082).
■ 시각형(서류마감·Cargo Closing): D-N 기준일 = `cutoff_scan_date`(min(현지, KST)), **도과 = 현재 UTC > 유효 시각**(R-20), 현지 날짜 별도(R-25).
■ 보드는 **고정 질의 수**다(마일스톤 1·롤오버 통계 1·통관 1·ORDER_DATE 앵커 0~1·휴일 0~2) — 행 수와 무관(N+1 0).
■ L/C 운영 경로: `lc_terms`(S3-3) 미공급이라 대금만기·제시기한은 UNKNOWN `LC_TERMS_NOT_REGISTERED`(ADR-0081 — 대체 금지).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, column, exists, func, select, table
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.time import today_kst, utcnow
from app.modules.collaboration.models import CommLog
from app.modules.holidays import calc as holiday_calc
from app.modules.holidays import service as holidays
from app.modules.identity.models import User
from app.modules.partners.models import Partner
from app.modules.sales_orders.models import SalesOrder
from app.modules.shipments import service as shipments
from app.modules.shipments.models import (
    CustomsRecord,
    Milestone,
    MilestoneChange,
    MilestoneChangeNotice,
    Shipment,
)
from app.modules.trade_docs import schedule
from app.modules.trade_docs.constants import (
    DATETIME_MILESTONES,
    DERIVED_MILESTONES,
    SHIPMENT_BOARD_ORDER,
    SHIPMENT_MILESTONES_BY_KIND,
    BalanceAnchor,
    MilestoneChangeKind,
    MilestoneType,
    PaymentType,
    ShipmentKind,
)

#: 마일스톤·통관·통보를 쓸 수 있는 선적 상태 — S3-2 활성(계획·출고지시). 취소는 409(마일스톤 OWNER_NOT_ACTIVE·통관 NOT_ACTIVE).
#: S4-2가 피킹~종결 엣지를 열면 이 집합을 함께 재판정한다(인계 계약).
RECORD_EDITABLE_STATES = frozenset({"PLANNED", "RELEASE_ORDERED"})

#: PO는 원가 열을 품은 모델이라 임포트하지 않고 테이블 이름으로 `frozen_at`만 읽는다(원가 9채널 봉쇄 — ADR-0024, shipment_view 선례).
_PURCHASE_ORDERS = table(
    "purchase_orders", column("id", Integer), column("frozen_at", DateTime(timezone=True))
)


@dataclass(frozen=True, slots=True)
class _Value:
    """한 칸의 값 — 날짜형(on) 또는 시각형(at, tz)."""

    on: date | None
    at: datetime | None
    tz: str | None


def _value_body(value: _Value) -> Any:
    if value.at is not None:
        return {"at_utc": value.at.astimezone(UTC).isoformat(), "tz": value.tz}
    if value.on is not None:
        return value.on.isoformat()
    return None


def _effective_date(planned: date | None, actual: date | None) -> schedule.DateValue | None:
    return schedule.effective(planned, actual)


def _due_body(result: schedule.DueResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "value": result.value.isoformat() if result.value else None,
        "basis": result.basis.value if result.basis else None,
        "reason_code": result.reason.value if result.reason else None,
    }


def _rollover_stats(session: Session, milestone_ids: list[int]) -> dict[int, tuple[int, int]]:
    """마일스톤 id → (롤오버 횟수, 통보 미연결 롤오버 수) — 질의 1회."""
    if not milestone_ids:
        return {}
    unnotified = ~exists().where(MilestoneChangeNotice.change_id == MilestoneChange.id)
    return {
        int(r[0]): (int(r[1]), int(r[2]))
        for r in session.execute(
            select(
                MilestoneChange.milestone_id,
                func.count(),
                func.count().filter(unnotified),
            )
            .where(
                MilestoneChange.milestone_id.in_(milestone_ids),
                MilestoneChange.change_kind == MilestoneChangeKind.PLAN_CHANGED.value,
            )
            .group_by(MilestoneChange.milestone_id)
        ).all()
    }


def _order_at(session: Session, row: Shipment) -> datetime | None:
    """ORDER_DATE 앵커 원천 — 수출 = SO `confirmed_at`, 수입 = PO `frozen_at`(R-29 — 사람 입력 `doc_date` 미사용)."""
    if row.balance_anchor != BalanceAnchor.ORDER_DATE.value:
        return None
    if row.so_id is not None:
        found: datetime | None = session.execute(
            select(SalesOrder.confirmed_at).where(SalesOrder.id == row.so_id)
        ).scalar_one_or_none()
        return found
    value = session.execute(
        select(_PURCHASE_ORDERS.c.frozen_at).where(_PURCHASE_ORDERS.c.id == row.po_id)
    ).scalar_one_or_none()
    return value if isinstance(value, datetime) else None


@dataclass(frozen=True, slots=True)
class Assembled:
    board: dict[str, Any]
    customs_accepted: list[date | None]


def assemble(session: Session, row: Shipment) -> Assembled:
    """선적 1건의 마일스톤 보드 + 통관 수리일 목록(상세의 통관 요약이 재사용 — 질의 재실행 0)."""
    today = today_kst()
    now = utcnow()
    kind_set = SHIPMENT_MILESTONES_BY_KIND.get(row.shipment_kind, frozenset())
    stored = {
        m.milestone_type: m
        for m in session.execute(
            select(Milestone).where(Milestone.shipment_id == row.id, Milestone.deleted_at.is_(None))
        ).scalars()
    }
    stats = _rollover_stats(session, sorted(m.id for m in stored.values()))
    # 통관 기록 — 선적 구분과 같은 신고 구분만(KIND_MISMATCH 가드가 다른 구분을 막지만 파생은 문면대로 '구분 일치'로 거른다)
    accepted = shipments.live_customs_accepted(session, row.id, row.shipment_kind)
    clearance = schedule.customs_clearance(accepted)

    etd_row = stored.get(MilestoneType.ETD.value)
    bl_row = stored.get(MilestoneType.BL_ISSUED.value)
    eta_row = stored.get(MilestoneType.ETA.value)
    etd = _effective_date(etd_row.planned_on, etd_row.actual_on) if etd_row else None
    bl = _effective_date(bl_row.planned_on, bl_row.actual_on) if bl_row else None
    eta = _effective_date(eta_row.planned_on, eta_row.actual_on) if eta_row else None
    etd_actual = etd_row.actual_on if etd_row else None
    bl_actual = bl_row.actual_on if bl_row else None
    eta_applicable = MilestoneType.ETA.value in kind_set
    lookup = (
        holidays.lookup_days(session, row.dest_country_code, {eta.value})
        if eta is not None and eta_applicable
        else None
    )
    is_lc = row.payment_type == PaymentType.LC.value
    rows: list[dict[str, Any]] = []
    holiday_count = 0
    unverified_count = 0
    for milestone_type in SHIPMENT_BOARD_ORDER:
        if milestone_type in DERIVED_MILESTONES:
            rows.append(
                _derived_row(
                    session,
                    row,
                    milestone_type,
                    today=today,
                    clearance=clearance,
                    etd=etd,
                    bl=bl,
                    eta=eta,
                    etd_actual=etd_actual,
                    bl_actual=bl_actual,
                    is_lc=is_lc,
                )
            )
            continue
        body = _stored_row(
            stored.get(milestone_type),
            milestone_type,
            applicable=milestone_type in kind_set,
            today=today,
            now=now,
            clearance=clearance,
            stats=stats,
        )
        if milestone_type == MilestoneType.ETA.value and eta_applicable and eta is not None:
            assert lookup is not None
            flag, name = holiday_calc.holiday_flag(
                eta.value, row.dest_country_code, lookup.covered_years, lookup.holidays
            )
            body["holiday"] = {"flag": flag.value, "country": row.dest_country_code, "name": name}
            holiday_count += flag is holiday_calc.HolidayFlag.HOLIDAY
            unverified_count += flag is holiday_calc.HolidayFlag.UNVERIFIED
            if etd is not None and eta.value < etd.value:
                body["order_warning"] = "ETA_BEFORE_ETD"  # 경고만(날짜변경선 — 차단 0, B8 ⑤)
        rows.append(body)
    board = {
        "today_kst": today.isoformat(),
        "holiday_summary": {"holiday": holiday_count, "unverified": unverified_count},
        "rows": rows,
    }
    return Assembled(board, accepted)


def _empty_row(milestone_type: str, *, kind: str, applicable: bool) -> dict[str, Any]:
    return {
        "milestone_type": milestone_type,
        "kind": kind,
        "value_shape": "DATETIME" if milestone_type in DATETIME_MILESTONES else "DATE",
        "applicable": applicable,
        "planned": None,
        "actual": None,
        "effective": None,
        "derived": None,
        "scan_date": None,
        "local_date": None,
        "customs_state": None,
        "customs_pending_count": None,
        "days_left": None,
        "is_overdue": None,
        "fulfilment": None,
        "holiday": None,
        "rollover_count": 0,
        "unnotified_rollovers": 0,
        "order_warning": None,
        "milestone_id": None,
        "version": None,
        "input_source": None,
    }


def _stored_row(
    m: Milestone | None,
    milestone_type: str,
    *,
    applicable: bool,
    today: date,
    now: datetime,
    clearance: schedule.ClearanceSummary,
    stats: dict[int, tuple[int, int]],
) -> dict[str, Any]:
    body = _empty_row(milestone_type, kind="STORED", applicable=applicable)
    body["input_source"] = "MILESTONE"
    if m is not None:
        rollovers, unnotified = stats.get(m.id, (0, 0))
        body.update(
            {
                "milestone_id": m.id,
                "version": m.version,
                "rollover_count": rollovers,
                "unnotified_rollovers": unnotified,
            }
        )
    if milestone_type in DATETIME_MILESTONES:
        planned = _Value(None, m.planned_at if m else None, m.tz if m else None)
        actual = _Value(None, m.actual_at if m else None, m.tz if m else None)
        body["planned"] = _value_body(planned)
        body["actual"] = _value_body(actual)
        instant = actual.at or planned.at
        if instant is not None and m is not None and m.tz:
            basis = "ACTUAL" if actual.at is not None else "PLANNED"
            body["effective"] = {"value": instant.astimezone(UTC).isoformat(), "basis": basis}
            scan_date = schedule.cutoff_scan_date(instant, m.tz)
            body["scan_date"] = scan_date.isoformat()
            body["local_date"] = instant.astimezone(schedule.zone(m.tz)).date().isoformat()
            if actual.at is None:
                # 도과 = UTC 시각 비교(R-20 — 날짜 비교면 기한 전 최대 ~16시간 '도과' 오표시)
                overdue = now > instant
                days_left = (scan_date - today).days  # D-N 문턱 = 이른 날짜(B3 ④ — 더 일찍 경고)
                if days_left < 0 and not overdue:
                    days_left = 0  # 기준일은 지났지만 기한 시각 전 — 'D-day'(도과 표시는 is_overdue만 근거, R-20)
                body["days_left"] = days_left
                body["is_overdue"] = overdue
        return body
    planned_on = m.planned_on if m else None
    if milestone_type == MilestoneType.CUSTOMS_CLEARED.value:
        # 통관 기록 MIN(accepted_on) — 마일스톤 실적 열은 CHECK로 비어 있다(X-02)
        actual_on = clearance.cleared_on
        body["customs_state"] = clearance.state.value
        body["customs_pending_count"] = clearance.pending_count
        body["input_source"] = "CUSTOMS_RECORD"  # 실적 입력처 = 통관 기록(계획은 마일스톤 행)
    else:
        actual_on = m.actual_on if m else None
    body["planned"] = planned_on.isoformat() if planned_on else None
    body["actual"] = actual_on.isoformat() if actual_on else None
    effective = _effective_date(planned_on, actual_on)
    if effective is not None:
        body["effective"] = {"value": effective.value.isoformat(), "basis": effective.basis.value}
        if actual_on is None:
            body["days_left"] = (effective.value - today).days
            body["is_overdue"] = today > effective.value
    return body


def _derived_row(
    session: Session,
    row: Shipment,
    milestone_type: str,
    *,
    today: date,
    clearance: schedule.ClearanceSummary,
    etd: schedule.DateValue | None,
    bl: schedule.DateValue | None,
    eta: schedule.DateValue | None,
    etd_actual: date | None,
    bl_actual: date | None,
    is_lc: bool,
) -> dict[str, Any]:
    if milestone_type == MilestoneType.LOADING_DEADLINE.value:
        applicable = row.shipment_kind == ShipmentKind.EXPORT.value
        body = _empty_row(milestone_type, kind="DERIVED", applicable=applicable)
        if not applicable:
            return body
        deadline = schedule.loading_deadline(clearance.cleared_on)  # 계획 수리일 미사용(B7)
        state = schedule.loading_fulfilment(deadline, etd_actual, bl_actual, today)
        body["derived"] = _due_body(deadline)
        body["fulfilment"] = state.value
        if deadline.value is not None and state in (
            schedule.LoadingState.OPEN,
            schedule.LoadingState.OVERDUE,
        ):
            body["days_left"] = (deadline.value - today).days
            body["is_overdue"] = state is schedule.LoadingState.OVERDUE
        return body
    if milestone_type == MilestoneType.PRESENTATION_DEADLINE.value:
        body = _empty_row(milestone_type, kind="DERIVED", applicable=is_lc)
        if is_lc:
            # 운영 L/C — 유효기일·제시기간 원천(`lc_terms`)이 S3-3이라 산정 불가(B/L+21로 대체 금지 — ADR-0081)
            body["derived"] = _due_body(
                schedule.DueResult.unknown(schedule.DueReason.LC_TERMS_NOT_REGISTERED)
            )
        return body
    body = _empty_row(milestone_type, kind="DERIVED", applicable=True)  # 대금만기 — 수출·수입 공통
    context = schedule.AnchorContext(order_at=_order_at(session, row), etd=etd, bl=bl, eta=eta)
    # 결제조건 = 선적 헤더 사본(X-01), L/C 입력 None(운영 UNKNOWN — ADR-0081)
    due = schedule.payment_due(row, context, None)
    body["derived"] = _due_body(due)
    if due.value is not None:
        # 도과 판정은 입금 충족 신호(S3-3) 전이라 하지 않는다(is_overdue = null)
        body["days_left"] = (due.value - today).days
    return body


def board_body(session: Session, row: Shipment) -> dict[str, Any]:
    return assemble(session, row).board


def customs_summary(accepted: list[date | None]) -> dict[str, Any]:
    cleared = sorted(value for value in accepted if value is not None)
    return {
        "live_count": len(accepted),
        "pending_count": len(accepted) - len(cleared),
        "latest_accepted_on": cleared[-1].isoformat() if cleared else None,
    }


def get_board(shipment_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return board_body(uow.session, shipments.require_shipment(uow.session, shipment_id))


# ── 변경 이력(M5) ────────────────────────────────────────────────────────────


def _notices(session: Session, change_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """변경 id → 연결된 통보 기록(comm_logs SHIPMENT) — 질의 1회(N+1 0)."""
    if not change_ids:
        return {}
    found: dict[int, list[dict[str, Any]]] = {}
    for notice, log, partner_name in session.execute(
        select(MilestoneChangeNotice, CommLog, Partner.name_ko)
        .join(CommLog, CommLog.id == MilestoneChangeNotice.comm_log_id)
        .outerjoin(Partner, Partner.id == CommLog.partner_id)
        .where(MilestoneChangeNotice.change_id.in_(change_ids))
        .order_by(MilestoneChangeNotice.id)
    ).all():
        found.setdefault(notice.change_id, []).append(
            {
                "comm_log_id": log.id,
                "occurred_on": log.occurred_on.isoformat(),
                "summary": log.summary,
                "partner_id": log.partner_id,
                "partner_name": partner_name,
                "actor_user_id": notice.actor_user_id,
                "created_at": notice.created_at.isoformat(),
            }
        )
    return found


def _change_body(
    change: MilestoneChange,
    milestone_type: str,
    actor_name: str | None,
    notices: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": change.id,
        "milestone_id": change.milestone_id,
        "milestone_type": milestone_type,
        "change_kind": change.change_kind,
        "old": _value_body(_Value(change.old_on, change.old_at, change.old_tz)),
        "new": _value_body(_Value(change.new_on, change.new_at, change.new_tz)),
        "reason": change.reason,
        "actor_user_id": change.actor_user_id,
        "actor_name": actor_name,
        "created_at": change.created_at.isoformat(),
        "notices": notices,
    }


def change_body(session: Session, change: MilestoneChange) -> dict[str, Any]:
    """변경 1건 + 통보 목록(M6 응답)."""
    milestone_type, actor_name = session.execute(
        select(Milestone.milestone_type, User.display_name)
        .select_from(Milestone)
        .outerjoin(User, User.id == change.actor_user_id)
        .where(Milestone.id == change.milestone_id)
    ).one()
    return _change_body(
        change, str(milestone_type), actor_name, _notices(session, [change.id]).get(change.id, [])
    )


def list_changes(
    *,
    shipment_id: int,
    milestone_type: str | None,
    change_kind: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """선적의 마일스톤 변경 이력(Page — 최신순). 행마다 통보 기록 내장(변경 1건의 통보는 소수)."""
    with unit_of_work() as uow:
        session = uow.session
        shipments.require_shipment(session, shipment_id)
        conditions = [Milestone.shipment_id == shipment_id]
        if milestone_type is not None:
            conditions.append(Milestone.milestone_type == milestone_type)
        if change_kind is not None:
            conditions.append(MilestoneChange.change_kind == change_kind)
        total = session.execute(
            select(func.count())
            .select_from(MilestoneChange)
            .join(Milestone, Milestone.id == MilestoneChange.milestone_id)
            .where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(MilestoneChange, Milestone.milestone_type, User.display_name)
            .join(Milestone, Milestone.id == MilestoneChange.milestone_id)
            .outerjoin(User, User.id == MilestoneChange.actor_user_id)
            .where(*conditions)
            .order_by(MilestoneChange.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        notices = _notices(session, [change.id for change, _, _ in rows])
        return [
            _change_body(change, str(kind), name, notices.get(change.id, []))
            for change, kind, name in rows
        ], int(total)


# ── 통관 기록(S16) ───────────────────────────────────────────────────────────


def customs_record_body(record: CustomsRecord, broker_name: str | None) -> dict[str, Any]:
    return {
        "id": record.id,
        "shipment_id": record.shipment_id,
        "declaration_kind": record.declaration_kind,
        "declaration_no": record.declaration_no,
        "declared_on": record.declared_on.isoformat(),
        "accepted_on": record.accepted_on.isoformat() if record.accepted_on else None,
        "customs_broker": (
            {"partner_id": record.customs_broker_partner_id, "name": broker_name}
            if record.customs_broker_partner_id is not None and broker_name is not None
            else None
        ),
        "note": record.note,
        "version": record.version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def customs_body(session: Session, record: CustomsRecord) -> dict[str, Any]:
    name = None
    if record.customs_broker_partner_id is not None:
        name = session.execute(
            select(Partner.name_ko).where(Partner.id == record.customs_broker_partner_id)
        ).scalar_one_or_none()
    return customs_record_body(record, name)


def list_customs_records(
    *, shipment_id: int, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """선적의 살아 있는 통관 기록(Page — 신고일·id 순). 관세사명은 조인 1회(N+1 0)."""
    with unit_of_work() as uow:
        session = uow.session
        shipments.require_shipment(session, shipment_id)
        conditions = [CustomsRecord.shipment_id == shipment_id, CustomsRecord.deleted_at.is_(None)]
        total = session.execute(
            select(func.count()).select_from(CustomsRecord).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(CustomsRecord, Partner.name_ko)
            .outerjoin(Partner, Partner.id == CustomsRecord.customs_broker_partner_id)
            .where(*conditions)
            .order_by(CustomsRecord.declared_on, CustomsRecord.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [customs_record_body(record, name) for record, name in rows], int(total)
