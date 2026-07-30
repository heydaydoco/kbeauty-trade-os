"""성분(INCI)·전성분·성분 규칙 (DESIGN.md §4.3 / WBS S1-2).

■ 자사 취급 성분만 등록한다 (§4.3, §1 비범위)

  전 세계 성분 DB를 만들지 않는다. 여기 있는 것은 우리 처방에 실제로 들어가는
  성분과, 사람이 근거를 확인해 적어 넣은 국가별 규칙뿐이다.

■ 전성분은 처방(제품) 단위다 (§4.1·§4.3)

  인증·원산지 판정과 같은 이유다 — 30ml·50ml SKU가 같은 처방이면 전성분도
  하나다. 세트 SKU는 처방을 갖지 않으므로(ADR-0016) 전성분도 없다. 구성품이
  각자의 전성분을 갖는다.

■ INCI명은 표기를 보존하고, 유일성은 정규화 컬럼이 잡는다

  'Aqua'와 'AQUA'가 다른 행으로 들어오면 유일키가 중복을 못 잡는다 — HS 세번의
  점·공백 정규화(service._normalized_hs_code)와 같은 문제다. 표기(대소문자)는
  라벨 인쇄 관행이 있어 버릴 수 없으므로, 원문 컬럼과 정규화 컬럼(공백 축약 +
  대문자)을 나란히 두고 유일키는 정규화 컬럼에 건다.

■ 같은 성분×국가에 규칙은 한 행이다 (웹 세션 판정 2026-07-30)

  금지·제한이 공존하면 스크리닝이 같은 성분을 "금지"이면서 "제한초과"로도
  집계하는 모순 리포트가 가능해진다. 용도별(린스오프/리브온) 상이 한도는
  §4.3에 없는 축이라 담지 않는다 — 실데이터로 요구가 발생하면 재판정한다
  (PROGRESS 관찰 항목).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import positive, unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

#: 성분 규칙의 종류 (§4.3 "금지/제한").
#: PROHIBITED — 해당 국가에서 사용 금지. RESTRICTED — 농도 한도 내에서만 허용.
#: "미등재"는 값이 아니다 — 규칙 행이 **없다**는 사실이 곧 미등재다(스크리닝 §4.3).
INGREDIENT_RULE_TYPES = ("PROHIBITED", "RESTRICTED")


class Ingredient(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """성분 마스터 (§3 [M1] ingredients).

    별도 코드를 두지 않는다 — INCI명이 국제 표준 자연키다. 마스터 코드는 사람이
    정한다는 관례(brands 참조)에서 "사람이 정한 것"이 이미 INCI명이다.
    """

    __tablename__ = "ingredients"

    #: INCI 표기 원문. 라벨 인쇄가 이 표기를 쓴다.
    inci_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 유일성 판정용 정규화(공백 축약 + 대문자). 서비스가 만든다 — 화면·서류에
    #: 쓰는 값이 아니다.
    inci_name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 한글 표시명(전성분 국문 표기). 없을 수 있다 — 신규 원료는 국문명이 늦다.
    name_ko: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("length(btrim(inci_name)) > 0", name="inci_name_not_blank"),
        CheckConstraint(
            "inci_name_normalized = upper(inci_name_normalized)",
            name="inci_name_normalized_upper",
        ),
        unique_active("ingredients", "inci_name_normalized"),
    )


class ProductIngredient(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """전성분 — 제품(처방)×성분, 함량·표시순서 (§4.3)."""

    __tablename__ = "product_ingredients"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    #: 함량(%). NULL 허용 — OEM 처방은 함량이 비공개인 성분이 실무로 존재한다.
    #: 함량 없는 제한 성분은 스크리닝이 fail-closed로 "제한초과" 버킷에 넣는다
    #: (웹 세션 판정 D — 0이나 통과가 아니라 보수 분류).
    concentration_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    #: 전성분 표시순서. INCI 라벨 표기 순서의 원천 — 필수다.
    #: 순서 유일 제약은 걸지 않는다: 1% 이하 성분은 임의 순서 표기가 관행이라
    #: 같은 순번이 실무에 존재한다.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        positive("display_order"),
        positive("concentration_pct"),
        CheckConstraint("concentration_pct <= 100", name="concentration_pct_max"),
        unique_active("product_ingredients", "product_id", "ingredient_id"),
    )


class IngredientRule(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """성분×국가 규칙 (§4.3 / ADR-03 / ADR-0019 패턴).

    ■ 판정이 아니라 **사람이 확인해 적어 넣은 규칙의 기록**이다 (§1 비범위)

      sku_hs_codes와 같은 지위다. 근거링크·최종확인일이 NOT NULL인 이유도 같다
      (ADR-03: "모든 요건·세율·기한 데이터에 근거링크+최종확인일 필수") —
      nullable로 두면 "나중에 채우겠다"가 영구 미확인 규칙이 된다.

    ■ 제한이면 한도 필수, 금지면 한도 금지 — 양방향 CHECK

      skus.kind_product_link와 같은 이유로 양방향이다. 한쪽만 걸면 "한도 없는
      제한"(스크리닝이 판정 불능)이나 "한도 있는 금지"(어느 쪽이 진실인지 모호)가
      조용히 들어온다.
    """

    __tablename__ = "ingredient_rules"

    ingredient_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    #: ISO 3166-1 alpha-2. markets 테이블은 S2-1이라 아직 코드 문자열이다
    #: (sku_hs_codes와 같은 처지 — 승격 재판정은 S2-1 계획 보고, PROGRESS 관찰).
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 농도 한도(%). RESTRICTED 전용.
    max_concentration_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    #: ADR-03: 근거링크+최종확인일 필수.
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 업무 날짜다(시각이 아니다) — §2 ADR-02의 UTC 규율은 timestamptz 대상.
    last_verified_on: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_format"),
        value_in("rule_type", INGREDIENT_RULE_TYPES),
        # ★ 양방향이다 — 한쪽만 걸면 판정 불능 행이 조용히 들어온다(위 독스트링).
        CheckConstraint(
            "(rule_type = 'RESTRICTED' AND max_concentration_pct IS NOT NULL)"
            " OR (rule_type = 'PROHIBITED' AND max_concentration_pct IS NULL)",
            name="rule_type_limit_link",
        ),
        positive("max_concentration_pct"),
        CheckConstraint("max_concentration_pct <= 100", name="max_concentration_pct_max"),
        CheckConstraint("length(btrim(source_url)) > 0", name="source_url_not_blank"),
        unique_active("ingredient_rules", "ingredient_id", "country_code"),
    )
