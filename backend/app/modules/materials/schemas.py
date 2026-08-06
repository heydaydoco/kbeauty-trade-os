"""자재·BOM·라벨 요청·응답 (§4.4·§4.5).

★ BOM 응답 스키마가 **둘**인 이유 (ADR-0024)

  원가를 볼 수 있는 역할과 아닌 역할에게 서로 다른 **모양**을 준다. 같은
  스키마에 `unit_cost: int | None`을 두고 값만 비우는 방식은 쓰지 않는다 —
  null은 "0원"이나 "미입력"으로 읽히고, 그 오독 자체가 원가 추정 단서다.
  두 클래스는 상속 관계도 아니다(상속하면 필드가 딸려 온다).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.materials.models import (
    BOM_ORIGIN_STATUSES,
    LABEL_APPROVAL_STATUSES,
    MATERIAL_TYPES,
)
from app.modules.materials.service import LabelView, MaterialView

_MATERIAL_TYPE_PATTERN = f"^({'|'.join(MATERIAL_TYPES)})$"
_ORIGIN_STATUS_PATTERN = f"^({'|'.join(BOM_ORIGIN_STATUSES)})$"
_APPROVAL_STATUS_PATTERN = f"^({'|'.join(LABEL_APPROVAL_STATUSES)})$"


# ── 자재 마스터 ─────────────────────────────────────────────────────────────


class MaterialCreateRequest(BaseModel):
    material_code: str = Field(min_length=1, max_length=40)
    name_ko: str = Field(min_length=1, max_length=200)
    material_type: str = Field(pattern=_MATERIAL_TYPE_PATTERN)
    #: HS6. 없이도 등록된다 — 결측 탐지는 §14 ⑭ 헬스체크(P6)의 일이다.
    hs6: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    #: 기본공급사 = 거래처(유형 SUPPLIER — ADR-0020 부기 승격). 검증은 서비스가 한다.
    default_supplier_partner_id: int | None = None
    inventory_managed: bool = False
    #: 로트관리는 재고관리를 전제한다 — 짝 검증은 서비스·DB CHECK가 한다.
    lot_managed: bool = False
    note: str | None = None


class MaterialSummary(BaseModel):
    id: int
    material_code: str
    name_ko: str
    material_type: str
    hs6: str | None
    default_supplier_partner_id: int | None
    #: 표시용 파트너명(조인 값) — 편집은 default_supplier_partner_id로 한다.
    default_supplier_name: str | None
    inventory_managed: bool
    lot_managed: bool
    note: str | None

    @classmethod
    def of(cls, view: MaterialView) -> MaterialSummary:
        return cls(
            id=view.id,
            material_code=view.material_code,
            name_ko=view.name_ko,
            material_type=view.material_type,
            hs6=view.hs6,
            default_supplier_partner_id=view.default_supplier_partner_id,
            default_supplier_name=view.default_supplier_name,
            inventory_managed=view.inventory_managed,
            lot_managed=view.lot_managed,
            note=view.note,
        )


# ── 자재 BOM ───────────────────────────────────────────────────────────────


class BomLineCreateRequest(BaseModel):
    material_id: int
    #: 제품 1단위당 소요량(물리량 — ADR-0003 ⑧). Decimal로 받는다(Float 금지).
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    quantity_unit: str = Field(min_length=1, max_length=10)
    #: 매입 단가 = 원가. 사람이 쓰는 표기(12.34)로 받고 서버가 최소단위로 바꾼다.
    #: 통화와 한 쌍이며, 둘 다 비우면 "단가 미상"으로 등록된다.
    #: ★ 상한이 필요하다 — 없으면 최소단위 환산 뒤 BIGINT를 넘겨 DB가 DataError를
    #:   내는데, 그건 IntegrityError가 아니라서 서비스가 못 잡고 500이 된다.
    #:   15자리면 1조 단위 금액까지 담고, 최소단위 환산(최대 ×1000) 후에도
    #:   BIGINT(19자리) 안이다.
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=15)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    #: 기본값은 **미상**이다 (§4.4 "미상은 판정 시 역외 간주" — GC-B2).
    origin_status: str = Field(default="UNKNOWN", pattern=_ORIGIN_STATUS_PATTERN)
    note: str | None = None


class BomLineSummary(BaseModel):
    """원가를 볼 수 있는 역할의 응답 (ADR-0024)."""

    id: int
    product_id: int
    material_id: int
    material_code: str
    material_name_ko: str
    material_type: str
    hs6: str | None
    quantity: Decimal
    quantity_unit: str
    #: 정수 최소단위. 표시 변환은 통화별 자릿수(서버 제공)로만 한다.
    unit_cost: int | None
    currency: str | None
    origin_status: str
    note: str | None


class BomLineCostHiddenSummary(BaseModel):
    """원가를 볼 수 없는 역할의 응답 — `unit_cost`·`currency` **필드가 없다**.

    행을 통째로 감추지 않는 이유(ADR-0018과의 차이): 이 행은 원가만 담은 행이
    아니다. 소요량·원산지상태·HS6은 인증·원산지 업무에 필요하고, 행을 지우면
    "BOM이 비어 있다"로 읽혀 §6.3의 전수 대조가 조용히 어긋난다.
    """

    id: int
    product_id: int
    material_id: int
    material_code: str
    material_name_ko: str
    material_type: str
    hs6: str | None
    quantity: Decimal
    quantity_unit: str
    origin_status: str
    note: str | None


# ── 라벨 ───────────────────────────────────────────────────────────────────


class LabelCreateRequest(BaseModel):
    """라벨 판 **기록** 요청 — 시스템이 라벨을 읽어 판정하지 않는다(§1 비범위)."""

    #: 시장. markets는 S2-1이라 아직 국가코드다(ADR-0023). 소문자도 받는다.
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    #: 라벨 판번(1부터). 낙관적 잠금 `version`과 다른 값이다.
    label_version: int = Field(gt=0, le=9999)
    language: str = Field(min_length=2, max_length=10)
    approval_status: str = Field(default="DRAFT", pattern=_APPROVAL_STATUS_PATTERN)
    #: 컷인일. 승인 전 선입력을 허용한다(일정이 먼저 잡히는 실무).
    cut_in_date: date | None = None
    # 라벨 파일은 §4.7 documents로 승격 완료(ADR-0020 부기) — 등록 항목이 아니다.
    #: §4.5 검증 항목 — 사람이 확인했다는 기록이다.
    inci_local_verified: bool = False
    origin_mark_verified: bool = False
    note: str | None = None


class LabelDocumentSummary(BaseModel):
    """라벨에 붙은 문서 요약 — 구 file_url의 후신(§4.7 승격, ADR-0020 부기)."""

    document_id: int
    storage_kind: str
    url: str | None
    original_filename: str | None


class LabelSummary(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    country_code: str
    label_version: int
    language: str
    approval_status: str
    cut_in_date: date | None
    documents: list[LabelDocumentSummary]
    inci_local_verified: bool
    origin_mark_verified: bool
    note: str | None

    @classmethod
    def of(cls, view: LabelView) -> LabelSummary:
        return cls(
            id=view.id,
            sku_id=view.sku_id,
            sku_code=view.sku_code,
            country_code=view.country_code,
            label_version=view.label_version,
            language=view.language,
            approval_status=view.approval_status,
            cut_in_date=view.cut_in_date,
            documents=[
                LabelDocumentSummary(
                    document_id=doc.document_id,
                    storage_kind=doc.storage_kind,
                    url=doc.url,
                    original_filename=doc.original_filename,
                )
                for doc in view.documents
            ],
            inci_local_verified=view.inci_local_verified,
            origin_mark_verified=view.origin_mark_verified,
            note=view.note,
        )
