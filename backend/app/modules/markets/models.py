"""시장 마스터 (DESIGN.md §5.1·§3 [M2] / WBS S2-1 / s2-1-plan.md §0-1 ②).

■ code는 전역 UNIQUE다 — unique_active가 아니다 (S2-1 판정 조건 3)

  labels·ingredient_rules·sku_hs_codes의 country_code가 이 코드를 **값으로
  참조**한다(FK → markets.code). FK는 부분 유니크 인덱스를 참조할 수 없고,
  같은 코드의 신·구 행이 공존하면 코드 참조의 지시 대상이 무너진다.
  §17.4의 부분 유니크 규율은 **멱등 키** 대상이고, 이 코드는 참조 대상
  자연키 레지스트리다 — soft delete된 시장 코드의 재사용은 신규 행이 아니라
  **복원(명시 액션)**이 정본이다(판정 §0-1 ② 문면. 삭제 액션 자체가 아직
  없으므로 복원 액션도 그때 함께 온다).

■ 시드가 아니다 (계획서 §2 "시드 0건" / 함정 ⑩ 비적용)

  T1 시드 투입은 S2-4 몫이고, FK 승격 backfill이 만드는 행은 기존 데이터의
  승격(MIG 계보 — 빈 DB에서 0행)이다. 믹스인 전부(Actor 포함)를 단 채로
  마이그레이션 시드를 넣으면 함정 ⑩(users FK가 TRUNCATE CASCADE로
  PRESERVED_TABLES를 무력화)이 재발한다 — 이 상충은 관찰 원장에 등재됐고
  S2-4 계획 판정 안건이다(조건 12).

■ CBEC·Tier는 컬럼이 아니다 (계획서 §2-1)

  중국 CBEC는 §5.5 문면이 "CBEC **별도 템플릿**"이라 시장 축(CN)이 아니라
  템플릿 축에서 갈리고, Tier(T1~T3)는 템플릿 준비 우선순위라는 문서 개념이다.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)


class Market(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """시장 (§3 [M2] markets / §5.1 요건의 시장 축)."""

    __tablename__ = "markets"

    #: 시장 코드 — 기존 3테이블 country_code와 동일 형식(대문자 2자).
    #: EU처럼 ISO 국가가 아닌 시장 코드도 이 형식이 수용한다(실데이터 선례).
    code: Mapped[str] = mapped_column(String(2), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{2}$'", name="code_format"),
        # ★ 전역 UNIQUE — 위 독스트링. 소프트 삭제 행도 코드를 점유한다.
        UniqueConstraint("code"),
    )
