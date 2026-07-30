// 제품 상세 — 자재 BOM 원가 마스킹의 화면 쪽 (§4.4 / ADR-0024).
//
// 서버가 원가 필드를 주는지가 진실이고, 화면은 그 결과를 따른다. 이 파일이
// 고정하는 것: 필드가 오면 열이 보이고, 안 오면 **열 자체가 없다**(빈 칸이나
// "0원"이 아니다).

import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

const PRODUCT = {
  id: 1,
  product_code: "PRD-001",
  name_ko: "수분 세럼",
  name_en: null,
  description: null,
  status: "ACTIVE",
  brand_id: 1,
  brand_code: "BRD-001",
  brand_name_ko: "테스트 브랜드",
  item_profile_id: null,
  item_profile_name_ko: null,
};

/** 업무에 필요한 값 — 두 역할 모두 보는 부분. */
const LINE_BASE = {
  id: 10,
  material_id: 5,
  material_code: "MAT-001",
  material_name_ko: "정제수",
  material_type: "RAW_MATERIAL",
  hs6: "330499",
  quantity: "12.5000",
  quantity_unit: "g",
  origin_status: "FOREIGN",
  note: null,
};

const CURRENCIES = { items: [{ code: "USD", minor_units: 2 }], total: 1, page: 1, size: 200 };

function stubApi(me: unknown, bomLine: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.endsWith("/v1/products/1")) return Promise.resolve(jsonResponse(PRODUCT));
      if (input.includes("/v1/products/1/boms"))
        return Promise.resolve(jsonResponse(page([bomLine])));
      if (input.includes("/v1/system/currencies")) return Promise.resolve(jsonResponse(CURRENCIES));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("제품 상세 — BOM 원가 마스킹", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("원가 필드가 오면 매입단가 열이 보인다", async () => {
    stubApi(TRADER, { ...LINE_BASE, unit_cost: 1234, currency: "USD" });
    renderWithProviders(<AppRoutes />, { route: "/products/1" });

    expect(await screen.findByRole("heading", { name: "자재 BOM" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("columnheader", { name: "매입단가" })).toBeInTheDocument();
    });
    // 표시 변환은 서버 자릿수로만 한다(USD 2자리 → 12.34).
    expect(screen.getByText(/12\.34/)).toBeInTheDocument();
  });

  it("★ 원가 필드가 없으면 열 자체가 없다 — 빈 칸이 아니다 (ADR-0024)", async () => {
    stubApi(VIEWER, LINE_BASE); // 서버가 unit_cost·currency를 주지 않은 응답
    renderWithProviders(<AppRoutes />, { route: "/products/1" });

    expect(await screen.findByRole("heading", { name: "자재 BOM" })).toBeInTheDocument();
    await waitFor(() => {
      // 업무에 필요한 값은 그대로 보인다 — 행을 감추지 않는다.
      expect(screen.getByText("MAT-001")).toBeInTheDocument();
    });
    expect(screen.getByText("역외")).toBeInTheDocument();
    // 원가 열은 없다.
    expect(screen.queryByRole("columnheader", { name: "매입단가" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("12.34");
  });

  it("원산지 미상이 '미상'으로 보인다 — 판정에서 역외로 간주된다는 설명과 함께", async () => {
    stubApi(TRADER, { ...LINE_BASE, origin_status: "UNKNOWN", unit_cost: null, currency: null });
    renderWithProviders(<AppRoutes />, { route: "/products/1" });

    expect(await screen.findByRole("heading", { name: "자재 BOM" })).toBeInTheDocument();
    // 설명 문구에도 "미상"이 나오므로 표 셀로 한정해 본다.
    await waitFor(() => {
      expect(screen.getByRole("cell", { name: "미상" })).toBeInTheDocument();
    });
    expect(screen.getByText(/미상은 원산지 판정에서 역외로 간주/)).toBeInTheDocument();
    // 단가가 null이면 값 자리는 비어 있다(열은 권한이 있으므로 존재한다).
    expect(screen.getByRole("columnheader", { name: "매입단가" })).toBeInTheDocument();
  });
});
