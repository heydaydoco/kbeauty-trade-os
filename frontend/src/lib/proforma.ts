// PI(선수금 청구서)·은행 계좌 응답 타입 — backend/app/modules/{proforma_invoices,bank_accounts}/schemas.py 그대로.
// 금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

import type { QuotationDetail } from "./quotation";

export interface ProformaSummary {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  qt_id: number;
  qt_doc_number: string | null;
  buyer_partner_id: number;
  buyer_name: string;
  dest_market_code: string;
  currency: string;
  total_amount: number;
  total_text: string;
  valid_until: string;
  is_lapsed: boolean;
  assignee_id: number;
  copied_from_id: number | null;
  version: number;
  created_at: string;
}

export interface ProformaLine {
  id: number;
  line_no: number;
  qt_line_id: number;
  sku_id: number;
  sku_code: string;
  sku_name_ko: string;
  sku_name_en: string | null;
  sku_kind: string;
  quantity: number;
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
}

export interface BankSnapshot {
  account_id: number;
  beneficiary_name: string;
  beneficiary_address: string;
  bank_name: string;
  bank_address: string;
  account_no: string;
  swift_code: string;
}

export interface Advance {
  advance_amount: number;
  advance_text: string;
  balance_amount: number;
  balance_text: string;
}

export type PaymentTerms = QuotationDetail["payment_terms"];
export type Incoterm = QuotationDetail["incoterm"];

export interface ProformaDetail extends ProformaSummary {
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  fx_rate_age_days: number | null;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  buyer_address: string;
  internal_note: string | null;
  frozen_at: string;
  last_line_no: number;
  bank: BankSnapshot;
  advance: Advance | null;
  lines: ProformaLine[];
}

export interface ProformaPreviewLine {
  line_no: number;
  qt_line_id: number;
  sku_id: number;
  sku_code: string;
  sku_name_ko: string;
  sku_name_en: string | null;
  sku_kind: string;
  quantity: number;
  open_quantity_before: number;
  buyer_item_code: string | null;
  unit_price_amount: number;
  unit_price_text: string;
  line_amount: number;
  line_amount_text: string;
  price_basis: string;
  is_free: boolean;
  price_reason: string | null;
}

export interface ProformaPreview {
  qt_id: number;
  qt_doc_number: string;
  source_version: number;
  doc_date: string;
  valid_until: string;
  currency: string;
  minor_units: number;
  buyer_partner_id: number;
  buyer_name: string;
  buyer_address: string;
  dest_market_code: string;
  fx_rate: string | null;
  fx_rate_date: string | null;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  internal_note: string | null;
  assignee_id: number;
  bank: BankSnapshot;
  total_amount: number;
  total_text: string;
  advance: Advance | null;
  lines: ProformaPreviewLine[];
}

/** PI 생성·미리보기 요청 본문(같은 본문을 쓴다). `lines` 생략 = 각 라인 잔량 전부. */
export interface ProformaCreateBody {
  version: number;
  doc_date?: string;
  valid_until: string;
  bank_account_id: number;
  lines?: Array<{ source_line_id: number; quantity: number }>;
  overrides?: { internal_note?: string };
}

export interface BankAccount {
  id: number;
  label: string;
  currency: string;
  beneficiary_name: string;
  beneficiary_address: string;
  bank_name: string;
  bank_address: string;
  account_no: string;
  swift_code: string;
  version: number;
  created_at: string;
}

export const PROFORMAS_QUERY_KEY = ["proforma-invoices"] as const;
export const proformaDetailKey = (id: number) => ["proforma-invoices", "detail", id] as const;
export const BANK_ACCOUNTS_QUERY_KEY = ["bank-accounts"] as const;

/** 서버 잔량 초과 409(`TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`) — detail.open_quantity {원천 라인 id: 잔량}. */
export const QUANTITY_EXCEEDS_OPEN_CODE = "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN";
