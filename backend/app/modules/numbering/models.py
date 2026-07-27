"""문서번호 채번 (DESIGN.md §17.3 / §3 "채번 `SO-2026-0001`, MAX+1 금지").

★ MAX+1을 쓰지 않는 이유가 이 테이블의 존재 이유다.
  `SELECT MAX(no)+1`은 동시에 들어온 두 요청이 같은 값을 읽고 같은 번호를
  발급한다. 취소된 전표가 있으면 번호가 재사용되기까지 한다(§17.3 "취소 건
  번호 재사용 금지"). 그래서 카운터를 행 하나로 두고 그 행을 잠근다.

  발급된 번호 쪽에도 UNIQUE를 걸어 이중으로 막는다 — 카운터 로직에 버그가
  생겨도 중복 번호가 저장되지는 않게(§17.3 "행 잠금 + UNIQUE 이중 안전").
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin, TimestampMixin


class DocNumberSeq(PkMixin, TimestampMixin, Base):
    """(접두어 × 연도) 하나당 카운터 한 행.

    soft delete를 쓰지 않는다. 카운터를 지운다는 것은 곧 번호를 처음부터 다시
    발급한다는 뜻이고, 그건 §17.3이 금지하는 번호 재사용이다.
    """

    __tablename__ = "doc_number_seq"

    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 마지막으로 발급한 일련번호. 다음 발급은 이 값 + 1.
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("prefix", "year", name="uq_doc_number_seq_prefix_year"),
        CheckConstraint("last_number >= 0", name="last_number_nonnegative"),
        CheckConstraint("year BETWEEN 2000 AND 2999", name="year_plausible"),
    )
