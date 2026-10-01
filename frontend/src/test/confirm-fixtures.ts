// 확정 패널 테스트 픽스처 (S3-1 PR-12b) — 서버 응답 모양 그대로(backend trade_chain schemas·confirm.py `_blocked_detail`). 값은 서버가 준 것으로 고정한다.

import type { ApprovalRequestResult, BlockedGate, ConfirmResult } from "../lib/confirm";
import type { GateResult } from "../lib/gate";
import { approval } from "./approval-fixtures";
import { HASH_A, gate } from "./gate-fixtures";
import { jsonResponse } from "./render";
import { soDetail } from "./so-fixtures";

export const CONFIRM_URL = "/v1/sales-orders/9/confirm";
export const REQUEST_URL = "/v1/sales-orders/9/approval-requests";
export const GATES_URL = "/v1/sales-orders/9/gates";

/** 여신 한도 초과(승인으로만 해소) — basis는 정수 최소단위뿐(`*_text` 없음)이 실제 계약이다. */
export const CREDIT_BASIS = {
  verdict: "EXCEEDED",
  limit_amount: 123456789,
  limit_currency: "USD",
  open_orders_amount: 98765432,
  this_order_amount: 7500,
  receivables_reflected: false,
  exposure_after_amount: 555555555,
  excess_amount: 424242424,
  exposure_is_partial: false,
  reason_codes: "",
  advisory: true,
};

export const CREDIT_ROW: GateResult = gate({
  gate_code: "CREDIT",
  level: "BLOCK",
  resolution: "APPROVAL",
  reason_code: "LIMIT_EXCEEDED",
  message_ko: "여신 한도를 초과합니다. 승인을 요청해 승인된 뒤에 확정할 수 있습니다.",
  settlement: "UNRESOLVED",
  basis: CREDIT_BASIS,
  basis_hash: HASH_A,
});

export function blockedGate(over: Partial<BlockedGate> = {}): BlockedGate {
  return {
    gate_code: "CREDIT",
    line_id: null,
    line_no: null,
    level: "BLOCK",
    resolution: "APPROVAL",
    reason_code: "LIMIT_EXCEEDED",
    message_ko: "여신 한도를 초과합니다. 승인을 요청해 승인된 뒤에 확정할 수 있습니다.",
    basis: { ...CREDIT_BASIS, advisory: false },
    detail: {},
    basis_hash: HASH_A,
    override_roles: [],
    can_override: false,
    ...over,
  };
}

export const PRICE_BLOCKED = blockedGate({
  gate_code: "PRICE_DEVIATION",
  line_id: 41,
  line_no: 1,
  resolution: "OVERRIDE",
  reason_code: "PRICE_ABOVE_TOLERANCE",
  message_ko: "기준가 대비 편차가 허용치를 넘습니다.",
  basis: {},
  override_roles: ["TRADE", "ADMIN"],
  can_override: true,
});

export const MAPPING_BLOCKED = blockedGate({
  gate_code: "ITEM_MAPPING",
  line_id: 41,
  line_no: 1,
  resolution: "NONE",
  reason_code: "UNMAPPED_ITEM",
  message_ko: "바이어 품번이 SKU에 매핑되지 않았습니다.",
  basis: {},
});

export interface BlockedOver {
  blocked_gates?: BlockedGate[];
  needs_approval?: boolean;
  pending_approval_id?: number | null;
  pending_approval_status?: string | null;
}

/** 409 GATE_BLOCKED 응답. */
export function blockedResponse(over: BlockedOver = {}): Response {
  return jsonResponse(
    {
      error: {
        code: "TRADE_CHAIN.CONFIRM.GATE_BLOCKED",
        message: "해소되지 않은 게이트가 있어 수주를 확정할 수 없습니다.",
        detail: {
          blocked_gates: [blockedGate()],
          needs_approval: true,
          pending_approval_id: null,
          pending_approval_status: null,
          evaluation_id: 501,
          input_digest: "d".repeat(64),
          ...over,
        },
      },
    },
    409,
  );
}

/** 일반 오류 응답 — message는 영문 내부 문구(화면에 나오면 안 된다), detail에도 영문 코드. */
export const apiError = (code: string, status: number, detail: Record<string, unknown> = { internal_code: "RAW_DETAIL_CODE" }, message = "Internal raw message") =>
  jsonResponse({ error: { code, message, detail } }, status);

export function confirmOut(over: Partial<ConfirmResult> = {}): ConfirmResult {
  return {
    sales_order: soDetail({
      status: "CONFIRMED",
      confirmed_at: "2026-10-01T02:00:00Z",
      version: 4,
      credit_verdict: "APPROVED",
      credit_approval_id: 33,
      pi_gate_verdict: "OVERRIDDEN",
      confirm_evaluation_id: 77,
    }),
    gates: [
      gate({ gate_code: "PI_DEPOSIT", level: "BLOCK", resolution: "OVERRIDE", settlement: "OVERRIDDEN" }),
      gate({ gate_code: "DUPLICATE_PO", level: "WARN", message_ko: "PO번호 확인 불가" }),
      gate({ gate_code: "ITEM_MAPPING" }),
    ],
    allocation: { status: "NOT_IMPLEMENTED", note: "재고 할당 포트가 아직 연결되지 않았습니다." },
    evaluation_id: 77,
    ...over,
  };
}

export function approvalRequestOut(over: Partial<ApprovalRequestResult> = {}): ApprovalRequestResult {
  return { ...approval({ id: 61, target_id: 9, target_label: "SO-2026-0001", status: "REQUESTED" }), created: true, ...over };
}
