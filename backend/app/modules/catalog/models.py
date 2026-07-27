"""SKU (DESIGN.md §4.1 / §19 Phase 0 "SKU 등록→목록→CSV 끝-끝 관통").

★ 여기 있는 것은 **관통용 최소 컬럼**이다. 바코드·중량·박스입수·shelf_life·
  제조사·DG 속성(UN번호·Class·PG·인화점·MSDS 링크) 전체는 §4.1대로 **S1-1**이
  추가한다. 지금 다 만들면 쓰이지 않는 컬럼이 먼저 굳고, S1-1이 그것들을
  "이미 있으니까" 그대로 쓰게 된다.

제품(처방)과 SKU의 분리(§4.1)도 S1-1이다 — 인증·원산지 판정은 처방 단위,
재고·판매는 SKU 단위라는 구분은 products 테이블이 생겨야 표현된다.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

#: 판매 상태. 단종된 SKU는 지우지 않는다 — 과거 전표가 참조한다.
SKU_STATUSES = ("ACTIVE", "DISCONTINUED")


class Sku(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """재고·판매의 단위."""

    __tablename__ = "skus"

    #: 사내 품번. 사람이 정한다 — §17.3의 채번(SO-2026-0001)은 전표 번호이지
    #: 마스터 코드가 아니다.
    sku_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 수출 서류·채널 리스팅에 쓰이는 영문명(§7.6 서류 생성기가 소비한다).
    name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")

    __table_args__ = (
        value_in("status", SKU_STATUSES),
        unique_active("skus", "sku_code"),
    )
