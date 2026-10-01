// 수주(SO)·문서 흐름 응답 타입 — backend/app/modules/{sales_orders,trade_chain}/{schemas,router}.py 그대로.
// 금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

import type { Incoterm, PaymentTerms } from "./proforma";

export interface SalesOrderSummary {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  buyer_partner_id: number;
  buyer_name: string;
  buyer_po_no: string | null;
  buyer_po_date: string | null;
  dest_market_code: string;
  currency: string;
  total_amount: number;
  total_text: string;
  qt_id: number | null;
  qt_doc_number: string | null;
  pi_id: number | null;
  pi_doc_number: string | null;
  assignee_id: number;
  copied_from_id: number | null;
  confirmed_at: string | null;
  /** 확정 증적 3열(PR-12a) — 접수 SO는 null. 서버 값 그대로(화면은 판정을 다시 하지 않는다). */
  credit_verdict?: string | null;
  credit_approval_id?: number | null;
  pi_gate_verdict?: string | null;
  version: number;
  created_at: string;
}

export interface SoLineSource {
  kind: string;
  line_id: number;
  quantity: number;
  unit_price_amount: number;
  unit_price_text: string;
}

export interface SalesOrderLine {
  id: number;
  line_no: number;
  sku_id: number;
  sku_code: string;
  sku_name_ko: string;
  sku_name_en: string | null;
  sku_kind: string;
  sku_status: string | null;
  quantity: number;
  requested_delivery_date: string | null;
  buyer_item_code: string | null;
  unit_price_amount: number;
  unit_price_text: string;
  list_price_amount: number | null;
  list_price_text: string | null;
  line_amount: number;
  line_amount_text: string;
  price_basis: string;
  is_free: boolean;
  price_reason: string | null;
  qt_line_id: number | null;
  pi_line_id: number | null;
  source: SoLineSource | null;
  quantity_delta: number | null;
  price_changed: boolean | null;
}

export interface SalesOrderDetail extends SalesOrderSummary {
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  fx_rate_age_days: number | null;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  internal_note: string | null;
  /** 확정 증거(gate_evaluations CONFIRMED) id — 접수 SO는 null. */
  confirm_evaluation_id?: number | null;
  last_line_no: number;
  is_reference: boolean;
  lines: SalesOrderLine[];
}

export interface SoLineMutation {
  line: SalesOrderLine | null;
  header_version: number;
  total_amount: number;
  total_text: string;
}

/** QT/PI → SO 참조 생성 요청 본문 — 원천에 있는 값(SKU·단가·통화·환율·거래처)을 다시 받는 필드가 없다(DoD ①). */
export interface SalesOrderCreateBody {
  version: number;
  doc_date?: string;
  buyer_po_no?: string;
  buyer_po_date?: string;
  lines?: Array<{ source_line_id: number; quantity: number; requested_delivery_date?: string }>;
  internal_note?: string;
}

export interface FlowNode {
  kind: string;
  id: number;
  doc_number: string;
  status: string;
  doc_date: string;
  currency: string;
  total_amount: number;
  total_text: string | null;
  parent_kind: string | null;
  parent_id: number | null;
  is_current: boolean;
}

export interface DocumentFlow {
  root_kind: string;
  root_id: number;
  nodes: FlowNode[];
}

export const SALES_ORDERS_QUERY_KEY = ["sales-orders"] as const;
export const salesOrderDetailKey = (id: number) => ["sales-orders", "detail", id] as const;
export const DOCUMENT_FLOW_QUERY_KEY = ["document-flow"] as const;
export const documentFlowKey = (kind: string, id: number) => ["document-flow", kind, id] as const;

/** 같은 바이어의 같은 PO번호가 이미 점유됨 — detail {doc_number, status} (점유 문서 안내). */
export const DUPLICATE_BUYER_PO_CODE = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO";
/** PI당 활성 SO 1건 — detail {doc_number, status}. */
export const REFERENCE_ALREADY_CONVERTED_CODE = "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED";
