"""수주 확정·승인 요청 API 스키마 (S3-1 PR-12a / design-integrated §2.10·X-34 / design-E E5).

쓰기 요청은 `extra=forbid`이고 **`force`·`skip`·`bypass`·`override`·상태·확정 시각·판정 값 필드가 구조적으로 없다**(우회 표면 제거 — 아키텍처 스캔이 필드 집합을 고정한다).
확정 응답은 단일 `SalesOrderConfirmOut`(200만 성공 — 거부는 전부 4xx 예외)이다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.modules.approvals.schemas import ApprovalView
from app.modules.gates.schemas import GateResultOut
from app.modules.sales_orders.schemas import SalesOrderDetail


class SalesOrderConfirmRequest(BaseModel):
    """수주 확정 — 사용자가 본 SO 버전만 받는다(불일치 409). 게이트 해소 수단(승인·override)은 별도 사람 동작이라 여기에 실을 수 없다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class SalesOrderApprovalRequest(BaseModel):
    """여신 초과 승인 요청 — 사용자가 본 SO 버전(불일치 409). 요청은 확정 시도의 부작용이 아니라 이 명시 동작뿐이다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class AllocationOut(BaseModel):
    """재고 할당 포트의 결과 — P3 기본은 `NOT_IMPLEMENTED`(할당이 된 것처럼 보이지 않게 그대로 노출)."""

    status: str
    note: str


class SalesOrderConfirmOut(BaseModel):
    """확정 성공 응답 — 확정된 SO·확정 시점 게이트 결과 전건·할당 결과·증거 스냅샷 id."""

    sales_order: SalesOrderDetail
    gates: list[GateResultOut]
    allocation: AllocationOut
    #: `gate_evaluations`의 CONFIRMED 행 id(SO당 1행) — 확정 시점 값 재현의 원천.
    evaluation_id: int


class SalesOrderApprovalRequestOut(ApprovalView):
    """승인 요청 응답 — 승인 뷰 + `created`(False = 같은 스냅샷의 기존 활성 승인을 그대로 돌려줌, 알림·이벤트 재발생 없음)."""

    created: bool
