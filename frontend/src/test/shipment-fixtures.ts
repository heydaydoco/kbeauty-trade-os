// 선적 화면 테스트 픽스처 (S3-2 PR-3b) — 응답 모양은 backend shipments/schemas.py(ShipmentDetail·ShipmentListItem·ShipmentPreview) 그대로.

import type { ShipmentDetail, ShipmentLine, ShipmentListItem, ShipmentParty, ShipmentPreview } from "../lib/shipment";
import { exportBoard } from "./milestone-fixtures";

// 서버 응답의 자릿수 필드 — 화면이 쓰지 않는 값이라 변수로 둔다(응답 모양 재현).
const SERVER_MINOR_UNITS = 2;

export const SHIPMENT_LINE: ShipmentLine = {
  id: 501,
  line_no: 1,
  so_line_id: 41,
  sku: { id: 5, code: "SKU-001", name_ko: "수분 세럼", name_en: "Hydra Serum", kind: "SINGLE" },
  quantity: 4,
  currency: "USD",
  unit_price_amount: 1250,
  unit_price_text: "12.50",
  is_free: false,
  line_amount: 5000,
  line_amount_text: "50.00",
  source_line: { id: 41, line_no: 1, quantity: 6, remaining_after: 2 },
  dg: { flag: false, un_number: null, dg_class: null },
  availability: { status: "NOT_IMPLEMENTED" },
};

export const AUTO_CONSIGNEE: ShipmentParty = {
  id: 71,
  role: "CONSIGNEE",
  partner_id: 3,
  name_en: "ABC Trading Inc.",
  address_en: "1 Main St\nLos Angeles",
  auto: true,
  version: 1,
};

export const FORWARDER_PARTY: ShipmentParty = {
  id: 72,
  role: "FORWARDER",
  partner_id: 8,
  name_en: "Fast Forwarding Co.",
  address_en: null,
  auto: false,
  version: 2,
};

export function shipmentDetail(over: Partial<ShipmentDetail> = {}): ShipmentDetail {
  return {
    id: 31,
    doc_number: "SH-2026-0001",
    doc_date: "2026-10-04",
    status: "PLANNED",
    shipment_kind: "EXPORT",
    version: 3,
    frozen_at: null,
    source: { kind: "SALES_ORDER", id: 9, doc_number: "SO-2026-0001", status: "IN_SHIPMENT" },
    counterparty: { partner_id: 3, name: "ABC Trading" },
    origin_country_code: "KR",
    dest_country_code: "US",
    currency: "USD",
    minor_units: SERVER_MINOR_UNITS,
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-29",
    payment_terms: { payment_type: "TT_DEFERRED", advance_pct: null, advance_pct_bp: null, balance_anchor: "BL_DATE", balance_days: 30 },
    incoterm: { code: "FOB", place: "Busan", year: 2020 },
    total_amount: 5000,
    total_text: "50.00",
    internal_note: null,
    assignee: { id: 1, display_name: "무역 담당" },
    last_line_no: 1,
    dg_line_count: 0,
    lines: [SHIPMENT_LINE],
    parties: [AUTO_CONSIGNEE],
    // PR-4a 상세 내장 — 계획 전 수출선적의 보드(행 없음)·통관 요약 0. 마일스톤 버튼 동작은 시험이 allowed_actions에 더해 연다.
    milestones: exportBoard(),
    customs_summary: { live_count: 0, pending_count: 0, latest_accepted_on: null },
    allowed_actions: ["RELEASE_ORDER", "CANCEL", "EDIT_LINES", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES"],
    created_at: "2026-10-04T01:00:00Z",
    updated_at: "2026-10-04T02:30:00Z",
    ...over,
  };
}

export function shipmentListItem(over: Partial<ShipmentListItem> = {}): ShipmentListItem {
  return {
    id: 31,
    doc_number: "SH-2026-0001",
    doc_date: "2026-10-04",
    status: "PLANNED",
    shipment_kind: "EXPORT",
    source: { kind: "SALES_ORDER", id: 9, doc_number: "SO-2026-0001", status: "IN_SHIPMENT" },
    counterparty_name: "ABC Trading",
    origin_country_code: "KR",
    dest_country_code: "US",
    currency: "USD",
    total_amount: 5000,
    total_text: "50.00",
    line_count: 1,
    etd: null,
    eta: null,
    assignee: { id: 1, display_name: "무역 담당" },
    created_at: "2026-10-04T01:00:00Z",
    updated_at: "2026-10-04T02:30:00Z",
    ...over,
  };
}

export function shipmentPreview(over: Partial<ShipmentPreview> = {}): ShipmentPreview {
  return {
    so_id: 9,
    so_doc_number: "SO-2026-0001",
    so_status: "CONFIRMED",
    doc_date: "2026-10-04",
    shipment_kind: "EXPORT",
    counterparty: { partner_id: 3, name: "ABC Trading" },
    origin_country_code: "KR",
    dest_country_code: "US",
    currency: "USD",
    minor_units: SERVER_MINOR_UNITS,
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-29",
    payment_terms: { payment_type: "TT_DEFERRED", advance_pct: null, advance_pct_bp: null, balance_anchor: "BL_DATE", balance_days: 30 },
    incoterm: { code: "FOB", place: "Busan", year: 2020 },
    total_amount: 5000,
    total_text: "50.00",
    lines: [
      {
        so_line_id: 41,
        line_no: 1,
        sku: { id: 5, code: "SKU-001", name_ko: "수분 세럼", name_en: "Hydra Serum", kind: "SINGLE" },
        quantity: 4,
        open_quantity_before: 6,
        remaining_after: 2,
        unit_price_amount: 1250,
        unit_price_text: "12.50",
        is_free: false,
        line_amount: 5000,
        line_amount_text: "50.00",
        dg: { flag: true, un_number: "UN1950", dg_class: "2.1" },
      },
    ],
    parties: [{ role: "CONSIGNEE", partner_id: 3, name_en: "ABC Trading Inc.", address_en: null, auto: true }],
    ...over,
  };
}

export const apiErrorResponse = (code: string, message: string, detail: Record<string, unknown> = {}) => ({
  error: { code, message, detail, request_id: null },
});
