// 전표 상태 라벨·배지·버튼 노출 조건 (S3-1 design-integrated G-10 / 판정 표).
//
// ★ 화면이 버튼을 감추는 것은 편의일 뿐이다 — 전이 가능 여부의 정본은 서버 machine이고, 서버가 409/403으로 다시 막는다.
//   여기 조건은 서버 QT 전이표(DRAFT→ISSUED·CANCELLED, ISSUED→CANCELLED, 개정은 ISSUED 한정)를 그대로 옮긴 것이다.
//   PI·SO·PO는 각 전표 PR이 자기 표를 추가한다.

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
      return "border-gray-900 bg-gray-900 text-white";
    case "CANCELLED":
    case "EXPIRED":
      return "border-gray-300 bg-gray-100 text-gray-500 line-through";
    default:
      return "border-gray-300 bg-white text-gray-700";
  }
}

export const canEditQuotation = (status: string): boolean => status === "DRAFT";
export const canIssueQuotation = (status: string): boolean => status === "DRAFT";
export const canCancelQuotation = (status: string): boolean =>
  status === "DRAFT" || status === "ISSUED";
export const canReviseQuotation = (status: string): boolean => status === "ISSUED";

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
