// 입금 원장 테스트 픽스처 (S3-1 PR-10b) — 서버 응답 모양 그대로(금액 문자열은 서버가 준 값으로 고정).

import type { PaymentPageData, PaymentRow, PaymentSummary, PaymentWriteResult } from "../lib/payment";

export const PAYMENT_SUMMARY: PaymentSummary = {
  pi_id: 5,
  pi_status: "ISSUED",
  currency: "USD",
  payment_type: "TT_ADVANCE",
  due_amount: 2250,
  due_text: "22.50",
  net_received_amount: 0,
  net_received_text: "0.00",
  remaining_amount: 2250,
  remaining_text: "22.50",
};

export function summary(over: Partial<PaymentSummary> = {}): PaymentSummary {
  return { ...PAYMENT_SUMMARY, ...over };
}

export function paymentRow(over: Partial<PaymentRow> = {}): PaymentRow {
  return {
    id: 11,
    pi_id: 5,
    partner_id: 3,
    kind: "RECEIPT",
    received_amount: 1000,
    received_amount_text: "10.00",
    received_currency: "USD",
    received_on: "2026-09-30",
    reference: "SHB-REF-001",
    reverses_payment_id: null,
    reversed_by_payment_id: null,
    reason: null,
    recorded_by_id: 1,
    created_at: "2026-09-30T03:00:00Z",
    ...over,
  };
}

export function paymentPage(
  items: PaymentRow[],
  sum: PaymentSummary = PAYMENT_SUMMARY,
  over: Partial<PaymentPageData> = {},
): PaymentPageData {
  return { items, total: items.length, page: 1, size: 50, summary: sum, ...over };
}

export function writeResult(over: Partial<PaymentWriteResult> = {}): PaymentWriteResult {
  return { payment: paymentRow(), summary: PAYMENT_SUMMARY, warnings: [], ...over };
}
