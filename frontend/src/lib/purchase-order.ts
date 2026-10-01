// 발주(PO) 응답·요청 타입 — backend/app/modules/purchase_orders/schemas.py 그대로 (S3-1 PR-8b).
//
// ★ 원가 마스킹(ADR-0024·0057): 서버는 원가를 볼 수 없는 역할(VIEWER 등)에게 **원가 키 자체가 없는** 응답(CostHidden)을 준다.
//   그래서 원가 계열 필드(통화·합계·단가·라인금액·기준·환율)는 전부 optional이다 — 화면은 키가 있을 때만 그린다.
//   금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

import type { Incoterm, PaymentTerms } from "./proforma";

export interface PoLine {
  id: number;
  line_no: number;
  sku_id: number;
  sku_code: string;
  sku_name_ko: string;
  sku_name_en: string | null;
  sku_kind: string;
  quantity: number;
  requested_delivery_date: string | null;
  // ── 원가 키(없을 수 있다) ──
  unit_cost?: number;
  unit_cost_text?: string;
  line_cost?: number;
  line_cost_text?: string;
  price_basis?: string;
}

export interface PurchaseOrderSummary {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  po_kind: string;
  supplier_partner_id: number;
  supplier_name: string;
  assignee_id: number;
  copied_from_id: number | null;
  oc_received_on: string | null;
  oc_reference: string | null;
  version: number;
  created_at: string;
  // ── 원가 키(없을 수 있다) ──
  currency?: string;
  total_cost?: number;
  total_text?: string;
}

export interface PurchaseOrderDetail extends PurchaseOrderSummary {
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  internal_note: string | null;
  frozen_at: string;
  last_line_no: number;
  lines: PoLine[];
  // ── 원가 키(없을 수 있다) ──
  minor_units?: number;
  fx_rate?: string | null;
  fx_rate_date?: string | null;
}

export interface PoPreviewLine {
  line_no: number;
  sku_id: number;
  sku_code: string;
  sku_name_ko: string;
  sku_name_en: string | null;
  sku_kind: string;
  quantity: number;
  requested_delivery_date: string | null;
  unit_cost: number;
  unit_cost_text: string;
  line_cost: number;
  line_cost_text: string;
  price_basis: string;
}

/** 미리보기 — 쓰기 역할 전용이라 원가가 항상 있다(서버가 저장하지 않는다). */
export interface PurchaseOrderPreview {
  doc_date: string;
  po_kind: string;
  supplier_partner_id: number;
  supplier_name: string;
  currency: string;
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  internal_note: string | null;
  assignee_id: number;
  copied_from_id: number | null;
  total_cost: number;
  total_text: string;
  lines: PoPreviewLine[];
}

export interface PoCreateLineBody {
  sku_id: number;
  quantity: number;
  /** 사람 표기 문자열("12.34") — 생략하면 마스터 매입가(서버가 증빙일 기준으로 읽는다). */
  unit_cost?: string;
  requested_delivery_date?: string;
}

/** 생성(=발행)·미리보기 공용 본문. 생성은 낙관 잠금 대상이 없어 version이 없다. */
export interface PoCreateBody {
  supplier_partner_id: number;
  po_kind: string;
  currency: string;
  doc_date?: string;
  fx_rate?: string;
  fx_rate_date?: string;
  payment_terms: {
    payment_type: string;
    advance_pct?: string;
    balance_anchor?: string;
    balance_days?: number;
  };
  incoterm: { code: string; place: string; year: number };
  supplier_name?: string;
  internal_note?: string;
  assignee_id?: number;
  lines: PoCreateLineBody[];
}

export const PURCHASE_ORDERS_QUERY_KEY = ["purchase-orders"] as const;
export const purchaseOrderDetailKey = (id: number) => ["purchase-orders", "detail", id] as const;

/** 원가 열(합계)이 이 응답에 있는가 — 키 유무가 서버의 판정이다(화면이 역할을 따로 추측하지 않는다). */
export const hasCostColumns = (rows: ReadonlyArray<{ total_text?: string }>): boolean =>
  rows.some((row) => row.total_text !== undefined);

/** 상세·라인 원가 표시 여부 — 합계 키가 있으면 Full 응답이다. */
export const hasCost = (po: { total_text?: string }): boolean => po.total_text !== undefined;

/** 자유 텍스트 입력란 옆 경고(ADR-0057 수용된 사실) — 서버는 자유 텍스트의 내용을 판별하지 못한다. */
export const NO_COST_IN_FREE_TEXT =
  "원가(단가·금액)를 적지 마세요 — 원가 열람 권한이 없는 사용자에게도 보입니다.";

export const PO_RISK_NOTICE =
  "발주 확정은 되돌릴 수 없는 공급사 발송 대상입니다. 확정하면 발주번호가 붙고 바로 발행·동결되어 가격·조건·라인을 고칠 수 없습니다(잘못되면 취소하고 새로 만듭니다).";
