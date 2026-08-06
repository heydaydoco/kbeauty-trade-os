// SKU 상세 — 단가 이력 표의 통화표 가드 (부채 #16 회귀, 웹 세션 재개 승인 2026-08-05).
//
// S1-2 PR-2가 제품 상세에서 실측·수정한 것과 같은 결함 클래스: formatMoney를
// 통화표 도착 전에 부르면(빈 배열 폴백 포함) 렌더 중 예외 → 트리 언마운트 →
// 화면 백지. ListState의 isPending/error 가드는 children 평가를 못 막는다
// (주의 인계 ⑦). 이 파일이 그 백지가 다시는 안 생기게 지킨다.

import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const SKU = {
  id: 1,
  sku_code: "SER-001",
  name_ko: "수분 세럼 30ml",
  name_en: null,
  status: "ACTIVE",
  kind: "SINGLE",
  product_id: 1,
  product_code: "PRD-001",
  product_name_ko: "수분 세럼",
  brand_name_ko: "테스트 브랜드",
  item_profile_id: null,
  item_profile_name_ko: null,
  barcode: null,
  unit_weight_g: null,
  box_qty: null,
  shelf_life_months: null,
  manufacturer_partner_id: null,
  manufacturer_name: null,
  dg_flag: false,
  un_number: null,
  dg_class: null,
  packing_group: null,
  flash_point_c: null,
  alcohol_content_pct: null,
  is_aerosol: false,
  is_limited_quantity: false,
};

const PRICE_ROW = {
  id: 7,
  price_type: "SALES",
  amount: 2990,
  currency: "USD",
  effective_from: "2026-08-01",
  is_current: true,
  note: null,
};

const CURRENCIES = { items: [{ code: "USD", minor_units: 2 }], total: 1, page: 1, size: 200 };

function stubApi(currenciesResponse: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      if (input.endsWith("/v1/skus/1")) return Promise.resolve(jsonResponse(SKU));
      if (input.includes("/v1/skus/1/prices")) return Promise.resolve(jsonResponse(page([PRICE_ROW])));
      if (input.includes("/v1/system/currencies")) return Promise.resolve(currenciesResponse());
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("SKU 상세 — 단가 이력 통화표 가드 (부채 #16)", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("통화표가 도착하면 서버 자릿수로 금액이 보인다 (USD 2990 → 29.90)", async () => {
    stubApi(() => jsonResponse(CURRENCIES));
    renderWithProviders(<AppRoutes />, { route: "/skus/1" });

    expect(await screen.findByRole("heading", { name: "수분 세럼 30ml" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/29\.90 USD/)).toBeInTheDocument();
    });
  });

  it("★ 통화표가 실패해도 화면이 살아 있다 — 렌더 중 예외 금지 (부채 #16 회귀)", async () => {
    stubApi(() =>
      jsonResponse(
        {
          error: {
            code: "COMMON.INTERNAL.UNEXPECTED",
            message: "일시적인 오류입니다.",
            detail: {},
            request_id: null,
          },
        },
        500,
      ),
    );
    renderWithProviders(<AppRoutes />, { route: "/skus/1" });

    // 제목·다른 섹션이 계속 보인다(백지가 아니다). 수정 전에는 단가 행의
    // formatMoney가 빈 통화표 폴백으로 예외를 던져 트리가 통째로 사라졌다.
    expect(await screen.findByRole("heading", { name: "수분 세럼 30ml" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "단가 이력" })).toBeInTheDocument();
    });
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(100);
  });
});
