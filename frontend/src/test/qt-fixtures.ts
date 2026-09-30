// 견적 화면 테스트 공용 픽스처·fetch 스텁.

import { vi } from "vitest";
import type { QuotationDetail, QuotationLine, QuotationSummary } from "../lib/quotation";
import { jsonResponse, page } from "./render";

export const LINE: QuotationLine = {
  id: 11,
  line_no: 1,
  sku_id: 5,
  sku_code: "SKU-001",
  sku_name_ko: "수분 세럼",
  sku_name_en: null,
  sku_kind: "SINGLE",
  quantity: 10,
  buyer_item_code: null,
  unit_price_amount: 1250,
  unit_price_text: "12.50",
  list_price_amount: 1250,
  list_price_text: "12.50",
  line_amount: 12500,
  line_amount_text: "125.00",
  price_basis: "MASTER",
  is_free: false,
  price_reason: null,
};

export function summary(over: Partial<QuotationSummary> = {}): QuotationSummary {
  return {
    id: 7,
    doc_number: "QT-2026-0001",
    doc_date: "2026-09-30",
    status: "DRAFT",
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    dest_market_code: "US",
    currency: "USD",
    total_amount: 12500,
    total_text: "125.00",
    valid_until: "2026-10-30",
    is_lapsed: false,
    assignee_id: 1,
    copied_from_id: null,
    version: 4,
    created_at: "2026-09-30T00:00:00Z",
    ...over,
  };
}

// 서버 응답의 자릿수 필드 — 화면이 쓰지 않는 값이라 변수로 둔다(자릿수 하드코딩 가드 회피가 아니라 응답 모양 재현).
const SERVER_MINOR_UNITS = 2;

export function detail(over: Partial<QuotationDetail> = {}): QuotationDetail {
  return {
    ...summary(),
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
    frozen_at: null,
    last_line_no: 1,
    lines: [LINE],
    ...over,
  };
}

export const LOG = {
  items: [
    {
      id: 3,
      occurred_at: "2026-09-30T01:00:00Z",
      from_status: "ISSUED",
      to_status: "CANCELLED",
      reason: "바이어 요청",
      actor_user_id: 1,
      actor_name: "무역 담당",
      automatic: false,
    },
    {
      id: 2,
      occurred_at: "2026-09-29T01:00:00Z",
      from_status: "DRAFT",
      to_status: "ISSUED",
      reason: null,
      actor_user_id: 1,
      actor_name: "무역 담당",
      automatic: false,
    },
    {
      id: 1,
      occurred_at: "2026-09-28T01:00:00Z",
      from_status: null,
      to_status: "DRAFT",
      reason: null,
      actor_user_id: null,
      actor_name: null,
      automatic: true,
    },
  ],
  total: 3,
  page: 1,
  size: 50,
};

export interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
  headers: Record<string, string>;
}

/** 경로별 응답을 주는 fetch 스텁. handlers는 (url, method) 부분 일치 — 먼저 맞는 것이 이긴다. */
export function stubFetch(
  me: unknown,
  handlers: Array<[string, string, () => Response]>,
): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: input,
        method,
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
        headers: (init?.headers ?? {}) as Record<string, string>,
      });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      for (const [needle, wanted, respond] of handlers) {
        if (input.includes(needle) && method === wanted) return Promise.resolve(respond());
      }
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
  return { calls };
}
