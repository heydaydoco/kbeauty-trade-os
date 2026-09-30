"""잔량(open qty)·소비 직렬화 계약 (S3-1 ADR-0052 / design-B B5 / X-19).

잔량은 **저장하지 않고 파생**한다: `open = ordered − Σ(FULFILL 소비자 SUM, 살아 있는 행)` — 소비 코드 없는 유지 컬럼은
죽은 컬럼이고 §8.3이 P4 ADR로 남긴 결정을 선점한다. 이 결정은 **S3-1 로컬 결정**이며 S4-1 착수 ADR이 유지 컬럼/파생
테이블로 대체할 수 있다 — `open_quantity`·`lock_lines_for_consumption` **시그니처와 라인 안정 id**가 유지되면 소비자
코드는 무영향이다.

■ 소비자 레지스트리(`LINE_CONSUMERS`): 키는 원천 라인 종류 4개(QT_LINE·PI_LINE·SO_LINE·PO_LINE). S3-1 등록은 PR-6·7이
  하고(QT_LINE←PI_LINE.qt_line_id·QT_LINE←SO_LINE.qt_line_id·PI_LINE←SO_LINE.pi_line_id), 선적·입고 소비는 S3-2·S4-1이 더한다.
  이 PR에는 등록 0건이다 — 레지스트리·계약만 세운다(소비자 픽스처 테스트로 산식 검증).
■ 소비 절차(소비 세션 의무): ① `lock_lines_for_consumption` ② `open_quantity` 재계산 ③ 요청 ≤ 잔량(초과 409
  EXCEEDS_OPEN, 부분 허용) ④ 자기 행 INSERT — 한 트랜잭션. 부모 헤더 `FOR SHARE`가 취소(헤더 FOR UPDATE)와 직렬화한다.
  (참조 생성은 원천 헤더 `FOR UPDATE`→원천 라인 `FOR UPDATE` id순 — PR-6이 별도 헬퍼로 잠근다.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Integer, column, func, select, table
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.trade_docs.constants import DOC_TABLES, LINE_HEADER_FK, LINE_TABLES, DocKind
from app.modules.trade_docs.machine import DEAD_STATUSES

LineKind = Literal["QT_LINE", "PI_LINE", "SO_LINE", "PO_LINE"]

#: 원천 라인 종류 → (전표 종류, 원천 라인 수량 열).
LINE_KINDS: dict[str, tuple[DocKind, str]] = {
    "QT_LINE": (DocKind.QUOTATION, "quantity"),
    "PI_LINE": (DocKind.PROFORMA_INVOICE, "quantity"),
    "SO_LINE": (DocKind.SALES_ORDER, "quantity"),
    "PO_LINE": (DocKind.PURCHASE_ORDER, "quantity"),
}

#: 소비(선적·입고 등)를 받을 수 있는 원천 문서 상태 — 그 외는 409 DOCUMENT_NOT_CONSUMABLE.
CONSUMABLE_STATUSES: dict[DocKind, frozenset[str]] = {
    DocKind.SALES_ORDER: frozenset({"CONFIRMED"}),
    DocKind.PURCHASE_ORDER: frozenset({"ISSUED", "SUPPLIER_CONFIRMED"}),
}


@dataclass(frozen=True, slots=True)
class ConsumerSpec:
    """원천 라인을 소비하는 자식 라인 한 종류.

    자식 라인 테이블 `child_line_table`의 `line_fk_col`이 원천 라인 id를 가리키고 `qty_col`이 소비 수량이다.
    살아 있음은 자식 **헤더**(`child_header_table`, 라인의 `child_header_fk`)가 삭제되지 않고 죽은 상태가 아니며 자식
    라인 자신도 삭제되지 않은 것이다.
    """

    name: str
    child_line_table: str
    line_fk_col: str
    qty_col: str
    child_header_table: str
    child_header_fk: str
    #: FULFILL만 잔량을 줄인다(다른 소비 성격은 후속 세션이 정의).
    kind: str = "FULFILL"


#: 원천 라인 종류별 소비자 — 이 PR에는 등록 0건(소비 세션이 등록한다).
LINE_CONSUMERS: dict[str, tuple[ConsumerSpec, ...]] = {
    "QT_LINE": (),
    "PI_LINE": (),
    "SO_LINE": (),
    "PO_LINE": (),
}


@dataclass(frozen=True, slots=True)
class OpenQuantity:
    ordered: int
    consumed: int

    @property
    def open(self) -> int:
        return self.ordered - self.consumed


def open_quantity(
    session: Session, line_kind: LineKind, line_ids: list[int]
) -> dict[int, OpenQuantity]:
    """원천 라인별 (주문량, 소비량) — 소비량은 소비자 레지스트리 위의 SUM 파생(살아 있는 행만)."""
    if not line_ids:
        return {}
    doc_kind, qty_name = LINE_KINDS[line_kind]
    source = table(LINE_TABLES[doc_kind], column("id", Integer), column(qty_name, Integer))
    ordered = {
        int(r[0]): int(r[1])
        for r in session.execute(
            select(source.c.id, source.c[qty_name]).where(source.c.id.in_(set(line_ids)))
        ).all()
    }
    consumed = dict.fromkeys(ordered, 0)
    for spec in LINE_CONSUMERS[line_kind]:
        child = table(
            spec.child_line_table,
            column(spec.line_fk_col, Integer),
            column(spec.qty_col, Integer),
            column(spec.child_header_fk, Integer),
            column("deleted_at"),
        )
        header = table(
            spec.child_header_table, column("id", Integer), column("deleted_at"), column("status")
        )
        rows = session.execute(
            select(child.c[spec.line_fk_col], func.coalesce(func.sum(child.c[spec.qty_col]), 0))
            .join(header, header.c.id == child.c[spec.child_header_fk])
            .where(
                child.c[spec.line_fk_col].in_(list(ordered)),
                child.c.deleted_at.is_(None),
                header.c.deleted_at.is_(None),
                header.c.status.notin_(DEAD_STATUSES),
            )
            .group_by(child.c[spec.line_fk_col])
        ).all()
        for line_id, total in rows:
            consumed[int(line_id)] += int(total)
    return {line_id: OpenQuantity(ordered[line_id], consumed[line_id]) for line_id in ordered}


def require_within_open(quantities: dict[int, OpenQuantity], requested: dict[int, int]) -> None:
    """요청 수량 ≤ 잔량 — 초과는 409(detail: 라인별 잔량, 금액 없음)."""
    exceeded = {
        line_id: quantities[line_id].open
        for line_id, want in requested.items()
        if line_id not in quantities or want > quantities[line_id].open
    }
    if exceeded:
        raise AppError(
            ErrorCode.TRADE_DOCS_QUANTITY_EXCEEDS_OPEN,
            detail={"open_quantity": {str(k): v for k, v in exceeded.items()}},
        )


def lock_lines_for_consumption(
    session: Session, doc_kind: DocKind, doc_id: int, line_ids: list[int]
) -> list[Any]:
    """소비용 잠금 — 부모 헤더 `FOR SHARE`(상태 검증) → 라인 `FOR UPDATE ORDER BY id`(교착 방지).

    원천 문서가 소비 가능 상태가 아니면 409 DOCUMENT_NOT_CONSUMABLE. 라인 id 오름차순 잠금이라 서로 반대 순서로 라인을
    요청하는 두 트랜잭션도 교착하지 않는다.
    """
    header = table(
        DOC_TABLES[doc_kind], column("id", Integer), column("status"), column("deleted_at")
    )
    row = session.execute(
        select(header.c.status)
        .where(header.c.id == doc_id, header.c.deleted_at.is_(None))
        .with_for_update(read=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError(log_context={"doc_kind": doc_kind.value, "id": doc_id})
    if row[0] not in CONSUMABLE_STATUSES.get(doc_kind, frozenset()):
        raise AppError(
            ErrorCode.TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE,
            log_context={"doc_kind": doc_kind.value, "status": row[0]},
        )
    lines = table(
        LINE_TABLES[doc_kind],
        column("id", Integer),
        column(LINE_HEADER_FK[doc_kind], Integer),
        column("deleted_at"),
    )
    return list(
        session.execute(
            select(lines.c.id)
            .where(
                lines.c[LINE_HEADER_FK[doc_kind]] == doc_id,
                lines.c.id.in_(sorted(set(line_ids))),
                lines.c.deleted_at.is_(None),
            )
            .order_by(lines.c.id)
            .with_for_update()
        ).scalars()
    )
