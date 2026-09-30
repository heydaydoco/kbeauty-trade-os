"""사슬 잠금과 부모 상태 수렴 (S3-1 design-B B3 / X-18).

■ `lock_chain` — 조상을 **위에서부터**(QT→PI→SO) 잠근 뒤 자기 행을 잠근다(전역 잠금 순서 (3)~(5)). 참조 FK는 ORIGIN이라
  불변이므로 잠금 전 무잠금 조회로 조상 id를 얻어도 안전하다. `with_partner`는 여신 직렬화가 필요한 경로(SO 확정 등)
  전용이며 이 PR에는 소비자가 없다.
■ `converge_quotation` — QT를 **살아 있는 확정 SO ≥ 1 → CONVERTED / 아니면 ISSUED**(유효기간 경과 + 살아 있는 후속 0 →
  EXPIRED)로 맞춘다(수주전환=SO 확정 시점, X-18). 호출 시점: SO 확정·SO/PI 취소·만료. 후속이 부모를 붙잡는다 —
  살아 있는 후속(PI·SO)이 있으면 유효기간이 지나도 EXPIRED로 닫지 않는다. 전이는 전부 자동(`automatic=True`, 행위자=
  유발자)이고 `record_transition` 통로를 거친다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.time import today_kst
from app.modules.quotations.models import Quotation
from app.modules.trade_docs.chain import has_live_children, has_live_confirmed_children
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.expiry import is_lapsed
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.transition import record_transition

#: 전표 종류 → 모델. 각 전표 PR이 자기 모델을 등록한다(PI·SO는 PR-6·7).
DOC_MODELS: dict[DocKind, Any] = {DocKind.QUOTATION: Quotation}

#: 조상 사슬 — (조상 종류, 자식 행의 FK 열) 위→아래 순서.
ANCESTORS: dict[DocKind, tuple[tuple[DocKind, str], ...]] = {
    DocKind.QUOTATION: (),
    DocKind.PROFORMA_INVOICE: ((DocKind.QUOTATION, "qt_id"),),
    DocKind.SALES_ORDER: ((DocKind.QUOTATION, "qt_id"), (DocKind.PROFORMA_INVOICE, "pi_id")),
    DocKind.PURCHASE_ORDER: (),
}


def lock_chain(session: Session, kind: DocKind, doc_id: int) -> dict[DocKind, Any]:
    """조상 → 자기 순서로 `FOR UPDATE` 잠금. 돌려주는 값은 종류별 잠근 행(없는 조상은 빠진다)."""
    model = DOC_MODELS[kind]
    peek = session.get(model, doc_id)
    locked: dict[DocKind, Any] = {}
    if peek is not None:
        for ancestor_kind, fk_column in ANCESTORS[kind]:
            ancestor_id = getattr(peek, fk_column, None)
            if ancestor_id is not None:
                locked[ancestor_kind] = lock_document(
                    session, DOC_MODELS[ancestor_kind], ancestor_id
                )
    locked[kind] = lock_document(session, model, doc_id)
    return locked


def converge_quotation(session: Session, qt_id: int, *, actor_user_id: int | None) -> str | None:
    """QT 상태 수렴 — 바꿨으면 도달 상태, 아니면 None. 호출자는 사슬 잠금을 이미 잡았다는 전제다."""
    qt = lock_document(session, Quotation, qt_id)
    if qt.status not in ("ISSUED", "CONVERTED"):
        return None
    changed: str | None = None
    converted = has_live_confirmed_children(session, DocKind.QUOTATION, qt_id)
    if converted and qt.status == "ISSUED":
        record_transition(
            session,
            qt,
            "CONVERTED",
            actor_user_id=actor_user_id,
            reason="살아 있는 확정 수주 발생(자동 전환)",
            automatic=True,
        )
        changed = "CONVERTED"
    elif not converted and qt.status == "CONVERTED":
        record_transition(
            session,
            qt,
            "ISSUED",
            actor_user_id=actor_user_id,
            reason="살아 있는 확정 수주 없음(자동 복귀)",
            automatic=True,
        )
        changed = "ISSUED"
    # 복귀 직후 유효기간이 이미 지났고 붙잡는 후속이 없으면 같은 트랜잭션에서 EXPIRED로 수렴(이력 2행).
    if (
        qt.status == "ISSUED"
        and is_lapsed(DocKind.QUOTATION, qt.status, qt.valid_until, today_kst())
        and not has_live_children(session, DocKind.QUOTATION, qt_id)
    ):
        record_transition(
            session,
            qt,
            "EXPIRED",
            actor_user_id=actor_user_id,
            reason=f"자동: 유효기간 경과 (valid_until={qt.valid_until}, 기준일={today_kst()})",
            automatic=True,
        )
        changed = "EXPIRED"
    return changed


def converge_parent(
    session: Session, child_kind: DocKind, child: Any, *, actor_user_id: int | None
) -> str | None:
    """후속 전표(PI·SO)의 생성·확정·취소·만료 뒤 부모 QT를 수렴시킨다(PR-6·7·12가 호출)."""
    qt_id = getattr(child, "qt_id", None)
    if child_kind not in (DocKind.PROFORMA_INVOICE, DocKind.SALES_ORDER) or qt_id is None:
        return None
    return converge_quotation(session, qt_id, actor_user_id=actor_user_id)
