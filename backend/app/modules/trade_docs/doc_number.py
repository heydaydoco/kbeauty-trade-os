"""전표 채번 래퍼 (S3-1 ADR-0054 / design-B B4) — `numbering.next_document_number`의 전표 전용 진입점.

접두어는 `DOC_PREFIXES`가 유일 출처이고 전표 서비스가 접두어 문자열을 직접 쓰지 않는다(아키텍처 테스트가 스캔).
발급은 **행 최초 저장 트랜잭션의 마지막 단계**(모든 검증·라인 구성 후, INSERT 직전)다 — 카운터 행 잠금은 트랜잭션
끝까지 유지되므로 잠금 보유 시간을 최소화한다(잠금 순서 (9) doc_number_seq 항상 마지막). 연도는 발급 시각의 KST다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.numbering.service import next_document_number
from app.modules.trade_docs.constants import DOC_PREFIXES, DocKind


def issue_document_number(session: Session, kind: DocKind, *, at: datetime | None = None) -> str:
    return next_document_number(session, DOC_PREFIXES[kind], at=at)
