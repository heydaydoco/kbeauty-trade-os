// SKU 목록 51건+ 쪽 이동 회귀 (판정 ② 2026-08-06 — 공용 목록 계층 1회 구현).
//
// 이 파일이 고정하는 것: ① 51건째가 조용히 사라지지 않는다 — 총계·쪽 표시가
// 있고 "다음"이 2쪽을 보여준다(50건 표시 한계 관찰 항목의 종결 회귀). ② 1쪽
// 요청 주소는 기존과 같다(page 파라미터 없음 — 서버 계약 §18.4 불변). ③ 2쪽은
// page 파라미터 하나로 조회한다. ④ 마지막 쪽에서 "다음"은 눌리지 않는다.

import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { SkuListPage } from "./skus";

function makeSku(n: number) {
  return {
    id: n,
    sku_code: `RGR-${String(n).padStart(3, "0")}`,
    name_ko: `회귀 세럼 ${n}호`,
    name_en: null,
    status: "ACTIVE",
    kind: "SINGLE",
    product_id: 7,
    product_code: "PRD-001",
    product_name_ko: "수분 세럼 처방",
    brand_name_ko: "테스트 브랜드",
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
}

// 서버 기본 50건 + 1건 — 관찰 항목 문면("51건째부터 표에 안 보인다") 그대로의 경계.
const PAGE_1 = {
  items: Array.from({ length: 50 }, (_, index) => makeSku(index + 1)),
  total: 51,
  page: 1,
  size: 50,
};
const PAGE_2 = { items: [makeSku(51)], total: 51, page: 2, size: 50 };

function stubPagedApi() {
  const fetchMock = vi.fn((input: string) => {
    if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(VIEWER));
    if (input.includes("/v1/skus")) {
      if (input.includes("page=2")) return Promise.resolve(jsonResponse(PAGE_2));
      return Promise.resolve(jsonResponse(PAGE_1));
    }
    return Promise.resolve(jsonResponse(page([])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("SKU 목록 쪽 이동 (51건+ 회귀)", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-idempotency-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("★ 51건째는 1쪽 표에 없지만 총계·쪽 표시로 존재가 보인다", async () => {
    const fetchMock = stubPagedApi();
    renderWithProviders(<SkuListPage />);

    await screen.findByText("회귀 세럼 1호");
    const table = within(screen.getByRole("table"));
    expect(table.getByText("회귀 세럼 50호")).toBeInTheDocument();
    expect(table.queryByText("회귀 세럼 51호")).not.toBeInTheDocument();

    expect(screen.getByText("전체 51건")).toBeInTheDocument();
    expect(screen.getByText("1/2쪽")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();

    // 1쪽 요청 주소는 기존 계약 그대로다 — page 파라미터가 붙지 않는다.
    const firstListCall = fetchMock.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes("/v1/skus"));
    expect(firstListCall).toBe("/api/v1/skus");
  });

  it("★ '다음'을 누르면 page 파라미터로 2쪽을 조회해 51건째를 보여준다", async () => {
    const fetchMock = stubPagedApi();
    renderWithProviders(<SkuListPage />);

    await screen.findByText("회귀 세럼 1호");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));

    expect(await screen.findByText("회귀 세럼 51호")).toBeInTheDocument();
    expect(screen.getByText("2/2쪽")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();

    const pagedCall = fetchMock.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes("page=2"));
    expect(pagedCall).toBe("/api/v1/skus?page=2");

    // 되돌아오기 — 1쪽 내용이 다시 보인다.
    fireEvent.click(screen.getByRole("button", { name: "이전" }));
    expect(await screen.findByText("회귀 세럼 1호")).toBeInTheDocument();
  });
});
