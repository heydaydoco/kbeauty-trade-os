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
from app.modules.order_intake.models import MAX_BUYER_ITEM_CODE, MAX_INTAKE_LINES, MAX_PO_INPUT


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

    buyer_item_code: StrictStr = Field(min_length=1, max_length=MAX_BUYER_ITEM_CODE)
    quantity: StrictInt
    unit_price: StrictStr = Field(min_length=1, max_length=40)
    requested_delivery_date: date | None = None


class IntakeCreateRequest(BaseModel):
    """수동 입구 `POST /order-intakes` — 헤더+라인 일괄. 항상 PENDING으로만 착지한다(상태 필드 없음)."""

    model_config = ConfigDict(extra="forbid")

    buyer_partner_id: StrictInt = Field(ge=1)
    buyer_po_no: StrictStr = Field(min_length=1, max_length=MAX_PO_INPUT)
    buyer_po_date: date | None = None
    currency: StrictStr = Field(min_length=3, max_length=3)
    dest_market_code: StrictStr = Field(min_length=2, max_length=2)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    copied_from_so_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[IntakeLineRequest] = Field(min_length=1, max_length=MAX_INTAKE_LINES)


class IntakeLineEdit(BaseModel):
    """수정의 라인 — `id`가 있으면 그 라인을 제자리 수정, 없으면 새 라인. 목록에 없는 기존 라인은 제외(soft delete)된다."""

    model_config = ConfigDict(extra="forbid")

    id: StrictInt | None = Field(default=None, ge=1)
    buyer_item_code: StrictStr = Field(min_length=1, max_length=MAX_BUYER_ITEM_CODE)
    quantity: StrictInt
    unit_price: StrictStr = Field(min_length=1, max_length=40)
    requested_delivery_date: date | None = None


class IntakeUpdateRequest(BaseModel):
    """대기(PENDING) 인테이크 편집 — 보낸 필드만 바뀐다. **거래처·통화·상태·소스·PO 비교 키·SKU는 필드가 없다**(거래처·통화를 잘못 골랐으면 거부 후 재등록)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    buyer_po_no: StrictStr | None = Field(default=None, min_length=1, max_length=MAX_PO_INPUT)
    buyer_po_date: date | None = None
    dest_market_code: StrictStr | None = Field(default=None, min_length=2, max_length=2)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[IntakeLineEdit] | None = Field(
        default=None, min_length=1, max_length=MAX_INTAKE_LINES
    )


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


class PoOccupiedOut(BaseModel):
    """PENDING 인테이크의 (거래처, PO키)를 **다른 문서**가 점유 중이다 — 이 인테이크는 중복 PO 하드 게이트로 확정할 수 없다(막다른 PENDING). 금액 없음."""

    #: SALES_ORDER(비취소 SO가 점유 — 착지 뒤 참조 생성·PO번호 편집으로 생길 수 있다) 또는 INTAKE.
    kind: str
    #: 점유 문서번호·상태 — 무역·관리자에게만(그 외 역할은 null).
    doc_number: str | None
    status: str | None


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
    #: 서버 계산 — PENDING이고 다른 문서가 같은 PO를 점유 중이면 값이 있다(없으면 null).
    po_occupied: PoOccupiedOut | None
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
    #: 자유 텍스트 — 무역·관리자에게만(그 외 역할은 null).
    reject_reason: str | None
    decided_at: datetime | None
    decided_by_id: int | None
    sales_order_id: int | None
    copied_from_so_id: int | None
    po_occupied: PoOccupiedOut | None
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
    """`GET /order-intakes/{id}/gates` — 인테이크 시점 게이트 평가(실시간 계산·**정보**). 접수 확정의 **게이트 하드 조건**은 품번 매핑·중복 PO뿐이다(입력 완결성은 게이트가 아니라 확정 시 별도 422)."""

    intake_id: int
    status: str
    phase: str
    evaluated_at: datetime
    #: 시장 준비도는 계산값 표시이며 법적 판정이 아니다 — 고정 문구.
    note: str
    readiness_scope_note: str
    #: **하드 게이트 2종(품번 매핑·중복 PO)만의 통과 여부 — 서버 사전 점검**(`clearance`, 화면이 다시 판정하지 않는다). **확정 가능 보장이 아니다**:
    #: 입력 완결성(거래처 유형·시장·통화·라인≥1·같은 SKU 유상 라인 중복·금액 상한)·확정 시점 납기 경과·복제 원본 자격은 확정 때 다시 검사한다(422·409).
    intake_confirmable: bool
    gates: list[IntakeGateResultOut]


# ── CSV 입구 응답 (PR-14a) ──────────────────────────────────────────────────────


class CsvImportIntakeOut(BaseModel):
    """CSV 한 파일이 만든 인테이크 1건(바이어 PO 1건)의 요약 — 상세는 `GET /order-intakes/{id}`."""

    id: int
    version: int
    buyer_partner_id: int
    buyer_name: str | None
    buyer_po_no: str
    currency: str
    dest_market_code: str
    line_count: int
    #: 품번이 아직 SKU에 매핑되지 않은 라인 수 — 오류가 아니다(검토 화면이 품번 등록을 유도한다).
    unmapped_line_count: int
    total_amount: int
    total_text: str | None
    #: 이 PO의 첫 엑셀 행번호(헤더=1).
    first_row_no: int


class CsvImportResult(BaseModel):
    """`POST /order-intakes/import-csv` 201 — 전부 PENDING으로 착지했다(오류가 있으면 이 응답이 아니라 422·409 오류 응답이다)."""

    original_filename: str
    source_sha256: str
    row_count: int
    group_count: int
    line_count: int
    unmapped_line_count: int
    intakes: list[CsvImportIntakeOut]
