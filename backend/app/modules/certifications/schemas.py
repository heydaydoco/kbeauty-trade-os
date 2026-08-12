"""인증 인스턴스 요청·응답 (§5.1·§5.2 / S2-2 PR-1).

★ 생성·편집 요청에 status 필드가 **구조적으로 없다** — 상태 도달 경로는 전이
  액션(POST /certifications/{id}/transitions) 하나뿐이다(층2 — #16 "PATCH의
  status 불가" 선례의 전면화. 부재는 아키텍처 테스트가 고정한다).

★ 편집(PATCH)은 exclude_unset 의미론이다 — 안 보낸 필드는 안 바뀐다.
  만료일 정정은 달력 파생 3태에서만 받는다(서비스 409 — 조건 A 수렴과 세트).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.certifications.service import CertificationView, StatusLogView, TaskView

TargetType = Literal["PRODUCT", "SKU", "FACILITY", "COMPANY", "INGREDIENT"]
#: 전이 도달값 — 값 형식은 스키마가 거르고, 허용 여부(§5.2 전이 표)는 서비스가
#: 409로 판정한다(110쌍 전수는 서비스 층 테스트 — 조건 C). machine.
#: CERTIFICATION_STATUSES와의 일치는 아키텍처 테스트가 고정한다.
Status = Literal[
    "NOT_STARTED",
    "PREPARING",
    "SUBMITTED",
    "IN_REVIEW",
    "SUPPLEMENTING",
    "APPROVED",
    "EXPIRING",
    "RENEWING",
    "REJECTED",
    "EXPIRED",
    "SUSPENDED",
]


class CertificationCreateRequest(BaseModel):
    #: 모르는 필드(특히 status 주입 시도)는 무시가 아니라 422다.
    model_config = ConfigDict(extra="forbid")

    template_id: int = Field(ge=1)
    #: 템플릿 적용단위와 일치해야 한다(서비스 422 — 안건 ②).
    target_type: TargetType
    #: COMPANY(자사 단일)만 비운다 — 그 외는 필수(서비스 검증+DB CHECK).
    target_id: int | None = Field(default=None, ge=1)
    assignee_id: int | None = Field(default=None, ge=1)
    note: str | None = None


class CertificationTransitionRequest(BaseModel):
    """전이 액션 본문 — 부속 데이터는 같은 요청·같은 트랜잭션이다(§17.1).

    부속 필드는 전이별 허용 목록 밖이면 무시가 아니라 422다(조용한 무시 금지).
    """

    model_config = ConfigDict(extra="forbid")

    to: Status
    #: 낙관 잠금(§17.2) — 목록·상세 응답의 version을 그대로 되돌려 준다.
    version: int = Field(ge=1)
    #: 반려·중단은 필수(§5.2 — 결여 시 422), 그 외 전이는 선택 메모.
    reason: str | None = None
    applied_on: date | None = None
    approved_on: date | None = None
    valid_from: date | None = None
    expires_on: date | None = None
    cert_number: str | None = Field(default=None, min_length=1, max_length=100)


class CertificationUpdateRequest(BaseModel):
    """비상태 필드 편집 — status는 여기 없다(파일 독스트링).

    extra=forbid — status를 실어 보내면 조용한 무시가 아니라 422다(#16 선례의
    전면화: 상태 도달은 전이 액션뿐).
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expires_on: date | None = None
    cert_number: str | None = Field(default=None, max_length=100)
    assignee_id: int | None = Field(default=None, ge=1)
    note: str | None = None


class TaskAddRequest(BaseModel):
    seq: int = Field(ge=1)
    item_name: str = Field(min_length=1, max_length=200)
    is_required: bool
    note: str | None = None


class TaskUpdateRequest(BaseModel):
    """체크 토글·항목 편집·서류 링크 — exclude_unset 의미론.

    document_id는 3값 의미론이다: 필드 생략=미변경 / null=연결 해제 /
    양수=해당 문서로 연결(실재 검증은 서비스 — 폴리모픽 §5.6 공용 참조라
    소유자 제한이 없다).
    """

    version: int = Field(ge=1)
    done: bool | None = None
    seq: int | None = Field(default=None, ge=1)
    item_name: str | None = Field(default=None, min_length=1, max_length=200)
    document_id: int | None = Field(default=None, ge=1)
    note: str | None = None


class CertificationSummary(BaseModel):
    id: int
    template_id: int
    market_code: str
    template_name: str
    requirement_type: str
    target_type: str
    target_id: int | None
    target_label: str
    status: str
    validity_months: int | None
    renewal_cycle_months: int | None
    renewal_lead_days: int | None
    source_url: str | None
    last_verified_on: date | None
    cert_number: str | None
    applied_on: date | None
    approved_on: date | None
    valid_from: date | None
    expires_on: date | None
    assignee_id: int | None
    note: str | None
    version: int

    @classmethod
    def of(cls, view: CertificationView) -> CertificationSummary:
        return cls(**asdict(view))


class TaskSummary(BaseModel):
    id: int
    certification_id: int
    seq: int
    item_name: str
    document_type_id: int | None
    is_required: bool
    done: bool
    done_at: str | None
    document_id: int | None
    note: str | None
    version: int

    @classmethod
    def of(cls, view: TaskView) -> TaskSummary:
        return cls(**asdict(view))


class StatusLogSummary(BaseModel):
    id: int
    certification_id: int
    occurred_at: str
    from_status: str
    to_status: str
    reason: str | None
    actor_user_id: int | None
    expires_on_snapshot: date | None
    cert_number_snapshot: str | None

    @classmethod
    def of(cls, view: StatusLogView) -> StatusLogSummary:
        return cls(**asdict(view))
