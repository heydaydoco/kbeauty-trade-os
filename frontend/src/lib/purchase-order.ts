// 발주(PO) 응답·요청 타입 — backend/app/modules/purchase_orders/schemas.py 그대로 (S3-1 PR-8b).
//
// ★ 원가 마스킹(ADR-0024·0057): 서버는 원가를 볼 수 없는 역할(VIEWER 등)에게 **원가 키 자체가 없는** 응답(CostHidden)을 준다.
//   그래서 원가 계열 필드(통화·합계·단가·라인금액·기준·환율)는 전부 optional이다 — 화면은 키가 있을 때만 그린다.
//   금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

import type { Incoterm, PaymentTerms } from "./proforma";

/**
 * PO 라인 입고예정(S3-2 PR-5a 계산값 — 열 없음, design-B B17·ADR-0085). 그 라인을 참조하는 살아 있는 수입선적들의 ETA 유효값(실적 우선) 중
 * 가장 늦은 날. ETA 없는 선적이 하나라도 있으면 UNSCHEDULED(`value` null), 선적이 없으면 NONE. 원가 무관 — 전 역할 같은 값.
 */
export interface ExpectedReceipt {
  status: "NONE" | "UNSCHEDULED" | "SCHEDULED";
  /** 'YYYY-MM-DD'(도착 현지 날짜) — SCHEDULED일 때만. 문자열 그대로 표시한다(`new Date` 금지 — UTC 자정 해석으로 하루 밀림). */
  value: string | null;
  basis: "ACTUAL" | "PLANNED" | null;
  shipment_count: number;
  unscheduled_count: number;
}

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
  /**
   * 수입선적 **배정 가능량**(S3-2 PR-5a 파생값) = 라인 수량 − 살아 있는 수입선적 수량. PO 잔량(입고 전 수량)과 다르다.
   * null·없음 = 이 필드가 생기기 전 저장된 멱등 재생 본문(R-5a-6) — 화면은 '정보 없음 — 새로고침'(0으로 보지 않는다).
   */
  assignable_quantity?: number | null;
  /** 입고예정 계산값 — null·없음은 위와 같은 재생 본문. */
  expected_receipt?: ExpectedReceipt | null;
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

// ── 수입선적(S3-2 PR-5b — PROGRESS 'S3-2 PR-5a' 인계 계약) ──

/** 수입선적을 만들 수 있는 발주 상태(서버 `CONSUMABLE_STATUSES[PO]` — 발행·공급사 확인). 표시 편의일 뿐 서버가 정본(409 DOCUMENT_NOT_CONSUMABLE). */
export const IMPORT_SHIPPABLE_PO_STATUSES: ReadonlySet<string> = new Set(["ISSUED", "SUPPLIER_CONFIRMED"]);

/**
 * 라인 배정 가능량 합 — 모르는 값(null·없음·음수)은 0으로 센다(fail-closed: 알 수 없으면 '만들기'를 열지 않는다).
 * 버튼 노출 조건 `Σ lines[].assignable_quantity > 0`의 화면 쪽 계산(합산만 — 수량 산식 재현 0).
 */
export const assignableTotal = (lines: ReadonlyArray<Pick<PoLine, "assignable_quantity">>): number =>
  lines.reduce((sum, line) => sum + (typeof line.assignable_quantity === "number" ? Math.max(line.assignable_quantity, 0) : 0), 0);

/** 입고예정·배정 가능량이 없는 재생 본문(R-5a-6)인가 — 한 라인이라도 두 필드 중 하나가 null·없으면 true. */
export const lacksReceiptFields = (lines: ReadonlyArray<Pick<PoLine, "assignable_quantity" | "expected_receipt">>): boolean =>
  lines.some((line) => typeof line.assignable_quantity !== "number" || line.expected_receipt == null);

/**
 * 입고예정 표시 문구 — NONE "입고예정 미정(수입선적 없음)" / UNSCHEDULED "ETA 미정 n건" / SCHEDULED 날짜 문자열 그대로(+ 실적·예정 표지는
 * `basis`로 화면이 붙인다). 모르는 상태·모양이 어긋난 값은 null(화면 '정보 없음' — 날짜를 지어내지 않는다).
 */
export function expectedReceiptText(receipt: ExpectedReceipt | null | undefined): string | null {
  if (receipt == null) return null;
  if (receipt.status === "NONE") return "입고예정 미정(수입선적 없음)";
  if (receipt.status === "UNSCHEDULED") return `ETA 미정 ${receipt.unscheduled_count}건`;
  if (receipt.status === "SCHEDULED" && typeof receipt.value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(receipt.value)) {
    return receipt.value;
  }
  return null;
}
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
