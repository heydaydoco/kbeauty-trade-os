"""전표 전이·참조 생성 엔드포인트 — QT 발행·취소·개정·PI/SO 참조 생성·PI 취소·SO 보류·재개·취소·문서 흐름 (S3-1 design-B B8).

전부 **사람 1클릭 + `Idempotency-Key` 필수**이고 무역(관리자 상시 통과)이 한다. 동결 액션(`issue`)은 범용
`/transitions`로 못 넘는다(스키마 `to` Literal에서 동결 엣지·자동 엣지·RESERVED를 구조적으로 제외 — 우회 표면 제거).
이 라우터가 `issue_quotation`·`confirm_sales_order`·`confirm_intake`·`request_credit_approval`의 **유일한 호출처**다(자동 확정·자동 승인 요청 경로 부재 — test_no_auto_confirm_code_path_exists).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.modules.gates.schemas import GateOverrideOut, GateOverrideRequest, GateReportOut
from app.modules.identity.models import RoleCode
from app.modules.order_intake.schemas import (
    IntakeConfirmOut,
    IntakeGateReportOut,
    IntakeVersionRequest,
)
from app.modules.payments.schemas import (
    PaymentReceiptRequest,
    PaymentReversalRequest,
    PaymentWriteOut,
)
from app.modules.proforma_invoices.schemas import (
    ProformaInvoiceCreateRequest,
    ProformaInvoiceDetail,
    ProformaInvoicePreview,
)
from app.modules.purchase_orders.router import detail_response as purchase_order_response
from app.modules.purchase_orders.schemas import PurchaseOrderCostHiddenDetail, PurchaseOrderDetail
from app.modules.quotations.schemas import QuotationDetail
from app.modules.sales_orders.schemas import SalesOrderDetail, SalesOrderReferenceRequest
from app.modules.trade_chain import (
    approval_requests,
    confirm,
    document_flow,
    gate_flow,
    intake_flow,
    lifecycle,
    payment_flow,
    reference,
    so_reference,
)
from app.modules.trade_chain.schemas import (
    SalesOrderApprovalRequest,
    SalesOrderApprovalRequestOut,
    SalesOrderConfirmOut,
    SalesOrderConfirmRequest,
)
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import public_transition_targets

CAN_WRITE = (RoleCode.TRADE,)

#: 범용 전이의 `to` — machine에서 파생한 값이 Literal과 같은지 아키텍처 테스트가 대사한다.
QuotationTarget = Literal["CANCELLED"]
assert set(QuotationTarget.__args__) == public_transition_targets(DocKind.QUOTATION)  # type: ignore[attr-defined]


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class QuotationTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: QuotationTarget
    version: StrictInt = Field(ge=1)
    #: 취소는 사유 필수(1~500자, 공백 불가).
    reason: StrictStr | None = Field(default=None, max_length=500)


#: PI 범용 전이의 `to` — 사람 엣지는 취소뿐이다(입금 수렴·만료는 자동 엣지).
ProformaInvoiceTarget = Literal["CANCELLED"]
assert set(ProformaInvoiceTarget.__args__) == public_transition_targets(DocKind.PROFORMA_INVOICE)  # type: ignore[attr-defined]


class ProformaInvoiceTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: ProformaInvoiceTarget
    version: StrictInt = Field(ge=1)
    #: 취소는 사유 필수(1~500자, 공백 불가).
    reason: StrictStr | None = Field(default=None, max_length=500)


#: SO 범용 전이의 `to` — 보류·재개(RECEIVED·CONFIRMED)·취소. 확정(RECEIVED→CONFIRMED)은 동결 액션 엣지라 값이 같아도 record_transition이 거부한다.
SalesOrderTarget = Literal["ON_HOLD", "RECEIVED", "CONFIRMED", "CANCELLED"]
assert set(SalesOrderTarget.__args__) == public_transition_targets(DocKind.SALES_ORDER)  # type: ignore[attr-defined]


class SalesOrderTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: SalesOrderTarget
    version: StrictInt = Field(ge=1)
    #: 보류·취소는 사유 필수(1~500자, 공백 불가). 재개는 사유 선택.
    reason: StrictStr | None = Field(default=None, max_length=500)


#: PO 범용 전이의 `to` — 공급사 확인(OC)·취소. 자동 엣지·RESERVED 후반 상태는 공개 대상이 아니다(machine에서 파생한 값과 같은지 아래 assert가 대사한다).
PurchaseOrderTarget = Literal["SUPPLIER_CONFIRMED", "CANCELLED"]
assert set(PurchaseOrderTarget.__args__) == public_transition_targets(DocKind.PURCHASE_ORDER)  # type: ignore[attr-defined]


class PurchaseOrderTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: PurchaseOrderTarget
    version: StrictInt = Field(ge=1)
    #: 취소는 사유 필수(1~500자, 공백 불가). 공급사 확인은 사유 선택.
    reason: StrictStr | None = Field(default=None, max_length=500)
    #: 공급사 확인(OC)의 부속 — `oc_received_on` 필수(발행일≤OC≤오늘), `oc_reference`는 선택. 취소에는 싣지 않는다.
    oc_received_on: date | None = None
    oc_reference: StrictStr | None = Field(default=None, max_length=100)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


router = APIRouter(prefix="/quotations", tags=["trade-chain"])


@router.post(
    "/{qt_id}/issue",
    summary="견적 발행 (동결 액션 — 개정본이면 같은 트랜잭션에서 원본 취소)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def issue_quotation(
    qt_id: int, payload: IssueRequest, current: CurrentUser, key: IdempotencyKey, response: Response
) -> QuotationDetail:
    status_code, body = lifecycle.issue_quotation(
        actor=current, idempotency_key=key, qt_id=qt_id, version=payload.version
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


@router.post(
    "/{qt_id}/transitions",
    summary="견적 취소 (사유 필수 — 후속 생존 시 409, 발행은 /issue 전용)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_quotation(
    qt_id: int,
    payload: QuotationTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> QuotationDetail:
    status_code, body = lifecycle.transition_quotation(
        actor=current,
        idempotency_key=key,
        qt_id=qt_id,
        to=payload.to,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


@router.post(
    "/{qt_id}/revisions",
    summary="견적 개정 초안 작성 (발행 상태 원본 한정 — 초안 발행 시 원본이 취소된다)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_revision(
    qt_id: int,
    payload: RevisionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> QuotationDetail:
    status_code, body = lifecycle.create_revision(
        actor=current, idempotency_key=key, qt_id=qt_id, version=payload.version
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


# ── 참조 생성: QT → PI ──────────────────────────────────────────────────────


@router.post(
    "/{qt_id}/proforma-invoices/preview",
    summary="PI 미리보기 (비저장 — 채번·이벤트·멱등 키 소비 없음, 생성과 같은 검증)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def preview_proforma_invoice(
    qt_id: int, payload: ProformaInvoiceCreateRequest, current: CurrentUser
) -> ProformaInvoicePreview:
    body = reference.preview_proforma_invoice(
        actor=current, qt_id=qt_id, payload=payload.model_dump(exclude_unset=True)
    )
    return ProformaInvoicePreview.model_validate(body)


@router.post(
    "/{qt_id}/proforma-invoices",
    summary="PI 생성 (QT 참조 생성 — 생성=발행=동결, 값은 원천에서 복사·잔량 초과 409)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_proforma_invoice(
    qt_id: int,
    payload: ProformaInvoiceCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProformaInvoiceDetail:
    status_code, body = reference.create_proforma_invoice(
        actor=current,
        idempotency_key=key,
        qt_id=qt_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return ProformaInvoiceDetail.model_validate(body)


# ── PI 전이(취소) ───────────────────────────────────────────────────────────

pi_router = APIRouter(prefix="/proforma-invoices", tags=["trade-chain"])


@pi_router.post(
    "/{pi_id}/transitions",
    summary="PI 취소 (사유 필수 — 입금이 붙은 PI·후속 생존 시 409, 입금 수렴·만료는 자동)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_proforma_invoice(
    pi_id: int,
    payload: ProformaInvoiceTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProformaInvoiceDetail:
    status_code, body = lifecycle.transition_proforma_invoice(
        actor=current,
        idempotency_key=key,
        pi_id=pi_id,
        to=payload.to,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return ProformaInvoiceDetail.model_validate(body)


# ── 입금 기록·역기록 (PI 선수금 — payments 원장 + PI 상태 자동 수렴, 한 트랜잭션) ─────────────

payments_router = APIRouter(prefix="/payments", tags=["payments"])


@pi_router.post(
    "/{pi_id}/payments",
    summary="PI 선수금 입금 기록 (선수금 T/T 전용·통화 일치·선수금 청구액 초과 422 — PI 상태 자동 수렴)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def record_pi_payment(
    pi_id: Annotated[int, Path(ge=1)],
    payload: PaymentReceiptRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PaymentWriteOut:
    status_code, body = payment_flow.record_receipt(
        actor=current,
        idempotency_key=key,
        pi_id=pi_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return PaymentWriteOut.model_validate(body)


@payments_router.post(
    "/{payment_id}/reversal",
    summary="입금 역기록 (전액·사유 필수 — 반대 부호 신규 행, 확정 SO가 있으면 경고만 싣고 취소하지 않는다)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def reverse_payment(
    payment_id: Annotated[int, Path(ge=1)],
    payload: PaymentReversalRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PaymentWriteOut:
    status_code, body = payment_flow.reverse_payment(
        actor=current, idempotency_key=key, payment_id=payment_id, reason=payload.reason
    )
    response.status_code = status_code
    return PaymentWriteOut.model_validate(body)


# ── 참조 생성: QT → SO (직접 경로), PI → SO (활성 1:1) ─────────────────────────────


@router.post(
    "/{qt_id}/sales-orders",
    summary="수주 생성 (QT 참조 생성 — 접수 상태로 시작, 값은 원천에서 복사·잔량 초과 409)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_sales_order_from_quotation(
    qt_id: int,
    payload: SalesOrderReferenceRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SalesOrderDetail:
    status_code, body = so_reference.create_sales_order_from_quotation(
        actor=current,
        idempotency_key=key,
        qt_id=qt_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return SalesOrderDetail.model_validate(body)


@pi_router.post(
    "/{pi_id}/sales-orders",
    summary="수주 생성 (PI 참조 생성 — PI당 활성 수주 1건, 접수 상태로 시작)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_sales_order_from_proforma_invoice(
    pi_id: int,
    payload: SalesOrderReferenceRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SalesOrderDetail:
    status_code, body = so_reference.create_sales_order_from_proforma_invoice(
        actor=current,
        idempotency_key=key,
        pi_id=pi_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return SalesOrderDetail.model_validate(body)


# ── SO 전이(보류·재개·취소) ─────────────────────────────────────────────────

so_router = APIRouter(prefix="/sales-orders", tags=["trade-chain"])


@so_router.post(
    "/{so_id}/transitions",
    summary="수주 보류·재개·취소 (사유 필수는 보류·취소 — 확정은 별도 액션, 후속 생존 시 취소 409)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_sales_order(
    so_id: int,
    payload: SalesOrderTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SalesOrderDetail:
    status_code, body = lifecycle.transition_sales_order(
        actor=current,
        idempotency_key=key,
        so_id=so_id,
        to=payload.to,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return SalesOrderDetail.model_validate(body)


# ── 수주 확정·여신 초과 승인 요청 (S3-1 PR-12a) ──────────────────────────────────────────


@so_router.post(
    "/{so_id}/confirm",
    summary="수주 확정 (사람 1클릭 — 게이트 7종 잠금 하 재평가·승인 소비·동결을 한 트랜잭션에서. 미해소 게이트는 409 GATE_BLOCKED, 관리자도 우회 불가)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def confirm_sales_order(
    so_id: Annotated[int, Path(ge=1)],
    payload: SalesOrderConfirmRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SalesOrderConfirmOut:
    status_code, body = confirm.confirm_sales_order(
        actor=current, idempotency_key=key, so_id=so_id, version=payload.version
    )
    response.status_code = status_code
    return SalesOrderConfirmOut.model_validate(body)


@so_router.post(
    "/{so_id}/approval-requests",
    summary="여신 초과 승인 요청 (사람의 명시 동작 — 초과분 0·평가 불능이면 422, 같은 입력의 활성 승인이 있으면 그대로 반환)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def request_sales_order_credit_approval(
    so_id: Annotated[int, Path(ge=1)],
    payload: SalesOrderApprovalRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SalesOrderApprovalRequestOut:
    status_code, body = approval_requests.request_credit_approval(
        actor=current, idempotency_key=key, so_id=so_id, version=payload.version
    )
    response.status_code = status_code
    return SalesOrderApprovalRequestOut.model_validate(body)


# ── 게이트 판정·override (S3-1 PR-11a — 확정 통로 배선은 위 confirm) ─────────────────────────────


@so_router.get(
    "/{so_id}/gates",
    summary="수주 게이트 판정 7종 + 확정 가능 여부 (참고값 — 잠금·저장 없음, 여신 포함, 전 역할 열람·여신 수치는 무역·관리자만)",
)
def get_sales_order_gates(so_id: Annotated[int, Path(ge=1)], current: CurrentUser) -> GateReportOut:
    return GateReportOut.model_validate(gate_flow.get_gates(so_id=so_id, roles=current.roles))


@so_router.post(
    "/{so_id}/gate-overrides",
    summary="게이트 예외 통과(override) 부여 (사유 5~500자·판정 해시 결속·역할은 서비스가 판정 — 가격·MOQ=무역·관리자, 준비도·PI=관리자, 품번·중복 PO·여신은 불가)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def grant_sales_order_gate_override(
    so_id: Annotated[int, Path(ge=1)],
    payload: GateOverrideRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> GateOverrideOut:
    status_code, body = gate_flow.grant_gate_override(
        actor=current,
        idempotency_key=key,
        so_id=so_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return GateOverrideOut.model_validate(body)


@so_router.post(
    "/{so_id}/gate-overrides/revoke",
    summary="게이트 예외 통과(override) 철회 (부여자 본인 또는 관리자 — REVOKE 행 추가, 삭제 없음)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def revoke_sales_order_gate_override(
    so_id: Annotated[int, Path(ge=1)],
    payload: GateOverrideRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> GateOverrideOut:
    status_code, body = gate_flow.revoke_gate_override(
        actor=current,
        idempotency_key=key,
        so_id=so_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return GateOverrideOut.model_validate(body)


# ── 오더 인테이크 확정·게이트 (S3-1 PR-13a) ─────────────────────────────────────────────

intake_router = APIRouter(prefix="/order-intakes", tags=["trade-chain"])


@intake_router.post(
    "/{intake_id}/confirm",
    summary="오더 인테이크 접수 확정 (사람 1클릭 — SO(접수) 생성·인테이크 CONFIRMED를 한 트랜잭션에서. 품번 미매핑 422·매핑 변경 409·중복 바이어 PO 409, 관리자도 우회 불가)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def confirm_order_intake(
    intake_id: Annotated[int, Path(ge=1)],
    payload: IntakeVersionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> IntakeConfirmOut:
    status_code, body = intake_flow.confirm_intake(
        actor=current, idempotency_key=key, intake_id=intake_id, version=payload.version
    )
    response.status_code = status_code
    return IntakeConfirmOut.model_validate(body)


@intake_router.get(
    "/{intake_id}/gates",
    summary="오더 인테이크 게이트 평가 (검토 시점 정보 — 잠금·저장 없음. 접수 확정을 막는 것은 품번 매핑·중복 PO뿐, 전 역할 열람)",
)
def get_order_intake_gates(
    intake_id: Annotated[int, Path(ge=1)], current: CurrentUser
) -> IntakeGateReportOut:
    return IntakeGateReportOut.model_validate(
        intake_flow.get_intake_gates(intake_id=intake_id, roles=current.roles)
    )


# ── PO 전이(공급사 확인·취소) ──────────────────────────────────────────────────

po_router = APIRouter(prefix="/purchase-orders", tags=["trade-chain"])


@po_router.post(
    "/{po_id}/transitions",
    summary="PO 공급사 확인(OC 일자 필수)·취소 (사유 필수는 취소 — 사람 1클릭, 자동 전이 없음)",
    # ★ 응답 스키마 갈림은 PO 라우터 경계 1곳(detail_response)에서 한다 — 여기도 response_model=None.
    response_model=None,
    responses={200: {"model": PurchaseOrderDetail}},
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_purchase_order(
    po_id: int,
    payload: PurchaseOrderTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PurchaseOrderDetail | PurchaseOrderCostHiddenDetail:
    status_code, body = lifecycle.transition_purchase_order(
        actor=current,
        idempotency_key=key,
        po_id=po_id,
        to=payload.to,
        version=payload.version,
        reason=payload.reason,
        oc_received_on=payload.oc_received_on,
        oc_reference=payload.oc_reference,
    )
    response.status_code = status_code
    return purchase_order_response(current, body)


# ── 문서 흐름 ───────────────────────────────────────────────────────────────

flow_router = APIRouter(prefix="/document-flow", tags=["trade-chain"])

FlowKind = Literal["QUOTATION", "PROFORMA_INVOICE", "SALES_ORDER"]


class FlowNode(BaseModel):
    kind: str
    id: int
    doc_number: str
    status: str
    doc_date: date
    currency: str
    total_amount: int
    total_text: str | None
    parent_kind: str | None
    parent_id: int | None
    is_current: bool


class DocumentFlowOut(BaseModel):
    """`nodes`는 위→아래(QT, PI…, PI의 SO…, QT 직접 SO…) 순서다. 직접(인테이크) 수주는 노드 1개다."""

    root_kind: str
    root_id: int
    nodes: list[FlowNode]


@flow_router.get(
    "/{doc_kind}/{doc_id}", summary="문서 흐름 (QT→PI→SO 사슬 전체 — 취소·만료 전표 포함)"
)
def get_document_flow(
    doc_kind: FlowKind, doc_id: Annotated[int, Path(ge=1)], current: CurrentUser
) -> DocumentFlowOut:
    return DocumentFlowOut.model_validate(document_flow.document_flow(DocKind(doc_kind), doc_id))
