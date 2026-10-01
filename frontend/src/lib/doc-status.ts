// 전표 상태 라벨·배지·버튼 노출 조건 (S3-1 design-integrated G-10 / 판정 표).
//
// ★ 화면이 버튼을 감추는 것은 편의일 뿐이다 — 전이 가능 여부의 정본은 서버 machine이고, 서버가 409/403으로 다시 막는다.
//   여기 조건은 서버 QT 전이표(DRAFT→ISSUED·CANCELLED, ISSUED→CANCELLED, 개정은 ISSUED 한정)를 그대로 옮긴 것이다.
//   SO·PO는 각 전표 PR이 자기 표를 추가한다(PI는 PR-6b).

const QUOTATION_STATUS: Record<string, string> = {
  DRAFT: "초안",
  ISSUED: "발행",
  CONVERTED: "수주전환",
  EXPIRED: "만료",
  CANCELLED: "취소",
};

/** 모르는 코드는 감추지 않고 그대로 보여 준다. */
export const quotationStatusLabel = (code: string): string => QUOTATION_STATUS[code] ?? code;

/** 색만으로 구분하지 않도록 배지에는 늘 글자를 함께 쓴다. */
export function statusBadgeClass(code: string): string {
  switch (code) {
    case "ISSUED":
    case "CONVERTED":
    case "PARTIALLY_PAID":
    case "PAID":
    case "CONFIRMED":
    case "SUPPLIER_CONFIRMED":
      return "border-gray-900 bg-gray-900 text-white";
    case "CANCELLED":
    case "EXPIRED":
      return "border-gray-300 bg-gray-100 text-gray-500 line-through";
    case "ON_HOLD":
      return "border-gray-500 bg-gray-200 text-gray-800";
    default:
      return "border-gray-300 bg-white text-gray-700";
  }
}

export const canEditQuotation = (status: string): boolean => status === "DRAFT";
export const canIssueQuotation = (status: string): boolean => status === "DRAFT";
export const canCancelQuotation = (status: string): boolean =>
  status === "DRAFT" || status === "ISSUED";
export const canReviseQuotation = (status: string): boolean => status === "ISSUED";

// ── PI(선수금 청구서) — 서버 machine: 사람 엣지는 ISSUED→CANCELLED 하나뿐(입금 수렴·만료는 자동) ──

const PROFORMA_STATUS: Record<string, string> = {
  ISSUED: "발행",
  PARTIALLY_PAID: "일부입금",
  PAID: "입금완료",
  EXPIRED: "만료",
  CANCELLED: "취소",
};

export const proformaStatusLabel = (code: string): string => PROFORMA_STATUS[code] ?? code;

/** PI는 생성=발행=동결이라 편집 가능 상태가 없다. 취소는 미입금 발행 상태만(입금이 붙으면 서버가 409). */
export const canCancelProforma = (status: string): boolean => status === "ISSUED";

/** PI를 만들 수 있는 원천 QT 상태(서버 원천 자격: ISSUED·CONVERTED — 유효기간은 서버가 직접 검사). */
export const canCreateProformaFrom = (qtStatus: string): boolean =>
  qtStatus === "ISSUED" || qtStatus === "CONVERTED";

export const PAYMENT_TYPE_LABEL: Record<string, string> = {
  TT_ADVANCE: "선수금 T/T",
  TT_DEFERRED: "후불 T/T",
  LC: "L/C",
};

export const BALANCE_ANCHOR_LABEL: Record<string, string> = {
  ORDER_DATE: "주문일",
  INVOICE_DATE: "송장일",
  ETD_DATE: "선적예정일",
  BL_DATE: "B/L일",
  ARRIVAL_DATE: "도착일",
};

export const INCOTERM_CODES = [
  "EXW",
  "FCA",
  "FAS",
  "FOB",
  "CFR",
  "CIF",
  "CPT",
  "CIP",
  "DAP",
  "DPU",
  "DDP",
  "DAT",
] as const;

export const PRICE_BASIS_LABEL: Record<string, string> = {
  MASTER: "마스터",
  MANUAL: "수동",
  BUYER_PO: "바이어 PO",
};

// ── SO(수주) — 서버 machine: 사람 엣지는 보류·재개·취소(확정은 PR-12 동결 액션 전용) ──

const SALES_ORDER_STATUS: Record<string, string> = {
  RECEIVED: "접수",
  CONFIRMED: "확정",
  ON_HOLD: "보류",
  CANCELLED: "취소",
  PARTIALLY_ALLOCATED: "부분할당",
  ALLOCATED: "할당완료",
  IN_SHIPMENT: "선적중",
  COMPLETED: "완료",
};

export const salesOrderStatusLabel = (code: string): string => SALES_ORDER_STATUS[code] ?? code;

/** 접수(RECEIVED)만 CONTENT(헤더 조건·라인) 편집 — 보류·확정 이후·취소는 읽기 전용(보류 중 편집은 재개 뒤). */
export const canEditSalesOrder = (status: string): boolean => status === "RECEIVED";
/** 보류는 접수·확정에서만 들어간다. */
export const canHoldSalesOrder = (status: string): boolean =>
  status === "RECEIVED" || status === "CONFIRMED";
export const canResumeSalesOrder = (status: string): boolean => status === "ON_HOLD";
export const canCancelSalesOrder = (status: string): boolean =>
  status === "RECEIVED" || status === "CONFIRMED" || status === "ON_HOLD";

/** SO를 만들 수 있는 원천 QT 상태(서버 원천 자격: ISSUED·CONVERTED — 유효기간은 서버가 직접 검사). */
export const canCreateSalesOrderFromQuotation = (qtStatus: string): boolean =>
  qtStatus === "ISSUED" || qtStatus === "CONVERTED";
/** SO를 만들 수 있는 원천 PI 상태(미입금 발행·일부입금·입금완료 — 취소·만료는 불가). */
export const canCreateSalesOrderFromProforma = (piStatus: string): boolean =>
  piStatus === "ISSUED" || piStatus === "PARTIALLY_PAID" || piStatus === "PAID";

// ── PO(구매 발주) — 서버 machine: 생성=발행(초안 없음), 사람 엣지는 공급사 확인(OC)·취소뿐(입고 후반 3값은 S4-1) ──

const PURCHASE_ORDER_STATUS: Record<string, string> = {
  ISSUED: "발행",
  SUPPLIER_CONFIRMED: "공급사 확인",
  PARTIALLY_RECEIVED: "부분입고",
  FULLY_RECEIVED: "입고완료",
  CLOSED: "종결",
  CANCELLED: "취소",
};

export const purchaseOrderStatusLabel = (code: string): string => PURCHASE_ORDER_STATUS[code] ?? code;

/** 공급사 확인(OC)은 발행 상태에서만(서버 엣지 ISSUED→SUPPLIER_CONFIRMED). */
export const canConfirmPurchaseOrder = (status: string): boolean => status === "ISSUED";
/** 취소는 발행·공급사 확인에서만(서버 엣지 ISSUED·SUPPLIER_CONFIRMED→CANCELLED). */
export const canCancelPurchaseOrder = (status: string): boolean =>
  status === "ISSUED" || status === "SUPPLIER_CONFIRMED";
/** 내부 메모는 취소 뒤에도 고칠 수 있다 — 원가를 잘못 적은 채 취소한 경우의 사후 정정 경로(ADR-0057 수용된 사실). */
export const canEditPurchaseOrderNote = (_status: string): boolean => true;
/** 담당자는 취소된 발주에서 닫는다(죽은 전표의 담당 이관은 의미가 없다). */
export const canEditPurchaseOrderAssignee = (status: string): boolean => status !== "CANCELLED";
/** OC 일자·참조는 공급사 확인 상태에서만 고칠 수 있다(서버 422와 같은 규칙). */
export const canEditPurchaseOrderOc = (status: string): boolean => status === "SUPPLIER_CONFIRMED";

export const PO_KIND_LABEL: Record<string, string> = {
  PURCHASE: "구매 발주",
  OEM_PRODUCTION: "OEM 생산 발주",
};

/** PO 전용 잔금 기산점(입고 확정일)까지 포함 — 판매 전표의 선택지(BALANCE_ANCHOR_LABEL)에는 섞지 않는다. */
export const PO_BALANCE_ANCHOR_LABEL: Record<string, string> = {
  ...BALANCE_ANCHOR_LABEL,
  RECEIPT_DATE: "입고 확정일",
};

/** 문서 흐름 노드의 종류별 상태 라벨. */
export function docStatusLabel(kind: string, status: string): string {
  if (kind === "QUOTATION") return quotationStatusLabel(status);
  if (kind === "PROFORMA_INVOICE") return proformaStatusLabel(status);
  if (kind === "SALES_ORDER") return salesOrderStatusLabel(status);
  if (kind === "PURCHASE_ORDER") return purchaseOrderStatusLabel(status);
  return status;
}
