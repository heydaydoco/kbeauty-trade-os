"""편집 가능 구간의 공통 규칙 — 편집 가드·헤더 version 상승·합계 재계산 (S3-1 design-B B2 / design-A A9·A13).

모든 라인 쓰기는 `assert_editable` → 헤더 `FOR UPDATE`(locking.lock_document) → version 대조 → 라인 변경 →
`recompute_total` → `bump_header_version` 순서다. 라인만 바뀌어도 부모 낙관 잠금이 상승해야 겹친 편집·승인
요청이 409를 우회하지 못한다(S1-3 PR-3 결함 재발 방지).

★ `total_amount`(PO는 `total_cost`) 직접 대입은 이 모듈의 `recompute_total` 안에서만 허용된다 — AST 스캔이 고정한다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Numeric, func, select
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import utcnow
from app.modules.trade_docs.constants import (
    HEADER_TOTAL_COLUMN,
    LINE_AMOUNT_COLUMN,
    LINE_HEADER_FK,
    MAX_LINES,
    MAX_SAFE_INTEGER,
    DocKind,
)
from app.modules.trade_docs.machine import EDITABLE_STATES


def assert_editable(kind: DocKind, status: str, *, fields: list[str] | None = None) -> None:
    """편집 가능 상태(QT:DRAFT·SO:RECEIVED)가 아니면 409 FROZEN — detail은 필드명 목록뿐(값·금액 미기재)."""
    if status not in EDITABLE_STATES[kind]:
        raise AppError(
            ErrorCode.TRADE_DOCS_DOCUMENT_FROZEN,
            detail={"fields": sorted(fields)} if fields else {},
            log_context={"kind": kind.value, "status": status},
        )


def bump_header_version(header: Any) -> None:
    """헤더를 dirty로 만들어 version_id_col이 +1 되게 한다(라인만 바뀐 경우에도 부모 version 상승)."""
    header.updated_at = utcnow()


def compute_total(amounts: list[int]) -> int:
    """라인 금액 합 — 2^53−1 초과는 422(프런트 number 보호). 생성 경로가 합을 미리 구할 때 쓴다."""
    total = sum(amounts)
    if total > MAX_SAFE_INTEGER:
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE,
            detail={"total": "문서 합계가 허용 범위를 넘었습니다."},
        )
    return total


def recompute_total(session: Session, kind: DocKind, header: Any, line_model: Any) -> int:
    """헤더 합계 = Σ 활성 라인 금액 — 라인 변경 트랜잭션마다 헤더 행 잠금 하에서 재계산·저장한다."""
    amount_col = getattr(line_model, LINE_AMOUNT_COLUMN[kind])
    fk_col = getattr(line_model, _line_fk(kind))
    total = session.execute(
        select(func.coalesce(func.sum(amount_col.cast(Numeric)), 0)).where(
            fk_col == header.id, line_model.deleted_at.is_(None)
        )
    ).scalar_one()
    value = compute_total([int(total)])
    setattr(header, HEADER_TOTAL_COLUMN[kind], value)
    return value


def count_live_lines(session: Session, kind: DocKind, header_id: int, line_model: Any) -> int:
    fk_col = getattr(line_model, _line_fk(kind))
    return int(
        session.execute(
            select(func.count())
            .select_from(line_model)
            .where(fk_col == header_id, line_model.deleted_at.is_(None))
        ).scalar_one()
    )


def require_line_capacity(current_lines: int, adding: int = 1) -> None:
    """한 전표의 라인 수 상한(500) — 합계 검산·응답 크기 보호."""
    if current_lines + adding > MAX_LINES:
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE,
            detail={"lines": f"한 문서의 라인은 최대 {MAX_LINES}개입니다."},
        )


def _line_fk(kind: DocKind) -> str:
    return LINE_HEADER_FK[kind]
