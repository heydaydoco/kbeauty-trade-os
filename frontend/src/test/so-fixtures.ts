// SO(수주)·문서 흐름 화면 테스트 공용 픽스처 (S3-1 PR-7b). 응답 모양은 backend sales_orders/trade_chain schemas 그대로.

import type { DocumentFlow, FlowNode, SalesOrderDetail, SalesOrderLine, SalesOrderSummary } from "../lib/sales-order";

// 서버 응답의 자릿수 필드 — 화면이 쓰지 않는 값이라 변수로 둔다(응답 모양 재현).
const SERVER_MINOR_UNITS = 2;

export const SO_LINE: SalesOrderLine = {
  id: 41,
  line_no: 1,
  sku_id: 5,
  sku_code: "SKU-001",
  sku_name_ko: "수분 세럼",
  sku_name_en: null,
  sku_kind: "SINGLE",
  sku_status: "ACTIVE",
  quantity: 6,
  requested_delivery_date: null,
  buyer_item_code: null,
  unit_price_amount: 1250,
  unit_price_text: "12.50",
  list_price_amount: 1250,
  list_price_text: "12.50",
  line_amount: 7500,
  line_amount_text: "75.00",
  price_basis: "MASTER",
  is_free: false,
  price_reason: null,
  qt_line_id: 11,
  pi_line_id: 31,
  source: { kind: "PI", line_id: 31, quantity: 6, unit_price_amount: 1250, unit_price_text: "12.50" },
  quantity_delta: 0,
  price_changed: false,
};

export function soSummary(over: Partial<SalesOrderSummary> = {}): SalesOrderSummary {
  return {
    id: 9,
    doc_number: "SO-2026-0001",
    doc_date: "2026-09-30",
    status: "RECEIVED",
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    buyer_po_no: "PO-2026-001",
    buyer_po_date: "2026-09-29",
    dest_market_code: "US",
    currency: "USD",
    total_amount: 7500,
    total_text: "75.00",
    qt_id: 7,
    qt_doc_number: "QT-2026-0001",
    pi_id: 5,
    pi_doc_number: "PI-2026-0001",
    assignee_id: 1,
    copied_from_id: null,
    confirmed_at: null,
    version: 3,
    created_at: "2026-09-30T00:00:00Z",
    ...over,
  };
}

export function soDetail(over: Partial<SalesOrderDetail> = {}): SalesOrderDetail {
  return {
    ...soSummary(),
    minor_units: SERVER_MINOR_UNITS,
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-29",
    fx_rate_age_days: 1,
    payment_terms: {
      payment_type: "TT_ADVANCE",
      advance_pct: "30",
      advance_pct_bp: 3000,
      balance_anchor: "BL_DATE",
      balance_days: 30,
    },
    incoterm: { code: "FOB", place: "Busan", year: 2020 },
    internal_note: null,
    last_line_no: 1,
    is_reference: true,
    lines: [SO_LINE],
    ...over,
  };
}

export const SO_LOG = {
  items: [
    {
      id: 1,
      occurred_at: "2026-09-30T01:00:00Z",
      from_status: null,
      to_status: "RECEIVED",
      reason: null,
      actor_user_id: 1,
      actor_name: "무역 담당",
      automatic: false,
    },
  ],
  total: 1,
  page: 1,
  size: 50,
};

export function flowNode(over: Partial<FlowNode> = {}): FlowNode {
  return {
    kind: "QUOTATION",
    id: 7,
    doc_number: "QT-2026-0001",
    status: "ISSUED",
    doc_date: "2026-09-30",
    currency: "USD",
    total_amount: 12500,
    total_text: "125.00",
    parent_kind: null,
    parent_id: null,
    is_current: false,
    ...over,
  };
}

/** QT → PI → SO 한 줄 사슬. `current`가 가리키는 종류가 현재 문서. */
export function chainFlow(current: "QUOTATION" | "PROFORMA_INVOICE" | "SALES_ORDER", withSo = true): DocumentFlow {
  const nodes: FlowNode[] = [
    flowNode({ is_current: current === "QUOTATION" }),
    flowNode({
      kind: "PROFORMA_INVOICE",
      id: 5,
      doc_number: "PI-2026-0001",
      status: "ISSUED",
      total_amount: 7500,
      total_text: "75.00",
      parent_kind: "QUOTATION",
      parent_id: 7,
      is_current: current === "PROFORMA_INVOICE",
    }),
  ];
  if (withSo) {
    nodes.push(
      flowNode({
        kind: "SALES_ORDER",
        id: 9,
        doc_number: "SO-2026-0001",
        status: "RECEIVED",
        total_amount: 7500,
        total_text: "75.00",
        parent_kind: "PROFORMA_INVOICE",
        parent_id: 5,
        is_current: current === "SALES_ORDER",
      }),
    );
  }
  return { root_kind: "QUOTATION", root_id: 7, nodes };
}
