"""문서 흐름 — 전표 사슬(QT→PI→SO)의 상·하 양방향 트리 (S3-1 design-A A4 / design-integrated G-03 / DESIGN §14 ⑦).

FK로 사슬의 **뿌리 QT**를 찾아 아래로 전개한다: QT → (PI → 그 PI의 SO) · (QT 직접 SO). 직접(인테이크) 수주는 뿌리가 자기 자신이다.
취소·만료 전표도 상태와 함께 보인다(흐름은 이력이다 — 살아 있음 판정과 무관). 읽기 전용·잠금 없음·금액은 판매 문서의 표시 값(원가·마진 없음).
쿼리는 고정 5회 이내(N+1 없음)이고 한 뿌리 아래 전표는 소수라 페이지네이션 대상이 아니다(목록 API가 아니라 그래프 응답).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import NotFoundError
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.quotations.models import Quotation
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.views import money_text

#: 문서 흐름을 조회할 수 있는 전표 — PO는 판매 사슬 밖이다(PR-8).
FLOW_KINDS = (DocKind.QUOTATION, DocKind.PROFORMA_INVOICE, DocKind.SALES_ORDER)

_MODELS: dict[DocKind, Any] = {
    DocKind.QUOTATION: Quotation,
    DocKind.PROFORMA_INVOICE: ProformaInvoice,
    DocKind.SALES_ORDER: SalesOrder,
}


def _node(
    kind: DocKind, row: Any, parent: tuple[DocKind, int] | None, current: bool
) -> dict[str, Any]:
    return {
        "kind": kind.value,
        "id": row.id,
        "doc_number": row.doc_number,
        "status": row.status,
        "doc_date": row.doc_date,
        "currency": row.currency,
        "total_amount": row.total_amount,
        "total_text": money_text(row.total_amount, row.currency),
        "parent_kind": parent[0].value if parent else None,
        "parent_id": parent[1] if parent else None,
        "is_current": current,
    }


def _root_qt_id(session: Session, kind: DocKind, doc_id: int) -> tuple[int | None, Any]:
    """(뿌리 QT id 또는 None[직접 수주 자신이 뿌리], 조회한 행) — 없거나 삭제된 전표는 404."""
    model = _MODELS[kind]
    row = session.execute(
        select(model).where(model.id == doc_id, model.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"doc_kind": kind.value, "id": doc_id})
    if kind is DocKind.QUOTATION:
        return row.id, row
    return row.qt_id, row


def document_flow(kind: DocKind, doc_id: int) -> dict[str, Any]:
    """`kind`·`doc_id`가 속한 사슬 전체 — `nodes`는 위→아래(QT, PI…, SO…) 순서, `current`가 요청한 전표."""
    with unit_of_work() as uow:
        session = uow.session
        root_qt, row = _root_qt_id(session, kind, doc_id)
        nodes: list[dict[str, Any]] = []
        if root_qt is None:  # 직접(인테이크) 수주 — 사슬이 자기 자신뿐이다
            nodes.append(_node(DocKind.SALES_ORDER, row, None, True))
            return {"root_kind": DocKind.SALES_ORDER.value, "root_id": row.id, "nodes": nodes}
        qt = session.execute(select(Quotation).where(Quotation.id == root_qt)).scalar_one()
        pis = (
            session.execute(
                select(ProformaInvoice)
                .where(ProformaInvoice.qt_id == root_qt, ProformaInvoice.deleted_at.is_(None))
                .order_by(ProformaInvoice.id)
            )
            .scalars()
            .all()
        )
        orders = (
            session.execute(
                select(SalesOrder)
                .where(SalesOrder.qt_id == root_qt, SalesOrder.deleted_at.is_(None))
                .order_by(SalesOrder.id)
            )
            .scalars()
            .all()
        )
        q = DocKind.QUOTATION
        nodes.append(_node(q, qt, None, kind is q and qt.id == doc_id))
        for pi in pis:
            nodes.append(
                _node(
                    DocKind.PROFORMA_INVOICE,
                    pi,
                    (q, qt.id),
                    kind is DocKind.PROFORMA_INVOICE and pi.id == doc_id,
                )
            )
        by_pi: dict[int | None, list[SalesOrder]] = {}
        for so in orders:
            by_pi.setdefault(so.pi_id, []).append(so)
        for pi in pis:
            for so in by_pi.get(pi.id, []):
                nodes.append(
                    _node(
                        DocKind.SALES_ORDER,
                        so,
                        (DocKind.PROFORMA_INVOICE, pi.id),
                        kind is DocKind.SALES_ORDER and so.id == doc_id,
                    )
                )
        for so in by_pi.get(None, []):
            nodes.append(
                _node(
                    DocKind.SALES_ORDER,
                    so,
                    (q, qt.id),
                    kind is DocKind.SALES_ORDER and so.id == doc_id,
                )
            )
        return {"root_kind": q.value, "root_id": qt.id, "nodes": nodes}
