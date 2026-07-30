// 제품 상세 — 스크리닝 리포트 렌더 (§4.3 / 웹 세션 판정 D).
//
// 여기서 고정하는 것: 3분류가 한국어 라벨(금지/제한초과/미등재)로 보이고,
// 고정 문구 "참고용·근거 확인"이 있으며, 판정 워딩(적합/통과/합격)이 화면
// 어디에도 없다는 부정 단언.

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

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

const REPORT = {
  product_id: 1,
  countries: ["US", "EU"],
  notice: "참고용·근거 확인",
  checked_ingredient_count: 3,
  within_limit_count: 1,
  findings: [
    {
      classification: "PROHIBITED",
      country_code: "US",
      ingredient_id: 11,
      inci_name: "Hydroquinone",
      ingredient_name_ko: "하이드로퀴논",
      display_order: 1,
      concentration_pct: "1.0000",
      max_concentration_pct: null,
      source_url: "https://www.fda.gov/cosmetics",
      last_verified_on: "2026-07-30",
      note: null,
    },
    {
      classification: "OVER_LIMIT",
      country_code: "EU",
      ingredient_id: 12,
      inci_name: "Retinol",
      ingredient_name_ko: null,
      display_order: 2,
      concentration_pct: null,
      max_concentration_pct: "0.3000",
      source_url: "https://ec.europa.eu/cosing",
      last_verified_on: "2026-07-30",
      note: "함량 미입력 — 한도와 비교할 수 없어 보수적으로 분류",
    },
    {
      classification: "UNLISTED",
      country_code: "EU",
      ingredient_id: 13,
      inci_name: "Bakuchiol",
      ingredient_name_ko: null,
      display_order: 3,
      concentration_pct: "0.5000",
      max_concentration_pct: null,
      source_url: null,
      last_verified_on: null,
      note: null,
    },
  ],
};

describe("제품 상세 — 스크리닝", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/screening")) return Promise.resolve(jsonResponse(REPORT));
        if (input.endsWith("/v1/products/1")) return Promise.resolve(jsonResponse(PRODUCT));
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function runScreening() {
    renderWithProviders(<AppRoutes />, { route: "/products/1" });
    await screen.findByRole("heading", { name: "수분 세럼" });

    fireEvent.change(screen.getByLabelText(/대상국/), { target: { value: "us, eu" } });
    fireEvent.click(screen.getByRole("button", { name: "스크리닝 실행" }));
    await screen.findByText("참고용·근거 확인");
  }

  it("3분류가 한국어 라벨로 렌더된다 (금지/제한초과/미등재)", async () => {
    await runScreening();

    expect(screen.getByText("금지")).toBeInTheDocument();
    expect(screen.getByText("제한초과")).toBeInTheDocument();
    expect(screen.getByText("미등재")).toBeInTheDocument();
    // 보수 분류의 사유는 사실 서술로 보인다.
    expect(screen.getByText(/함량 미입력/)).toBeInTheDocument();
    // 미등재는 근거 자리가 비어 있고, 그 부재가 "확인 필요"의 신호다.
    expect(screen.getByText("근거 없음 — 확인 필요")).toBeInTheDocument();
  });

  it("소문자 입력이 대문자 코드로 요청된다 (us, eu → US·EU)", async () => {
    await runScreening();

    const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(
      (call) => call[0] as string,
    );
    const screeningCall = calls.find((url) => url.includes("/screening"));
    expect(screeningCall).toContain("country=US");
    expect(screeningCall).toContain("country=EU");
  });

  it("★ 판정 워딩이 화면에 없다 — 부정 단언 (웹 세션 판정 D-②)", async () => {
    await runScreening();

    await waitFor(() => {
      for (const verdictWord of ["적합", "통과", "합격"]) {
        expect(document.body.textContent).not.toContain(verdictWord);
      }
    });
    // 집계는 사실 서술로만 나온다.
    expect(screen.getByText(/한도 이내 1건/)).toBeInTheDocument();
  });
});
