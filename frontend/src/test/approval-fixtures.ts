// 승인 화면 테스트 공용 픽스처 — 서버 ApprovalView·이력·결재선·대결 모양 그대로(S3-1 PR-9b).

import type { ApprovalEvent, ApprovalLine, ApprovalView, Delegation } from "../lib/approval";
import { jsonResponse, page } from "./render";

export const ADMIN = { id: 9, email: "admin@example.com", display_name: "관리자", roles: ["ADMIN"] };
export const CERT_USER = { id: 4, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

// 서버 /v1/system/currencies 응답 재현 — 자릿수는 서버 통화표가 출처다(화면 코드에는 없다). 값은 변수로 둔다(자릿수 하드코딩 가드와 무관한 응답 모양).
const USD_UNITS = 2;
const KRW_UNITS = 0;
export const CURRENCY_HANDLER: [string, string, () => Response] = [
  "/v1/system/currencies",
  "GET",
  () =>
    jsonResponse(
      page([
        { code: "USD", minor_units: USD_UNITS },
        { code: "KRW", minor_units: KRW_UNITS },
      ]),
    ),
];

export function approval(over: Partial<ApprovalView> = {}): ApprovalView {
  return {
    id: 7,
    approval_type: "SO_CREDIT_EXCEEDED",
    target_type: "SALES_ORDER",
    target_id: 12,
    target_label: "SO-2026-0012",
    status: "REQUESTED",
    required_role: "TRADE",
    requested_by_id: 2,
    requester_name: "영업 기안자",
    basis_amount: 30000,
    basis_currency: "USD",
    snapshot: {
      verdict: "EXCEEDED",
      limit_amount: 100000,
      limit_currency: "USD",
      open_orders_amount: 80000,
      this_order_amount: 50000,
      receivables_reflected: false,
      exposure_after_amount: 130000,
      excess_amount: 30000,
      exposure_is_partial: false,
      reason_codes: "",
      fx_doc_currency: "USD",
      fx_rate: null,
      fx_rate_date: null,
    },
    decided_by_name: null,
    decided_on_behalf_of_name: null,
    decided_at: null,
    consumed_at: null,
    status_reason: null,
    void_reason_code: null,
    can_decide: true,
    decide_blocked_reason: null,
    can_withdraw: false,
    created_at: "2026-09-30T01:00:00Z",
    version: 3,
    ...over,
  };
}

export const EVENTS: { items: ApprovalEvent[]; total: number; page: number; size: number } = {
  items: [
    {
      id: 1,
      occurred_at: "2026-09-30T01:00:00Z",
      from_status: null,
      to_status: "REQUESTED",
      actor_name: "영업 기안자",
      on_behalf_of_name: null,
      reason: null,
      reason_code: null,
    },
    {
      id: 2,
      occurred_at: "2026-10-01T01:00:00Z",
      from_status: "REQUESTED",
      to_status: "APPROVED",
      actor_name: "수임자",
      on_behalf_of_name: "휴가 중 결재자",
      reason: null,
      reason_code: null,
    },
  ],
  total: 2,
  page: 1,
  size: 50,
};

export function line(over: Partial<ApprovalLine> = {}): ApprovalLine {
  return {
    id: 21,
    approval_type: "SO_CREDIT_EXCEEDED",
    threshold_amount: 500000,
    threshold_currency: "USD",
    threshold_text: "5,000.00",
    approver_role: "TRADE",
    note: null,
    version: 2,
    ...over,
  };
}

export function delegation(over: Partial<Delegation> = {}): Delegation {
  return {
    id: 31,
    delegator_user_id: 1,
    delegator_name: "무역 담당",
    delegate_user_id: 4,
    delegate_name: "인증 담당",
    approval_type: "SO_CREDIT_EXCEEDED",
    delegated_role: "TRADE",
    start_on: "2026-10-01",
    end_on: "2026-10-10",
    note: null,
    state: "ACTIVE",
    inert_reason: null,
    revoked_at: null,
    can_revoke: true,
    version: 1,
    ...over,
  };
}
