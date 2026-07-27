"""채번 (DESIGN.md §17.3 "MAX+1 금지 — doc_number_seq 행 잠금 + UNIQUE 이중 안전").

    SO-2026-0001

★ 이 함수는 **업무 트랜잭션 안에서** 불린다. 별도 트랜잭션으로 번호만 먼저
  따 두면, 업무가 롤백돼도 번호는 소비돼 결번이 생긴다. 반대로 같은 트랜잭션에
  두면 롤백 시 번호도 함께 돌아간다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.modules.numbering.models import DocNumberSeq

#: 일련번호 자릿수. 넘치면 자릿수가 늘어날 뿐 번호가 겹치지는 않는다.
SEQUENCE_DIGITS = 4


def next_document_number(session: Session, prefix: str, *, at: datetime | None = None) -> str:
    """다음 문서번호를 발급한다.

    ★ 여기서 하는 일이 정확히 §17.3이다.
      ① (접두어, 연도) 카운터 행을 **SELECT FOR UPDATE로 잠근다** — 잠그지 않으면
         동시 요청 둘이 같은 값을 읽고 같은 번호를 발급한다.
      ② 행이 없으면 만든다. 그 생성 자체도 경합하므로 ON CONFLICT DO NOTHING으로
         받아내고 다시 잠근다(먼저 넣은 쪽이 커밋될 때까지 여기서 대기한다).
      ③ 발급된 번호 쪽 UNIQUE가 두 번째 안전망이다 — 이 로직에 버그가 생겨도
         중복 번호가 전표 테이블에 저장되지는 않는다.
    """
    year = (at or utcnow()).year
    counter = _lock_counter(session, prefix, year)
    if counter is None:
        session.execute(
            insert(DocNumberSeq)
            .values(prefix=prefix, year=year, last_number=0)
            .on_conflict_do_nothing(index_elements=["prefix", "year"])
        )
        session.flush()
        counter = _lock_counter(session, prefix, year)
        if counter is None:  # pragma: no cover — 위 INSERT 후에는 반드시 존재한다
            raise RuntimeError(f"채번 카운터를 만들지 못했습니다: {prefix} {year}")

    counter.last_number += 1
    return f"{prefix}-{year}-{counter.last_number:0{SEQUENCE_DIGITS}d}"


def _lock_counter(session: Session, prefix: str, year: int) -> DocNumberSeq | None:
    return session.execute(
        select(DocNumberSeq)
        .where(DocNumberSeq.prefix == prefix, DocNumberSeq.year == year)
        .with_for_update()
    ).scalar_one_or_none()
