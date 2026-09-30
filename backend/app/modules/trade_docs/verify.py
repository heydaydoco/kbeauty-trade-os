"""합계 검산 — 헤더 합계 = Σ 활성 라인 금액 (S3-1 ADR-0056 / design-A A14 / DESIGN §17.5 이중망).

§17.5의 "라인합=헤더합은 저장 시점 검증+야간 검산 이중망" 중 후자다. 전표를 처음 만드는 세션이 이중망을 완성한다.

■ 읽기 전용·잠금 없음. **불일치는 알림이고 자동 보정하지 않는다**(조용한 보정 금지 — ADMIN이 원인을 본다).
■ 삭제된 헤더만 제외한다(취소 전표도 대상 — 취소는 금액을 지우지 않는다). 라인은 활성(`deleted_at IS NULL`)만 합한다.
■ 테이블 이름 기반이라 아직 만들어지지 않은 전표(PI·SO·PO)는 건너뛴다 — 각 PR이 테이블을 만들면 자동 편입된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Numeric, func, select
from sqlalchemy.orm import Session

from app.core.db.base import Base
from app.core.db.uow import unit_of_work
from app.modules.notifications import service as notifications
from app.modules.trade_docs.constants import (
    DOC_TABLES,
    HEADER_TOTAL_COLUMN,
    LINE_AMOUNT_COLUMN,
    LINE_HEADER_FK,
    LINE_TABLES,
    DocKind,
)


@dataclass(frozen=True, slots=True)
class TotalMismatch:
    doc_kind: DocKind
    doc_id: int
    doc_number: str
    header_total: int
    lines_total: int


def verify_document_totals(session: Session) -> list[TotalMismatch]:
    import app.registry  # noqa: F401 — 모든 전표 모델 적재

    mismatches: list[TotalMismatch] = []
    for kind in DocKind:
        header = Base.metadata.tables.get(DOC_TABLES[kind])
        lines = Base.metadata.tables.get(LINE_TABLES[kind])
        if header is None or lines is None:
            continue
        total_col = header.c[HEADER_TOTAL_COLUMN[kind]]
        line_sum = func.coalesce(func.sum(lines.c[LINE_AMOUNT_COLUMN[kind]].cast(Numeric)), 0)
        rows = session.execute(
            select(header.c.id, header.c.doc_number, total_col, line_sum)
            .select_from(
                header.outerjoin(
                    lines,
                    (lines.c[LINE_HEADER_FK[kind]] == header.c.id) & lines.c.deleted_at.is_(None),
                )
            )
            .where(header.c.deleted_at.is_(None))
            .group_by(header.c.id, header.c.doc_number, total_col)
            .having(total_col != line_sum)
            .order_by(header.c.id)
        ).all()
        mismatches.extend(TotalMismatch(kind, r[0], r[1], int(r[2]), int(r[3])) for r in rows)
    return mismatches


def run_totals_verify() -> dict[str, int]:
    """잡 `trade-docs-totals-verify` 본체 — 불일치마다 ADMIN 인앱 알림(문서번호만·금액 미기재, 일자 dedup).

    검사 예외는 삼키지 않는다(잡 FAILED — §15 실패 알림 소비). 알림이 이미 나간 건은 같은 KST 일자에 다시 만들지 않는다.
    """
    from app.core.time import today_kst

    with unit_of_work() as uow:
        session = uow.session
        found = verify_document_totals(session)
        today = today_kst().strftime("%Y%m%d")
        notified = 0
        for item in found:
            created = notifications.notify(
                session,
                subject_key=f"trade_docs.totals_mismatch:{item.doc_kind.value}:{item.doc_id}:{today}",
                title=f"전표 합계 불일치 — {item.doc_number}",
                body=(
                    f"{item.doc_number}의 헤더 합계와 라인 합계가 다릅니다. "
                    "자동 보정하지 않았으니 원인을 확인해 주세요."
                ),
                severity="CRITICAL",
                routing=notifications.Routing.ADMIN,
                entity_type=DOC_TABLES[item.doc_kind],
                entity_id=item.doc_id,
            )
            notified += len(created)
    return {"mismatches": len(found), "notified": notified}
