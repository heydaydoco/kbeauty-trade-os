"""오더 보드 요청·응답 (S3-1 PR-15a / design-D D6·D7 / ADR-0066).

■ 요청·쿼리 모델은 **전부 `extra="forbid"`**다 — 모르는 쿼리 키·본문 키는 422(조용한 무시 금지). 보드 필터(`BoardFilter`)는 쿼리 모델이자 저장 필터의
  `filter_config` 스키마이고, 저장 시·읽기 시 같은 모델로 재검증한다(낡은 저장 필터는 `needs_resave=true`).
■ 응답 카드(`BoardCard`)에는 **원가·마진·매입가·여신·게이트 배지 필드 자체가 없다**(어느 역할에도 — 게이트를 카드마다 평가하면 N+1·낡은 판정 오독, 상세에서 실시간 계산).
  판매 금액(헤더 합계)은 마스킹 비대상이다.
■ 벌크 요청에는 override·승인·사유·force 같은 필드가 **구조적으로 없다** — 벌크는 기존 단일 통로를 그대로 부를 뿐 통제를 부여·우회하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.core.text import invisible_char_problem
from app.modules.order_board.constants import (
    ACTION_KINDS,
    BULK_SCHEMA_HARD_LIMIT,
    QUERY_MAX,
    SAVED_FILTER_NAME_MAX,
    BoardStage,
    BulkAction,
    BulkOutcome,
    CardKind,
)

# ── 필터(쿼리·저장 필터 공용) ────────────────────────────────────────────────

#: 접수일 필터의 허용 범위 — 밖이면 422(KST 경계 계산의 `date + 1일` 오버플로·의미 없는 연도를 입력 경계에서 막는다).
FILTER_DATE_MIN = date(2000, 1, 1)
FILTER_DATE_MAX = date(2999, 12, 31)


class BoardFilter(BaseModel):
    """보드 공통 필터 — 쿼리 파라미터 모델이자 저장 필터 `filter_config`의 스키마. 모르는 키는 422다.

    `q`는 바이어명(거래처 마스터 국문·영문, 수주는 헤더 바이어 표기도)·바이어 PO번호·수주번호 부분 일치(LIKE 와일드카드 이스케이프), `IN-{숫자}`면 그 인테이크 id.
    보이지 않는 글자(제어·서식·채움, NUL 포함)는 422. `created_from`·`created_to`는 **KST 날짜**(양끝 포함, 2000-01-01~2999-12-31)다.
    """

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=QUERY_MAX)
    buyer_partner_id: int | None = Field(default=None, ge=1)
    assignee_id: int | None = Field(default=None, ge=1)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    dest_market_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    created_from: date | None = Field(default=None, ge=FILTER_DATE_MIN, le=FILTER_DATE_MAX)
    created_to: date | None = Field(default=None, ge=FILTER_DATE_MIN, le=FILTER_DATE_MAX)

    @field_validator("q")
    @classmethod
    def _clean_query(cls, value: str | None) -> str | None:
        """제어·서식·채움 문자(NUL 포함)는 422 — DB·JSONB에 닿아 500이 되거나 보이지 않는 글자로 검색을 속이는 경로를 막는다. 빈 값은 None."""
        if value is None:
            return None
        problem = invisible_char_problem(value, label="검색어")
        if problem is not None:
            raise ValueError(problem)
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _date_range(self) -> Self:
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("접수일 시작은 끝보다 늦을 수 없습니다.")
        return self


class BoardItemsQuery(BoardFilter):
    """`GET /order-board/items` — 한 열의 '더 보기'(Page 봉투). 열·페이지는 필터와 같은 쿼리 모델에 둔다(모르는 키 422 유지)."""

    stage: BoardStage
    page: int = Field(default=1, ge=1)
    size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class BoardExportQuery(BoardFilter):
    """`GET /order-board/export.csv` — 같은 필터, `stage`를 주면 그 열만(없으면 4열 전부)."""

    stage: BoardStage | None = None


# ── 보드 응답 ─────────────────────────────────────────────────────────────────


class BoardCard(BaseModel):
    """보드 카드 — 판매 측 식별·합계·담당만. **원가·마진·매입가·여신·게이트 필드는 없다**(필드 부재 — 역할 분기 없음)."""

    kind: CardKind
    id: int
    #: 수주는 수주번호, 인테이크는 `IN-{id}`.
    ref_label: str
    buyer_partner_id: int
    buyer_name: str | None
    buyer_po_no: str | None
    line_count: int
    #: 판매 합계(통화 최소단위 정수) — 마스킹 비대상.
    total_amount: int
    total_text: str
    currency: str
    #: 접수(생성)일로부터 지난 날수 — KST 날짜 기준 계산값(저장 열 없음).
    age_days: int
    assignee_id: int
    assignee_name: str | None
    updated_at: datetime
    version: int


class BoardColumn(BaseModel):
    stage: BoardStage
    label_ko: str
    #: 필터 적용 후 이 열의 전체 건수.
    total: int
    #: `items`가 `total`보다 적다(나머지는 `GET /order-board/items?stage=`로).
    has_more: bool
    items: list[BoardCard]


class OrderBoardOut(BaseModel):
    """`GET /order-board` — **비-Page 단일 객체**(의도된 예외 — 고정 4열·열당 상한). 최상위 배열이 아니다."""

    columns: list[BoardColumn]
    generated_at: datetime


# ── 벌크 ─────────────────────────────────────────────────────────────────────


class BulkTargetIn(BaseModel):
    """벌크 대상 1건 — 화면이 본 카드의 version을 함께 보낸다(낙관 잠금 — 없으면 422)."""

    model_config = ConfigDict(extra="forbid")

    kind: CardKind
    id: StrictInt = Field(ge=1)
    expected_version: StrictInt = Field(ge=1)


class OrderBoardBulkRequest(BaseModel):
    """`POST /order-board/bulk` — 액션 3종·대상(서비스 상한 50, 초과 422 `ORDER_BOARD.BULK.TOO_MANY`)·담당자(ASSIGN 전용).

    override·승인·사유·force 필드는 없다(보류·거부·취소·override·승인 요청은 벌크 제외).
    """

    model_config = ConfigDict(extra="forbid")

    action: BulkAction
    targets: list[BulkTargetIn] = Field(min_length=1, max_length=BULK_SCHEMA_HARD_LIMIT)
    assignee_id: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _action_shape(self) -> Self:
        if self.action is BulkAction.ASSIGN and self.assignee_id is None:
            raise ValueError("담당자 지정에는 assignee_id가 필요합니다.")
        if self.action is not BulkAction.ASSIGN and self.assignee_id is not None:
            raise ValueError("assignee_id는 담당자 지정(ASSIGN)에만 보낼 수 있습니다.")
        allowed = ACTION_KINDS[self.action]
        if any(target.kind not in allowed for target in self.targets):
            raise ValueError(
                f"{self.action.value}의 대상 종류는 {', '.join(sorted(k.value for k in allowed))}만 가능합니다."
            )
        return self


class BulkBlockedGateOut(BaseModel):
    """확정 게이트 미해소 항목 — 단일 확정 통로가 돌려준 서버 값 그대로(원가·여신 수치·basis는 싣지 않는다)."""

    gate_code: str
    line_id: int | None
    line_no: int | None
    level: str
    resolution: str
    reason_code: str
    message_ko: str


class BulkItemResultOut(BaseModel):
    kind: CardKind
    id: int
    outcome: BulkOutcome
    #: 거부·경합 코드(에러 카탈로그 3세그먼트). OK·SKIPPED는 null.
    code: str | None
    message_ko: str
    blocked_gates: list[BulkBlockedGateOut]
    #: 처리 뒤 대상의 version(OK·SKIPPED) — 카드 갱신용. 거부면 null.
    version: int | None
    #: 인테이크 확정으로 생긴 수주(CONFIRM_INTAKE OK)·확정한 수주(CONFIRM_SO OK).
    sales_order_id: int | None
    doc_number: str | None


class OrderBoardBulkOut(BaseModel):
    """벌크 결과 리포트 — 부분 성공이 정상(200). 합계는 **모든 건의 커밋·롤백이 끝난 뒤** 결과 목록에서 산출한다."""

    action: BulkAction
    results: list[BulkItemResultOut]
    total: int
    ok_count: int
    skipped_count: int
    #: OK·SKIPPED가 아닌 건(BLOCKED·CONFLICT·FORBIDDEN·FAILED).
    fail_count: int
    outcome_counts: dict[str, int]


# ── 저장 필터 ─────────────────────────────────────────────────────────────────


class SavedFilterCreateRequest(BaseModel):
    """저장 필터 등록 — 본인 것으로만 만들어진다(소유자 필드 없음)."""

    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=SAVED_FILTER_NAME_MAX)
    filter_config: BoardFilter


class SavedFilterUpdateRequest(BaseModel):
    """저장 필터 수정 — 보낸 필드만 바뀐다. `version` 필수(낙관 잠금)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    name: StrictStr | None = Field(default=None, min_length=1, max_length=SAVED_FILTER_NAME_MAX)
    filter_config: BoardFilter | None = None


class SavedFilterOut(BaseModel):
    id: int
    name: str
    #: 지금의 `BoardFilter`로 다시 검증한 값 — 검증에 실패하면(스키마 변경으로 낡음) null이고 `needs_resave=true`.
    filter_config: BoardFilter | None
    needs_resave: bool
    version: int
    created_at: datetime
    updated_at: datetime
