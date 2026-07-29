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

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    #: S1-3에서 partners FK로 승격한다(ADR-0020).
    manufacturer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

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
    #: S1-3에서 documents 링크로 승격한다(ADR-0020).
    msds_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        value_in("status", SKU_STATUSES),
        value_in("kind", SKU_KINDS, name="kind_valid"),
        # ★ 양방향이다. "SET이면 NULL"만 걸면 처방 없는 단품이 조용히 들어온다.
        CheckConstraint(
            "(kind = 'SINGLE' AND product_id IS NOT NULL) OR (kind = 'SET' AND product_id IS NULL)",
            name="kind_product_link",
        ),
        # ① 세트는 DG 속성을 하나도 갖지 않는다 (ADR-0016 부기 — P4까지 미결).
        CheckConstraint(
            "kind <> 'SET' OR ("
            " dg_flag = false AND un_number IS NULL AND dg_class IS NULL"
            " AND packing_group IS NULL AND flash_point_c IS NULL"
            " AND alcohol_content_pct IS NULL AND is_aerosol = false"
            " AND is_limited_quantity = false AND msds_url IS NULL)",
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
