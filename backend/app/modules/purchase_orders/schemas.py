"""구매 발주서(PO) 요청·응답 (S3-1 design-integrated §2.1·§2.4 / design-A A12 / design-F F2~F5 / ADR-0057·0024).

■ 요청 스키마는 전부 `extra="forbid"`이고 **`status`·`doc_number`·`total_cost`·`line_cost`·`frozen_at`·`version`(생성 본문)·
  `material_id`·`item_type`이 구조적으로 없다** — 상태는 전이 엔드포인트로만, 합계·라인원가는 서버가 계산하고, 생성은 곧 발행이라 낙관 잠금 대상이
  없으며, 라인 품목은 SKU 전용이다(밀반입 시 422). 금액은 사람 표기 문자열(`"12.34"`)로 받아 서버가 정수 최소단위로 바꾼다(반올림 금지).
■ 응답은 **원가를 볼 수 있는 역할용(Full)과 없는 역할용(CostHidden) 스키마 2종**이다(ADR-0024 필드 부재 방식 — 목록·상세·라인 각각).
  CostHidden은 Full을 **상속하지 않고** 원가·통화·가격 계열 필드를 **가지지 않는다**(`total_cost`·`currency`·`unit_cost`·`line_cost`·`price_basis`,
  그리고 통화를 역추론할 수 있는 `minor_units`·`fx_rate`·`fx_rate_date`·`*_text` 금액 표기 — 설계 목록보다 **좁은 쪽**으로 자율 확정).
  갈림은 라우터 경계 1곳(`response_model=None`+`may_see_cost`)이며 직렬화 후 dict 키 삭제는 하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.modules.trade_docs.schemas import (
    IncotermIn,
    IncotermOut,
    PaymentTermsIn,
    PaymentTermsOut,
    StatusLogOut,
)

__all__ = ["StatusLogOut"]  # 라우터가 이 모듈에서 가져온다(공용 조각의 재노출)

PoKindLiteral = Literal["PURCHASE", "OEM_PRODUCTION"]

#: 목록 정렬 화이트리스트 — **원가 값 없음, 전 역할 공통**. 밖의 값은 422(FastAPI는 미지정 쿼리 키를 무시하므로 Literal이 유일한 방어다).
PoSortKey = Literal["doc_date", "doc_number", "status", "supplier_name", "created_at"]


# ── 요청 ────────────────────────────────────────────────────────────────────


class PoLineIn(BaseModel):
    """라인 입력 — 단가를 생략하면 마스터 매입가(`price_at` PURCHASE, 증빙일 기준 — MASTER), 주면 수동 입력(MANUAL)이다."""

    model_config = ConfigDict(extra="forbid")

    sku_id: StrictInt = Field(ge=1)
    quantity: StrictInt
    unit_cost: StrictStr | None = None
    requested_delivery_date: date | None = None


class PurchaseOrderCreateRequest(BaseModel):
    """PO 생성 = 발행 = 발주 확정(사람 1클릭). `/preview`도 같은 본문을 받는다(비저장).

    결제조건·Incoterms·환율(비KRW)은 **필수**다(동결 완결성 — 발행 시점에 모두 있어야 한다). 복제는 `copied_from_id`(원본이 취소·만료이고 같은 공급사일 때만).
    """

    model_config = ConfigDict(extra="forbid")

    supplier_partner_id: StrictInt = Field(ge=1)
    po_kind: PoKindLiteral = "PURCHASE"
    currency: StrictStr = Field(min_length=3, max_length=3)
    doc_date: date | None = None
    fx_rate: StrictStr | None = None
    fx_rate_date: date | None = None
    payment_terms: PaymentTermsIn | None = None
    incoterm: IncotermIn | None = None
    supplier_name: StrictStr | None = Field(default=None, max_length=200)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    copied_from_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[PoLineIn] = Field(default_factory=list, max_length=500)


class PurchaseOrderMetaUpdateRequest(BaseModel):
    """동결 후에도 고칠 수 있는 FREE 열(내부 메모·담당자·OC 일자·OC 참조)만 — 가격·조건 필드가 구조적으로 없다.

    OC 두 열은 **공급사 확인(SUPPLIER_CONFIRMED) 상태에서만** 고칠 수 있다(발행 중에는 전이가 기록한다).
    """

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    oc_received_on: date | None = None
    oc_reference: StrictStr | None = Field(default=None, max_length=100)


# ── 응답: 원가와 무관한 라인 파생값 — Full·CostHidden 양쪽 동일(S3-2 PR-5a) ─────────────────────


class ExpectedReceiptOut(BaseModel):
    """PO 라인 입고예정 — **계산값**(열 없음 · design-B B17 · ADR-0085). 그 라인을 참조하는 살아 있는 수입선적들의 ETA 유효값(실적 우선) 중
    가장 늦은 날. ETA가 없는 선적이 하나라도 있으면 UNSCHEDULED(`value` null — 아는 날짜로 대신 채우지 않는다), 선적이 없으면 NONE('입고예정 미정').
    `basis` = 전 선적 ETA가 실적이면 ACTUAL, 하나라도 계획이면 PLANNED. 원가·금액과 무관해 원가 비열람 역할에게도 같다."""

    status: Literal["NONE", "UNSCHEDULED", "SCHEDULED"]
    #: 'YYYY-MM-DD'(도착 현지 날짜 — `new Date()` 금지). SCHEDULED일 때만 값이 있다.
    value: date | None
    basis: Literal["ACTUAL", "PLANNED"] | None
    #: 이 라인을 참조하는 살아 있는 수입선적 수 / 그중 ETA가 없는 선적 수.
    shipment_count: int
    unscheduled_count: int


# ── 응답: 원가를 볼 수 있는 역할(ADMIN·TRADE·LOGISTICS·CERT) ──────────────────────────


class PoLineOut(BaseModel):
    id: int
    line_no: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    requested_delivery_date: date | None
    #: 파생값(S3-2 PR-5a) — 수입선적 **배정 가능량** = 라인 수량 − 살아 있는 수입선적 수량(PO 잔량 아님 — PO 잔량은 입고에서만 준다, ADR-0077).
    #: 상세 응답에는 항상 실린다(None = 이 필드가 생기기 전에 저장된 멱등 재생 본문뿐).
    assignable_quantity: int | None = None
    #: 입고예정 계산값(S3-2 PR-5a — 위 `ExpectedReceiptOut`). None은 위와 같은 재생 본문뿐이다.
    expected_receipt: ExpectedReceiptOut | None = None
    #: 정수 최소단위 + 표시용 십진 문자열(프런트 산술 0).
    unit_cost: int
    unit_cost_text: str
    line_cost: int
    line_cost_text: str
    price_basis: str


class PurchaseOrderSummary(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    po_kind: str
    supplier_partner_id: int
    supplier_name: str
    currency: str
    total_cost: int
    total_text: str
    assignee_id: int
    copied_from_id: int | None
    oc_received_on: date | None
    oc_reference: str | None
    version: int
    created_at: datetime


class PurchaseOrderDetail(PurchaseOrderSummary):
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    frozen_at: datetime
    last_line_no: int
    lines: list[PoLineOut]


class PoPreviewLineOut(BaseModel):
    """미리보기 라인 — 아직 저장되지 않아 id가 없다. 서버가 정한 단가·기준(마스터/수동)을 보여 준다."""

    line_no: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    requested_delivery_date: date | None
    unit_cost: int
    unit_cost_text: str
    line_cost: int
    line_cost_text: str
    price_basis: str


class PurchaseOrderPreview(BaseModel):
    """비저장 미리보기 — 채번·감사·이벤트·멱등 키 소비가 전혀 없다. 확인 뒤 같은 본문으로 생성한다(생성·미리보기는 쓰기 역할 전용이라 원가 포함)."""

    doc_date: date
    po_kind: str
    supplier_partner_id: int
    supplier_name: str
    currency: str
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    assignee_id: int
    copied_from_id: int | None
    total_cost: int
    total_text: str
    lines: list[PoPreviewLineOut]


# ── 응답: 원가를 볼 수 없는 역할(VIEWER 등) — 원가·통화·가격 계열 필드가 **없다** ─────────────


class PoLineCostHiddenOut(BaseModel):
    id: int
    line_no: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    requested_delivery_date: date | None
    #: S3-2 PR-5a — 원가와 무관한 수량·날짜 파생값(Full과 같은 값 — 물류·조회 역할의 입고 준비 정보).
    assignable_quantity: int | None = None
    expected_receipt: ExpectedReceiptOut | None = None


class PurchaseOrderCostHiddenSummary(BaseModel):
    """행·건수·상태·공급사·OC는 보인다(혼합 목적 행 — 행을 감추면 물류·조회 업무가 끊긴다, ADR-0024)."""

    id: int
    doc_number: str
    doc_date: date
    status: str
    po_kind: str
    supplier_partner_id: int
    supplier_name: str
    assignee_id: int
    copied_from_id: int | None
    oc_received_on: date | None
    oc_reference: str | None
    version: int
    created_at: datetime


class PurchaseOrderCostHiddenDetail(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    po_kind: str
    supplier_partner_id: int
    supplier_name: str
    assignee_id: int
    copied_from_id: int | None
    oc_received_on: date | None
    oc_reference: str | None
    version: int
    created_at: datetime
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    frozen_at: datetime
    last_line_no: int
    lines: list[PoLineCostHiddenOut]
