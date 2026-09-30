"""라인 품목 규칙 — 판매 가능 SKU 검사의 단일 통로 (S3-1 ADR-0056 / design-A A11).

모든 라인 생성 경로(수기·참조·인테이크·임포트)가 이 함수로 SKU 상태를 본다(현 `catalog.require_sku`는 상태를
안 본다). 마스터 값(SKU·판가·바이어 품번)을 전표로 복사하는 통로는 `snapshot`·`lines` 두 모듈뿐이다 — 이 밖에서
`catalog.models.Sku`·`SkuPrice`를 임포트하는 코드는 아키텍처 테스트가 막는다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.catalog.models import Sku
from app.modules.trade_docs.constants import DocKind

#: 단종(DISCONTINUED) SKU를 새 라인으로 받지 않는 문서 — QT·PO는 즉시 차단한다.
#: PI는 원천 라인 SKU가 단종·삭제로 바뀐 경우 사용자가 제외하도록 열거하고(PR-6), SO 접수는 저장을 허용하되
#: 확정(동결) 시 재검사한다(PR-7·12). 이미 동결된 전표는 SKU가 나중에 단종돼도 영향이 없다(과거 사실).
BLOCKS_DISCONTINUED_ON_ADD: frozenset[DocKind] = frozenset(
    {DocKind.QUOTATION, DocKind.PURCHASE_ORDER}
)


def require_sellable_sku(
    session: Session, sku_id: int, kind: DocKind, *, field: str = "sku_id"
) -> Sku:
    """존재하는(삭제되지 않은) SKU를 돌려준다. 단종은 kind에 따라 차단한다.

    삭제된 SKU·없는 SKU는 422(필드별 안내)다 — 라인 편집 경로에서 404로 흘려 IDOR 오라클이 되지 않게 한다.
    """
    sku = session.execute(
        select(Sku).where(Sku.id == sku_id, Sku.deleted_at.is_(None))
    ).scalar_one_or_none()
    if sku is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={field: "존재하지 않는 SKU입니다. SKU를 다시 선택해 주세요."},
            log_context={"sku_id": sku_id},
        )
    if sku.status == "DISCONTINUED" and kind in BLOCKS_DISCONTINUED_ON_ADD:
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_SKU_DISCONTINUED,
            detail={field: f"단종된 SKU입니다: {sku.sku_code}"},
            log_context={"sku_id": sku_id},
        )
    return sku


def unusable_sku_reasons(session: Session, sku_ids: list[int]) -> dict[int, str]:
    """참조 생성(PI·SO)이 원천 라인을 복사하기 전의 SKU 재검사 — 사용할 수 없는 SKU만 {id: 사유}로 돌려준다.

    원천 라인은 동결 값이라 복사는 마스터를 다시 읽지 않지만, **새로 만드는 전표에 단종·삭제된 SKU가 실려 나가는 것**은
    막는다(A11 — 사용자가 그 라인을 제외하도록 detail이 목록을 준다). 이미 발행된 원천 전표 자체는 영향이 없다.
    """
    if not sku_ids:
        return {}
    found = {
        sku.id: sku
        for sku in session.execute(
            select(Sku).where(Sku.id.in_(set(sku_ids)), Sku.deleted_at.is_(None))
        ).scalars()
    }
    reasons: dict[int, str] = {}
    for sku_id in sku_ids:
        sku = found.get(sku_id)
        if sku is None:
            reasons[sku_id] = "삭제된 SKU"
        elif sku.status == "DISCONTINUED":
            reasons[sku_id] = f"단종된 SKU: {sku.sku_code}"
    return reasons


def sku_statuses(session: Session, sku_ids: list[int]) -> dict[int, str]:
    """라인 표시용 SKU 현재 상태 — 삭제된 SKU는 `DELETED`. SO 접수는 단종 SKU 저장을 허용하고 화면이 이 값을 표시한다(A11).

    마스터를 읽는 두 통로(`snapshot`·`lines`) 중 하나라 L1 전표 서비스가 SKU 모델을 직접 임포트하지 않고 이 함수를 쓴다.
    """
    if not sku_ids:
        return {}
    found = {
        int(row[0]): (row[1], row[2])
        for row in session.execute(
            select(Sku.id, Sku.status, Sku.deleted_at).where(Sku.id.in_(set(sku_ids)))
        ).all()
    }
    return {
        sku_id: ("DELETED" if found[sku_id][1] is not None else str(found[sku_id][0]))
        for sku_id in found
    }
