"""PO 라인 입고예정 — 계산값 조회 (S3-2 PR-5a / design-B B17 / ADR-0085 · P-05 — PO 라인에 열을 두지 않는다).

■ **정의는 순수 함수 `schedule.expected_receipt` 하나**(가장 늦은 ETA · ETA 없는 선적이 있으면 UNSCHEDULED · 선적 없음 = NONE) — 여기는 입력을
  모으는 조회뿐이다.
■ "그 라인을 참조하는 살아 있는 수입선적"은 **배정 가능량과 같은 소비자 등록**(`LINE_CONSUMERS["PO_LINE"]`의 IN_TRANSIT 소비자)에서 읽는다 —
  살아 있음(선적 헤더 미삭제·죽은 상태 아님·선적 라인 미삭제)의 정의가 `open_quantity`와 같다(두 곳이 갈리면 배정된 수량과 입고예정이 서로 다른
  선적 집합을 본다).
■ ETA 유효값 = `schedule.effective`(실적 우선, 없으면 계획) — 선적 목록 ETD·ETA 열(`milestone_view.etd_eta_by_shipment`)과 같은 정의다.
■ L0(커널): 선적·마일스톤·PO 모델을 임포트하지 않고 테이블 이름으로 필요한 열만 읽는다(계층 DAG — PO 서비스[L1]가 부른다, 원가 열 0).
  질의 1회 — 라인·선적 수와 무관(N+1 0).
"""

from __future__ import annotations

from sqlalchemy import Date, Integer, String, and_, column, select, table
from sqlalchemy.orm import Session

from app.modules.trade_docs.constants import DOC_TABLES, DocKind, MilestoneType
from app.modules.trade_docs.machine import DEAD_STATUSES
from app.modules.trade_docs.quantities import ASSIGNABLE_KINDS, LINE_CONSUMERS, ConsumerSpec
from app.modules.trade_docs.schedule import DateValue, ReceiptEstimate, effective, expected_receipt

#: 마일스톤 표(선적 계열 — 모델은 L1 `shipments`라 이름으로 읽는다). ETA는 날짜형(DATE) 종류다.
_MILESTONES = table(
    "milestones",
    column("shipment_id", Integer),
    column("milestone_type", String),
    column("planned_on", Date),
    column("actual_on", Date),
    column("deleted_at"),
)


def _import_consumer() -> ConsumerSpec:
    """PO 라인의 IN_TRANSIT 소비자(수입선적 라인) — 정확히 하나이고 그 헤더가 선적 표여야 한다(등록이 바뀌면 조용히 0이 아니라 실패)."""
    specs = [spec for spec in LINE_CONSUMERS["PO_LINE"] if spec.kind in ASSIGNABLE_KINDS]
    if len(specs) != 1 or specs[0].child_header_table != DOC_TABLES[DocKind.SHIPMENT]:
        raise RuntimeError(f"PO 라인 IN_TRANSIT 소비자 등록이 선적 1건이 아니다: {specs!r}")
    return specs[0]


def po_line_receipts(session: Session, po_line_ids: list[int]) -> dict[int, ReceiptEstimate]:
    """PO 라인 id → 입고예정 계산값. 요청한 id는 전부 키로 돌아온다(살아 있는 수입선적이 없으면 NONE)."""
    if not po_line_ids:
        return {}
    spec = _import_consumer()
    lines = table(
        spec.child_line_table,
        column(spec.line_fk_col, Integer),
        column(spec.child_header_fk, Integer),
        column("deleted_at"),
    )
    headers = table(
        spec.child_header_table, column("id", Integer), column("deleted_at"), column("status")
    )
    rows = session.execute(
        select(
            lines.c[spec.line_fk_col],
            headers.c.id,
            _MILESTONES.c.planned_on,
            _MILESTONES.c.actual_on,
        )
        .join(headers, headers.c.id == lines.c[spec.child_header_fk])
        .outerjoin(
            _MILESTONES,
            and_(
                _MILESTONES.c.shipment_id == headers.c.id,
                _MILESTONES.c.milestone_type == MilestoneType.ETA.value,
                _MILESTONES.c.deleted_at.is_(None),
            ),
        )
        .where(
            lines.c[spec.line_fk_col].in_(sorted(set(po_line_ids))),
            lines.c.deleted_at.is_(None),
            headers.c.deleted_at.is_(None),
            headers.c.status.notin_(DEAD_STATUSES),
        )
    ).all()
    # 선적당 1개 — (선적, PO 라인) 살아 있는 라인 유일·(선적, 종류) 살아 있는 마일스톤 유일(부분 유니크)이라 행이 겹치지 않는다.
    etas: dict[int, dict[int, DateValue | None]] = {int(i): {} for i in po_line_ids}
    for line_id, shipment_id, planned_on, actual_on in rows:
        etas[int(line_id)][int(shipment_id)] = effective(planned_on, actual_on)
    return {line_id: expected_receipt(found.values()) for line_id, found in etas.items()}
