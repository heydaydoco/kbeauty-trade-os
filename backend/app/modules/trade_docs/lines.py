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
