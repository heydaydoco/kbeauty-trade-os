"""선적 요청·응답 (S3-2 PR-3a / design-D D2-1 S1~S4·S7~S15·D3 / design-integrated §2.9·§9 R-15).

요청 스키마는 전부 `extra="forbid"`이고 **SKU·단가·통화·환율·거래처·Incoterms·결제조건·구분·상태·번호·증빙일 필드가 구조적으로 없다**
(원천 사본 — 재입력 금지 `D:175` ①, 증빙일은 생성 시 서버가 KST 오늘로 1회 설정 — R-15). 생성 본문은 원천 라인 id·수량·국가 2개·
선택적 당사자·내부 메모뿐이다. 범용 전이의 `to_status`는 `Literal["CANCELLED"]` 1값이다(출고지시는 전용 경로 `release-order`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.modules.trade_docs.constants import MAX_LINES
from app.modules.trade_docs.schemas import IncotermOut, PaymentTermsOut, StatusLogOut

__all__ = ["StatusLogOut"]  # 라우터가 이 모듈에서 가져온다(공용 조각의 재노출)

#: ISO 3166-1 alpha-2 대문자 두 글자(소문자를 조용히 올리지 않는다 — markets FK 아님).
COUNTRY_PATTERN = r"^[A-Z]{2}$"

#: 범용 전이의 `to_status` — 사람 엣지 중 동결 액션(출고지시)을 뺀 도달 상태. machine 파생값과 같은지 라우터 임포트 시 assert가 대사한다.
ShipmentTarget = Literal["CANCELLED"]
#: 생성·추가로 지정할 수 있는 당사자 역할(수출 SHIPPER·CONSIGNEE는 자사·자동 스냅샷이라 본문으로 받지 않는다 — 422).
PartyRoleIn = Literal["SHIPPER", "CONSIGNEE", "NOTIFY", "FORWARDER", "CUSTOMS_BROKER"]


# ── 요청 ───────────────────────────────────────────────────────────────────


class ShipmentLineFromSo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    so_line_id: StrictInt = Field(ge=1)
    quantity: StrictInt


class PartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: PartyRoleIn
    partner_id: StrictInt = Field(ge=1)


class ShipmentCreateFromSo(BaseModel):
    """SO 참조 수출선적 생성·미리보기 본문(S3·S4)."""

    model_config = ConfigDict(extra="forbid")

    lines: list[ShipmentLineFromSo] = Field(min_length=1, max_length=MAX_LINES)
    origin_country_code: StrictStr = Field(pattern=COUNTRY_PATTERN)
    dest_country_code: StrictStr = Field(pattern=COUNTRY_PATTERN)
    parties: list[PartyIn] | None = Field(default=None, max_length=5)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)


class ShipmentUpdateRequest(BaseModel):
    """헤더 편집(S7) — FREE 2열(메모·담당자)은 상태 무관, 국가 2열은 계획(PLANNED) 중에만(그 밖은 409 FROZEN). 보낸 필드만 바뀐다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    origin_country_code: StrictStr | None = Field(default=None, pattern=COUNTRY_PATTERN)
    dest_country_code: StrictStr | None = Field(default=None, pattern=COUNTRY_PATTERN)


class ShipmentLineAddRequest(BaseModel):
    """라인 추가(S8) — 원천 SO 라인 1줄과 수량. 헤더 version 필수(라인 편집 = 헤더 version +1)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    source_line_id: StrictInt = Field(ge=1)
    quantity: StrictInt


class ShipmentLineUpdateRequest(BaseModel):
    """라인 수량 수정(S9) — 원천 잔량(+이 라인의 현재 수량) 안에서. 헤더 version 필수."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    quantity: StrictInt


class ShipmentVersionRequest(BaseModel):
    """출고지시(S11) — 동결 액션. 헤더 version 필수."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class ShipmentTransitionRequest(BaseModel):
    """범용 전이(S12) — 취소 1값, 사유 필수(1~500자, 공백 불가)."""

    model_config = ConfigDict(extra="forbid")

    to_status: ShipmentTarget
    version: StrictInt = Field(ge=1)
    reason: StrictStr | None = Field(default=None, max_length=500)


class PartyAddRequest(PartyIn):
    """당사자 추가(S14) — 역할·거래처. 영문 스냅샷은 서버가 거래처에서 복사한다."""


# ── 응답 ───────────────────────────────────────────────────────────────────


class ShipmentSourceOut(BaseModel):
    kind: Literal["SALES_ORDER", "PURCHASE_ORDER"]
    id: int
    doc_number: str
    status: str


class CounterpartyOut(BaseModel):
    partner_id: int
    name: str


class AssigneeOut(BaseModel):
    id: int
    display_name: str | None


class ShipmentSkuOut(BaseModel):
    id: int
    code: str
    name_ko: str
    name_en: str | None
    kind: str


class SourceLineOut(BaseModel):
    """원천 라인 대비 — 수출 = SO 라인 수량·잔량(이 선적 포함 소비 후). 화면은 산술하지 않는다."""

    id: int
    line_no: int
    quantity: int
    #: 원천 라인의 남은 수량(살아 있는 선적 전부를 뺀 값 — 저장 아님).
    remaining_after: int


class DgOut(BaseModel):
    """SKU 마스터의 DG 표시(배지만 — 차단 0, 판정은 S4-4 게이트)."""

    flag: bool
    un_number: str | None
    dg_class: str | None


class AvailabilityOut(BaseModel):
    """§8.3 '자리' — 포트 무변경, 값은 항상 NOT_IMPLEMENTED('가용재고 미산정' 배지, 0·현재고 표시 금지 — X-26)."""

    status: Literal["NOT_IMPLEMENTED"]


class ShipmentLineOut(BaseModel):
    id: int
    line_no: int
    so_line_id: int | None
    sku: ShipmentSkuOut
    quantity: int
    currency: str
    unit_price_amount: int | None
    unit_price_text: str | None
    is_free: bool
    line_amount: int
    line_amount_text: str
    source_line: SourceLineOut
    dg: DgOut
    availability: AvailabilityOut


class ShipmentPartyOut(BaseModel):
    id: int
    role: str
    partner_id: int
    name_en: str
    address_en: str | None
    #: 원천 거래처 자동 스냅샷 행(수출 CONSIGNEE) — 삭제·교체 불가.
    auto: bool
    version: int


class ShipmentDetail(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    shipment_kind: str
    version: int
    frozen_at: datetime | None
    source: ShipmentSourceOut
    counterparty: CounterpartyOut
    origin_country_code: str
    dest_country_code: str
    currency: str
    minor_units: int
    #: 고정 환율(원천 사본 — 선적 시점 신규 입력 없음).
    fx_rate: str | None
    fx_rate_date: date | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    total_amount: int
    total_text: str
    internal_note: str | None
    assignee: AssigneeOut
    last_line_no: int
    dg_line_count: int
    lines: list[ShipmentLineOut]
    parties: list[ShipmentPartyOut]
    #: 표시 편의(서버가 쓰기 시 다시 검사한다) — RELEASE_ORDER·CANCEL·EDIT_LINES·EDIT_COUNTRIES·EDIT_META·EDIT_PARTIES 중 요청자 역할·상태로 가능한 것.
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


class ShipmentListItem(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    shipment_kind: str
    source: ShipmentSourceOut
    counterparty_name: str
    origin_country_code: str
    dest_country_code: str
    currency: str
    total_amount: int
    total_text: str
    line_count: int
    assignee: AssigneeOut
    created_at: datetime
    updated_at: datetime


class ShipmentPreviewLine(BaseModel):
    so_line_id: int
    line_no: int
    sku: ShipmentSkuOut
    quantity: int
    #: 이 선적 전 원천 잔량 / 이 선적 뒤 남을 수량(파생).
    open_quantity_before: int
    remaining_after: int
    unit_price_amount: int
    unit_price_text: str
    is_free: bool
    line_amount: int
    line_amount_text: str
    dg: DgOut


class ShipmentPreviewParty(BaseModel):
    role: str
    partner_id: int
    name_en: str
    address_en: str | None
    auto: bool


class ShipmentPreview(BaseModel):
    """비저장 미리보기(S3) — 채번·이벤트·멱등 키·잠금 소비 0. 생성과 같은 검증을 같은 순서로 한다."""

    so_id: int
    so_doc_number: str
    so_status: str
    doc_date: date
    shipment_kind: str
    counterparty: CounterpartyOut
    origin_country_code: str
    dest_country_code: str
    currency: str
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    total_amount: int
    total_text: str
    lines: list[ShipmentPreviewLine]
    parties: list[ShipmentPreviewParty]
