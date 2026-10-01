"""오더 인테이크 요청·응답 (S3-1 PR-13a / design-D D1·D2·D7 / ADR-0071).

쓰기 요청은 전부 `extra="forbid"`이고 **`status`·`sales_order_id`·`decided_*`·`source_*`·`buyer_po_no_key`·`extracted_snapshot`·라인 `sku_id`가 구조적으로 없다** —
상태는 확정·거부 엔드포인트로만, PO 비교 키와 SKU 해석은 서버가 산출한다(매핑 우회 금지). 거래처·통화는 **등록 후 변경 불가**라 수정 요청에 필드가 없다.
낙관 잠금 필드는 저장소 관용대로 `version`이다. 응답에는 원가·마진·매입가 필드가 없다(전 역할 열람).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.modules.gates.text import REASON_MAX, reason_problem


@dataclass(frozen=True, slots=True)
class IntakeLineIn:
    """`register_intake`가 받는 라인 — 단가는 사람 표기 문자열(서비스가 통화 최소단위 정수로 변환, 반올림 금지). `sku_id`는 없다."""

    buyer_item_code: str
    quantity: int
    unit_price: str
    requested_delivery_date: date | None = None
    #: CSV 입구(PR-14)만 채운다(엑셀 행번호). 수동 입구는 None.
    source_row_no: int | None = None


@dataclass(frozen=True, slots=True)
class IntakeHeaderIn:
    """`register_intake`가 받는 헤더 값(거래처 id는 별도 인자). PO 비교 키는 서버가 산출한다."""

    buyer_po_no: str
    currency: str
    dest_market_code: str
    buyer_po_date: date | None = None
    assignee_id: int | None = None
    #: 복제 재접수의 원본 SO(X-13) — 서비스가 "같은 바이어·취소 SO"를 검증한다.
    copied_from_so_id: int | None = None


# ── 요청 ────────────────────────────────────────────────────────────────────


class IntakeLineRequest(BaseModel):
    """수동 등록의 라인 — 바이어 품번·수량·단가(사람 표기)·요청납기. **`sku_id` 입력 필드가 없다**(품번→SKU는 서버 해석만)."""

    model_config = ConfigDict(extra="forbid")

    buyer_item_code: StrictStr = Field(min_length=1, max_length=100)
    quantity: StrictInt
    unit_price: StrictStr = Field(min_length=1, max_length=40)
    requested_delivery_date: date | None = None


class IntakeCreateRequest(BaseModel):
    """수동 입구 `POST /order-intakes` — 헤더+라인 일괄. 항상 PENDING으로만 착지한다(상태 필드 없음)."""

    model_config = ConfigDict(extra="forbid")

    buyer_partner_id: StrictInt = Field(ge=1)
    buyer_po_no: StrictStr = Field(min_length=1, max_length=200)
    buyer_po_date: date | None = None
    currency: StrictStr = Field(min_length=3, max_length=3)
    dest_market_code: StrictStr = Field(min_length=2, max_length=2)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    copied_from_so_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[IntakeLineRequest] = Field(min_length=1, max_length=200)


class IntakeLineEdit(BaseModel):
    """수정의 라인 — `id`가 있으면 그 라인을 제자리 수정, 없으면 새 라인. 목록에 없는 기존 라인은 제외(soft delete)된다."""

    model_config = ConfigDict(extra="forbid")

    id: StrictInt | None = Field(default=None, ge=1)
    buyer_item_code: StrictStr = Field(min_length=1, max_length=100)
    quantity: StrictInt
    unit_price: StrictStr = Field(min_length=1, max_length=40)
    requested_delivery_date: date | None = None


class IntakeUpdateRequest(BaseModel):
    """대기(PENDING) 인테이크 편집 — 보낸 필드만 바뀐다. **거래처·통화·상태·소스·PO 비교 키·SKU는 필드가 없다**(거래처·통화를 잘못 골랐으면 거부 후 재등록)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    buyer_po_no: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    buyer_po_date: date | None = None
    dest_market_code: StrictStr | None = Field(default=None, min_length=2, max_length=2)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[IntakeLineEdit] | None = Field(default=None, min_length=1, max_length=200)


class IntakeVersionRequest(BaseModel):
    """해석(resolve)·확정 — 사용자가 본 인테이크 버전만 받는다(불일치 409)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class IntakeRejectRequest(BaseModel):
    """거부 — 사유 필수(5~500자, 제어문자·채움 문자 불가). 거부는 종결이며 행·스냅샷·사유는 영구 보존된다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    reason: StrictStr = Field(max_length=REASON_MAX * 2)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        problem = reason_problem(value)
        if problem is not None:
            raise ValueError(problem)
        return value.strip()


# ── 응답 ────────────────────────────────────────────────────────────────────


class IntakeLineOut(BaseModel):
    id: int
    line_no: int
    buyer_item_code: str
    #: 검토자가 본 해석 저장본(NULL=미매핑).
    sku_id: int | None
    sku_code: str | None
    sku_name_ko: str | None
    #: 파생값 — SKU 현재 상태(ACTIVE·DISCONTINUED…·삭제=DELETED), 미매핑이면 null.
    sku_status: str | None
    #: 파생값(저장 아님) — 지금 품번 매핑으로 해석하면: MAPPED(저장본과 같음)·UNMAPPED·STALE(저장본과 다름 → 재해석 필요).
    mapping_state: str
    quantity: int
    unit_price_amount: int
    unit_price_text: str
    line_amount: int
    requested_delivery_date: date | None
    source_row_no: int | None


class IntakeSummary(BaseModel):
    id: int
    version: int
    source_kind: str
    status: str
    buyer_partner_id: int
    buyer_name: str | None
    buyer_po_no: str
    buyer_po_date: date | None
    currency: str
    dest_market_code: str
    assignee_id: int
    line_count: int
    total_amount: int
    total_text: str
    sales_order_id: int | None
    copied_from_so_id: int | None
    created_at: datetime
    decided_at: datetime | None


class IntakeDetail(BaseModel):
    id: int
    version: int
    source_kind: str
    status: str
    buyer_partner_id: int
    buyer_name: str | None
    buyer_po_no: str
    buyer_po_date: date | None
    currency: str
    dest_market_code: str
    assignee_id: int
    reject_reason: str | None
    decided_at: datetime | None
    decided_by_id: int | None
    sales_order_id: int | None
    copied_from_so_id: int | None
    last_line_no: int
    created_at: datetime
    updated_at: datetime
    total_amount: int
    total_text: str
    lines: list[IntakeLineOut]
    #: 불변 원본(최초 제출) — "원본 나란히 검토"의 원천. 현재 값과 다를 수 있다(수정 후).
    original: dict[str, Any]


class IntakeConfirmOut(BaseModel):
    """접수 확정 응답 — 만들어진 SO(접수 상태)의 id·번호와 확정된 인테이크."""

    intake_id: int
    sales_order_id: int
    doc_number: str
    intake: IntakeDetail


class IntakeGateResultOut(BaseModel):
    """인테이크 검토용 게이트 결과 1건 — 서버가 계산한 값(저장하지 않는다). override는 SO에서만 부여된다(인테이크 단계는 정보)."""

    gate_code: str
    line_id: int | None
    line_no: int | None
    level: str
    resolution: str
    reason_code: str
    message_ko: str
    basis: dict[str, Any]
    #: 표시 전용 상세(다른 점유 문서 등) — 무역·관리자에게만.
    detail: dict[str, Any]
    #: NOT_REQUIRED(통과·경고) / UNRESOLVED(미해소) — `clearance`의 정산 결과.
    settlement: str
    #: 이 결과가 **접수 확정을 막는가**(품번·중복 PO만 하드 조건 — 나머지는 SO 확정 시점에 판정한다).
    blocks_intake_confirm: bool


class IntakeGateReportOut(BaseModel):
    """`GET /order-intakes/{id}/gates` — 인테이크 시점 게이트 평가(실시간 계산·**정보**). 접수 확정의 하드 조건은 품번 매핑·중복 PO·입력 완결성뿐이다."""

    intake_id: int
    status: str
    phase: str
    evaluated_at: datetime
    #: 시장 준비도는 계산값 표시이며 법적 판정이 아니다 — 고정 문구.
    note: str
    readiness_scope_note: str
    #: 하드 게이트(품번·중복 PO)가 전부 통과(경고 포함)인가 — 서버 판정(`clearance`), 화면이 다시 판정하지 않는다.
    intake_confirmable: bool
    gates: list[IntakeGateResultOut]
