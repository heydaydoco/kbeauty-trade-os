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
