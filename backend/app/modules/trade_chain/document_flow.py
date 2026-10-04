"""문서 흐름 — 전표 사슬(QT→PI→SO→수출선적)의 상·하 양방향 트리 (S3-1 design-A A4 / design-integrated G-03 / DESIGN §14 ⑦ / S3-2 PR-3c design-D X1·D8).

FK로 사슬의 **뿌리 QT**를 찾아 아래로 전개한다: QT → (PI → 그 PI의 SO) · (QT 직접 SO) → 각 SO의 **수출선적**(S3-2 PR-3c).
직접(인테이크) 수주는 뿌리가 자기 자신이다(그 아래 수출선적은 그대로 붙는다).
취소·만료 전표도 상태와 함께 보인다(흐름은 이력이다 — 살아 있음 판정과 무관). 읽기 전용·잠금 없음·금액은 판매 문서의 표시 값(원가·마진 없음 —
수출선적 합계도 SO 단가 사본 합, 판매가 축).
쿼리는 **고정 5회 이내**(N+1 없음 — 선적은 SO id 목록으로 1쿼리)이고 한 뿌리 아래 전표는 소수라 페이지네이션 대상이 아니다(목록 API가 아니라
그래프 응답). 선적에서 들어오면 선적·원천 SO를 조인 1회로 읽어 뿌리를 찾는다. 실측(PR-3c 시험): QT 사슬 = 진입 종류 무관 5회
(뿌리 1·QT 1·PI 1·SO 1·선적 1), 직접 수주 사슬 = 2회(SO 1·선적 1).

■ **수입선적(PO 원천)은 흐름 밖**이다 — 이 흐름은 판매 사슬(QT·PI·SO)이고 PO가 없다(design-D D6 ⑩). 수입선적 id로 들어오면 404(범위 밖,
  존재 여부와 무관 — design-integrated R-05 ③ "범용 범위 밖 = 404" 선례). 수입 노드는 PR-5a·부채 Q-13 몫이다.
■ **노드 순서**: 위→아래(부모가 자식보다 먼저). 선적 노드는 **자기 SO 노드 바로 뒤**에 붙는다(SO 사이에 끼어 다른 SO 아래로 보이지 않게 —
  화면은 받은 순서대로 그리고 들여쓰기만 부모 링크로 센다). SO 안의 선적은 id 순.
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
from app.modules.shipments.models import Shipment
from app.modules.trade_docs.constants import DocKind, ShipmentKind
from app.modules.trade_docs.views import money_text

#: 문서 흐름을 조회할 수 있는 전표 — PO는 판매 사슬 밖이다(PR-8). 선적은 수출선적만(SO 자식 — S3-2 PR-3c).
FLOW_KINDS = (DocKind.QUOTATION, DocKind.PROFORMA_INVOICE, DocKind.SALES_ORDER, DocKind.SHIPMENT)

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
    """(뿌리 QT id 또는 None[직접 수주가 뿌리], 직접 수주 뿌리일 때 쓸 행) — 없거나 삭제된 전표·수입선적은 404. 쿼리 1회.

    선적이면 원천 SO를 **내부 조인**으로 함께 읽는다 — 수입선적(`so_id` NULL)·삭제 선적·삭제 SO의 선적은 행이 없어 404가 된다(수출만 흐름에 든다).
    돌려주는 행은 선적이 아니라 그 SO다(직접 수주 뿌리 노드).
    """
    if kind is DocKind.SHIPMENT:
        found = session.execute(
            select(SalesOrder)
            .join(Shipment, Shipment.so_id == SalesOrder.id)
            .where(
                Shipment.id == doc_id,
                Shipment.deleted_at.is_(None),
                Shipment.shipment_kind == ShipmentKind.EXPORT.value,
                SalesOrder.deleted_at.is_(
                    None
                ),  # 삭제 SO의 선적 진입도 SO 진입과 같은 404(PR-3c 적대 검토)
            )
        ).scalar_one_or_none()
        if found is None:
            raise NotFoundError(log_context={"doc_kind": kind.value, "id": doc_id})
        return found.qt_id, found
    model = _MODELS[kind]
    row = session.execute(
        select(model).where(model.id == doc_id, model.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"doc_kind": kind.value, "id": doc_id})
    if kind is DocKind.QUOTATION:
        return row.id, row
    return row.qt_id, row


def _shipments_by_so(session: Session, so_ids: list[int]) -> dict[int, list[Shipment]]:
    """SO id 목록 → 그 SO들의 수출선적(취소 포함·삭제 제외, id 순) — 쿼리 1회(SO 수와 무관). SO가 없으면 쿼리 0."""
    if not so_ids:
        return {}
    grouped: dict[int, list[Shipment]] = {}
    for shipment in session.execute(
        select(Shipment)
        .where(
            Shipment.so_id.in_(so_ids),
            Shipment.deleted_at.is_(None),
            Shipment.shipment_kind == ShipmentKind.EXPORT.value,
        )
        .order_by(Shipment.id)
    ).scalars():
        assert shipment.so_id is not None  # CHECK kind_source — 수출은 SO 원천
        grouped.setdefault(shipment.so_id, []).append(shipment)
    return grouped


def _append_order(
    nodes: list[dict[str, Any]],
    so: SalesOrder,
    parent: tuple[DocKind, int] | None,
    shipments: dict[int, list[Shipment]],
    kind: DocKind,
    doc_id: int,
) -> None:
    """SO 노드 1개 + 그 바로 뒤에 그 SO의 수출선적 노드들(부모 = 이 SO)."""
    nodes.append(
        _node(DocKind.SALES_ORDER, so, parent, kind is DocKind.SALES_ORDER and so.id == doc_id)
    )
    for shipment in shipments.get(so.id, []):
        nodes.append(
            _node(
                DocKind.SHIPMENT,
                shipment,
                (DocKind.SALES_ORDER, so.id),
                kind is DocKind.SHIPMENT and shipment.id == doc_id,
            )
        )


def document_flow(kind: DocKind, doc_id: int) -> dict[str, Any]:
    """`kind`·`doc_id`가 속한 사슬 전체 — `nodes`는 위→아래(QT, PI…, SO…[각 SO 바로 뒤에 그 수출선적…]) 순서, `current`가 요청한 전표."""
    with unit_of_work() as uow:
        session = uow.session
        root_qt, row = _root_qt_id(session, kind, doc_id)
        nodes: list[dict[str, Any]] = []
        if root_qt is None:  # 직접(인테이크) 수주 — 사슬이 자기 자신(과 그 수출선적)뿐이다
            shipments = _shipments_by_so(session, [row.id])
            _append_order(nodes, row, None, shipments, kind, doc_id)
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
        shipments = _shipments_by_so(session, [so.id for so in orders])
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
                _append_order(nodes, so, (DocKind.PROFORMA_INVOICE, pi.id), shipments, kind, doc_id)
        for so in by_pi.get(None, []):
            _append_order(nodes, so, (q, qt.id), shipments, kind, doc_id)
        return {"root_kind": q.value, "root_id": qt.id, "nodes": nodes}
