"""제품 계층 — 브랜드·제품(처방)·SKU (DESIGN.md §4.1·§4.2 / WBS S1-1).

■ 왜 제품과 SKU가 갈라져 있나 (§4.1)

  **인증·원산지 판정은 처방(제품) 단위, 재고·판매는 SKU 단위다.** 같은 처방을
  30ml·50ml로 담으면 SKU는 둘이지만 CPNP 통보도 원산지 판정도 하나다. 이걸
  SKU 하나로 합쳐 두면 판정을 용량 수만큼 중복 등록하게 되고, 그중 하나만
  갱신된 상태가 조용히 생긴다.

■ 세트 SKU는 처방에 속하지 않는다 (§4.2 / ADR-0016)

  세트는 서로 다른 처방의 구성품을 담은 실물이라 처방 **하나**를 가리킬 수
  없다. §4.2 원문이 "인증·원산지 판정·C/O는 세트가 아니라 구성품 처방별
  유지(세트 화면은 구성품 판정의 롤업 뷰)"라고 못박는다.

  그래서 `kind`로 가르고 CHECK로 **양방향** 강제한다 — SINGLE이면 처방 필수,
  SET이면 처방 금지. 한쪽만 걸면 나머지 방향으로 들어온 행이 S3-4 판정에서
  "처방 없는 SKU"나 "구성품 대신 자기 처방으로 판정된 세트"로 새어 나간다.
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
from app.core.db.constraints import in_range, positive, unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

#: 판매 상태. 단종된 SKU·제품은 지우지 않는다 — 과거 전표가 참조한다.
SKU_STATUSES = ("ACTIVE", "DISCONTINUED")
PRODUCT_STATUSES = SKU_STATUSES

#: SKU의 종류 (§4.2 / ADR-0016).
#: SINGLE — 처방 하나를 담은 단품. SET — 구성품 조합의 실물 세트.
SKU_KINDS = ("SINGLE", "SET")


class Brand(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """브랜드 (§3 [M1] brands).

    마스터 코드는 사람이 정한다 — §17.3의 채번(SO-2026-0001)은 전표 번호이지
    마스터 코드가 아니다.
    """

    __tablename__ = "brands"

    brand_code: Mapped[str] = mapped_column(String(20), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 수출 서류·채널 리스팅이 쓰는 영문명(§7.6).
    name_en: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: 자유 메모. 마이그레이션이 자동 생성한 행은 여기에 출처를 남긴다(ADR-0016 A2).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (unique_active("brands", "brand_code"),)


class Product(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """제품 = 처방. 인증·원산지 판정의 단위(§4.1).

    용량·세트 구성 같은 "담는 방식"은 여기 오지 않는다 — 그건 SKU다.
    """

    __tablename__ = "products"

    brand_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False
    )
    product_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 자유 메모. 마이그레이션이 자동 생성한 행은 여기에 출처를 남긴다(ADR-0016 A2).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    #: 품목군 프로파일 (§4.8). 지금은 분류일 뿐이다 — 요건·서류·마일스톤 세트는
    #: 대상 테이블이 생기는 세션이 붙인다(ADR-0021).
    item_profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("item_profiles.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        value_in("status", PRODUCT_STATUSES),
        unique_active("products", "product_code"),
    )


class Sku(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """재고·판매의 단위."""

    __tablename__ = "skus"

    #: 사내 품번. 사람이 정한다.
    sku_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 수출 서류·채널 리스팅에 쓰이는 영문명(§7.6 서류 생성기가 소비한다).
    name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")

    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="SINGLE")
    #: SINGLE의 처방. SET은 NULL이다(구성품이 각자의 처방을 갖는다 — §4.2).
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    #: 품목군 프로파일 (§4.8 / ADR-0021). 제품과 별도로 붙는다 — 요건은 처방
    #: 단위지만 서류·마일스톤 세트는 SKU·선적 단위이기 때문이다.
    item_profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("item_profiles.id", ondelete="RESTRICT"), nullable=True
    )

    # ── 물류 속성 (§4.1) ───────────────────────────────────────────────────
    barcode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: **소매포장을 포함한 판매단위 1개의 중량(g)** — 선적 서류의 G.W.(포장·팔레트
    #: 포함)·N.W.(내용물만)와는 다른 값이다. 그 둘은 마스터가 아니라 선적
    #: 라인(§7.6)이 갖는다. [ADR-0003 ⑧]
    unit_weight_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    #: 박스입수. 원장은 EA 단일이고 BOX는 화면 환산이다(§8.2) — 그 환산 계수가 이 값이다.
    box_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 제조일로부터의 사용기한(개월). 화장품 표기 관행이 개월이고,
    #: P4의 로트 유통기한 계산이 이 값을 입력으로 쓴다.
    shelf_life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 제조사 = 거래처(유형 OEM)다 — S1-3에서 문자열에서 승격했다(ADR-0020 /
    #: [M1] 보강(S1-3) ⑤). NULL 허용 유지 — 차단 지점은 등록이 아니라 게이트다.
    manufacturer_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=True
    )

    # ── DG 속성 (§4.1 / §7.7) ──────────────────────────────────────────────
    #
    # ★ 여기서 잠그는 것은 두 방향뿐이다(ADR-0016 정정):
    #     ① SET이면 DG 속성 전부 부재 ② 비DG면 UN번호·Class·PG·LQ 부재.
    #   dg_flag=true 방향의 완결성(UN번호 필수 등)과 교차 정합(에어로졸↔DG)은
    #   §7.7 DG 게이트(P4)가 맡는다 — 등록 시점에 DG 정보가 다 모여 있지 않은
    #   것이 실무의 정상 상태이고, 차단해야 할 지점은 등록이 아니라 선적이다.
    dg_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    un_number: Mapped[str | None] = mapped_column(String(10), nullable=True)
    dg_class: Mapped[str | None] = mapped_column(String(10), nullable=True)
    packing_group: Mapped[str | None] = mapped_column(String(3), nullable=True)
    #: 인화점(℃). 에탄올 13℃처럼 낮고 음수도 가능해 범위 제약을 걸지 않는다.
    flash_point_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    alcohol_content_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_aerosol: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    #: LQ(Limited Quantity) 포장 적용 여부.
    is_limited_quantity: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # MSDS는 §4.7 documents(소유 SKU×종류 MSDS)로 승격 완료 — 컬럼 없음
    # (ADR-0020 / S1-3 PR-2). "SET SKU에 MSDS 금지"는 documents 서비스 가드가
    # 잇는다(웹 세션 승인 — 다른 테이블이라 CHECK로 표현 불가).

    __table_args__ = (
        value_in("status", SKU_STATUSES),
        value_in("kind", SKU_KINDS, name="kind_valid"),
        # ★ 양방향이다. "SET이면 NULL"만 걸면 처방 없는 단품이 조용히 들어온다.
        CheckConstraint(
            "(kind = 'SINGLE' AND product_id IS NOT NULL) OR (kind = 'SET' AND product_id IS NULL)",
            name="kind_product_link",
        ),
        # ① 세트는 DG 속성을 하나도 갖지 않는다 (ADR-0016 부기 — P4까지 미결).
        #    msds_url 절은 documents 승격(S1-3 PR-2)과 함께 빠졌다 — 마이그레이션
        #    d5e8f2a6c4b9가 이 정의로 수기 재생성했다(함정 ①).
        CheckConstraint(
            "kind <> 'SET' OR ("
            " dg_flag = false AND un_number IS NULL AND dg_class IS NULL"
            " AND packing_group IS NULL AND flash_point_c IS NULL"
            " AND alcohol_content_pct IS NULL AND is_aerosol = false"
            " AND is_limited_quantity = false)",
            name="set_has_no_dg",
        ),
        # ② 비DG 행에는 DG 분류가 있어야만 의미가 생기는 값이 남아 있으면 안 된다.
        #    인화점·알코올함량·에어로졸·MSDS는 여기 없다 — 그건 "왜 DG가 아닌지"의
        #    근거값이라 비DG 행에도 있어야 한다.
        CheckConstraint(
            "dg_flag = true OR ("
            " un_number IS NULL AND dg_class IS NULL AND packing_group IS NULL"
            " AND is_limited_quantity = false)",
            name="dg_classification_only_when_dangerous",
        ),
        # 상식 범위. NULL은 통과한다(값이 있으면 지켜야 한다는 뜻).
        positive("unit_weight_g"),
        positive("box_qty"),
        positive("shelf_life_months"),
        in_range("alcohol_content_pct", 0, 100),
        unique_active("skus", "sku_code"),
    )


class SkuHsCode(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """국가별 HS 세번 (§4.1 / ADR-03 / ADR-0019).

    ■ 시스템은 HS를 **판정하지 않는다** (§1 비범위)

      여기 있는 것은 사람이 확인해 적어 넣은 값과 그 근거다. 자동 판정·추천
      기능은 만들지 않으며, 그 부재를 아키텍처 테스트가 지킨다 — "없어야 하는
      기능"은 사람이 지킬 수 없다(다음 세션이 편의 기능으로 추가한다).

    ■ 세율은 메모다

      `tariff_note`는 텍스트이고 계산에 쓰이지 않는다. 숫자 컬럼으로 두면
      곧 관세 계산에 쓰이기 시작하고, 그 순간 §1이 비범위로 못박은 "자동 판정"에
      들어선다. 실제 세액 계산은 §10.1 비용 원장이 근거를 갖고 한다.

    ■ hs_version을 유일키에 넣는 이유 (GC-B3)

      HS는 개정마다 코드가 바뀐다. 버전을 키에서 빼면 개정 때 기존 행을
      덮어써야 하고, 덮어쓰는 순간 "과거 판정은 당시 버전으로 표시"(GC-B3)가
      마스터 쪽에서 무너진다. 신·구 버전이 공존해야 감사에 답할 수 있다.
    """

    __tablename__ = "sku_hs_codes"

    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    #: 시장 코드 — markets.code를 **값으로** 참조한다(S2-1 승격 — ADR-0023 종착,
    #: 판정 조건 3: 컬럼·유일키·CSV 불변, FK만 얹는다).
    country_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("markets.code", ondelete="RESTRICT"), nullable=False
    )
    #: "HS2022" 형식. 값 목록을 못박지 않고 형식만 본다 — WCO 개정(HS2027)이
    #: 오면 마이그레이션 없이 받되, 오타는 막는다.
    hs_version: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 숫자만 6~12자리. 점·공백은 서비스가 지운다 — "3304.99"와 "330499"가
    #: 다른 행으로 들어오면 유일키가 중복을 못 잡는다.
    hs_code: Mapped[str] = mapped_column(String(12), nullable=False)
    #: 세율 **메모**. 계산에 쓰지 않는다(§1 비범위).
    tariff_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ADR-03: 모든 요건·세율·기한 데이터는 근거링크와 최종확인일을 갖는다.
    #: nullable로 두면 "나중에 채우겠다"가 영구 미확인 데이터가 된다.
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 업무 날짜다(시각이 아니다) — §2 ADR-02의 UTC 규율은 timestamptz 대상.
    last_verified_on: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_format"),
        CheckConstraint("hs_version ~ '^HS[0-9]{4}$'", name="hs_version_format"),
        CheckConstraint("hs_code ~ '^[0-9]{6,12}$'", name="hs_code_digits"),
        CheckConstraint("length(btrim(source_url)) > 0", name="source_url_not_blank"),
        unique_active("sku_hs_codes", "sku_id", "country_code", "hs_version"),
    )


class SetComponent(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """세트 구성 (§4.2 / GC-E1).

    세트 1개를 만들 때 들어가는 구성품과 수량이다. 조립·해체는 재고 원장의
    ASSEMBLY/DISASSEMBLY(§8.2)가 하고, 그건 Phase 4다 — 여기는 **구성의 정의**만
    갖는다.

    ★ DB가 못 잠그는 두 가지가 있다: "set_sku는 kind='SET'이어야 한다"와
      "구성품은 kind='SINGLE'이어야 한다"(§4.2 중첩 세트 금지, ADR-0016). 둘 다
      **다른 행**을 봐야 판정되는 조건이라 CHECK로 표현할 수 없다. 트리거를 쓰지
      않는 이유는 ORM에서 보이지 않아 마이그레이션·테스트가 어려워지고, 규칙이
      코드와 DB 두 곳으로 갈라지기 때문이다 — 서비스가 검증하고 테스트가 지킨다.
      자기 자신 참조만은 한 행 안에서 판정되므로 CHECK로 막는다.
    """

    __tablename__ = "set_components"

    set_sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    component_sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    #: 세트 1개당 들어가는 수량. GC-E1의 "본품 2 + 미니 1"이 이 값이다.
    #: 원장은 EA 단일이므로(§8.2) 여기도 EA다.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        positive("quantity"),
        CheckConstraint("set_sku_id <> component_sku_id", name="no_self_reference"),
        unique_active("set_components", "set_sku_id", "component_sku_id"),
    )


#: 단가의 종류 (§4.1 "판가·매입가는 이력 테이블로").
#: PURCHASE는 **원가**다 — 조회 역할에게는 행 자체를 보이지 않는다(ADR-0018).
PRICE_TYPES = ("SALES", "PURCHASE")


class SkuPrice(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """단가 이력 (§4.1 "그때 단가" 분쟁 대비 / ADR-0017).

    ■ 종료일을 저장하지 않는다

      다음 행의 발효일이 곧 이전 행의 종료다. 종료일을 두면 기간 겹침이 표현
      가능해지고, 겹친 이력은 "그때 단가"를 두 개로 만든다 — 이 테이블의 목적
      자체가 무너진다. 종료일이 없으면 겹침이 **원천적으로 불가능**하다.

    ■ 기준일 조회는 없으면 오류다

      `effective_from <= 기준일` 중 최댓값 1행. 없으면 0이나 null이 아니라
      오류로 실패한다(service.price_at). S3-1의 전표 단가 스냅샷이 이 조회를
      소비하므로, 0을 돌려주면 금액 0원 전표가 조용히 확정된다.

    ■ 금액은 정수 최소단위 + 통화 (§2 ADR-02)

      USD 12.34 → 1234. 통화별 자릿수는 app/core/money.py가 유일한 출처이고,
      서비스가 그 표로 변환·검증한다. DB에 통화 목록 CHECK를 두지 않는 이유는
      그 목록이 money.py와 갈라지는 순간 어느 쪽이 진실인지 알 수 없어지기 때문이다.
    """

    __tablename__ = "sku_prices"

    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    price_type: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    #: 정수 최소단위. Float 금지(§2 ADR-02 / GC-G1).
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 발효일(업무 날짜). 미래 날짜를 허용한다 — 인상 예고가 실무에 있다.
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("price_type", PRICE_TYPES),
        # 0을 막지 않는다 — 사은품 0원이 실재하고, §20 B도 무상(금액 0)을 인정한다.
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        unique_active("sku_prices", "sku_id", "price_type", "currency", "effective_from"),
    )


class ItemProfile(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """품목군 프로파일 (§4.8 / ADR-0021).

    ★ 지금은 **헤더뿐이다.** §4.8이 말하는 "기본 요건 세트·서류 세트·마일스톤
      세트"는 대상 테이블이 전부 후속 세션이라(요건 S2-1 / 서류 S1-3 /
      마일스톤 S3-2) 연결할 것이 하나도 없다.

      지금 연결 테이블을 만들면 참조할 대상이 없어 FK 없는 문자열 코드로 채우게
      되고, 그 코드는 대상 테이블이 생겨도 FK로 바뀌지 않는다("이미 돌아가니까").
      반대로 헤더까지 미루면 제품·SKU 화면이 제품군을 고를 자리를 못 갖고,
      S2-1이 요건 세트를 만들면서 프로파일 자체를 새로 설계하게 된다.

      §4.8의 "신규 등록 시 자동 적용"도 적용할 내용이 생긴 뒤다.
    """

    __tablename__ = "item_profiles"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (unique_active("item_profiles", "code"),)
