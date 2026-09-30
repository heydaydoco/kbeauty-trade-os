// 견적(QT) 응답 타입 — backend/app/modules/quotations/schemas.py 그대로.
// 금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

export interface QuotationSummary {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  buyer_partner_id: number;
  buyer_name: string;
  dest_market_code: string;
  currency: string;
  total_amount: number;
  total_text: string;
  valid_until: string | null;
  is_lapsed: boolean;
  assignee_id: number;
  copied_from_id: number | null;
  version: number;
  created_at: string;
}

export interface QuotationLine {
  id: number;
  line_no: number;
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

export interface QuotationDetail extends QuotationSummary {
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  fx_rate_age_days: number | null;
  payment_terms: {
    payment_type: string | null;
    advance_pct: string | null;
    advance_pct_bp: number | null;
    balance_anchor: string | null;
    balance_days: number | null;
  };
  incoterm: { code: string | null; place: string | null; year: number | null };
  buyer_address: string | null;
  internal_note: string | null;
  frozen_at: string | null;
  last_line_no: number;
  lines: QuotationLine[];
}

export interface LineMutation {
  line: QuotationLine | null;
  header_version: number;
  total_amount: number;
  total_text: string;
}

export const QUOTATIONS_QUERY_KEY = ["quotations"] as const;
export const quotationDetailKey = (id: number) => ["quotations", "detail", id] as const;
