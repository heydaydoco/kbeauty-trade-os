// 거래처 화면 — 최소 렌더 + 여신한도 셀의 통화표 가드 회귀 (웹 세션 지적 이행).
//
// PR-1의 vitest 증가폭 +2는 sku-detail-prices.test.tsx였고 거래처 화면 vitest는
// 없었다 — PR-2 첫 프런트 커밋에서 보강한다. 가드 자체는 PR-1부터 있었다
// (partners.tsx 여신 셀 — 주의 인계 ⑦, product-detail-bom.test.tsx 선례).

import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const PARTNER = {
  id: 1,
  partner_code: "PTN-001",
  name_ko: "한국콜마",
  type_codes: ["OEM", "SUPPLIER"],
  credit_limit_amount: 1234,
  credit_limit_currency: "USD",
  dg_capable: null,
  strengths: null,
  weaknesses: null,
  note: null,
};

const CURRENCIES = { items: [{ code: "USD", minor_units: 2 }], total: 1, page: 1, size: 200 };

function stubApi(currenciesResponse: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([PARTNER])));
      if (input.includes("/v1/system/currencies")) return Promise.resolve(currenciesResponse());
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("거래처 목록", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록·유형·여신한도가 표시된다 (최소 렌더)", async () => {
    stubApi(() => jsonResponse(CURRENCIES));
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    expect(await screen.findByRole("heading", { name: "거래처" })).toBeInTheDocument();
    expect(await screen.findByText("한국콜마")).toBeInTheDocument();
    expect(screen.getByText("OEM · 공급사")).toBeInTheDocument();
    // 정수 최소단위 1234 → 통화표(minor_units 2)로 12.34 USD.
    await waitFor(() => {
      expect(screen.getByText("12.34 USD")).toBeInTheDocument();
    });
  });

  it("★ 통화표가 실패해도 화면이 살아 있다 — 여신 셀 가드 회귀 (주의 인계 ⑦)", async () => {
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
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    // 목록·거래처명이 계속 보인다(백지가 아니다). 가드가 없으면 여신 셀의
    // formatMoney가 빈 통화표에 예외를 던져 트리가 통째로 사라진다.
    expect(await screen.findByRole("heading", { name: "거래처" })).toBeInTheDocument();
    expect(await screen.findByText("한국콜마")).toBeInTheDocument();
    // 여신한도 값은 만들지 않는다 — 빈 표시(—)로 남는다.
    expect(screen.queryByText("12.34 USD")).not.toBeInTheDocument();
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(100);
  });
});
