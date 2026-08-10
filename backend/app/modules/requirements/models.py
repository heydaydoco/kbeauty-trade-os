"""요건 템플릿 (DESIGN.md §5.1 / §2 ADR-03 / s2-1-plan.md §0-1 ①·④ — S2-1 PR-2).

■ 근거 2필드가 nullable인 이유 — 초안 상태가 실재해야 한다 (§5.5)

  §5.5가 "근거 링크 없는 항목 **확정 차단**"을 명시한다 — 근거 없는 초안의
  존재가 전제다. sku_hs_codes·ingredient_rules의 NOT NULL과 다른 이유:
  그 둘은 확정 개념 없는 즉시 등록 데이터다. 강제는 이중이다:
  ① DB CHECK(confirmed_requires_evidence) — 확정 상태의 행은 어떤 경로로도
    근거를 비울 수 없다.
  ② 서비스 확정 액션 — 사람 1클릭 + idempotency key(§17.4), 근거 없으면 422.

■ 확정 상태의 편집 규칙 (판정 조건 11 — ADR-0033)

  CONFIRMED 행의 내용 편집은 서비스가 차단한다(409) — 편집하려면 명시 액션으로
  초안 전환(revert) 후 수정하고 **재확정**한다. PATCH로 status='CONFIRMED'를
  넣는 경로는 스키마·서비스가 거부한다(확정은 확정 액션 하나뿐). DB CHECK는
  그 아래의 마지막 층이다 — 어느 층이 뚫려도 근거 없는 확정 행은 생기지 않는다.

■ estimated_cost는 비마스킹 (판정 안건 4 — 마스킹 원장 #3)

  ADR-0018·0024의 보호 대상은 원가·마진이고, 인증 예상 비용은 요건 준비 판단이
  소비하는 운영 데이터다(여신한도 ADR-0026 계보). 네이밍 `_amount` 접미사로
  금액 감지기(is_money_column_name)에 자동 편입된다 — 유니크 키 금지 스캔 대상.

■ 시드가 아니다 — T1 템플릿 시드는 S2-4 몫 (조건 12·함정 ⑩)

  믹스인 전부(Actor 포함)를 단 편집 마스터라 마이그레이션 시드를 넣을 수 없다
  (ActorMixin의 users FK가 TRUNCATE CASCADE로 PRESERVED_TABLES를 무력화).
  시드 투입 방식은 S2-4 계획 판정 안건이다(관찰 원장 등재).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
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

#: 적용단위 5종 — WBS S2-1 DoD 문면 그대로(처방/SKU/시설/기업/성분).
APPLIES_TO_VALUES = ("PRODUCT", "SKU", "FACILITY", "COMPANY", "INGREDIENT")

#: 템플릿 상태 3값 — 라벨 선례(ADR-0025): 값 고정까지. 전이는 서비스 액션이
#: 관리한다(확정·초안 전환 — ADR-0033). CONFIRMED 진입은 확정 액션 하나뿐이다.
TEMPLATE_STATUSES = ("DRAFT", "CONFIRMED", "RETIRED")


class RequirementTemplate(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """요건 템플릿 헤더 (§5.1 3층 모델의 1층 — 시장별 요건 정의)."""

    __tablename__ = "requirement_templates"

    market_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 유형은 코드 문자열로 시작한다 — 별도 유형 테이블 승격은 필터·집계 소비가
    #: 생길 때 재판정(ADR-11 승격 규율, 판정 §0-1 ①).
    requirement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: 주기 3필드는 요건마다 없을 수 있다 — MoCRA 시설등록(격년) 같은 주기형과
    #: 1회성 요건이 공존한다(계획서 §2-2).
    validity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    renewal_cycle_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    renewal_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 예상 비용 — 금액 규약(ADR-0003 ④: BIGINT 최소단위+CHAR(3) 쌍 CHECK,
    #: credit_limit 선례 꼴). 비마스킹(판정 안건 4 — 마스킹 원장 #3).
    estimated_cost_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_cost_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    #: 근거 2필드(ADR-03) — nullable인 이유는 파일 독스트링. 확정의 전제 조건.
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="DRAFT")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("applies_to", APPLIES_TO_VALUES),
        value_in("status", TEMPLATE_STATUSES),
        CheckConstraint(
            "requirement_type ~ '^[A-Z][A-Z0-9_]{0,39}$'", name="requirement_type_format"
        ),
        positive("validity_months"),
        positive("renewal_cycle_months"),
        positive("renewal_lead_days"),
        # 금액과 통화는 한 쌍이다(ADR-0003 ④ — partners.credit_limit_pair와 같은 꼴).
        CheckConstraint(
            "(estimated_cost_amount IS NULL AND estimated_cost_currency IS NULL)"
            " OR (estimated_cost_amount IS NOT NULL AND estimated_cost_currency IS NOT NULL)",
            name="estimated_cost_pair",
        ),
        CheckConstraint("estimated_cost_amount >= 0", name="estimated_cost_amount_nonnegative"),
        CheckConstraint(
            "estimated_cost_currency = upper(estimated_cost_currency)",
            name="estimated_cost_currency_uppercase",
        ),
        # 공백 URL이 근거 게이트를 통과하면 게이트가 형식이 된다(sku_hs_codes 선례).
        CheckConstraint(
            "source_url IS NULL OR length(btrim(source_url)) > 0",
            name="source_url_not_blank",
        ),
        # ★ 확정 게이트의 마지막 층 — 확정 상태의 행은 근거 2필드를 비울 수 없다
        #   (§5.5 "근거 링크 없는 항목 확정 차단" / GC-C8 / ADR-0033).
        CheckConstraint(
            "status <> 'CONFIRMED' OR (source_url IS NOT NULL AND last_verified_on IS NOT NULL)",
            name="confirmed_requires_evidence",
        ),
        unique_active("requirement_templates", "market_id", "name"),
    )


class TemplateChecklistItem(
    PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """템플릿 체크리스트 (§5.1 — certification_tasks의 원천, §5.6 공용 문서 참조).

    S2-2가 인스턴스 생성 시 certification_tasks로 참조 복사한다(ADR-05) —
    이번 세션은 정의까지. document_type_id가 nullable인 이유: 서류 없는 행정
    절차 항목이 실재한다(§5.6은 서류 참조 항목만 연결).
    """

    __tablename__ = "template_checklist"

    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("requirement_templates.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    document_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=True
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        positive("seq"),
        unique_active("template_checklist", "template_id", "seq"),
    )


class TemplatePrerequisite(
    PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """선행요건 교차 테이블 (§5.1 "선행요건" — 판정 §0-1 ① 확정, 조건 1).

    다중 선행의 실재(중국 비안=경내책임자+안전성 자료 선행, EU 통보=RP 선행 —
    웹 세션 도메인 판정)가 교차 테이블을 확정했다. 0·1·다수 전부 포용.

    자기참조는 DB CHECK가 막고, **순환 참조는 서비스가 거부한다**(깊이 있는
    그래프 제약은 DB CHECK로 불가 — service.require_no_cycle). 소비(선행
    미충족 표시·태스크 순서)는 S2-2 이후다.
    """

    __tablename__ = "template_prerequisites"

    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("requirement_templates.id", ondelete="RESTRICT"), nullable=False
    )
    prerequisite_template_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "requirement_templates.id",
            ondelete="RESTRICT",
            # 규약 이름(fk_<table>_<col>_<referred>)이 63자를 넘어 수기 축약 —
            # item_profile_document_types의 유니크 이름 축약과 같은 사정.
            name="fk_template_prerequisites_prerequisite_requirement_templates",
        ),
        nullable=False,
    )

    __table_args__ = (
        # 조건 1 — 자기 자신을 선행요건으로 걸 수 없다.
        CheckConstraint("template_id <> prerequisite_template_id", name="no_self_reference"),
        # unique_active()가 만들 이름이 63자 상한을 넘어 수기 축약(같은 꼴).
        Index(
            "uq_template_prerequisites_pair_active",
            "template_id",
            "prerequisite_template_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ItemProfileRequirementTemplate(
    PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base
):
    """품목군 → 기본 요건 세트 (§4.8 / ADR-0021 "요건=S2-1" — 부채 #15의 S2-1 몫).

    구조는 item_profile_document_types(S1-3 ⑧) 선례 그대로다. "신규 등록 시
    자동 적용"은 적용 결과물(certifications)이 서는 S2-2 이후(§3-2 재이월).
    """

    __tablename__ = "item_profile_requirement_templates"

    item_profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "item_profiles.id",
            ondelete="RESTRICT",
            # 규약 이름이 63자를 넘어 수기 축약.
            name="fk_item_profile_requirement_templates_profile",
        ),
        nullable=False,
    )
    requirement_template_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "requirement_templates.id",
            ondelete="RESTRICT",
            # 규약 이름이 63자를 넘어 수기 축약.
            name="fk_item_profile_requirement_templates_template",
        ),
        nullable=False,
    )

    __table_args__ = (
        # unique_active()가 만들 이름이 63자 상한을 넘어 수기 축약(선례 동일).
        Index(
            "uq_item_profile_requirement_templates_profile_template_active",
            "item_profile_id",
            "requirement_template_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
