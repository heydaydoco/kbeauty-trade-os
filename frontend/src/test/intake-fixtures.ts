// 오더 인테이크 화면 테스트 공용 픽스처 (S3-1 PR-13b). 응답 모양은 backend order_intake/schemas.py 그대로.

import type { IntakeDetail, IntakeGateReport, IntakeGateResult, IntakeLine, IntakeSummary } from "../lib/order-intake";
import { jsonResponse } from "./render";

export function intakeLine(over: Partial<IntakeLine> = {}): IntakeLine {
  return {
    id: 101,
    line_no: 1,
    buyer_item_code: "ABC-1",
    sku_id: 5,
    sku_code: "SKU-001",
    sku_name_ko: "수분 세럼",
    sku_status: "ACTIVE",
    mapping_state: "MAPPED",
    quantity: 10,
    unit_price_amount: 1250,
    unit_price_text: "12.50",
    line_amount: 12500,
    requested_delivery_date: "2099-12-31",
    source_row_no: null,
    ...over,
  };
}

export function intakeSummary(over: Partial<IntakeSummary> = {}): IntakeSummary {
  return {
    id: 21,
    version: 2,
    source_kind: "MANUAL",
    status: "PENDING",
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    buyer_po_no: "PO-2026-001",
    buyer_po_date: "2026-09-29",
    currency: "USD",
    dest_market_code: "US",
    assignee_id: 1,
    line_count: 1,
    total_amount: 12500,
    total_text: "125.00",
    sales_order_id: null,
    copied_from_so_id: null,
    po_occupied: null,
    created_at: "2026-09-30T00:00:00Z",
    decided_at: null,
    ...over,
  };
}

export function intakeDetail(over: Partial<IntakeDetail> = {}): IntakeDetail {
  const summary: Omit<IntakeSummary, "line_count"> & { line_count?: number } = { ...intakeSummary() };
  delete summary.line_count;
  return {
    ...summary,
    reject_reason: null,
    decided_by_id: null,
    last_line_no: 1,
    updated_at: "2026-09-30T00:00:00Z",
    lines: [intakeLine()],
    original: {
      kind: "MANUAL",
      header: { buyer_po_no: "PO-2026-001", buyer_po_date: "2026-09-29", currency: "USD", dest_market_code: "US" },
      lines: [{ buyer_item_code: "ABC-1", quantity: 10, unit_price: "12.50", requested_delivery_date: "2099-12-31" }],
    },
    ...over,
  };
}

export function intakeGate(over: Partial<IntakeGateResult> = {}): IntakeGateResult {
  return {
    gate_code: "ITEM_MAPPING",
    line_id: null,
    line_no: null,
    level: "PASS",
    resolution: "NONE",
    reason_code: "MAPPED",
    message_ko: "품번 매핑이 확인되었습니다.",
    basis: {},
    detail: {},
    settlement: "NOT_REQUIRED",
    blocks_intake_confirm: false,
    ...over,
  };
}

export function intakeGateReport(over: Partial<IntakeGateReport> = {}): IntakeGateReport {
  return {
    intake_id: 21,
    status: "PENDING",
    phase: "INTAKE",
    evaluated_at: "2026-09-30T01:00:00Z",
    note: "준비 상태 안내(법적 판정 아님)",
    readiness_scope_note: "시장 준비도는 계산값이며 법적 판정이 아닙니다.",
    intake_confirmable: true,
    gates: [intakeGate()],
    ...over,
  };
}

export const apiError = (code: string, status: number, message = "서버 영문 원문 message", detail: Record<string, unknown> = {}) =>
  jsonResponse({ error: { code, message, detail, request_id: "req-1" } }, status);
