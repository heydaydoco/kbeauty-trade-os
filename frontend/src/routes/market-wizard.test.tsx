// 신규 시장 개설 위저드 (S2-4 PR-2 안건 ⑥).
//
// 고정하는 계약: ① 5단계와 서버 계산 상태·건수를 그대로 보인다(화면이 다시 계산하지 않는다)
// ② 협정은 "모듈 도래 전"으로 보이고 진행률 분모에서 빠진다(서버 값) ③ 고지문은 서버 문구 ④ T1 투입 버튼은
// 인증+관리자만 — 요청은 {markets:[코드]} ⑤ 카탈로그에 없는 시장은 투입 대신 안내 ⑥ 서버 오류 문구 표시.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";

const CERT_USER = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

const WIZARD = {
  code: "US",
  market_id: 1,
  market_name: "미국",
  catalog_available: true,
  catalog_template_count: 5,
  counted_steps: 4,
  done_steps: 1,
  steps: [
    { step: 1, key: "market", title: "시장 등록", status: "DONE", counts: {}, note: null },
    {
      step: 2,
      key: "templates",
      title: "요건 템플릿",
      status: "IN_PROGRESS",
      counts: { draft: 3, confirmed: 2, total: 5 },
      note: "초안은 근거링크를 열어 확인일을 입력한 뒤 확정해야 합니다.",
    },
    {
      step: 3,
      key: "hs",
      title: "HS 세번·세율",
      status: "TODO",
      counts: { hs_codes: 0 },
      note: "세율 마스터는 Phase 4에 도입됩니다.",
    },
    {
      step: 4,
      key: "agreements",
      title: "협정",
      status: "NOT_AVAILABLE",
      counts: {},
      note: "협정(FTA) 마스터는 Phase 4에 도입됩니다.",
    },
    { step: 5, key: "ingredient_rules", title: "성분 규칙", status: "TODO", counts: { rules: 0 }, note: null },
  ],
};

const SEEDS = {
  version: "2026-09-29.1",
  notice: ["서버가 준 고지문입니다 — 시스템이 규제를 단정하지 않습니다."],
  markets: [
    { code: "US", name_ko: "미국", registered: true },
    { code: "EU", name_ko: "유럽연합", registered: false },
  ],
};

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

function stubApi(me: unknown, calls: Recorded[] = [], wizard: unknown = WIZARD, applyStatus = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: input,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/seeds/t1/apply")) {
        if (applyStatus !== 200) {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "COMMON.VALIDATION.INVALID_FIELD",
                  message: "입력값이 올바르지 않습니다.",
                  detail: { markets: "시장 코드 US는 삭제된 시장이 점유하고 있어 투입할 수 없습니다." },
                  request_id: "r",
                },
              },
              applyStatus,
            ),
          );
        }
        return Promise.resolve(
          jsonResponse({
            version: "v",
            results: [{ code: "US", market_created: false, created: ["a", "b"], skipped: ["c"] }],
          }),
        );
      }
      if (input.includes("/v1/seeds/t1")) return Promise.resolve(jsonResponse(SEEDS));
      if (input.includes("/v1/market-wizard/")) return Promise.resolve(jsonResponse(wizard));
      return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, size: 50 }));
    }),
  );
}

describe("신규 시장 개설 위저드", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("시장을 고르기 전에는 안내만 보이고 위저드를 부르지 않는다", async () => {
    const calls: Recorded[] = [];
    stubApi(TRADER, calls);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard" });

    expect(await screen.findByText("시장 코드를 고르거나 입력해 주세요.")).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/v1/market-wizard/"))).toBe(false);
  });

  it("5단계와 서버 계산 상태·건수·진행률·고지문을 그대로 보인다", async () => {
    const calls: Recorded[] = [];
    stubApi(TRADER, calls);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard?market=us" });

    expect(await screen.findByText("1/4 단계 완료 (모듈 도래 전 단계는 제외)")).toBeInTheDocument();
    // 소문자 주소 파라미터도 대문자 코드로 부른다
    expect(calls.some((call) => call.url.endsWith("/v1/market-wizard/US"))).toBe(true);
    const steps = document.querySelectorAll("[data-step]");
    expect(steps).toHaveLength(5);
    const templates = within(steps[1] as HTMLElement);
    expect(templates.getByText("◐ 진행 중")).toBeInTheDocument();
    expect(templates.getByText("초안 3건 · 확정 2건")).toBeInTheDocument();
    expect(within(steps[3] as HTMLElement).getByText("– 모듈 도래 전")).toBeInTheDocument();
    expect(within(steps[2] as HTMLElement).getByText("등록된 HS 세번 0건")).toBeInTheDocument();
    expect(screen.getByText("서버가 준 고지문입니다 — 시스템이 규제를 단정하지 않습니다.")).toBeInTheDocument();
    // 딥링크
    expect(within(steps[1] as HTMLElement).getByRole("link", { name: "요건 템플릿 화면" })).toHaveAttribute(
      "href",
      "/requirement-templates",
    );
    expect(within(steps[2] as HTMLElement).getByRole("link", { name: "SKU 화면(HS 세번)" })).toHaveAttribute(
      "href",
      "/skus",
    );
  });

  it("무역 역할에는 투입 버튼이 없다 — 인증 역할만 T1 초안을 투입한다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard?market=US" });
    await screen.findByText("1/4 단계 완료 (모듈 도래 전 단계는 제외)");
    expect(screen.queryByRole("button", { name: /T1 초안/ })).not.toBeInTheDocument();
  });

  it("투입 버튼은 {markets:[코드]}로 요청하고 결과(투입·건너뜀)를 보인다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, calls);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard?market=US" });

    fireEvent.click(await screen.findByRole("button", { name: "T1 초안 5건 투입" }));
    expect(await screen.findByText("초안 2건 투입 · 1건 건너뜀")).toBeInTheDocument();
    const posted = calls.find((call) => call.url.includes("/v1/seeds/t1/apply"));
    expect(posted?.method).toBe("POST");
    expect(posted?.body).toEqual({ markets: ["US"] });
    // 투입 뒤 위저드를 다시 계산해 온다
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes("/v1/market-wizard/")).length).toBeGreaterThan(1),
    );
  });

  it("투입이 거부되면 서버 문구를 보인다", async () => {
    stubApi(CERT_USER, [], WIZARD, 422);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard?market=US" });
    fireEvent.click(await screen.findByRole("button", { name: "T1 초안 5건 투입" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("삭제된 시장이 점유하고 있어");
  });

  it("카탈로그에 없는 시장은 투입 대신 직접 등록 안내를 보인다", async () => {
    stubApi(CERT_USER, [], { ...WIZARD, code: "ZZ", catalog_available: false, catalog_template_count: 0 });
    renderWithProviders(<AppRoutes />, { route: "/market-wizard?market=ZZ" });
    expect(await screen.findByText(/T1 카탈로그에 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /T1 초안/ })).not.toBeInTheDocument();
  });

  it("칩을 누르면 그 시장으로 위저드가 열린다", async () => {
    const calls: Recorded[] = [];
    stubApi(TRADER, calls);
    renderWithProviders(<AppRoutes />, { route: "/market-wizard" });
    fireEvent.click(await screen.findByRole("button", { name: /EU 유럽연합/ }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/v1/market-wizard/EU"))).toBe(true));
  });
});
