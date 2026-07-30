"""자재·BOM·라벨 (DESIGN.md §4.4·§4.5 / WBS S1-2).

■ 자재 BOM은 원산지·원가·구매·사급의 공통 전제다 (§4.4)

  `product_boms`가 제품(처방)에 들어가는 자재와 소요량·단가·원산지상태를 갖는다.
  S3-4의 RVC 계산(§6.3)이 "비원산지 재료 합계"를 이 표에서 만들고, 판정 시점의
  값은 §6.2의 BOM 스냅샷이 동결한다.

■ 자재 BOM과 세트 구성은 다른 것이다 (ADR-0016)

  `set_components`는 SKU→SKU(실물 키팅), `product_boms`는 제품→자재(처방 구성)다.
  하나로 뭉치면 P3 판정 스냅샷과 P4 조립 원장이 같은 표를 서로 다른 뜻으로 읽는다.

■ 원산지 "미상"이 기본값이다 (§4.4 / GC-B2)

  §4.4 원문이 "미상은 판정 시 역외 간주"라고 못박는다. 기본값을 역내나 NULL로
  두면 확인하지 않은 자재가 유리한 쪽으로 계산된다 — 판정(S3-4) 전에 마스터가
  먼저 보수적이어야 한다.

■ 라벨의 판번은 `label_version`이다

  `version`은 VersionMixin의 낙관적 잠금 카운터라(§17.2) 업무 판번으로 쓸 수
  없다. 같은 이름으로 두면 메모 한 줄 고칠 때마다 라벨 판번이 올라가고,
  유일키(sku·국가·판번)까지 오염된다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
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

#: 자재 유형 (§4.4 "벌크/원료/용기/단상자/라벨/부자재").
MATERIAL_TYPES = ("BULK", "RAW_MATERIAL", "CONTAINER", "CARTON", "LABEL", "AUX")

#: BOM 라인의 원산지 상태 (§4.4). UNKNOWN(미상)은 판정 시 역외 간주다(GC-B2).
BOM_ORIGIN_STATUSES = ("DOMESTIC", "FOREIGN", "UNKNOWN")

#: 라벨 승인 상태. 전이 강제는 하지 않는다 — 상태 머신 명문은 §5.2(인증)뿐이고,
#: ADR-11의 "상태는 고정, 태스크는 자유"에서 여기 해당하는 것은 값의 고정까지다.
LABEL_APPROVAL_STATUSES = ("DRAFT", "APPROVED", "RETIRED")


class Material(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """자재 마스터 (§3 [M1] materials / §4.4).

    ★ 재고 편입은 **선택**이다 — §4.4가 "사급하는 자재만 선택적으로 재고 편입"
      이라고 한다. 플래그만 여기 두고, 실제 원장 편입은 P4(§8.1)다.
    """

    __tablename__ = "materials"

    #: 사내 자재 코드. 사람이 정한다(brands·products와 같은 관례).
    material_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    material_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: HS6 (§4.4). NULL 허용 — 등록 문턱을 낮춘다. 결측 탐지는 §14 ⑭ 헬스체크
    #: 항목('HS6 없는 자재')이 P6에서 맡고, 소비자는 §6.3의 CTC 전수 대조다.
    hs6: Mapped[str | None] = mapped_column(String(6), nullable=True)
    #: S1-3에서 partners FK로 승격한다(ADR-0020 부기).
    default_supplier_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: 재고관리 대상인가(§4.4 "재고관리 플래그"). 원장 편입은 P4.
    inventory_managed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: 로트관리 대상인가(§4.4 "로트관리 플래그").
    lot_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("material_type", MATERIAL_TYPES),
        CheckConstraint("hs6 ~ '^[0-9]{6}$'", name="hs6_digits"),
        # 로트관리는 재고관리를 전제한다 — 원장에 없는 품목의 로트는 §8.1의
        # "로트 없는 재고 금지"를 뒤집은 "재고 없는 로트"라 추적할 대상이 없다.
        CheckConstraint(
            "lot_managed = false OR inventory_managed = true",
            name="lot_managed_requires_inventory_managed",
        ),
        unique_active("materials", "material_code"),
    )


class ProductBom(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """자재 BOM — 제품(처방)×자재 (§4.4 / GC-B1·B2).

    ■ 단가는 이력을 두지 않는다 (ADR-0017 비적용)

      `sku_prices`가 발효일 이력인 이유는 "그때 단가" 분쟁(§4.1)과 전표 스냅샷
      때문이다. BOM 단가는 그 자리가 다르다 — 판정 시점의 값을 얼어붙게 하는
      것은 §6.2가 규정한 **판정 BOM 스냅샷**이고(S3-4), 마스터는 현재 값 하나만
      들면 된다. 변경 추적은 version + audit_log가 맡는다.

    ■ 단가는 원가다

      조회 역할에게는 `unit_cost`·`currency` **필드 자체가 응답에 없다**
      (ADR-0024). 행 단위로 감추는 sku_prices와 다른 이유는 이 행이 원가만
      담은 행이 아니기 때문이다 — 소요량·원산지상태는 인증·판정 업무에 필요하다.
    """

    __tablename__ = "product_boms"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    material_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
    )
    #: 제품 1단위당 소요량. 물리량이라 NUMERIC이다(ADR-0003 ⑧).
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    #: 소요량의 단위(g·ml·EA…). 값 목록을 못박지 않는다 — 자재마다 관행이 다르고,
    #: 원장 단위 규율(§8.2 EA 단일)은 재고의 일이지 BOM의 일이 아니다.
    quantity_unit: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 매입 단가 = **원가**. 정수 최소단위(§2 ADR-02, Float 금지 — GC-G1).
    #: 이름이 `_cost`로 끝나 로그 마스킹 키와 금액 컬럼 판정에 함께 걸린다.
    unit_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    #: 역내/역외/미상 — 미상이 기본값이다(§4.4 "미상은 판정 시 역외 간주").
    origin_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="UNKNOWN")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        positive("quantity"),
        value_in("origin_status", BOM_ORIGIN_STATUSES),
        # 금액과 통화는 한 쌍이다 — 통화 없는 금액은 해석할 수 없고, 금액 없는
        # 통화는 뜻이 없다(§2 ADR-02 "금액=정수 최소단위+통화코드").
        CheckConstraint(
            "(unit_cost IS NULL AND currency IS NULL)"
            " OR (unit_cost IS NOT NULL AND currency IS NOT NULL)",
            name="cost_currency_pair",
        ),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonnegative"),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        unique_active("product_boms", "product_id", "material_id"),
    )


class Label(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """라벨·아트웍 버전 — SKU×시장 (§4.5).

    §4.5의 검증 항목 중 **INCI 현지어 표기**와 **원산지 표기(Made in Korea)**를
    체크 두 칸으로 든다. 시스템이 라벨 이미지를 읽어 판정하지 않는다 — 사람이
    확인한 사실의 기록이다(§1 비범위, HS·성분 규칙과 같은 지위).

    컷인 시 구라벨 재고 소진 리포트(§4.5)는 재고 원장이 서는 P4다(관찰 항목).
    """

    __tablename__ = "labels"

    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    #: 시장. markets 테이블은 S2-1이라 아직 국가코드 문자열이다(ADR-0023).
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    #: 라벨 판번(1부터). ★ `version`이 아니다 — 그건 낙관적 잠금이다(위 독스트링).
    label_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 표기 언어(ko·en·zh-CN…). 국가와 다르다 — 한 시장에 다국어 라벨이 있다.
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="DRAFT")
    #: 컷인일(업무 날짜). 승인 전 선입력을 허용한다 — 일정이 먼저 잡히는 실무가 있다.
    cut_in_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: S1-3에서 documents 링크로 승격한다(ADR-0020 부기).
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: §4.5 검증 항목 — 사람이 확인했다는 기록이다.
    inci_local_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    origin_mark_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_format"),
        value_in("approval_status", LABEL_APPROVAL_STATUSES),
        positive("label_version"),
        # ★ language가 키에 들어간다. 빼면 "한 시장에 다국어 라벨"(위 language
        #   컬럼의 존재 이유)이 성립하지 않는다 — 캐나다 영문판 v1을 넣은 뒤
        #   불문판을 v1로 못 넣어 판번을 올려야 하고, 그 순간 판번이 "아트웍
        #   개정"이 아니라 "언어 추가"로도 증가해 §4.5 컷인의 기준이 흐려진다.
        unique_active("labels", "sku_id", "country_code", "language", "label_version"),
    )
