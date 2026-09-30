"""전역 잠금 순서와 행 잠금 헬퍼 (S3-1 ADR-0059 / design-integrated §2.9 · design-B B8).

한 트랜잭션의 잠금은 이 순서만 따른다(부분수열 허용 — 건너뛸 수는 있어도 뒤집을 수는 없다). 교착 방지는 코드
리뷰가 아니라 헬퍼(`lock_document`·`chain.lock_chain`·`quantities.lock_lines_for_consumption`)로 구조화한다.

    (0) 멱등 claim 행 → (1) order_intakes → (2) partners(바이어) → (3) QT → (4) PI → (5) SO → (6) PO
    → (7) approvals → (8) 라인(id 오름차순) → (9) doc_number_seq(항상 마지막)

거래처 잠금 모드: 여신 직렬화 경로=`FOR NO KEY UPDATE`, 유형·활성 검증 소비자(전표 생성 등)=`FOR KEY SHARE`,
유형 해제(임포트)=`FOR UPDATE`. 55P03·40P01은 409 `COMMON.CONCURRENCY.LOCK_BUSY`로 번역된다(PR-2 핸들러).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import NotFoundError, VersionConflictError

LOCK_ORDER: tuple[str, ...] = (
    "idempotency_keys",
    "order_intakes",
    "partners",
    "quotations",
    "proforma_invoices",
    "sales_orders",
    "purchase_orders",
    "approvals",
    "lines",
    "doc_number_seq",
)


def lock_order_index(name: str) -> int:
    """잠금 대상 이름의 순서 번호 — 낮은 것부터 잠근다(테스트·헬퍼가 참조)."""
    return LOCK_ORDER.index(name)


def lock_document[T](
    session: Session, model: type[T], doc_id: int, *, expected_version: int | None = None
) -> T:
    """전표 헤더를 `FOR UPDATE`로 잠근다 — 존재·삭제 검증과 낙관 잠금 대조를 한 곳에서 한다.

    `populate_existing`은 필수다: 세션 identity map에 남은 옛 스냅샷이 잠금 뒤 재확인을 무력화하지 않게 한다
    (잠금→재조회→재검증 — 확인→기록 창 방어).
    """
    row: Any = session.execute(
        select(model)
        .where(model.id == doc_id, model.deleted_at.is_(None))  # type: ignore[attr-defined]
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"doc_table": model.__tablename__, "id": doc_id})  # type: ignore[attr-defined]
    if expected_version is not None and row.version != expected_version:
        raise VersionConflictError(
            log_context={"doc_table": model.__tablename__, "id": doc_id}  # type: ignore[attr-defined]
        )
    return row  # type: ignore[no-any-return]
