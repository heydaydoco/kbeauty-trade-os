"""전표 사슬의 후속 판정 레지스트리 — L0, 테이블 이름 기반 Core 쿼리 (S3-1 ADR-0052 / design-B B3 / X-20).

후속 생존 판정(`has_live_children`)은 "역순 취소만 허용"(ADR-05)의 유일한 정의다. 이 모듈은 **모델을 임포트하지
않는다**(L0가 L1을 임포트하면 계층이 순환한다) — 후속 테이블은 이름으로 다룬다.

■ LIVE = `deleted_at IS NULL AND status NOT IN (CANCELLED, EXPIRED)` — COMPLETED·CLOSED·FULLY_RECEIVED·ON_HOLD도
  살아 있다(이행된 체인의 선행 취소 사고 차단). 술어는 `machine.DEAD_STATUSES`에서 만들어 이중 정의하지 않는다.
■ 사슬 후속만 등록한다: QT←PI(qt_id)·QT←SO(qt_id, PI 경유 SO도 qt_id가 채워진다)·PI←SO(pi_id)·SO←선적(so_id)·PO←선적(po_id — S3-2 PR-3a).
  `copied_from_id`(복제 계보)·상태이력 FK·라인→헤더 FK는 후속이 아니다(`NON_CHILD_FK_ALLOWLIST`).
■ 아직 만들어지지 않은 후속 테이블은 **건너뛴다**(PR-7a 이전의 SO가 그랬다 — 지금은 없다) — 그 테이블이 없으면 후속도 있을 수 없다.
  누락 방지: 후속 테이블이 metadata에 생기면 그 등록이 이미 이 표에 있어 바로 판정에 편입되고, 표에 없는
  전표 FK는 `test_every_fk_to_chain_docs_is_registered`가 CI에서 실패시킨다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, Table, func, select
from sqlalchemy.orm import Session

from app.core.db.base import Base
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import DEAD_STATUSES


@dataclass(frozen=True, slots=True)
class ChildLink:
    """부모 전표 → 후속 전표 한 연결.

    `confirmed_column`이 있으면 그 열이 NOT NULL인 후속(=확정 SO)이 부모의 수주전환(QT CONVERTED)을 만든다(X-18).
    """

    parent: DocKind
    child_table: str
    fk_column: str
    confirmed_column: str | None = None


CHILD_LINKS: tuple[ChildLink, ...] = (
    ChildLink(DocKind.QUOTATION, "proforma_invoices", "qt_id"),
    ChildLink(DocKind.QUOTATION, "sales_orders", "qt_id", confirmed_column="confirmed_at"),
    ChildLink(DocKind.PROFORMA_INVOICE, "sales_orders", "pi_id"),
    # S3-2 PR-3a(ADR-0074·design-A A6) — 살아 있는 선적이 있는 SO·PO는 취소 409 SUCCESSOR_ALIVE(역순 취소). 선적 종결(CLOSED)도 LIVE다.
    # nullable FK라도 equality 술어라 반대 구분(수입선적의 so_id NULL)은 잡히지 않는다. 수입선적 생성 경로는 PR-5a지만 FK가 생기는 이 PR에서 등록한다.
    ChildLink(DocKind.SALES_ORDER, "shipments", "so_id"),
    ChildLink(DocKind.PURCHASE_ORDER, "shipments", "po_id"),
)

#: 전표·라인 테이블을 가리키지만 사슬 후속이 아닌 FK(사유 필수) — (자식 테이블, FK 열).
NON_CHILD_FK_ALLOWLIST: dict[tuple[str, str], str] = {
    ("quotation_lines", "qt_id"): "라인은 자기 헤더의 구성 요소다(소비는 LINE_CONSUMERS)",
    ("quotation_status_log", "quotation_id"): "상태이력은 전표의 사건 기록이다",
    (
        "quotations",
        "copied_from_id",
    ): "복제 계보 표시 — 사슬 후속이 아니다(살아 있음 판정 제외, X-08)",
    ("proforma_invoice_lines", "pi_id"): "라인은 자기 헤더의 구성 요소다(소비는 LINE_CONSUMERS)",
    (
        "proforma_invoice_lines",
        "qt_id",
    ): "원천 QT 라인의 소속 보증용 복합 FK((qt_id, qt_line_id) — 소비 관계는 LINE_CONSUMERS가 정본)",
    ("proforma_invoice_status_log", "proforma_invoice_id"): "상태이력은 전표의 사건 기록이다",
    (
        "payments",
        "pi_id",
    ): "입금 원장은 PI의 사건 기록(INSERT-only)이다 — 후속 전표가 아니며 PI 상태는 순입금에서 자동 수렴한다(PR-10a)",
    ("sales_order_lines", "so_id"): "라인은 자기 헤더의 구성 요소다(소비는 LINE_CONSUMERS)",
    ("sales_order_status_log", "sales_order_id"): "상태이력은 전표의 사건 기록이다",
    (
        "sales_orders",
        "copied_from_id",
    ): "복제 계보 표시 — 사슬 후속이 아니다(살아 있음 판정 제외, X-08)",
    (
        "proforma_invoices",
        "copied_from_id",
    ): "복제 계보 표시 — 사슬 후속이 아니다(살아 있음 판정 제외, X-08)",
    (
        "purchase_order_lines",
        "po_id",
    ): "라인은 자기 헤더의 구성 요소다(소비는 LINE_CONSUMERS — S3-2·S4-1이 PO_LINE 소비자를 등록)",
    ("purchase_order_status_log", "purchase_order_id"): "상태이력은 전표의 사건 기록이다",
    (
        "order_intakes",
        "sales_order_id",
    ): "인테이크→SO 단방향 백링크(출처 기록) — SO의 후속 전표가 아니며 SO 취소·만료를 막지 않는다(PR-13a, 순환 FK 방지)",
    (
        "order_intakes",
        "copied_from_so_id",
    ): "복제 재접수의 원본 SO 계보 표시(X-13) — 사슬 후속이 아니다(원본은 이미 취소 상태여야 한다)",
    (
        "purchase_orders",
        "copied_from_id",
    ): "복제 계보 표시 — 사슬 후속이 아니다(살아 있음 판정 제외, X-08)",
    # S3-2 PR-3a — 선적 계열(design-A A6). 선적 자신의 후속은 S3-2에 0건이다(CI/PL = S3-3이 ChildLink(SHIPMENT, …)를 더한다).
    (
        "shipment_lines",
        "shipment_id",
    ): "라인은 자기 헤더의 구성 요소다(원천 SO·PO 라인 소비는 LINE_CONSUMERS)",
    ("shipment_status_log", "shipment_id"): "상태이력은 전표의 사건 기록이다",
    (
        "shipment_parties",
        "shipment_id",
    ): "당사자 영문 스냅샷은 선적 헤더의 구성 요소다(후속 전표 아님 — 상태·문서번호 없음)",
    (
        "shipments",
        "copied_from_id",
    ): "믹스인이 주는 복제 계보 열 — 선적은 복제 경로가 없다(CHECK no_copy_lineage로 항상 NULL)",
}


def _table(name: str) -> Table | None:
    """metadata의 테이블 — 아직 정의되지 않은 후속(PR-6·7 이전)은 None. 모든 모델을 먼저 적재한다."""
    import app.registry  # noqa: F401 — 프로세스가 후속 모델을 임포트하지 않아 '없음'으로 오판하는 것을 막는다

    return Base.metadata.tables.get(name)


def _live_conditions(table: Table) -> list[ColumnElement[bool]]:
    return [table.c.deleted_at.is_(None), table.c.status.notin_(DEAD_STATUSES)]


def links_for(parent: DocKind) -> list[ChildLink]:
    return [link for link in CHILD_LINKS if link.parent == parent]


def has_live_children(session: Session, parent: DocKind, doc_id: int) -> bool:
    """살아 있는 후속 전표가 있는가(취소 가드 — 삭제·취소·만료 후속은 세지 않는다)."""
    for link in links_for(parent):
        table = _table(link.child_table)
        if table is None:
            continue
        found = session.execute(
            select(func.count())
            .select_from(table)
            .where(table.c[link.fk_column] == doc_id, *_live_conditions(table))
        ).scalar_one()
        if found:
            return True
    return False


def live_children_numbers(session: Session, parent: DocKind, doc_id: int) -> list[str]:
    """살아 있는 후속의 문서번호 목록(에러 detail용 — 금액 없음)."""
    numbers: list[str] = []
    for link in links_for(parent):
        table = _table(link.child_table)
        if table is None:
            continue
        numbers.extend(
            session.execute(
                select(table.c.doc_number)
                .where(table.c[link.fk_column] == doc_id, *_live_conditions(table))
                .order_by(table.c.id)
            ).scalars()
        )
    return numbers


def has_live_confirmed_children(session: Session, parent: DocKind, doc_id: int) -> bool:
    """살아 있는 **확정** 후속(확정 SO)이 있는가 — QT 수주전환(CONVERTED) 판정의 원천(X-18)."""
    for link in links_for(parent):
        if link.confirmed_column is None:
            continue
        table = _table(link.child_table)
        if table is None:
            continue
        found = session.execute(
            select(func.count())
            .select_from(table)
            .where(
                table.c[link.fk_column] == doc_id,
                table.c[link.confirmed_column].is_not(None),
                *_live_conditions(table),
            )
        ).scalar_one()
        if found:
            return True
    return False
