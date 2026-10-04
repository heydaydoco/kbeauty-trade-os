"""잔량(open qty)·소비 직렬화 계약 (S3-1 ADR-0052 / design-B B5 / X-19).

잔량은 **저장하지 않고 파생**한다: `open = ordered − Σ(FULFILL 소비자 SUM, 살아 있는 행)` — 소비 코드 없는 유지 컬럼은
죽은 컬럼이고 §8.3이 P4 ADR로 남긴 결정을 선점한다. 이 결정은 **S3-1 로컬 결정**이며 S4-1 착수 ADR이 유지 컬럼/파생
테이블로 대체할 수 있다 — `open_quantity`·`lock_lines_for_consumption` **시그니처와 라인 안정 id**가 유지되면 소비자
코드는 무영향이다.

■ 소비자 레지스트리(`LINE_CONSUMERS`): 키는 원천 라인 종류 4개(QT_LINE·PI_LINE·SO_LINE·PO_LINE). S3-1 등록은 PR-6·7이
  하고(QT_LINE←PI_LINE.qt_line_id·QT_LINE←SO_LINE.qt_line_id·PI_LINE←SO_LINE.pi_line_id), 선적·입고 소비는 S3-2·S4-1이 더한다.
  PR-6a가 3건을 **모두 등록**했고 자식 라인 테이블이 아직 없는 등록(SO 라인)은 `open_quantity`가 건너뛰었다 —
  PR-7a가 `sales_order_lines`를 만들며 `PENDING_CONSUMER_TABLES`를 비웠다(핀 테스트가 소거를 강제한 것 — CHILD_LINKS의 PENDING 관용과 같다).
  이후 소비자 등록은 자식 테이블이 이미 있을 때만 하고, 테이블이 아직 없는 등록을 더하면 그 테이블을 이 집합에 적는다.
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
    #: 소비 성격 — `CONSUMER_KINDS` 중 하나. 잔량(`open_quantity` 기본)은 FULFILL만 줄인다(S3-2 PR-2a / ADR-0077).
    kind: str = "FULFILL"


#: 소비 성격의 폐쇄 집합 (S3-2 PR-2a / ADR-0077 / design-A A4).
#: FULFILL = 원천 잔량을 줄이는 이행 소비(PI·SO·수출선적·S4-1 입고). IN_TRANSIT = 수입선적(PR-3a 등록) — PO 잔량은 입고에서만 줄고
#: 수입선적은 **배정 가능량**(`open_quantity(..., kinds=frozenset({"IN_TRANSIT"}))`)만 줄인다. S4-1 입고 FULFILL과 겹쳐 세지 않는다.
CONSUMER_KINDS: frozenset[str] = frozenset({"FULFILL", "IN_TRANSIT"})

#: `open_quantity`의 기본 kind 필터 — 잔량 = 주문량 − FULFILL 소비(기존 4종 전표 동작 그대로).
DEFAULT_OPEN_KINDS: frozenset[str] = frozenset({"FULFILL"})

#: 원천 라인 종류별 소비자. S3-1 등록 3건(X-19) — 선적·입고 소비(S3-2·S4-1)는 각 세션이 더한다.
#: 소비 = 살아 있는 후속 전표(취소·만료 아님)의 라인 수량 합이다 — 만료·취소 PI의 수량은 QT로 **환원**된다(파생이라 자동).
LINE_CONSUMERS: dict[str, tuple[ConsumerSpec, ...]] = {
    "QT_LINE": (
        ConsumerSpec(
            name="PI_LINE.qt_line_id",
            child_line_table="proforma_invoice_lines",
            line_fk_col="qt_line_id",
            qty_col="quantity",
            child_header_table="proforma_invoices",
            child_header_fk="pi_id",
        ),
        ConsumerSpec(
            name="SO_LINE.qt_line_id",  # 직접 경로(PI 없는 후불 거래) — 테이블은 PR-7
            child_line_table="sales_order_lines",
            line_fk_col="qt_line_id",
            qty_col="quantity",
            child_header_table="sales_orders",
            child_header_fk="so_id",
        ),
    ),
    "PI_LINE": (
        ConsumerSpec(
            name="SO_LINE.pi_line_id",  # PI 경유 경로 — 테이블은 PR-7
            child_line_table="sales_order_lines",
            line_fk_col="pi_line_id",
            qty_col="quantity",
            child_header_table="sales_orders",
            child_header_fk="so_id",
        ),
    ),
    "SO_LINE": (),
    "PO_LINE": (),
}

#: 등록은 됐으나 자식 라인 테이블이 아직 없는 소비자의 테이블 — 각 전표 PR이 테이블을 만들며 **지워야** 한다
#: (test_doc_chain_contract가 metadata와 대사해 안 지우면 실패 — 메타데이터를 런타임에 읽지 않는 이유는 테스트의 임시 소비자 테이블이
#: 메타데이터 밖이어도 산식을 시험할 수 있게 하기 위해서다). 없는 테이블의 소비량은 0이다(행이 있을 수 없다).
PENDING_CONSUMER_TABLES: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class OpenQuantity:
    ordered: int
    consumed: int

    @property
    def open(self) -> int:
        return self.ordered - self.consumed


def open_quantity(
    session: Session,
    line_kind: LineKind,
    line_ids: list[int],
    *,
    kinds: frozenset[str] = DEFAULT_OPEN_KINDS,
) -> dict[int, OpenQuantity]:
    """원천 라인별 (주문량, 소비량) — 소비량은 소비자 레지스트리 위의 SUM 파생(살아 있는 행만).

    `kinds`(S3-2 PR-2a / ADR-0077): 합산할 소비 성격. 기본은 FULFILL만 — 수입선적(IN_TRANSIT)이 등록돼도 PO 잔량은
    줄지 않는다. 배정 가능량은 `kinds=frozenset({"IN_TRANSIT"})`로 같은 함수에서 파생한다(§8.3 "시그니처 하나").
    빈 집합·모르는 kind는 ValueError(조용히 0 소비로 읽히면 초과 소비가 통과한다 — fail-closed).
    """
    if not kinds or not kinds <= CONSUMER_KINDS:
        raise ValueError(
            f"open_quantity kinds는 {sorted(CONSUMER_KINDS)}의 비지 않은 부분집합이어야 합니다: {kinds!r}"
        )
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
        if spec.kind not in kinds:
            continue  # 다른 소비 성격(예: 수입선적 IN_TRANSIT는 PO 잔량을 줄이지 않는다 — ADR-0077)
        if spec.child_line_table in PENDING_CONSUMER_TABLES:
            continue  # 아직 만들어지지 않은 후속(PR-7 이전의 SO 라인) — 소비량 0
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
        line_id: (quantities[line_id].open if line_id in quantities else 0)
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


def lock_source_lines(
    session: Session, doc_kind: DocKind, doc_id: int, line_ids: list[int] | None = None
) -> list[int]:
    """참조 생성용 원천 라인 잠금 — **호출자가 원천 헤더를 이미 `FOR UPDATE`로 잡았다**(잠금 순서 (3)~(5) → (8)).

    살아 있는 라인을 id 오름차순 `FOR UPDATE`로 잠가 돌려준다(교착 방지 — 라인 요청 순서와 무관). `line_ids`가
    None이면 그 헤더의 살아 있는 라인 전부. `lock_lines_for_consumption`(선적·입고용: 헤더 SHARE+소비 가능 상태 검증)과
    달리 참조 생성은 헤더 잠금이 취소·후속 생성과의 직렬화를 이미 맡는다.
    """
    lines = table(
        LINE_TABLES[doc_kind],
        column("id", Integer),
        column(LINE_HEADER_FK[doc_kind], Integer),
        column("deleted_at"),
    )
    query = select(lines.c.id).where(
        lines.c[LINE_HEADER_FK[doc_kind]] == doc_id, lines.c.deleted_at.is_(None)
    )
    if line_ids is not None:
        query = query.where(lines.c.id.in_(sorted(set(line_ids))))
    return [int(v) for v in session.execute(query.order_by(lines.c.id).with_for_update()).scalars()]
