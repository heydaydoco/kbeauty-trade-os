// 발주(PO) 화면 테스트 공용 픽스처 (S3-1 PR-8b). 응답 모양은 backend purchase_orders/schemas.py 그대로 —
// Full(원가 열람 역할)과 CostHidden(원가 키 자체가 없음) 두 형태를 모두 만든다.

import type {
  PoLine,
  PurchaseOrderDetail,
  PurchaseOrderPreview,
  PurchaseOrderSummary,
} from "../lib/purchase-order";

// 서버 응답의 자릿수 필드 — 화면이 쓰지 않는 값이라 변수로 둔다(응답 모양 재현).
const SERVER_MINOR_UNITS = 2;

/** 센티널 원가 — 원가 열람 권한이 없는 응답의 렌더·로그·저장소 어디에도 나오면 안 된다. */
export const SENTINEL_TOTAL = "76543219.87";
export const SENTINEL_UNIT = "7654321.98";
export const SENTINEL_LINE = "76543219.80";
export const SENTINELS = [SENTINEL_TOTAL, SENTINEL_UNIT, SENTINEL_LINE, "7654321987"];

export const PO_LINE: PoLine = {
  id: 41,
  line_no: 1,
  sku_id: 5,
  sku_code: "SKU-001",
  sku_name_ko: "수분 세럼",
  sku_name_en: null,
  sku_kind: "SINGLE",
  quantity: 10,
  requested_delivery_date: "2026-10-20",
  unit_cost: 765432198,
  unit_cost_text: SENTINEL_UNIT,
  line_cost: 7654321980,
  line_cost_text: SENTINEL_LINE,
  price_basis: "MASTER",
};

/** 같은 라인의 CostHidden 형태 — 원가 키가 없다. */
export const PO_LINE_HIDDEN: PoLine = {
  id: 41,
  line_no: 1,
  sku_id: 5,
  sku_code: "SKU-001",
  sku_name_ko: "수분 세럼",
  sku_name_en: null,
  sku_kind: "SINGLE",
  quantity: 10,
  requested_delivery_date: "2026-10-20",
};

const BASE_SUMMARY = {
  id: 9,
  doc_number: "PO-2026-0001",
  doc_date: "2026-09-30",
  status: "ISSUED",
  po_kind: "PURCHASE",
  supplier_partner_id: 4,
  supplier_name: "Seoul Supplier Co.",
  assignee_id: 1,
  copied_from_id: null,
  oc_received_on: null,
  oc_reference: null,
  version: 2,
  created_at: "2026-09-30T00:00:00Z",
};

export function poSummary(over: Partial<PurchaseOrderSummary> = {}): PurchaseOrderSummary {
  return { ...BASE_SUMMARY, currency: "USD", total_cost: 7654321987, total_text: SENTINEL_TOTAL, ...over };
}

/** CostHidden 목록 행 — 통화·합계 키가 없다. */
export function poSummaryHidden(over: Partial<PurchaseOrderSummary> = {}): PurchaseOrderSummary {
  return { ...BASE_SUMMARY, ...over };
}

const COMMON_DETAIL = {
  payment_terms: {
    payment_type: "TT_DEFERRED",
    advance_pct: null,
    advance_pct_bp: null,
    balance_anchor: "RECEIPT_DATE",
    balance_days: 30,
  },
  incoterm: { code: "FOB", place: "Busan", year: 2020 },
  internal_note: null,
  frozen_at: "2026-09-30T00:00:00Z",
  last_line_no: 1,
};

export function poDetail(over: Partial<PurchaseOrderDetail> = {}): PurchaseOrderDetail {
  return {
    ...poSummary(),
    ...COMMON_DETAIL,
    minor_units: SERVER_MINOR_UNITS,
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-29",
    lines: [PO_LINE],
    ...over,
  };
}

/** CostHidden 상세 — 원가·통화·환율·minor_units 키가 전부 없다. */
export function poDetailHidden(over: Partial<PurchaseOrderDetail> = {}): PurchaseOrderDetail {
  return { ...poSummaryHidden(), ...COMMON_DETAIL, lines: [PO_LINE_HIDDEN], ...over };
}

export const PO_LOG = {
  items: [
    {
      id: 1,
      occurred_at: "2026-09-30T01:00:00Z",
      from_status: null,
      to_status: "ISSUED",
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

export function poPreview(over: Partial<PurchaseOrderPreview> = {}): PurchaseOrderPreview {
  return {
    doc_date: "2026-09-30",
    po_kind: "PURCHASE",
    supplier_partner_id: 4,
    supplier_name: "Seoul Supplier Co.",
    currency: "USD",
    minor_units: SERVER_MINOR_UNITS,
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-30",
    payment_terms: {
      payment_type: "TT_DEFERRED",
      advance_pct: null,
      advance_pct_bp: null,
      balance_anchor: "RECEIPT_DATE",
      balance_days: 30,
    },
    incoterm: { code: "FOB", place: "Busan", year: 2020 },
    internal_note: null,
    assignee_id: 1,
    copied_from_id: null,
    total_cost: 12500,
    total_text: "125.00",
    lines: [
      {
        line_no: 1,
        sku_id: 5,
        sku_code: "SKU-001",
        sku_name_ko: "수분 세럼",
        sku_name_en: null,
        sku_kind: "SINGLE",
        quantity: 10,
        requested_delivery_date: null,
        unit_cost: 1250,
        unit_cost_text: "12.50",
        line_cost: 12500,
        line_cost_text: "125.00",
        price_basis: "MASTER",
      },
    ],
    ...over,
  };
}
