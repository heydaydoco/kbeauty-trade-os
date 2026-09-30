// PI·은행 계좌 화면 테스트 공용 픽스처 (S3-1 PR-6b). 응답 모양은 backend proforma_invoices/bank_accounts schemas 그대로.

import type {
  BankAccount,
  BankSnapshot,
  ProformaDetail,
  ProformaLine,
  ProformaPreview,
  ProformaSummary,
} from "../lib/proforma";

// 서버 응답의 자릿수 필드 — 화면이 쓰지 않는 값이라 변수로 둔다(응답 모양 재현).
const SERVER_MINOR_UNITS = 2;

export const BANK: BankSnapshot = {
  account_id: 2,
  beneficiary_name: "K-Beauty Co., Ltd.",
  beneficiary_address: "1 Seoul-ro, Seoul",
  bank_name: "Shinhan Bank",
  bank_address: "20 Sejong-daero, Seoul",
  account_no: "110-123-456789",
  swift_code: "SHBKKRSE",
};

export const PI_LINE: ProformaLine = {
  id: 31,
  line_no: 1,
  qt_line_id: 11,
  sku_id: 5,
  sku_code: "SKU-001",
  sku_name_ko: "수분 세럼",
  sku_name_en: null,
  sku_kind: "SINGLE",
  quantity: 6,
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
};

export function piSummary(over: Partial<ProformaSummary> = {}): ProformaSummary {
  return {
    id: 5,
    doc_number: "PI-2026-0001",
    doc_date: "2026-09-30",
    status: "ISSUED",
    qt_id: 7,
    qt_doc_number: "QT-2026-0001",
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    dest_market_code: "US",
    currency: "USD",
    total_amount: 7500,
    total_text: "75.00",
    valid_until: "2026-10-30",
    is_lapsed: false,
    assignee_id: 1,
    copied_from_id: null,
    version: 2,
    created_at: "2026-09-30T00:00:00Z",
    ...over,
  };
}

export function piDetail(over: Partial<ProformaDetail> = {}): ProformaDetail {
  return {
    ...piSummary(),
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
    buyer_address: "1 Main St",
    internal_note: null,
    frozen_at: "2026-09-30T00:00:00Z",
    last_line_no: 1,
    bank: BANK,
    advance: { advance_amount: 2250, advance_text: "22.50", balance_amount: 5250, balance_text: "52.50" },
    lines: [PI_LINE],
    ...over,
  };
}

export function piPreview(over: Partial<ProformaPreview> = {}): ProformaPreview {
  return {
    qt_id: 7,
    qt_doc_number: "QT-2026-0001",
    source_version: 4,
    doc_date: "2026-09-30",
    valid_until: "2026-10-30",
    currency: "USD",
    minor_units: SERVER_MINOR_UNITS,
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    buyer_address: "1 Main St",
    dest_market_code: "US",
    fx_rate: "1350.5",
    fx_rate_date: "2026-09-29",
    payment_terms: {
      payment_type: "TT_ADVANCE",
      advance_pct: "30",
      advance_pct_bp: 3000,
      balance_anchor: "BL_DATE",
      balance_days: 30,
    },
    incoterm: { code: "FOB", place: "Busan", year: 2020 },
    internal_note: null,
    assignee_id: 1,
    bank: BANK,
    total_amount: 7500,
    total_text: "75.00",
    advance: { advance_amount: 2250, advance_text: "22.50", balance_amount: 5250, balance_text: "52.50" },
    lines: [
      {
        line_no: 1,
        qt_line_id: 11,
        sku_id: 5,
        sku_code: "SKU-001",
        sku_name_ko: "수분 세럼",
        sku_name_en: null,
        sku_kind: "SINGLE",
        quantity: 6,
        open_quantity_before: 6,
        buyer_item_code: null,
        unit_price_amount: 1250,
        unit_price_text: "12.50",
        line_amount: 7500,
        line_amount_text: "75.00",
        price_basis: "MASTER",
        is_free: false,
        price_reason: null,
      },
    ],
    ...over,
  };
}

export function bankAccount(over: Partial<BankAccount> = {}): BankAccount {
  return {
    id: 2,
    label: "신한 USD",
    currency: "USD",
    beneficiary_name: BANK.beneficiary_name,
    beneficiary_address: BANK.beneficiary_address,
    bank_name: BANK.bank_name,
    bank_address: BANK.bank_address,
    account_no: BANK.account_no,
    swift_code: BANK.swift_code,
    version: 3,
    created_at: "2026-09-30T00:00:00Z",
    ...over,
  };
}

export const PI_LOG = {
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
