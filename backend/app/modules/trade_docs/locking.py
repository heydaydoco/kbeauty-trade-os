"""전역 잠금 순서와 행 잠금 헬퍼 (S3-1 ADR-0059 / design-integrated §2.9 · design-B B8).

한 트랜잭션의 잠금은 이 순서만 따른다(부분수열 허용 — 건너뛸 수는 있어도 뒤집을 수는 없다). 교착 방지는 코드
리뷰가 아니라 헬퍼(`lock_document`·`chain.lock_chain`·`quantities.lock_lines_for_consumption`)로 구조화한다.

    (−1) 파일 해시 advisory xact lock(CSV 입구 전용, 트랜잭션 첫 문장 — `order_intake.csv_import`, PR-14a)
    → (0) 멱등 claim 행 → (1) order_intakes → (2) partners(바이어·당사자·관세사) → (3) QT → (4) PI → (5) SO → (6) PO
    → (7) shipments → (8) shipment_children → (9) approvals → (10) 라인(id 오름차순) → (11) doc_number_seq(항상 마지막)

S3-2 PR-3a 개정(ADR-0078 — §17.2 부기 ② "변경은 ADR"): 선적은 SO·PO의 후속이라 조상 → 자기 순서로 (7)에 들어간다. (8)
`shipment_children` = 선적 1건의 비-라인 하위 행(당사자 — 마일스톤·통관 기록은 PR-4a가 같은 슬롯을 쓴다), 여러 행이면 id 오름차순.
**PO 소유 OEM 마일스톤 행도 이 슬롯**이다(T13, PR-4c — 선적 없이 (6) PO 다음에 바로 (8)을 잡는 부분수열).
라인 범주는 **원천 라인 → 선적 라인**(id 순). 잠금 모드: SO 수렴을 동반할 수 있는 선적 쓰기(생성·라인·출고지시·취소)는 SO를
`FOR UPDATE`로 **선점**한다(`lock_lines_for_consumption`의 헤더 `FOR SHARE`는 기보유 잠금에 흡수 — SHARE→UPDATE 승격 교착 차단).
당사자 쓰기는 멱등 → partners `FOR KEY SHARE`(id 순) → shipments(R-08 — 거래처 검증이 선적 잠금 뒤로 가지 않게).
PO `FOR SHARE`(PO 무수정 — PO 취소의 `FOR UPDATE`와 직렬화)는 두 경로다: **T2** 수입선적 생성(PR-5a)과 **T13** OEM 생산 일정 계획·실적
(PR-4c — 멱등 → purchase_orders `FOR SHARE` → (8) milestones `FOR UPDATE` + 행 version).

(−1)은 행 잠금이 아니라 같은 파일(sha256)의 업로드끼리만 직렬화하는 advisory 잠금이라 `LOCK_ORDER` 튜플(행 잠금 대상)에는 넣지 않는다 —
트랜잭션의 첫 문장이므로 어떤 행 잠금보다 앞선다. 한 트랜잭션 안에서 같은 표의 여러 행을 잠그는 곳(임포트 확정 `load_targets_for_update` 등)은 **id 오름차순**이다.

거래처 잠금 모드: 여신 직렬화 경로=`FOR NO KEY UPDATE`, 유형·활성 검증 소비자(전표 생성 등)=`FOR KEY SHARE`,
유형 해제(임포트)=`FOR UPDATE`. 55P03·40P01은 409 `COMMON.CONCURRENCY.LOCK_BUSY`로 번역된다(PR-2 핸들러).

전표 사슬 밖의 잠금(같은 트랜잭션에 전표 잠금과 섞이지 않는다 — 순서표 튜플에는 넣지 않는다):
  · 오더 보드 저장 필터 등록(S3-1 PR-15a): (0) 멱등 claim → **사용자 단위 advisory lock**
    `pg_advisory_xact_lock(order_board.saved_filters.SAVED_FILTER_LOCK_NS, user_id)`(2인자 키 공간 — 스케줄러 잡 잠금 `SCHEDULER_LOCK_KEY`와
    네임스페이스가 다르다) → `board_saved_filters` 재계수·INSERT. 행 잠금이 아닌 이유: 행이 0개일 때도 직렬화해야 상한(20)이 지켜진다.
  · 오더 보드 벌크(S3-1 PR-15a): 자체 잠금 없음 — 건마다 독립 트랜잭션에서 단일 통로가 위 순서를 지킨다.
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
    "shipments",
    "shipment_children",
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
