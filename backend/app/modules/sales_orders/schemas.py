"""수주(SO) 요청·응답 (S3-1 design-integrated §2.1·§2.4 / design-A A4 / design-B B2·B8).

요청 스키마는 전부 `extra="forbid"`이고 **`status`·`doc_number`·`total_amount`·`line_amount`·`confirmed_at`·`buyer_po_no_key`·
거래처(`buyer_partner_id`)·참조 FK(`qt_id`·`pi_id`)·통화가 구조적으로 없다** — 상태는 전이 엔드포인트로만, 합계는 서버가 라인에서 계산하고,
바이어 PO 비교 키는 서버가 산출하며, 거래처·참조·통화는 생성 후 불변(ORIGIN)이다. 참조 생성 요청은 원천에 있는 값(SKU·단가·통화·환율·
거래처·시장)을 다시 받는 필드가 없다(재입력 화면 없음 — DoD ①). 낙관 잠금 필드는 저장소 관용대로 `version`이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from app.modules.trade_docs.schemas import (
    IncotermIn,
    IncotermOut,
    PaymentTermsIn,
    PaymentTermsOut,
    StatusLogOut,
)

__all__ = ["StatusLogOut"]  # 라우터가 이 모듈에서 가져온다(공용 조각의 재노출)


class SoLineRequest(BaseModel):
    """참조 생성 — 원천 라인 1줄에서 가져올 수량(+요청납기). 전체를 다 받으려면 `lines`를 생략한다(각 라인 잔량 전부)."""

    model_config = ConfigDict(extra="forbid")

    source_line_id: StrictInt = Field(ge=1)
    quantity: StrictInt
    requested_delivery_date: date | None = None


class SalesOrderReferenceRequest(BaseModel):
    """QT/PI → SO 참조 생성. SO는 접수(RECEIVED) 상태가 편집 가능 초안이라 미리보기가 없고, 조건·환율·단가 조정은 생성 후 편집 API로 한다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(
        ge=1, description="화면이 본 원천(QT 또는 PI) 헤더 version(낙관 잠금)"
    )
    doc_date: date | None = None
    buyer_po_no: StrictStr | None = Field(default=None, max_length=200)
    buyer_po_date: date | None = None
    lines: list[SoLineRequest] | None = Field(default=None, min_length=1, max_length=500)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    copied_from_id: StrictInt | None = Field(default=None, ge=1)


class SalesOrderUpdateRequest(BaseModel):
    """접수(RECEIVED) 상태 편집 — 보낸 필드만 바뀐다. 확정 이후(동결) CONTENT 필드가 현재값과 다르면 409 FROZEN.

    **거래처·통화·참조·상태·번호는 필드가 없다**(ORIGIN 불변). 목적지 시장은 직접(인테이크) 수주만 바꿀 수 있다(참조 수주는 원천과 어긋나면 안 된다).
    """

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    doc_date: date | None = None
    dest_market_code: StrictStr | None = Field(default=None, min_length=2, max_length=2)
    fx_rate: StrictStr | None = None
    fx_rate_date: date | None = None
    payment_terms: PaymentTermsIn | None = None
    incoterm: IncotermIn | None = None
    buyer_name: StrictStr | None = Field(default=None, max_length=200)
    buyer_po_no: StrictStr | None = Field(default=None, max_length=200)
    buyer_po_date: date | None = None
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


class SalesOrderMetaUpdateRequest(BaseModel):
    """동결(확정) 후에도 고칠 수 있는 FREE 열(내부 메모·담당자)만 — 가격·조건 필드가 구조적으로 없다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


class SoLineAddRequest(BaseModel):
    """라인 추가 — 직접 수주는 마스터 값을 1회 스냅샷한다(단가 생략=마스터 판가, 주면 수동 MANUAL). 참조 수주는 원천에 있는 SKU만
    (원천 라인 값이 복사되며 이 요청의 가격 필드는 받지 않는다)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    sku_id: StrictInt = Field(ge=1)
    quantity: StrictInt
    unit_price: StrictStr | None = None
    is_free: StrictBool = False
    price_reason: StrictStr | None = Field(default=None, max_length=200)
    buyer_item_code: StrictStr | None = Field(default=None, max_length=100)
    requested_delivery_date: date | None = None


class SoLineUpdateRequest(BaseModel):
    """라인 부분 수정 — `sku_id`는 못 바꾼다(라인 제외 후 신규 추가). 단가를 바꾸면 기준은 MANUAL이다(BUYER_PO 값 불변이면 유지)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    quantity: StrictInt | None = None
    unit_price: StrictStr | None = None
    is_free: StrictBool | None = None
    price_reason: StrictStr | None = Field(default=None, max_length=200)
    buyer_item_code: StrictStr | None = Field(default=None, max_length=100)
    requested_delivery_date: date | None = None


# ── 응답 ────────────────────────────────────────────────────────────────────


class SoLineSource(BaseModel):
    """원천 라인(QT 또는 PI)의 값 — 원천은 동결이라 재현 가능하다. 차이는 조회 시 계산한다(저장 안 함)."""

    kind: str
    line_id: int
    quantity: int
    unit_price_amount: int
    unit_price_text: str


class SalesOrderLineOut(BaseModel):
    id: int
    line_no: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    #: 파생값(저장 아님) — SKU 현재 상태(ACTIVE·DISCONTINUED…·삭제=DELETED). 접수는 단종 SKU 저장을 허용하고 확정 시 차단한다.
    sku_status: str | None
    quantity: int
    requested_delivery_date: date | None
    buyer_item_code: str | None
    unit_price_amount: int
    unit_price_text: str
    list_price_amount: int | None
    list_price_text: str | None
    line_amount: int
    line_amount_text: str
    price_basis: str
    is_free: bool
    price_reason: str | None
    qt_line_id: int | None
    pi_line_id: int | None
    source: SoLineSource | None
    #: 파생값 — 접수 수량 − 원천 수량(음수=원천보다 적게 받음). 직접 수주는 None.
    quantity_delta: int | None
    #: 파생값 — 접수 단가가 원천 단가와 다른가. 직접 수주는 None.
    price_changed: bool | None
    #: 파생값(S3-2 PR-3a) — 선적 잔량 = 라인 수량 − 살아 있는 선적 라인 합. 상세 응답에만 싣는다(라인 편집 응답은 접수 상태라 None — 선적 0).
    shipment_open_quantity: int | None = None


class SalesOrderSummary(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    buyer_partner_id: int
    buyer_name: str
    buyer_po_no: str | None
    buyer_po_date: date | None
    dest_market_code: str
    currency: str
    total_amount: int
    total_text: str
    qt_id: int | None
    qt_doc_number: str | None
    pi_id: int | None
    pi_doc_number: str | None
    assignee_id: int
    copied_from_id: int | None
    confirmed_at: datetime | None
    #: 확정 증적 — 접수 SO는 None. 여신 판정(WITHIN_LIMIT·NOT_MANAGED·APPROVED)·소비한 승인 id·PI 입금 게이트 판정(PASS·NOT_APPLICABLE·WARN·OVERRIDDEN·SKIPPED_OFF).
    credit_verdict: str | None
    credit_approval_id: int | None
    pi_gate_verdict: str | None
    version: int
    created_at: datetime


class SalesOrderDetail(SalesOrderSummary):
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    fx_rate_age_days: int | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    #: 확정 증거 스냅샷(`gate_evaluations` CONFIRMED) id — 접수 SO는 None. 12b가 이 id로 override·WARN·승인 ref 상세를 연결한다.
    confirm_evaluation_id: int | None = None
    last_line_no: int
    #: 참조 수주(QT/PI에서 만든 수주)인가 — 참조 수주는 목적지 시장 변경·원천 외 품목 추가가 안 된다.
    is_reference: bool
    lines: list[SalesOrderLineOut]

    @classmethod
    def of(cls, body: dict[str, Any]) -> SalesOrderDetail:
        return cls.model_validate(body)


class SoLineMutationOut(BaseModel):
    """라인 변경 응답 — 갱신된 헤더 version·합계를 실어 연속 편집이 자기 자신에게 409를 내지 않게 한다."""

    line: SalesOrderLineOut | None
    header_version: int
    total_amount: int
    total_text: str
