"""선적 요청·응답 (S3-2 PR-3a / design-D D2-1 S1~S4·S7~S15·D3 / design-integrated §2.9·§9 R-15).

요청 스키마는 전부 `extra="forbid"`이고 **SKU·단가·통화·환율·거래처·Incoterms·결제조건·구분·상태·번호·증빙일 필드가 구조적으로 없다**
(원천 사본 — 재입력 금지 `D:175` ①, 증빙일은 생성 시 서버가 KST 오늘로 1회 설정 — R-15). 생성 본문은 원천 라인 id·수량·국가 2개·
선택적 당사자·내부 메모뿐이다. 범용 전이의 `to_status`는 `Literal["CANCELLED"]` 1값이다(출고지시는 전용 경로 `release-order`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter

from app.modules.trade_docs.constants import MAX_LINES, MilestoneType
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


class ShipmentLineFromPo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_line_id: StrictInt = Field(ge=1)
    quantity: StrictInt


class ShipmentCreateFromPo(BaseModel):
    """PO 참조 수입선적 생성·미리보기 본문(S5·S6 — S3-2 PR-5a). 수출 본문과 같은 모양이고 원천 라인이 PO 라인이다.

    **단가·원가·통화·환율·공급사·조건 필드가 구조적으로 없다**(서버가 원천 PO에서 복사 — 원가는 복사하지 않는다, ADR-0024)."""

    model_config = ConfigDict(extra="forbid")

    lines: list[ShipmentLineFromPo] = Field(min_length=1, max_length=MAX_LINES)
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
    """라인 추가(S8) — 원천 라인 1줄(수출 = SO 라인 / 수입 = PO 라인 — 선적의 원천 전표 소속)과 수량. 헤더 version 필수(라인 편집 = 헤더 version +1)."""

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


# ── 마일스톤·통보·통관 요청 (S3-2 PR-4a / design-D D2-1 S16~S19·D2-2 M1~M6) ──────────────────────────────

#: 사유 상한(이력 CHECK `reason_clean`과 같다).
REASON_MAX = 500


class MilestonePlanRequest(BaseModel):
    """계획 설정·변경(M2) — 날짜형 `{planned_on}` / 시각형 `{planned_at(UTC 오프셋 필수), tz}`. 기존 계획을 바꾸면 롤오버(사유 필수).

    `version`은 행이 있을 때 그 행의 version(없으면 생략) — 화면이 본 상태와 다르면 409(겹친 편집). 계획을 지우는 경로는 없다.
    """

    model_config = ConfigDict(extra="forbid")

    planned_on: date | None = None
    planned_at: AwareDatetime | None = None
    tz: StrictStr | None = Field(default=None, max_length=64)
    reason: StrictStr | None = Field(default=None, max_length=REASON_MAX)
    version: StrictInt | None = Field(default=None, ge=1)


class MilestoneActualRequest(BaseModel):
    """실적 기록·정정(M3) — 날짜형 `{actual_on}` / 시각형 `{actual_at, tz}`. 값 필드는 **명시해야 한다**(지우기 = 명시적 null + 사유).

    기존 실적을 바꾸거나 지우면 정정(사유 필수). 신고수리 실적은 통관 기록에서만 온다(422).
    """

    model_config = ConfigDict(extra="forbid")

    actual_on: date | None = None
    actual_at: AwareDatetime | None = None
    tz: StrictStr | None = Field(default=None, max_length=64)
    reason: StrictStr | None = Field(default=None, max_length=REASON_MAX)
    version: StrictInt | None = Field(default=None, ge=1)


class MilestonePlanDraftRequest(BaseModel):
    """계획 초안 1클릭(M4) — 본문 없음(`{}`). 적용 종류의 빈 계획 행을 만들고 이미 있는 종류는 건너뛴다."""

    model_config = ConfigDict(extra="forbid")


class ProfileMilestoneTypeAddRequest(BaseModel):
    """품목군 마일스톤 세트에 종류 추가(S3-2 PR-4c — 관리자). 선적 저장형 8종만(파생·OEM 종류 = 도메인 422 TYPE_NOT_APPLICABLE, 모르는 값 = 스키마 422)."""

    model_config = ConfigDict(extra="forbid")

    milestone_type: MilestoneType


class MilestoneNoticeRequest(BaseModel):
    """롤오버 통보 기록(M6) — 실제로 알린 사실의 기록(발송 0). 수단(메일·전화 등)은 요지에 적는다(채널 열 없음 — X-23)."""

    model_config = ConfigDict(extra="forbid")

    occurred_on: date
    counterpart_partner_id: StrictInt | None = Field(default=None, ge=1)
    summary: StrictStr = Field(min_length=1, max_length=2000)


class CustomsRecordCreateRequest(BaseModel):
    """통관 기록 추가(S17) — 신고 구분은 선적 구분과 같아야 한다. 수리일은 미수리면 생략. 세율·세액·HS 필드는 없다."""

    model_config = ConfigDict(extra="forbid")

    declaration_kind: Literal["EXPORT", "IMPORT"]
    declaration_no: StrictStr = Field(min_length=1, max_length=40)
    declared_on: date
    accepted_on: date | None = None
    customs_broker_partner_id: StrictInt | None = Field(default=None, ge=1)
    note: StrictStr | None = Field(default=None, max_length=1000)


class CustomsRecordUpdateRequest(BaseModel):
    """통관 기록 정정(S18) — 보낸 필드만 바뀐다. 신고번호·신고일 변경이나 기존 수리일 변경·삭제는 사유 필수(수리일 첫 입력은 기록)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    declaration_no: StrictStr | None = Field(default=None, min_length=1, max_length=40)
    declared_on: date | None = None
    accepted_on: date | None = None
    customs_broker_partner_id: StrictInt | None = Field(default=None, ge=1)
    note: StrictStr | None = Field(default=None, max_length=1000)
    reason: StrictStr | None = Field(default=None, max_length=REASON_MAX)


class CustomsRecordDeleteRequest(BaseModel):
    """통관 기록 삭제(S19) — version·사유 필수(사유는 audit_log)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    reason: StrictStr | None = Field(default=None, max_length=REASON_MAX)


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
    """원천 라인 대비 — 수출 = SO 라인 수량·선적 잔량, 수입 = PO 라인 수량·배정 가능량(이 선적 포함 소비 후). 화면은 산술하지 않는다."""

    id: int
    line_no: int
    quantity: int
    #: 원천 라인의 남은 수량(살아 있는 선적 전부를 뺀 값 — 저장 아님). 수입은 **배정 가능량**(PO 잔량 아님 — PO 잔량은 입고에서만 준다).
    remaining_after: int


class DgOut(BaseModel):
    """SKU 마스터의 DG 표시(배지만 — 차단 0, 판정은 S4-4 게이트)."""

    flag: bool
    un_number: str | None
    dg_class: str | None


class AvailabilityOut(BaseModel):
    """§8.3 '자리' — 포트 무변경, 값은 항상 NOT_IMPLEMENTED('가용재고 미산정' 배지, 0·현재고 표시 금지 — X-26)."""

    status: Literal["NOT_IMPLEMENTED"]


class _ShipmentLineCommon(BaseModel):
    """수출·수입 라인 공통 — **금액·통화 계열 필드가 없다**(수입 라인은 이 공통 부분과 원천 PO 라인 id뿐 — S3-2 PR-5a, G3)."""

    id: int
    line_no: int
    sku: ShipmentSkuOut
    quantity: int
    #: 원천 라인 대비 — 수출 = SO 라인 수량·선적 잔량 / 수입 = PO 라인 수량·**배정 가능량**(이 선적 포함 살아 있는 수입선적 반영 후).
    source_line: SourceLineOut
    dg: DgOut
    availability: AvailabilityOut


class ShipmentLineOut(_ShipmentLineCommon):
    """수출선적 라인 — 판매가 축(SO 단가 사본, 마스킹 비대상)."""

    so_line_id: int | None
    currency: str
    unit_price_amount: int | None
    unit_price_text: str | None
    is_free: bool
    line_amount: int
    line_amount_text: str


class ImportShipmentLineOut(_ShipmentLineCommon):
    """수입선적 라인 — **단가·금액·통화·무상 표식 키가 아예 없다**(null이 아니라 미포함 — design-D D3, PO 원가 비복사 ADR-0024 10번째 채널 미개설)."""

    po_line_id: int


class ShipmentPartyOut(BaseModel):
    id: int
    role: str
    partner_id: int
    name_en: str
    address_en: str | None
    #: 원천 거래처 자동 스냅샷 행(수출 CONSIGNEE) — 삭제·교체 불가.
    auto: bool
    version: int


# ── 마일스톤 보드·변경 이력·통관 응답 (S3-2 PR-4a / design-D D3 MilestoneBoard·§9 R-06·R-19·R-20·R-25) ──────────


class InstantOut(BaseModel):
    """시각형 값 — UTC 시각 + IANA 시간대(화면이 KST·현지 병기)."""

    at_utc: datetime
    tz: str


class EffectiveOut(BaseModel):
    """유효값(실적 우선, 없으면 계획) — 날짜형 'YYYY-MM-DD' / 시각형 UTC ISO 문자열."""

    value: str
    basis: Literal["ACTUAL", "PLANNED"]


class DerivedOut(BaseModel):
    """파생값(비저장 계산값) — UNKNOWN은 통과가 아니다(사유 코드 동반, 빈칸·0일 대체 금지)."""

    status: Literal["OK", "UNKNOWN", "NOT_APPLICABLE"]
    value: date | None
    basis: Literal["ACTUAL", "PLANNED"] | None
    reason_code: str | None


class HolidayFlagOut(BaseModel):
    """휴일 경고(ETA·도착국만 — R-09). UNVERIFIED = 그 국가·연도 캘린더 미등록(평일 아님). 날짜는 옮기지 않는다(경고만)."""

    flag: Literal["HOLIDAY", "CLEAR", "UNVERIFIED"]
    country: str
    name: str | None


class MilestoneRowOut(BaseModel):
    milestone_type: str
    kind: Literal["STORED", "DERIVED"]
    value_shape: Literal["DATE", "DATETIME"]
    #: 구분(수출·수입)·결제유형 적용 여부 — false인 행은 화면이 숨긴다(L/C 제시기한은 LC 결제만).
    applicable: bool
    planned: InstantOut | date | None
    actual: InstantOut | date | None
    effective: EffectiveOut | None
    derived: DerivedOut | None
    #: 시각형만 — D-N 기준일(min(현지, KST) — 이른 경고) / 현지 날짜. 날짜형은 null(R-25).
    scan_date: date | None
    local_date: date | None
    #: 신고수리 행만 — NONE(기록 없음)·CLEARED(전건 수리)·PARTIAL(미수리 1건↑ — 산식은 MIN 유지, R-06).
    customs_state: Literal["NONE", "PARTIAL", "CLEARED"] | None
    customs_pending_count: int | None
    #: 열린 기일만(실적 있으면 null) — 날짜형 = 유효일 − KST 오늘, 시각형 = scan_date − KST 오늘.
    days_left: int | None
    #: 날짜형 = KST 오늘 > 유효일, **시각형 = 현재 UTC > 유효 시각**(R-20). 대금만기·제시기한은 충족 신호가 S3-3이라 null.
    is_overdue: bool | None
    #: 저장형 행의 판정 불가 사유 — `TZ_UNRESOLVED`(저장된 시간대를 앱의 tzdata가 모름)면 scan_date·local_date·days_left·is_overdue가
    #: 전부 null이다(KST 추정 계산 금지 — "판정 불가" 배지). 정상 행은 null.
    unknown_reason: Literal["TZ_UNRESOLVED"] | None
    #: 적재기한만 — MET·MET_LATE·OPEN·OVERDUE·UNKNOWN(이행일 = ETD·B/L 실적 MAX — R-10).
    fulfilment: str | None
    holiday: HolidayFlagOut | None
    #: 롤오버(계획 변경) 횟수·통보 기록이 연결되지 않은 롤오버 수.
    rollover_count: int
    unnotified_rollovers: int
    order_warning: Literal["ETA_BEFORE_ETD"] | None
    milestone_id: int | None
    version: int | None
    #: 입력처 — 신고수리 실적은 통관 기록(마일스톤 실적 버튼 없음), 파생 행은 null.
    input_source: Literal["MILESTONE", "CUSTOMS_RECORD"] | None


class HolidaySummaryOut(BaseModel):
    holiday: int
    unverified: int


class MilestoneBoardOut(BaseModel):
    """선적 마일스톤 보드(M1·상세 내장) — 저장형 8 + 파생 3 = 11행, 업무 흐름 순서. 판정은 전부 서버 계산값(프런트 날짜 산술 0)."""

    today_kst: date
    holiday_summary: HolidaySummaryOut
    rows: list[MilestoneRowOut]


class ChangeRefOut(BaseModel):
    id: int
    change_kind: str


class MilestoneWriteOut(BaseModel):
    """M2·M3 응답(R-19) — 이력 행이 안 생기는 no-op이면 change = null. 같은 Idempotency-Key 재요청 = 같은 change.id."""

    board: MilestoneBoardOut
    change: ChangeRefOut | None


class OemMilestoneBoardOut(MilestoneBoardOut):
    """OEM 생산 일정 보드(M7 — S3-2 PR-4c / design-D D7) — 원료수급 → 충진 → 포장 → 출하검사 4행(날짜형·파생 없음).
    행 모양은 선적 보드와 같다(화면 타임라인 컴포넌트 재사용). 알림·휴일 배지 없음(`holiday_summary`는 늘 0), 통보 통로 없음(미연결 0)."""

    po_id: int
    #: 표시 편의(서버가 쓰기 시 다시 검사한다) — EDIT_MILESTONES(무역·관리자, 발주가 발행·공급사 확인 중일 때).
    allowed_actions: list[str]


class ProfileMilestoneTypeOut(BaseModel):
    """품목군 마일스톤 세트 행 1개 — 제거는 `DELETE /item-profiles/{profile_id}/milestone-types/{id}`."""

    id: int
    item_profile_id: int
    milestone_type: str
    created_at: datetime


class OemMilestoneWriteOut(BaseModel):
    """M8 응답 — M2·M3과 같은 `{board, change}`(R-19 승계: no-op = null, 같은 Idempotency-Key 재요청 = 같은 change.id)."""

    board: OemMilestoneBoardOut
    change: ChangeRefOut | None


class NoticeOut(BaseModel):
    comm_log_id: int
    occurred_on: date
    summary: str
    partner_id: int | None
    partner_name: str | None
    actor_user_id: int
    created_at: datetime


class MilestoneChangeOut(BaseModel):
    id: int
    milestone_id: int
    milestone_type: str
    change_kind: str
    old: InstantOut | date | None
    new: InstantOut | date | None
    reason: str | None
    actor_user_id: int
    actor_name: str | None
    created_at: datetime
    notices: list[NoticeOut]


class CustomsBrokerOut(BaseModel):
    partner_id: int
    name: str


class CustomsRecordOut(BaseModel):
    id: int
    shipment_id: int
    declaration_kind: str
    declaration_no: str
    declared_on: date
    #: 수리일 — null = 미수리("미수리" 배지).
    accepted_on: date | None
    customs_broker: CustomsBrokerOut | None
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class CustomsSummaryOut(BaseModel):
    """상세 내장 요약 — 목록은 `GET /shipments/{id}/customs-records`(Page)."""

    live_count: int
    pending_count: int
    latest_accepted_on: date | None


class _ShipmentDetailCommon(BaseModel):
    """수출·수입 상세 공통 — **금액·통화 계열 필드가 없다**(통화·소수 자릿수·환율·합계는 수출 변형에만 — S3-2 PR-5a, G3·R-3c-2)."""

    id: int
    doc_number: str
    doc_date: date
    status: str
    version: int
    frozen_at: datetime | None
    source: ShipmentSourceOut
    counterparty: CounterpartyOut
    origin_country_code: str
    dest_country_code: str
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    assignee: AssigneeOut
    last_line_no: int
    dg_line_count: int
    parties: list[ShipmentPartyOut]
    #: 마일스톤 보드(PR-4a) — 쓰기 후 재조회는 `GET /shipments/{id}/milestones`(같은 형태).
    milestones: MilestoneBoardOut
    customs_summary: CustomsSummaryOut
    #: 표시 편의(서버가 쓰기 시 다시 검사한다) — RELEASE_ORDER·CANCEL·EDIT_LINES·EDIT_COUNTRIES·EDIT_META·EDIT_PARTIES·
    #: EDIT_MILESTONES·PLAN_DRAFT·EDIT_CUSTOMS 중 요청자 역할·상태로 가능한 것.
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


class ExportShipmentDetail(_ShipmentDetailCommon):
    """수출선적 상세 — 통화·고정 환율(원천 SO 사본)·판매가 합계(마스킹 비대상 — 여신·판매 합계 선례)."""

    shipment_kind: Literal["EXPORT"]
    currency: str
    minor_units: int
    #: 고정 환율(원천 사본 — 선적 시점 신규 입력 없음).
    fx_rate: str | None
    fx_rate_date: date | None
    total_amount: int
    total_text: str
    lines: list[ShipmentLineOut]


class ImportShipmentDetail(_ShipmentDetailCommon):
    """수입선적 상세 — **통화·소수 자릿수·환율·합계 키가 아예 없다**(null이 아니라 미포함). 원천 PO 통화·환율은 PO CostHidden이 원가 비열람
    역할에게 가리는 필드라(`^currency$`·`fx_rate`·`minor_units`) 선적이 우회 통로가 되지 않게 전 역할에게 같은 모양이다(원가 열람 역할로도 0 —
    GC-G3, ADR-0024 10번째 채널 미개설). CSV의 통화·합계 빈칸(PR-3c)과 같은 판정이다(R-3c-2 해소)."""

    shipment_kind: Literal["IMPORT"]
    lines: list[ImportShipmentLineOut]


#: 상세 응답 — 구분(`shipment_kind`)이 판별자인 합집합(수출·수입이 **필드 집합부터 다르다** — 원가 갈림이 아니라 구분 갈림이라 역할 분기 없음).
#: FastAPI가 반환 주석으로 응답을 재검증해도 판별자가 갈래를 정하므로 수출 금액 필드가 지워지거나 수입에 금액이 붙는 일이 없다(시험 고정).
ShipmentDetail = Annotated[
    ExportShipmentDetail | ImportShipmentDetail, Field(discriminator="shipment_kind")
]
_DETAIL_ADAPTER: TypeAdapter[ExportShipmentDetail | ImportShipmentDetail] = TypeAdapter(
    ShipmentDetail
)


def shipment_detail_out(body: dict[str, Any]) -> ExportShipmentDetail | ImportShipmentDetail:
    """서비스가 만든 상세 dict → 구분에 맞는 응답 모델(판별자 `shipment_kind`). 라우터가 응답 직전에 부른다."""
    return _DETAIL_ADAPTER.validate_python(body)


class _ShipmentListItemCommon(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    source: ShipmentSourceOut
    counterparty_name: str
    origin_country_code: str
    dest_country_code: str
    line_count: int
    #: ETD·ETA 유효값(실적 우선, 없으면 계획 — 'YYYY-MM-DD' 현지 날짜, `new Date()` 금지). 행이 없거나 값이 없으면 null(design-D D3).
    etd: EffectiveOut | None
    eta: EffectiveOut | None
    assignee: AssigneeOut
    created_at: datetime
    updated_at: datetime


class ExportShipmentListItem(_ShipmentListItemCommon):
    shipment_kind: Literal["EXPORT"]
    currency: str
    total_amount: int
    total_text: str


class ImportShipmentListItem(_ShipmentListItemCommon):
    """수입선적 목록 행 — 통화·합계 키 없음(상세와 같은 판정 — CSV는 같은 열을 빈칸으로)."""

    shipment_kind: Literal["IMPORT"]


ShipmentListItem = Annotated[
    ExportShipmentListItem | ImportShipmentListItem, Field(discriminator="shipment_kind")
]
_LIST_ITEM_ADAPTER: TypeAdapter[ExportShipmentListItem | ImportShipmentListItem] = TypeAdapter(
    ShipmentListItem
)


def shipment_list_item_out(
    body: dict[str, Any],
) -> ExportShipmentListItem | ImportShipmentListItem:
    return _LIST_ITEM_ADAPTER.validate_python(body)


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


class ImportShipmentPreviewLine(BaseModel):
    """수입 미리보기 라인 — **단가·금액·통화·무상 키 없음**(G3). 배정 가능량 = PO 라인 수량 − 살아 있는 수입선적 수량(PO 잔량 아님)."""

    po_line_id: int
    line_no: int
    sku: ShipmentSkuOut
    quantity: int
    #: 이 선적 전 배정 가능량 / 이 선적 뒤 남을 배정 가능량(파생 — 화면 산술 0).
    assignable_before: int
    remaining_after: int
    dg: DgOut


class ImportShipmentPreview(BaseModel):
    """수입선적 비저장 미리보기(S5 — S3-2 PR-5a). 채번·이벤트·멱등 키·잠금 0, 생성과 같은 검증. **통화·환율·합계 키 없음**(수입 상세와 같은 판정 — G3)."""

    po_id: int
    po_doc_number: str
    po_status: str
    doc_date: date
    shipment_kind: Literal["IMPORT"]
    counterparty: CounterpartyOut
    origin_country_code: str
    dest_country_code: str
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    lines: list[ImportShipmentPreviewLine]
    parties: list[ShipmentPreviewParty]
